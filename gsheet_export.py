"""
Live export of race passages to a Google Sheet.

Uses only Python's standard library (urllib, http.server, hashlib) to talk
directly to Google's OAuth2 and REST APIs (Sheets v4, Drive v3) — no
google-auth / google-api-python-client / cryptography needed, so there is
nothing to compile on any platform, including 32-bit Windows.

Each running instance of the app only ever writes to its own column
(Start or Finish) for a given bib, in a tab named "Round <N>" inside a
single spreadsheet named "RFID Time Racing" at the root of the user's
Google Drive. A start-line station and a finish-line station (which may
be two separate computers/instances) end up merging their data live, in
the same spreadsheet, simply by both targeting the same round tab.

Setup required (one-time, per Google account):
  1. In Google Cloud Console, create/select a project and enable the
     "Google Sheets API" and "Google Drive API".
  2. Create an OAuth 2.0 Client ID of type "Desktop app" (Cloud Console ->
     APIs & Services -> Credentials -> Create Credentials -> OAuth client
     ID). You only need its Client ID and Client Secret.
  3. The first time "Export live Google Sheet" is clicked in the app, a
     "Google Sheet Setup" window asks for that Client ID / Client Secret
     (with a button that opens the right Cloud Console page) and writes
     `credentials.json` for you.
  4. Right after that, a browser window opens asking you to sign in and
     grant access (PKCE authorization-code flow); the resulting token is
     cached in `token.json` so this only happens once per machine (until
     the token is revoked or the file is deleted).

No pip install needed for this feature.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SPREADSHEET_NAME = "RFID Time Racing"
HEADER_ROW = ["Bib", "Team", "Start", "Finish", "Duration"]

# Google Sheets' date-time serial epoch (days since 1899-12-30). Sending
# timestamps as this raw number, instead of a text string, sidesteps any
# locale-dependent auto-parsing on Google's side (the actual cause of
# "#VALUE!" errors when the Start/Finish cells end up stored as plain
# text instead of real date-time values).
_SHEETS_EPOCH = datetime(1899, 12, 30)


def _to_sheets_serial(timestamp: str) -> float:
    dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    delta = dt - _SHEETS_EPOCH
    return delta.days + delta.seconds / 86400

GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
GOOGLE_CLOUD_APIS_LIBRARY_URL = "https://console.cloud.google.com/apis/library"


def write_credentials_file(path: str, client_id: str, client_secret: str):
    """Writes a small credentials.json with just what this module needs."""
    content = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)


def credentials_path_for(base_dir: str) -> str:
    return os.path.join(base_dir, "credentials.json")


def _formula_separator(locale: str) -> str:
    """Google Sheets formula argument separator depends on the spreadsheet's
    locale: English-family locales use a comma, most others (French,
    German, Spanish, etc.) use a semicolon. Writing a formula with the
    wrong separator via USER_ENTERED doesn't error at write time — it just
    fails to parse later, showing "#ERROR!" ("Erreur d'analyse de formule")
    no matter how correct the underlying logic is."""
    if not locale:
        return ","
    comma_locales = {"en", "en_us", "en_gb", "en_ca", "en_au", "en_in", "en_ie", "en_nz", "en_za"}
    lang = locale.lower().split("_")[0]
    if locale.lower() in comma_locales or lang == "en":
        return ","
    return ";"


def _duration_formula(row: int, sep: str = ",") -> str:
    """Duration formula for a given row: a plain D - C subtraction. Both
    cells are always written as real Sheets date-time serial numbers (see
    _to_sheets_serial / _format_datetime_columns), so a simple subtraction
    is enough; the E column is formatted as a duration ([h]:mm:ss) so
    Sheets displays the result as elapsed time on its own."""
    c, d = f"C{row}", f"D{row}"
    return f'=IF(AND({c}<>""{sep}{d}<>""){sep}{d}-{c}{sep}"")'


class GoogleSheetError(Exception):
    pass


class _RedirectCaptureHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single OAuth redirect (?code=...) and shows a simple page."""

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        self.server.oauth_params = params  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in params:
            body = "<html><body><h2>Signed in. You can close this window.</h2></body></html>"
        else:
            body = "<html><body><h2>Sign-in failed or was cancelled.</h2></body></html>"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt, *args):
        pass  # keep the console quiet


class GoogleSheetExporter:
    """
    Handles the OAuth2 (PKCE) sign-in, finding/creating the spreadsheet and
    round tabs, and pushing individual passage rows in real time.

    All methods that talk to Google are synchronous/blocking (including the
    OAuth browser flow) — callers should run `connect()` off the UI thread.
    """

    def __init__(self, base_dir: str):
        self._credentials_path = credentials_path_for(base_dir)
        self._token_path = os.path.join(base_dir, "token.json")
        self._client_id = None
        self._client_secret = None
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0
        self.spreadsheet_id: str | None = None
        # per-tab cache: {round_tab_name: {bib: row_number}}
        self._row_cache: dict[str, dict[str, int]] = {}
        self._formatted_tabs: set[str] = set()
        self._sheet_ids: dict[str, int] = {}
        self._formula_sep = ","

    # ------------------------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------------------------
    def connect(self):
        """Loads credentials.json, reuses/refreshes a cached token if possible,
        otherwise runs the browser sign-in flow. Then makes sure the
        'RFID Time Racing' spreadsheet exists. Raises GoogleSheetError on failure."""
        if not os.path.isfile(self._credentials_path):
            raise GoogleSheetError(
                f"credentials.json not found at {self._credentials_path}. "
                "Use the Google Sheet Setup window to create it."
            )
        with open(self._credentials_path, "r", encoding="utf-8") as f:
            creds = json.load(f)
        self._client_id = creds["client_id"]
        self._client_secret = creds["client_secret"]

        if os.path.isfile(self._token_path):
            try:
                with open(self._token_path, "r", encoding="utf-8") as f:
                    tok = json.load(f)
                self._access_token = tok["access_token"]
                self._refresh_token = tok["refresh_token"]
                self._expires_at = tok.get("expires_at", 0)
                self._ensure_fresh_token()
            except Exception:
                self._access_token = None  # fall through to a fresh sign-in

        if not self._access_token:
            self._run_browser_sign_in()

        self._ensure_spreadsheet()

    def _run_browser_sign_in(self):
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")

        server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectCaptureHandler)
        server.oauth_params = {}
        port = server.server_address[1]
        # Google's loopback OAuth flow (Desktop app clients) matches
        # "http://localhost:<port>" specifically — use that exact host,
        # even though the server itself binds to the loopback IP either way.
        redirect_uri = f"http://localhost:{port}"

        auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode({
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })

        webbrowser.open(auth_url)
        server.timeout = 180
        server.handle_request()  # blocks until the single redirect arrives (or times out)
        params = getattr(server, "oauth_params", {})
        server.server_close()

        if "code" not in params:
            raise GoogleSheetError("Google sign-in was cancelled or timed out.")
        code = params["code"][0]

        token_resp = self._post_form(TOKEN_ENDPOINT, {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        self._store_token(token_resp)

    def _ensure_fresh_token(self):
        if self._access_token and time.time() < self._expires_at - 60:
            return  # still valid for at least another minute
        token_resp = self._post_form(TOKEN_ENDPOINT, {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        })
        # a refresh response usually omits refresh_token (it stays the same)
        token_resp.setdefault("refresh_token", self._refresh_token)
        self._store_token(token_resp)

    def _store_token(self, token_resp: dict):
        self._access_token = token_resp["access_token"]
        self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
        self._expires_at = time.time() + float(token_resp.get("expires_in", 3600))
        with open(self._token_path, "w", encoding="utf-8") as f:
            json.dump({
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
            }, f, indent=2)

    # ------------------------------------------------------------------------------------
    # Low-level HTTP helpers (stdlib only)
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _post_form(url: str, fields: dict) -> dict:
        data = urllib.parse.urlencode(fields).encode("ascii")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            raise GoogleSheetError(f"Google token request failed: {ex.read().decode('utf-8', 'ignore')}") from ex

    def _api(self, method: str, url: str, body: dict = None, retry: bool = True) -> dict:
        self._ensure_fresh_token()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._access_token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as ex:
            if ex.code == 401 and retry:
                self._expires_at = 0  # force a refresh and retry once
                return self._api(method, url, body, retry=False)
            raise GoogleSheetError(
                f"Google API request failed ({ex.code}): {ex.read().decode('utf-8', 'ignore')}"
            ) from ex

    # ------------------------------------------------------------------------------------
    def _ensure_spreadsheet(self):
        query = (
            f"name = '{SPREADSHEET_NAME}' and "
            "mimeType = 'application/vnd.google-apps.spreadsheet' and "
            "'root' in parents and trashed = false"
        )
        url = DRIVE_API + "?" + urllib.parse.urlencode({"q": query, "fields": "files(id,name)"})
        resp = self._api("GET", url)
        files = resp.get("files", [])
        if files:
            self.spreadsheet_id = files[0]["id"]
            print(f"[DEBUG] gsheet: reusing existing spreadsheet id={self.spreadsheet_id} "
                  f"(found {len(files)} match(es))", file=sys.stderr)
            return

        resp = self._api("POST", SHEETS_API, {"properties": {"title": SPREADSHEET_NAME}})
        self.spreadsheet_id = resp["spreadsheetId"]
        print(f"[DEBUG] gsheet: created NEW spreadsheet id={self.spreadsheet_id}", file=sys.stderr)

    # ------------------------------------------------------------------------------------
    def ensure_round_tab(self, round_num: int) -> str:
        """Makes sure a 'Round <N>' tab exists (with the header row), returns its name."""
        tab_name = f"Round {round_num}"
        meta = self._api("GET", f"{SHEETS_API}/{self.spreadsheet_id}")
        self._formula_sep = _formula_separator(meta.get("properties", {}).get("locale", ""))
        sheet_id = None
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == tab_name:
                sheet_id = s["properties"]["sheetId"]
                break

        if sheet_id is None:
            add_resp = self._api(
                "POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                {"requests": [{"addSheet": {"properties": {"title": tab_name}}}]})
            sheet_id = add_resp["replies"][0]["addSheet"]["properties"]["sheetId"]
            self._values_update(tab_name, "A1:E1", [HEADER_ROW])

        # Apply/refresh the Start/Finish date-time format once per tab per
        # session — harmless to repeat, and also fixes tabs that were
        # created by an older version of this module before this format
        # was introduced.
        if tab_name not in self._formatted_tabs:
            self._format_datetime_columns(tab_name, sheet_id)
            self._formatted_tabs.add(tab_name)

        self._sheet_ids[tab_name] = sheet_id
        if tab_name not in self._row_cache:
            self._row_cache[tab_name] = self._load_bib_rows(tab_name)
        return tab_name

    def _load_bib_rows(self, tab_name: str) -> dict:
        resp = self._values_get(tab_name, "A2:A")
        rows = resp.get("values", [])
        return {row[0]: idx + 2 for idx, row in enumerate(rows) if row}

    def _values_get(self, tab_name: str, a1_range: str) -> dict:
        rng = urllib.parse.quote(f"'{tab_name}'!{a1_range}", safe="")
        return self._api("GET", f"{SHEETS_API}/{self.spreadsheet_id}/values/{rng}")

    def _values_update(self, tab_name: str, a1_range: str, values: list, raw: bool = False):
        rng = urllib.parse.quote(f"'{tab_name}'!{a1_range}", safe="")
        option = "RAW" if raw else "USER_ENTERED"
        url = f"{SHEETS_API}/{self.spreadsheet_id}/values/{rng}?valueInputOption={option}"
        self._api("PUT", url, {"values": values})

    def _values_append(self, tab_name: str, values: list, raw: bool = False) -> dict:
        rng = urllib.parse.quote(f"'{tab_name}'!A:A", safe="")
        option = "RAW" if raw else "USER_ENTERED"
        url = (f"{SHEETS_API}/{self.spreadsheet_id}/values/{rng}:append"
               f"?valueInputOption={option}&insertDataOption=INSERT_ROWS")
        return self._api("POST", url, {"values": values})

    def _write_date_cell(self, sheet_id: int, row: int, column_index: int, serial: float):
        """Sets a cell's value AND its date-time number format in a single
        atomic batchUpdate call. Needed specifically when writing into a
        cell that already exists but was previously blank (e.g. filling in
        Finish on a row created earlier for Start-only): a plain
        values.update on such a cell does NOT reliably keep a number format
        that was applied to it while it was still empty — Sheets falls back
        to auto-detecting a format from the new value (which is locale-
        dependent), which is exactly the "dd/mm/yyyy vs yyyy-mm-dd" mismatch
        this works around."""
        self._api("POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate", {"requests": [{
            "updateCells": {
                "start": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": column_index},
                "rows": [{"values": [{
                    "userEnteredValue": {"numberValue": serial},
                    "userEnteredFormat": {"numberFormat": {
                        "type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss",
                    }},
                }]}],
                "fields": "userEnteredValue,userEnteredFormat.numberFormat",
            }
        }]})

    def _format_datetime_columns(self, tab_name: str, sheet_id: int):
        """Formats columns C:D (Start/Finish) as date-time and column E
        (Duration) as an elapsed-time duration, once per tab, so the raw
        numeric values we write display correctly without any TEXT()
        string conversion."""
        self._api("POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate", {"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startColumnIndex": 2, "endColumnIndex": 4},
                    "cell": {"userEnteredFormat": {"numberFormat": {
                        "type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss",
                    }}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startColumnIndex": 4, "endColumnIndex": 5},
                    "cell": {"userEnteredFormat": {"numberFormat": {
                        "type": "TIME", "pattern": "[h]:mm:ss",
                    }}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
        ]})

    # ------------------------------------------------------------------------------------
    def push_passage(self, round_num: int, mode: str, bib: str, team: str, timestamp: str):
        """mode is 'start_line' or 'finish_line'. Writes only that column; the
        Duration column formula (set when the row is created) recomputes itself
        live once both Start and Finish are present — no read-modify race
        between two independent app instances."""
        tab_name = self.ensure_round_tab(round_num)
        cache = self._row_cache[tab_name]
        column = "C" if mode == "start_line" else "D"
        serial = _to_sheets_serial(timestamp)
        print(f"[DEBUG] gsheet: push_passage sheet={self.spreadsheet_id} tab={tab_name!r} "
              f"mode={mode} bib={bib!r} timestamp={timestamp!r} serial={serial} "
              f"bib_in_cache={bib in cache}", file=sys.stderr)

        if bib in cache:
            row = cache[bib]
            col_index = 2 if mode == "start_line" else 3  # C=2, D=3 (0-based)
            self._write_date_cell(self._sheet_ids[tab_name], row, col_index, serial)
            print(f"[DEBUG] gsheet: updated existing row {row}, cell {column}{row}", file=sys.stderr)
        else:
            start_val = serial if mode == "start_line" else ""
            finish_val = serial if mode == "finish_line" else ""
            append_resp = self._values_append(
                tab_name, [[bib, team, start_val, finish_val, ""]], raw=True)
            updated_range = append_resp["updates"]["updatedRange"]  # e.g. "'Round 1'!A5:E5"
            row = int("".join(ch for ch in updated_range.split("!")[1].split(":")[0] if ch.isdigit()))
            cache[bib] = row
            print(f"[DEBUG] gsheet: appended new row {row} (updatedRange={updated_range!r})",
                  file=sys.stderr)
            self._values_update(
                tab_name, f"E{row}",
                [[_duration_formula(row, self._formula_sep)]],
            )


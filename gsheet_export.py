"""
Live export of race passages to a Google Sheet.

Each running instance of the app only ever writes to its own column
(Start or Finish) for a given bib, in a tab named "Round <N>" inside a
single spreadsheet named "RFID Time Racing" at the root of the user's
Google Drive. A start-line station and a finish-line station (which may
be two separate computers/instances) end up merging their data live,
in the same spreadsheet, simply by both targeting the same round tab.

Setup required (one-time, per Google account):
  1. In Google Cloud Console, create/select a project and enable the
     "Google Sheets API" and "Google Drive API".
  2. Create an OAuth 2.0 Client ID of type "Desktop app", download its
     JSON, and save it as `credentials.json` next to this file (or next
     to the packaged .exe).
  3. The first time "Export live Google Sheet" is clicked, a browser
     window opens asking you to sign in and grant access; the resulting
     token is cached in `token.json` so this only happens once per
     machine (until the token is revoked or deleted).

Dependencies (not needed unless this feature is used):
    pip install google-auth-oauthlib google-api-python-client google-auth-httplib2
"""
import os

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SPREADSHEET_NAME = "RFID Time Racing"
HEADER_ROW = ["Bib", "Team", "Start", "Finish", "Duration"]


class GoogleSheetError(Exception):
    pass


class GoogleSheetExporter:
    """
    Handles Google auth + finding/creating the spreadsheet and round tabs,
    and pushing individual passage rows in real time.

    All methods that talk to Google are synchronous/blocking (including
    the OAuth browser flow) — callers should run `connect()` off the UI
    thread and marshal the result back via the usual ui_queue mechanism.
    """

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._credentials_path = os.path.join(base_dir, "credentials.json")
        self._token_path = os.path.join(base_dir, "token.json")
        self._service = None
        self._drive_service = None
        self.spreadsheet_id: str | None = None
        # per-tab cache: {round_tab_name: {bib: row_number}}
        self._row_cache: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------------------------
    def connect(self):
        """Authenticates (opening a browser window if needed) and makes sure the
        'RFID Time Racing' spreadsheet exists. Raises GoogleSheetError on failure."""
        if not os.path.isfile(self._credentials_path):
            raise GoogleSheetError(
                f"credentials.json not found at {self._credentials_path}. "
                "See gsheet_export.py's module docstring for the one-time Google "
                "Cloud setup steps."
            )

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as ex:
            raise GoogleSheetError(
                "Missing dependency: pip install google-auth-oauthlib "
                "google-api-python-client google-auth-httplib2"
            ) from ex

        creds = None
        if os.path.isfile(self._token_path):
            try:
                creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self._credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)  # opens the Google sign-in window
            with open(self._token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        self._service = build("sheets", "v4", credentials=creds)
        self._drive_service = build("drive", "v3", credentials=creds)

        self._ensure_spreadsheet()

    # ------------------------------------------------------------------------------------
    def _ensure_spreadsheet(self):
        query = (
            f"name = '{SPREADSHEET_NAME}' and "
            "mimeType = 'application/vnd.google-apps.spreadsheet' and "
            "'root' in parents and trashed = false"
        )
        resp = self._drive_service.files().list(q=query, spaces="drive",
                                                  fields="files(id, name)").execute()
        files = resp.get("files", [])
        if files:
            self.spreadsheet_id = files[0]["id"]
            return

        body = {"properties": {"title": SPREADSHEET_NAME}}
        sheet = self._service.spreadsheets().create(body=body, fields="spreadsheetId").execute()
        self.spreadsheet_id = sheet["spreadsheetId"]
        # newly created spreadsheets land in "My Drive" root by default already.

    # ------------------------------------------------------------------------------------
    def ensure_round_tab(self, round_num: int) -> str:
        """Makes sure a 'Round <N>' tab exists (with the header row), returns its name."""
        tab_name = f"Round {round_num}"
        meta = self._service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        existing_titles = [s["properties"]["title"] for s in meta.get("sheets", [])]

        if tab_name not in existing_titles:
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
            self._service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!A1:E1",
                valueInputOption="USER_ENTERED",
                body={"values": [HEADER_ROW]},
            ).execute()

        if tab_name not in self._row_cache:
            self._row_cache[tab_name] = self._load_bib_rows(tab_name)
        return tab_name

    def _load_bib_rows(self, tab_name: str) -> dict:
        resp = self._service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"'{tab_name}'!A2:A"
        ).execute()
        rows = resp.get("values", [])
        return {row[0]: idx + 2 for idx, row in enumerate(rows) if row}

    # ------------------------------------------------------------------------------------
    def push_passage(self, round_num: int, mode: str, bib: str, team: str, timestamp: str):
        """mode is 'start_line' or 'finish_line'. Writes only that column; the
        Duration column formula (set when the row is created) recomputes itself
        live once both Start and Finish are present — no read-modify race
        between two independent app instances."""
        tab_name = self.ensure_round_tab(round_num)
        cache = self._row_cache[tab_name]
        column = "C" if mode == "start_line" else "D"

        if bib in cache:
            row = cache[bib]
            self._service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!{column}{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[timestamp]]},
            ).execute()
        else:
            start_val = timestamp if mode == "start_line" else ""
            finish_val = timestamp if mode == "finish_line" else ""
            append_resp = self._service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!A:A",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [[bib, team, start_val, finish_val, ""]]},
            ).execute()
            updated_range = append_resp["updates"]["updatedRange"]  # e.g. "'Round 1'!A5:E5"
            row = int("".join(ch for ch in updated_range.split("!")[1].split(":")[0] if ch.isdigit()))
            cache[bib] = row
            # Duration formula, written once when the row is created.
            self._service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!E{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[f'=IF(AND(C{row}<>"",D{row}<>""),TEXT(D{row}-C{row},"HH:MM:SS"),"")']]},
            ).execute()

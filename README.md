# RFID Time Racing

A desktop app (Python 3 / Tkinter) for timing races with UHF RFID tags:
scan tags at a start line and a finish line, cross-reference them by bib
number against a teams list, and export the results.

## Features

- **Connection** — opens/closes the UHF reader over USB/HID. Auto-connects
  on startup to the `\USB-open` interface (not `\Keyboard-can'topen`). The
  button turns **green** while connected.
- **Device Parameters** — RF power, work mode, output interface, buzzer
  on/off.
- **Frequency** — region/band selection and channel start/end.
- **Capture RFID** — a resizable popup with the raw list of every tag
  detected (No., data, length, per-antenna count, RSSI, channel), with its
  own independent **Start/Stop** (no round or team file needed), plus
  **Export** and **Clear**.
- **Start line / Finish line / Round** — two mutually exclusive choices
  (picking one deselects the other) and a round selector (1–10, default 1).
  Selecting Start line or Finish line is required before the main **Start**
  button will run.
- **teams.csv on Start** — clicking Start prompts for a CSV file with 3
  columns: **bib**, **rfid** (hex code), **team** (optional); with or
  without a header row, `;` or `,` delimiter auto-detected. Tags found in
  the file show as green rows, everything else as red.
- **Real-time CSV export** — right after teams.csv is loaded, you pick a
  destination folder; a `<start_line|finish_line>_round_<N>_<timestamp>.csv`
  file is created (bib, rfid, team, passage_time) and a row is appended and
  flushed immediately the first time each authorized tag is seen.
- **First-passage log** — a second list (Pass #, Bib, RFID, Team, Passage
  time) recording each authorized tag exactly once, reset at every Start.
- **Results** — pick a start_line file and a finish_line file (same
  round), and it cross-references every bib to compute Start / Finish /
  Duration, sorted fastest first, with an **Export CSV** button.
- **Export live Google Sheet** — pushes every passage live to a Google
  Sheet, merging start-line and finish-line data (even from two separate
  computers) into the same "Round N" tab. See [Google Sheet export
  setup](#google-sheet-export-setup) below. The button turns **green**
  once connected and the spreadsheet is ready. **Offline-resilient**: if a
  push fails (no internet, DNS down, Google unreachable, ...), the passage
  is queued to a local `gsheet_pending.jsonl` file instead of being lost,
  and automatically re-sent every 15s once the connection comes back —
  including across an app restart, since the queue is saved to disk.
- **Log window** — full timestamped history, buffered even before the
  window is first opened.

## Project layout

| File | Contents |
|---|---|
| `main.py` | Entry point |
| `main_window.py` | Tkinter UI and app logic |
| `reader_api.py` | `ctypes` bindings to the reader DLL + a high-level `Reader` wrapper |
| `reader_exception.py` | Reader error codes/exception |
| `channel_region.py` | Regional channel/frequency plans (USA, Europe, China, ...) |
| `freq_info.py` | Small frequency-plan value object |
| `tag_item.py` | Interop structs (`TagInfo`, `Devicepara`) and tag value objects |
| `util.py` | Hex/decimal parsing, CRC16 |
| `arch_check.py` | Warns at startup if Python isn't 32-bit (see below) |
| `gsheet_export.py` | Google Sheets live export (OAuth + Sheets/Drive API) |
| `lib/` | Bundled `UHFPrimeReader.dll` + `hidapi.dll` |
| `icon.ico` | App icon |
| `build_exe.bat` | Builds a standalone Windows `.exe` with PyInstaller |

## Google Sheet export setup

The **Export live Google Sheet** button needs a one-time setup per Google
account, since it can't create its own Google Cloud project on your
behalf. No extra `pip install` is needed for this feature — it talks to
Google's OAuth2 and REST APIs using only Python's standard library, so
there's nothing to compile on any platform (including 32-bit Windows,
where packages like `cryptography` often have no prebuilt wheel and
would otherwise require a full MSVC/Rust toolchain just to install).

1. In the [Google Cloud Console](https://console.cloud.google.com/), create
   (or pick) a project, then enable the **Google Sheets API** and the
   **Google Drive API** for it.
2. Under *APIs & Services → Credentials*, create an **OAuth client ID** of
   type **Desktop app**. Keep its **Client ID** and **Client Secret** handy
   — you don't need to download or rename any JSON file yourself.
3. Click **Export live Google Sheet** in the app: since no `credentials.json`
   exists yet, a **"Google Sheet Setup"** window opens with buttons that
   jump straight to the right Cloud Console pages, and two fields for the
   Client ID / Client Secret. Click **Save & Connect** — the app writes
   `credentials.json` for you, then a browser window opens asking you to
   sign in and grant access (PKCE authorization-code flow). The resulting
   token is cached in `token.json` next to it, so this only happens once
   per machine.

Once connected, a spreadsheet named **"RFID Time Racing"** is created (if
it doesn't already exist) at the root of your Drive, with one tab per
round (**"Round 1"**, **"Round 2"**, ...). A start-line station only ever
writes the *Start* column for a bib, a finish-line station only the
*Finish* column; the *Duration* column is a live formula that resolves
itself once both are present — so two independent stations (even on
different computers) merge correctly without stepping on each other.

`credentials.json` and `token.json` are personal secrets — they're already
excluded via `.gitignore` and must never be committed or shared. When
running as the built `.exe`, both files live next to the `.exe` itself
(never inside PyInstaller's temporary extraction folder, which is wiped on
every launch).

## The reader DLL

The physical reader is a **Chafon CF561 UHF reader module**. The app talks
to it through `UHFPrimeReader.dll` (and its `hidapi.dll` dependency), a
vendor-provided Windows DLL, via `ctypes` (`reader_api.py`). Both DLLs are
bundled in `lib/` and loaded from there directly (`os.add_dll_directory`
makes sure `UHFPrimeReader.dll` finds `hidapi.dll` alongside it) — no need
to add anything to `PATH`.

**Important: both DLLs, as supplied by Chafon for the CF561, are 32-bit
(PE i386).** This only works on Windows,
and only with a **32-bit ("x86") Python interpreter**, even on 64-bit
Windows. With a 64-bit Python, `ctypes.WinDLL(...)` fails with *"%1 is not
a valid Win32 application"*. On Linux/macOS the modules still import fine
(the DLL load is lazy), but any action that needs the reader (OPEN,
CONNECT, Scan USB, ...) raises a clear error instead of crashing.

At startup, `main.py` calls `arch_check.warn_if_wrong_architecture()`: if
the interpreter isn't 32-bit on Windows, it shows a warning (console +
Tkinter dialog) before you even try to open the reader.

## Dependencies

- Python 3.10+ (uses `X | None` syntax)
- `tkinter` (bundled with Python on Windows/macOS; on Linux:
  `sudo apt install python3-tk`)
- `pyserial` **optional** — only used to auto-list COM ports if a serial
  fallback is ever added; not required for normal USB/HID use.

No other dependencies: the Google Sheet export feature (`gsheet_export.py`)
uses only the standard library.

## Running

```bash
pip install pyserial   # optional
python main.py
```

## Building a standalone .exe

`build_exe.bat` uses [PyInstaller](https://pyinstaller.org/) to produce a
self-contained `dist\RFID_Time_Racing.exe` (icon included), bundling
`lib\UHFPrimeReader.dll`, `lib\hidapi.dll`, and `icon.ico`.

1. Run it with the **same 32-bit Python** you use to talk to the reader —
   the script checks the architecture and refuses to continue otherwise.
2. It installs/upgrades PyInstaller, then builds the executable.
3. The resulting `.exe` is standalone: it can be copied to another 32-bit-
   DLL-compatible Windows machine without needing Python installed there.

By default the build keeps a console window next to the app (so you still
see `[DEBUG]`/error output); once everything works well for you, remove
`--console` in `build_exe.bat` for a fully silent GUI app.

To use a different icon, just replace `icon.ico` (multi-resolution
16/24/32/48/64/128/256px recommended) before rerunning `build_exe.bat`.

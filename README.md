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
| `lib/` | Bundled `UHFPrimeReader.dll` + `hidapi.dll` |
| `icon.ico` | App icon |
| `build_exe.bat` | Builds a standalone Windows `.exe` with PyInstaller |

## The reader DLL

The physical reader is a **Chafon CF561 UHF reader module**. The app talks
to it through `UHFPrimeReader.dll` (and its `hidapi.dll` dependency), a
vendor-provided Windows DLL, via `ctypes` (`reader_api.py`). Both DLLs are
bundled in `lib/` and loaded from there directly (`os.add_dll_directory`
makes sure `UHFPrimeReader.dll` finds `hidapi.dll` alongside it) — no need
to add anything to `PATH`.

**Important: both DLLs are 32-bit (PE i386).** This only works on Windows,
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

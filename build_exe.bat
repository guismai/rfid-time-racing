@echo off
REM Builds a standalone RFID_Time_Racing.exe with PyInstaller.
REM
REM IMPORTANT: run this with the SAME Python you use to talk to the reader
REM (must be 32-bit / x86, matching lib\UHFPrimeReader.dll and lib\hidapi.dll).
REM PyInstaller cannot cross-build: a 32-bit Python produces a 32-bit exe,
REM a 64-bit Python would produce a 64-bit exe that can't load those DLLs.
REM
REM Usage: double-click this file, or run it from a command prompt in this
REM        folder: build_exe.bat

python -c "import struct,sys; bits=struct.calcsize('P')*8; print('Python bits:', bits); sys.exit(0 if bits==32 else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: this Python is not 32-bit. Re-run this script using your
    echo 32-bit Python interpreter ^(the one that already works with the
    echo reader^), e.g.:
    echo     C:\path\to\python32\python.exe -m pip install --upgrade pyinstaller
    echo     C:\path\to\python32\python.exe -m PyInstaller ...
    echo or edit this .bat to point "python" at that interpreter.
    pause
    exit /b 1
)

echo.
echo Installing/upgrading PyInstaller...
python -m pip install --upgrade pyinstaller --quiet

echo.
echo Building RFID_Time_Racing.exe (no console window: --windowed. Debug
echo [DEBUG]/error output won't be visible; if you need to see it again for
echo troubleshooting, change --windowed back to --console below and rebuild)...
python -m PyInstaller --noconfirm --onefile --windowed --name "RFID_Time_Racing" --icon icon.ico ^
    --add-data "lib;lib" --add-data "icon.ico;." ^
    main.py

echo.
echo Done. The executable is in dist\RFID_Time_Racing.exe
echo (You can delete the build\ folder and *.spec file afterwards, they are
echo  just PyInstaller's intermediate build artifacts.)
echo.
echo NOTE: credentials.json / token.json (Google Sheet export) are NOT bundled
echo into the exe on purpose - they are personal secrets. If you use the
echo "Export live Google Sheet" button, copy credentials.json next to
echo dist\RFID_Time_Racing.exe yourself; token.json will be created there
echo after the first successful sign-in.
pause

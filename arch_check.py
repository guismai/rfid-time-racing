"""
Startup architecture check.

UHFPrimeReader.dll and hidapi.dll (lib/ folder) are compiled as 32-bit.
ctypes can only load a 32-bit DLL from a 32-bit (x86) Python interpreter --
with a 64-bit Python, loading fails with OSError WinError 193 ("%1 is not
a valid Win32 application").

This module checks the current interpreter's architecture and clearly
warns the user if it's not suitable, before even attempting anything
with the DLL.
"""
import platform
import struct
import sys


def get_python_bits() -> int:
    """32 or 64 depending on the Python interpreter currently in use."""
    return struct.calcsize("P") * 8


def is_dll_compatible() -> bool:
    """True if the current Python architecture can load the bundled 32-bit DLLs."""
    return get_python_bits() == 32


def warn_if_wrong_architecture(show_dialog: bool = True) -> bool:
    """
    Shows a warning (console + optionally a Tkinter dialog) if the Python
    interpreter is not 32-bit on Windows.

    Returns True if everything is compatible, False if a warning was issued.
    Non-blocking: the caller decides whether to continue or not.
    """
    if platform.system() != "Windows":
        # Irrelevant outside Windows: the DLL won't load anyway.
        return True

    if is_dll_compatible():
        return True

    bits = get_python_bits()
    message = (
        f"{bits}-bit Python detected: UHFPrimeReader.dll and hidapi.dll "
        f"(lib/ folder) are compiled as 32-bit and cannot be loaded by a "
        f"{bits}-bit Python.\n\n"
        "The application will still launch, but any action requiring the "
        "reader (OPEN, CONNECT, Scan USB, ...) will fail.\n\n"
        "Install a 32-bit (x86) Python from python.org and relaunch the "
        "application with it to drive the RFID reader."
    )

    print(f"[WARNING] {message}", file=sys.stderr)

    if show_dialog:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("Incompatible Python architecture", message)
            root.destroy()
        except Exception:
            pass  # headless environment: the console message is enough

    return False

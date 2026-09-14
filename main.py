"""
Application entry point.
"""
import sys

# PyInstaller's --windowed build has no console: sys.stdout/stderr are None
# (not just hidden), so any bare print(...) — including our [DEBUG] lines —
# would crash with "AttributeError: 'NoneType' object has no attribute
# 'write'". Give them a harmless sink instead of touching every print call.
class _NullStream:
    def write(self, *_args, **_kwargs):
        pass

    def flush(self):
        pass


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

from arch_check import warn_if_wrong_architecture
from main_window import main

if __name__ == "__main__":
    warn_if_wrong_architecture()
    main()

"""
Application entry point.
"""
from arch_check import warn_if_wrong_architecture
from main_window import main

if __name__ == "__main__":
    warn_if_wrong_architecture()
    main()

#!/usr/bin/env python3
"""Local developer RBAC setup — entry point.

All logic lives in the rbac/ package alongside this file.
Run this script directly:  python scripts/setup_rbac.py [options]
"""

# ruff: noqa: F401  (re-export via __main__ only)
import os
import sys

# Ensure the scripts/ directory is on sys.path so the rbac package is importable
# whether the script is run from the repo root or from scripts/ directly.
sys.path.insert(0, os.path.dirname(__file__))

from rbac._cli import main  # noqa: E402

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        from rbac._utils import YELLOW, _c

        print(_c(YELLOW, "\n\n  Interrupted."))
        sys.exit(1)
    sys.exit(0)

"""PyInstaller entry point: windowed server by default, internal JSON child on demand."""

from __future__ import annotations

import os
import sys

# Windowed PyInstaller builds set std streams to None. The child protocol uses
# stdout to emit JSON, so restore inherited handles (or devnull on the server).
# These handles must stay open for the whole process: a context manager would
# close them before the child protocol finishes.
if sys.stdout is None or sys.stderr is None:  # pragma: no cover - frozen only
    try:
        if sys.stdout is None:
            sys.stdout = open(1, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(2, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
    except OSError:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from s3_glacier_restore.ui.entry import main

raise SystemExit(main())

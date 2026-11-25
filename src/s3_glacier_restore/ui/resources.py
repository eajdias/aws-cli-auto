"""Resolve UI assets from the source tree or the PyInstaller bundle."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "s3_glacier_restore" / "ui" / name
    return Path(__file__).parent / name

"""Native folder picker with a headless-safe lazy import."""

from __future__ import annotations


def pick_folder() -> str | None:
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    try:
        return filedialog.askdirectory() or None
    finally:
        root.destroy()

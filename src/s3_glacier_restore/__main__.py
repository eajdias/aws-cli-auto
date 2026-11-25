"""Package entry point: python -m s3_glacier_restore."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Package entry point for the local UI server and internal child commands."""

from __future__ import annotations

import sys
from pathlib import Path

from .server import serve

_CLI_COMMANDS = frozenset(
    {"perms", "plan", "restore", "status", "copy", "verify", "parity", "download", "auto", "batch"}
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and (args[0] == "__child" or args[0] in _CLI_COMMANDS):
        # The frozen exe has no console parser of its own; reuse the CLI router.
        # A real directory keeps precedence so the server can still receive appdata.
        if args[0] != "__child" and Path(args[0]).is_dir():
            return serve(appdata_dir=Path(args[0]))
        from ..cli import main as cli_main

        return cli_main(args)
    appdata_dir = Path(args[0]) if args else None
    return serve(appdata_dir=appdata_dir)


if __name__ == "__main__":
    raise SystemExit(main())

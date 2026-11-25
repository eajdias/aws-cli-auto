"""Internal ``__child`` commands; credentials come only from the child env."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from ..audit import sanitize
from ..aws_factory import build_session, s3_client, split_aws_error, sts_client
from ..cli import cmd_download
from ..config import DEFAULT_REGION
from ..locations import infer_label, normalize_root
from ..prefixes import list_prefixes
from ..prefixes import sample_heads as sample_heads_for_prefix
from ..s3ops import timed


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or DEFAULT_REGION


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _emit_error(code: str, message: str) -> int:
    _emit({"ok": False, "errors": [f"{code}: {sanitize(message)}"]})
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="__child")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _location(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--bucket", required=True)
        sp.add_argument("--root", default="", help="Raiz no bucket (serial ou prefixo generico)")
        sp.add_argument("--label", default="", help="Etiqueta do run (inferida da raiz)")

    check = sub.add_parser("validate")
    _location(check)

    listing = sub.add_parser("list-prefixes")
    _location(listing)
    listing.add_argument("--max-list", type=int, default=700000)

    heads = sub.add_parser("sample-heads")
    _location(heads)
    heads.add_argument("--prefix", action="append", default=[])
    heads.add_argument("--sample-size", type=int, default=5)

    download = sub.add_parser("download")
    _location(download)
    download.add_argument("--region", default="")
    download.add_argument("--profile", default="env")
    download.add_argument("--max-list", type=int, default=700000)
    download.add_argument("--log-dir", default="logs")
    download.add_argument("--report-dir", default="reports")
    download.add_argument("--dest", required=True)
    download.add_argument("--run-id", default="")
    download.add_argument("--prefix", action="append", default=[])
    return parser


def _clients(region: str) -> tuple[object, object]:
    session = build_session("env", region)
    return s3_client(session, region), sts_client(session, region)


def cmd_validate(args: argparse.Namespace) -> int:
    region = _region()
    s3, sts = _clients(region)
    ident, _, error = timed(sts.get_caller_identity)  # type: ignore[attr-defined]
    if error is not None:
        return _emit_error(*error)
    _, _, list_error = timed(
        s3.list_objects_v2,  # type: ignore[attr-defined]
        Bucket=args.bucket,
        Prefix=normalize_root(args.root),
        MaxKeys=1,
    )
    if list_error is not None:
        return _emit_error(*list_error)
    _emit({"ok": True, "region": region, "arn": str((ident or {}).get("Arn", ""))})
    return 0


def cmd_list_prefixes(args: argparse.Namespace) -> int:
    s3, _ = _clients(_region())
    try:
        result = list_prefixes(s3, args.bucket, args.root, args.max_list)  # type: ignore[arg-type]
    except Exception as exc:
        code, message = split_aws_error(exc)
        return _emit_error(code, message)
    _emit(asdict(result))
    return 0


def cmd_sample_heads(args: argparse.Namespace) -> int:
    s3, _ = _clients(_region())
    try:
        result = sample_heads_for_prefix(
            s3,  # type: ignore[arg-type]
            args.bucket,
            args.root,
            args.prefix,
            args.sample_size,
        )
    except Exception as exc:
        code, message = split_aws_error(exc)
        return _emit_error(code, message)
    _emit(asdict(result))
    return 0


def cmd_download_child(args: argparse.Namespace) -> int:
    root = normalize_root(args.root)
    label = args.label or infer_label("", root)
    namespace = argparse.Namespace(
        cmd="download",
        serial=label,
        profile=args.profile,
        region=args.region or _region(),
        src_bucket=args.bucket,
        src_prefix=root,
        dst_bucket=args.bucket,
        dst_prefix=root,
        max_list=args.max_list,
        log_dir=Path(args.log_dir),
        report_dir=Path(args.report_dir),
        dest=args.dest,
        key=[],
        prefix=args.prefix,
        skip_head=True,
        dry_run=False,
        run_id=args.run_id,
    )
    return cmd_download(namespace)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "validate": cmd_validate,
        "list-prefixes": cmd_list_prefixes,
        "sample-heads": cmd_sample_heads,
        "download": cmd_download_child,
    }
    return handlers[args.cmd](args)

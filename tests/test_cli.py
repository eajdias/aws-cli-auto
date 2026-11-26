"""Config validation and CLI parsing (no AWS)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from s3_glacier_restore.audit import AuditLogger
from s3_glacier_restore.aws_factory import build_session
from s3_glacier_restore.cli import (
    EXIT_BLOCKED,
    EXIT_OK,
    _setup,
    build_parser,
    cmd_download,
    local_path_for,
    main,
    resolve_locations,
)
from s3_glacier_restore.config import RestoreConfig


def test_local_path_for() -> None:
    from pathlib import Path

    assert local_path_for("1/a/b.png", "1/", Path("out")) == Path("out/a/b.png")
    assert local_path_for("other.png", "1/", Path("out")) == Path("out/other.png")


def test_config_prefixes() -> None:
    cfg = RestoreConfig(serial="1234-5678-90", src_bucket="src-test", dst_bucket="dst-test")
    assert cfg.src_prefix == "1234-5678-90/"
    assert cfg.src_uri() == "s3://src-test/1234-5678-90/"
    assert cfg.run_id.startswith("1234-5678-90-")


def test_config_accepts_generic_label() -> None:
    for label in ("1234-5678-90", "cliente-a", "2024", "job_01.final"):
        cfg = RestoreConfig(serial=label, src_bucket="src-test", dst_bucket="dst-test")
        assert cfg.src_prefix == f"{label}/"
        assert cfg.dst_prefix == f"{label}/"


def test_config_rejects_invalid_label() -> None:
    for bad in ("", "   ", "a/b", "s3://bucket/prefixo/", "etiqueta invalida!"):
        with pytest.raises(ValueError):
            RestoreConfig(serial=bad, src_bucket="src-test", dst_bucket="dst-test")


def test_config_explicit_prefixes_win_over_label() -> None:
    cfg = RestoreConfig(
        serial="job",
        src_bucket="src-test",
        dst_bucket="dst-test",
        src_prefix="backups/2024",
        dst_prefix="/entrega/cliente-a/",
    )
    assert cfg.src_prefix == "backups/2024/"
    assert cfg.dst_prefix == "entrega/cliente-a/"
    assert cfg.src_uri() == "s3://src-test/backups/2024/"
    assert cfg.dst_uri() == "s3://dst-test/entrega/cliente-a/"


def test_config_rejects_bad_tier() -> None:
    with pytest.raises(ValueError):
        RestoreConfig(
            serial="1234-5678-90",
            src_bucket="src-test",
            dst_bucket="dst-test",
            restore_tier="Fast",
        )


def test_config_requires_buckets() -> None:
    with pytest.raises(ValueError, match="origem"):
        RestoreConfig(serial="1234-5678-90", src_bucket="", dst_bucket="dst-test")
    with pytest.raises(ValueError, match="destino"):
        RestoreConfig(serial="1234-5678-90", src_bucket="src-test", dst_bucket="")


def _clear_bucket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_RESTORE_SRC_BUCKET", raising=False)
    monkeypatch.delenv("S3_RESTORE_DST_BUCKET", raising=False)


def test_parser_resolves_buckets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bucket_env(monkeypatch)
    args = build_parser().parse_args(["plan", "--serial", "1234-5678-90"])
    assert args.src_bucket == ""
    assert args.dst_bucket == ""

    monkeypatch.setenv("S3_RESTORE_SRC_BUCKET", "src-from-env")
    monkeypatch.setenv("S3_RESTORE_DST_BUCKET", "dst-from-env")
    args = build_parser().parse_args(["plan", "--serial", "1234-5678-90"])
    assert args.src_bucket == "src-from-env"
    assert args.dst_bucket == "dst-from-env"


def test_setup_blocks_when_buckets_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_bucket_env(monkeypatch)
    args = build_parser().parse_args(
        [
            "plan",
            "--serial",
            "1234-5678-90",
            "--log-dir",
            str(tmp_path / "logs"),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )

    class FakeSTS:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": "123", "Arn": "arn:aws:iam::123:user/x"}

    # Old code proceeds to AWS; the test must fail without touching the network.
    monkeypatch.setattr("s3_glacier_restore.cli.build_session", lambda *a, **k: object())
    monkeypatch.setattr("s3_glacier_restore.cli.sts_client", lambda *a, **k: FakeSTS())
    monkeypatch.setattr("s3_glacier_restore.cli.s3_client", lambda *a, **k: object())

    with pytest.raises(SystemExit) as exc:
        _setup(args)
    assert exc.value.code == EXIT_BLOCKED
    assert "S3_RESTORE_SRC_BUCKET" in capsys.readouterr().err


def test_setup_honors_explicit_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("S3_RESTORE_SRC_BUCKET", "src-test")
    monkeypatch.setenv("S3_RESTORE_DST_BUCKET", "dst-test")
    args = build_parser().parse_args(
        [
            "download",
            "--serial",
            "1234-5678-90",
            "--log-dir",
            str(tmp_path / "logs"),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    args.run_id = "explicit-run"

    class FakeSTS:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": "123", "Arn": "arn:aws:iam::123:user/x"}

    monkeypatch.setattr("s3_glacier_restore.cli.build_session", lambda *a, **k: object())
    monkeypatch.setattr("s3_glacier_restore.cli.sts_client", lambda *a, **k: FakeSTS())
    monkeypatch.setattr("s3_glacier_restore.cli.s3_client", lambda *a, **k: object())

    cfg, _, _ = _setup(args)

    assert cfg.run_id == "explicit-run"


def test_parser_prog_is_generic() -> None:
    assert build_parser().prog == "s3-glacier-restore"


def test_main_routes_child_internal_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_child(argv: list[str] | None) -> int:
        calls.append(list(argv or []))
        return 7

    monkeypatch.setattr("s3_glacier_restore.ui.child.main", fake_child)

    assert main(["__child", "validate", "--bucket", "b"]) == 7
    assert calls == [["validate", "--bucket", "b"]]


def test_cli_parses_plan() -> None:
    args = build_parser().parse_args(["plan", "--serial", "1234-5678-90", "--profile", "verificar"])
    assert args.cmd == "plan"
    assert args.serial == "1234-5678-90"
    assert args.profile == "verificar"


def test_cli_parses_download() -> None:
    args = build_parser().parse_args(
        [
            "download",
            "--serial",
            "1234-5678-90",
            "--profile",
            "verificar",
            "--dest",
            "./out",
            "--key",
            "1/a.png",
            "--key",
            "1/b.png",
        ]
    )
    assert args.cmd == "download"
    assert args.key == ["1/a.png", "1/b.png"]
    assert args.dest == "./out"


def test_cli_requires_serial_or_uris() -> None:
    args = build_parser().parse_args(["plan", "--profile", "verificar"])
    assert args.serial is None
    with pytest.raises(ValueError, match="--serial"):
        resolve_locations(args)


def test_cli_parses_src_dst_uris_and_label() -> None:
    args = build_parser().parse_args(
        [
            "plan",
            "--src",
            "s3://src-bucket/backups/2024/",
            "--dst",
            "s3://dst-bucket/entrega/cliente-a",
            "--label",
            "cliente-a",
        ]
    )
    assert args.serial == "cliente-a"
    label, src_bucket, src_prefix, dst_bucket, dst_prefix = resolve_locations(args)
    assert (label, src_bucket, src_prefix, dst_bucket, dst_prefix) == (
        "cliente-a",
        "src-bucket",
        "backups/2024/",
        "dst-bucket",
        "entrega/cliente-a/",
    )


def test_resolve_infers_label_from_serial_prefix() -> None:
    args = build_parser().parse_args(
        [
            "plan",
            "--src",
            "s3://src-bucket/1234-5678-90/",
            "--dst",
            "dst-bucket/1234-5678-90",
        ]
    )
    label, src_bucket, src_prefix, dst_bucket, dst_prefix = resolve_locations(args)
    assert label == "1234-5678-90"
    assert (src_bucket, src_prefix) == ("src-bucket", "1234-5678-90/")
    assert (dst_bucket, dst_prefix) == ("dst-bucket", "1234-5678-90/")


def test_resolve_rejects_half_uri_pair() -> None:
    args = build_parser().parse_args(["plan", "--src", "s3://src-bucket/a/"])
    with pytest.raises(ValueError, match="--src e --dst"):
        resolve_locations(args)


def test_build_session_env_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("s3_glacier_restore.aws_factory.boto3.Session", FakeSession)
    build_session("env", "sa-east-1")
    build_session("", "sa-east-1")
    assert calls == [
        {"profile_name": None, "region_name": "sa-east-1"},
        {"profile_name": None, "region_name": "sa-east-1"},
    ]
    build_session("verificar", "sa-east-1")
    assert calls[-1]["profile_name"] == "verificar"


def test_parser_accepts_repeatable_prefix_and_skip_head() -> None:
    args = build_parser().parse_args(
        [
            "download",
            "--serial",
            "1234-5678-90",
            "--src-bucket",
            "src-test",
            "--dst-bucket",
            "dst-test",
            "--prefix",
            "Images/",
            "--prefix",
            "Database/",
            "--skip-head",
        ]
    )
    assert args.prefix == ["Images/", "Database/"]
    assert args.skip_head is True


class _DownloadS3:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        return {
            "Contents": [
                {"Key": "1234-5678-90/Images/a.png", "Size": 3, "StorageClass": "STANDARD"},
                {"Key": "1234-5678-90/Images/sub/b.png", "Size": 3, "StorageClass": "STANDARD"},
                {"Key": "1234-5678-90/Database/dump.sql", "Size": 3, "StorageClass": "STANDARD"},
                {"Key": "1234-5678-90/", "Size": 0},
            ]
        }

    def download_file(self, bucket: str, key: str, path: str) -> None:
        Path(path).write_bytes(b"abc")
        self.downloaded.append(key)


def _download_args(tmp_path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "cmd": "download",
        "serial": "1234-5678-90",
        "profile": "env",
        "region": "sa-east-1",
        "src_bucket": "src-test",
        "dst_bucket": "dst-test",
        "max_list": 100,
        "log_dir": str(tmp_path / "logs"),
        "report_dir": str(tmp_path / "reports"),
        "dest": str(tmp_path / "out"),
        "key": [],
        "dry_run": False,
        "prefix": [],
        "skip_head": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _stub_download_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path, s3: _DownloadS3, args: argparse.Namespace
) -> None:
    cfg = RestoreConfig(
        serial=args.serial,
        profile=args.profile,
        region=args.region,
        src_bucket=args.src_bucket,
        dst_bucket=args.dst_bucket,
        max_list=args.max_list,
        dry_run=args.dry_run,
        log_dir=Path(args.log_dir),
        report_dir=Path(args.report_dir),
    )
    logger = AuditLogger(
        tmp_path / "logs", cfg.run_id, cfg.serial, cfg.profile, account="123", actor="arn:test"
    )
    monkeypatch.setattr("s3_glacier_restore.cli._setup", lambda _args: (cfg, logger, s3))


def _head_spy(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def spy(s3: object, bucket: str, key: str) -> tuple[str, dict[str, str], None]:
        calls.append(key)
        return "none", {"StorageClass": "STANDARD"}, None

    monkeypatch.setattr("s3_glacier_restore.cli.head_restore_state", spy)


def test_download_prefix_filters_keys(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    s3 = _DownloadS3()
    args = _download_args(tmp_path, prefix=["Images/"])
    _stub_download_setup(monkeypatch, tmp_path, s3, args)
    heads: list[str] = []
    _head_spy(monkeypatch, heads)

    rc = cmd_download(args)

    assert rc == EXIT_OK
    assert s3.downloaded == [
        "1234-5678-90/Images/a.png",
        "1234-5678-90/Images/sub/b.png",
    ]
    assert (tmp_path / "out" / "Images" / "sub" / "b.png").exists()


def test_download_prefix_accepts_serial_prefixed_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    s3 = _DownloadS3()
    args = _download_args(tmp_path, prefix=["1234-5678-90/Images/"])
    _stub_download_setup(monkeypatch, tmp_path, s3, args)
    _head_spy(monkeypatch, [])

    rc = cmd_download(args)

    assert rc == EXIT_OK
    assert s3.downloaded == [
        "1234-5678-90/Images/a.png",
        "1234-5678-90/Images/sub/b.png",
    ]


def test_download_skip_head_skips_head_and_logs_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    s3 = _DownloadS3()
    args = _download_args(tmp_path, prefix=["Images/"], dry_run=True, skip_head=True)
    _stub_download_setup(monkeypatch, tmp_path, s3, args)
    heads: list[str] = []
    _head_spy(monkeypatch, heads)

    rc = cmd_download(args)

    assert rc == EXIT_OK
    assert heads == []
    logs = list((tmp_path / "logs").glob("*.jsonl"))
    events = [
        json.loads(line) for path in logs for line in path.read_text(encoding="utf-8").splitlines()
    ]
    downloads = [e for e in events if e.get("action") == "s3:GetObject"]
    assert len(downloads) == 2
    assert all(e["extra"].get("head_skipped") is True for e in downloads)


def test_download_dry_run_without_skip_still_heads(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    s3 = _DownloadS3()
    args = _download_args(tmp_path, prefix=["Images/"], dry_run=True)
    _stub_download_setup(monkeypatch, tmp_path, s3, args)
    heads: list[str] = []
    _head_spy(monkeypatch, heads)

    rc = cmd_download(args)

    assert rc == EXIT_OK
    assert heads == ["1234-5678-90/Images/a.png", "1234-5678-90/Images/sub/b.png"]

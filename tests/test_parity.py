"""Parity check: source vs destination key sets (no AWS)."""

from __future__ import annotations

from datetime import UTC, datetime

from s3_glacier_restore.parity import compare_listings

T0 = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)


def test_compare_identical_listings_is_clean() -> None:
    src = {"Images/a.png": (10, T0), "root.txt": (1, T0)}
    dst = {"Images/a.png": (10, T0), "root.txt": (1, T0)}

    result = compare_listings(src, dst)

    assert result.ok
    assert result.missing == {}
    assert result.extra == {}
    assert result.diverged == {}
    assert result.src_objects == 2
    assert result.src_bytes == 11


def test_compare_reports_missing_extra_and_diverged() -> None:
    src = {
        "Images/a.png": (10, T0),
        "Images/miss.png": (5, T1),
        "doc.txt": (7, T0),
    }
    dst = {
        "Images/a.png": (10, T0),
        "Images/extra.png": (3, T1),
        "doc.txt": (9, T1),
    }

    result = compare_listings(src, dst)

    assert not result.ok
    assert list(result.missing) == ["Images/miss.png"]
    assert list(result.extra) == ["Images/extra.png"]
    assert list(result.diverged) == ["doc.txt"]
    assert result.diverged["doc.txt"] == ((7, T0), (9, T1))


def test_missing_age_bounds_come_from_source_dates() -> None:
    src = {"a.png": (1, T0), "b.png": (2, T1)}
    dst: dict[str, tuple[int, datetime]] = {}

    result = compare_listings(src, dst)

    assert result.missing_oldest == T0
    assert result.missing_newest == T1
    assert result.missing_bytes == 3


def test_empty_source_has_no_missing_bounds() -> None:
    result = compare_listings({}, {"a.png": (1, T0)})

    assert result.missing_oldest is None
    assert result.missing_newest is None
    assert not result.ok


class _ParityS3:
    """Fake S3: per-bucket Contents filtered by Prefix, single page, optional HEADs."""

    def __init__(
        self,
        objects: list[tuple[str, int]],
        heads: dict[str, dict[str, object]] | None = None,
        dst_objects: list[tuple[str, int]] | None = None,
    ) -> None:
        self.objects = objects
        self.heads = heads or {}
        self.dst_objects = objects if dst_objects is None else dst_objects
        self.head_calls: list[str] = []

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        prefix = str(kwargs.get("Prefix", ""))
        bucket = str(kwargs.get("Bucket", ""))
        pool = self.dst_objects if bucket == "dst-bkt" else self.objects
        return {
            "Contents": [
                {"Key": key, "Size": size, "LastModified": T0, "StorageClass": "STANDARD"}
                for key, size in pool
                if key.startswith(prefix)
            ]
        }

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.head_calls.append(key)
        if key not in self.heads:
            raise KeyError(key)
        return self.heads[key]


def _parity_args(tmp_path, **overrides: object):
    from s3_glacier_restore.cli import build_parser

    values: dict[str, object] = {
        "log-dir": str(tmp_path / "logs"),
        "report-dir": str(tmp_path / "reports"),
    }
    values.update(overrides)
    argv = ["parity", "--src", "s3://src-bkt/job/", "--dst", "s3://dst-bkt/job/"]
    for key, value in values.items():
        argv += [f"--{key}", str(value)]
    return build_parser().parse_args(argv)


def _stub_parity_setup(monkeypatch, tmp_path, s3: _ParityS3, max_list: int = 700000) -> None:
    from pathlib import Path

    from s3_glacier_restore.audit import AuditLogger
    from s3_glacier_restore.config import RestoreConfig

    cfg = RestoreConfig(
        serial="job",
        src_bucket="src-bkt",
        src_prefix="job/",
        dst_bucket="dst-bkt",
        dst_prefix="job/",
        max_list=max_list,
        log_dir=Path(tmp_path) / "logs",
        report_dir=Path(tmp_path) / "reports",
    )
    logger = AuditLogger(Path(tmp_path) / "logs", cfg.run_id, cfg.serial, "verificar")
    monkeypatch.setattr("s3_glacier_restore.cli._setup", lambda _args: (cfg, logger, s3))


def test_parity_clean_returns_ok(monkeypatch, tmp_path, capsys) -> None:
    from s3_glacier_restore.cli import EXIT_OK, cmd_parity

    s3 = _ParityS3([("job/a.png", 10), ("job/b.png", 20)])
    _stub_parity_setup(monkeypatch, tmp_path, s3)

    assert cmd_parity(_parity_args(tmp_path)) == EXIT_OK
    out = capsys.readouterr().out
    assert "ausentes=0 extras=0 divergentes=0" in out
    assert "OK - destino espelha a origem" in out


def test_parity_divergent_returns_blocked(monkeypatch, tmp_path, capsys) -> None:
    from s3_glacier_restore.cli import EXIT_BLOCKED, cmd_parity

    s3 = _ParityS3(
        [("job/a.png", 10), ("job/miss.png", 5)],
        heads={"job/miss.png": {}},
        dst_objects=[("job/a.png", 10)],
    )
    _stub_parity_setup(monkeypatch, tmp_path, s3)

    assert cmd_parity(_parity_args(tmp_path)) == EXIT_BLOCKED
    out = capsys.readouterr().out
    assert "ausentes=1" in out
    assert "nunca solicitados=1" in out


def test_parity_truncated_returns_inconclusive(monkeypatch, tmp_path, capsys) -> None:
    from s3_glacier_restore.cli import EXIT_BLOCKED, cmd_parity

    s3 = _ParityS3([("job/a.png", 1), ("job/b.png", 2), ("job/c.png", 3)])
    _stub_parity_setup(monkeypatch, tmp_path, s3, max_list=2)
    args = _parity_args(tmp_path)

    assert cmd_parity(args) == EXIT_BLOCKED
    assert "INCONCLUSIVO" in capsys.readouterr().out


def test_list_scope_honors_max_items() -> None:
    from s3_glacier_restore.parity import list_scope

    s3 = _ParityS3([("p/a.png", 1), ("p/b.png", 2), ("p/c.png", 3)])

    listing, truncated = list_scope(s3, "bkt", "p/", 2)

    assert sorted(listing) == ["a.png", "b.png"]
    assert truncated is True


def test_verify_truncated_is_blocked(monkeypatch, tmp_path, capsys) -> None:
    from s3_glacier_restore.audit import AuditLogger
    from s3_glacier_restore.cli import EXIT_BLOCKED, build_parser, cmd_verify
    from s3_glacier_restore.config import RestoreConfig

    cfg = RestoreConfig(
        serial="job",
        src_bucket="src-bkt",
        dst_bucket="dst-bkt",
        max_list=1,
        log_dir=tmp_path / "logs",
        report_dir=tmp_path / "reports",
    )
    logger = AuditLogger(tmp_path / "logs", cfg.run_id, cfg.serial, "verificar")

    class _S3:
        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            prefix = str(kwargs.get("Prefix", ""))
            return {
                "Contents": [
                    {"Key": f"{prefix}a.png", "Size": 1, "StorageClass": "STANDARD"}
                    for _ in range(3)
                ]
            }

    monkeypatch.setattr("s3_glacier_restore.cli._setup", lambda _args: (cfg, logger, _S3()))
    args = build_parser().parse_args(["verify", "--serial", "job"])

    assert cmd_verify(args) == EXIT_BLOCKED
    assert "INCONCLUSIVO" in capsys.readouterr().out

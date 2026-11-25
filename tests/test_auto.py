"""Unattended orchestration: auto/batch, checkpoints and handoff (no AWS)."""

from __future__ import annotations

from s3_glacier_restore.auto import (
    AutoCommands,
    AutoOptions,
    AutoState,
    run_auto,
    run_batch,
)
from s3_glacier_restore.cli import EXIT_BLOCKED
from s3_glacier_restore.s3ops import S3ObjectRef, StatusCounts, collect_status


def test_status_counts_complete() -> None:
    assert StatusCounts(restored=10, total=10).complete
    assert not StatusCounts(restored=9, restoring=1, total=10).complete
    assert not StatusCounts(restored=10, failed=1, total=11).complete
    assert not StatusCounts(total=0).complete
    assert "restaurados=10" in StatusCounts(restored=10, total=10).summary()


def test_collect_status_counts_states() -> None:
    class FakeS3:
        def head_object(self, **kwargs: object) -> dict:
            key = str(kwargs["Key"])
            return {
                "k1": {"Restore": 'ongoing-request="false"'},
                "k2": {"Restore": 'ongoing-request="true"'},
                "k3": {},
            }[key]

    refs = [
        S3ObjectRef(key="k1", size=1, storage_class="DEEP_ARCHIVE"),
        S3ObjectRef(key="k2", size=1, storage_class="DEEP_ARCHIVE"),
        S3ObjectRef(key="k3", size=1, storage_class="DEEP_ARCHIVE"),
    ]
    counts = collect_status(FakeS3(), "src-test", refs, workers=1)
    assert (counts.restored, counts.restoring, counts.not_requested) == (1, 1, 1)
    assert counts.total == 3
    assert not counts.complete


def test_state_roundtrip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = AutoState(serial="1234-5678-90", profile="verificar", phase="copy", status="running")
    state.save(path)
    loaded = AutoState.load(path, serial="1234-5678-90", profile="verificar")
    assert loaded.phase == "copy"
    assert loaded.status == "running"
    assert loaded.started_at == state.started_at
    assert AutoState.load(tmp_path / "missing.json", "1234-5678-90", "verificar").phase == "restore"


def _options(
    tmp_path, wait_seconds: int = 0, poll_seconds: int = 0, max_polls: int = 3
) -> AutoOptions:
    return AutoOptions(
        serial="1234-5678-90",
        profile="verificar",
        src_bucket="src-test",
        dst_bucket="dst-test",
        state_dir=tmp_path / "state",
        handoff_dir=tmp_path / "handoff",
        log_dir=tmp_path / "logs",
        report_dir=tmp_path / "reports",
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        max_polls=max_polls,
    )


def _commands(calls: list[str], counts: StatusCounts | None = None) -> AutoCommands:
    counts = counts or StatusCounts(restored=2, total=2)
    return AutoCommands(
        restore=lambda namespace: calls.append("restore") or 0,
        status_fn=lambda opts, s3: (calls.append("status"), counts)[1],
        copy=lambda namespace: calls.append("copy") or 0,
        verify=lambda namespace: calls.append("verify") or 0,
    )


def test_run_auto_blocks_when_buckets_missing(tmp_path) -> None:
    opts = _options(tmp_path)
    opts.src_bucket = ""
    rc = run_auto(opts, commands=_commands([]), sleep_fn=lambda seconds: None)
    assert rc == EXIT_BLOCKED


def test_run_auto_happy_path(tmp_path) -> None:
    calls: list[str] = []
    rc = run_auto(
        _options(tmp_path),
        commands=_commands(calls),
        sleep_fn=lambda seconds: None,
    )
    assert rc == 0
    assert calls == ["restore", "status", "copy", "verify"]
    state = AutoState.load(
        tmp_path / "state" / "1234-5678-90-verificar.json", "1234-5678-90", "verificar"
    )
    assert state.status == "done"
    assert state.phase == "done"
    assert (tmp_path / "state" / "1234-5678-90.done").exists()
    handoff = (tmp_path / "handoff" / "1234-5678-90-download.txt").read_text(encoding="utf-8")
    assert "s3_glacier_restore download" in handoff
    assert "--dst s3://dst-test/1234-5678-90/" in handoff
    assert "--serial 1234-5678-90" in handoff
    assert "--profile verificar" in handoff
    assert "--max-list 700000" in handoff
    old_name = "z" + "scan"  # assembled to keep the old name out of the tree
    assert old_name not in handoff.lower()


def test_run_auto_waits_until_all_restored(tmp_path) -> None:
    calls: list[str] = []
    seq = [StatusCounts(restoring=2, total=2), StatusCounts(restored=2, total=2)]
    commands = _commands(calls, counts=seq[0])

    def status_twice(opts, s3):
        calls.append("status")
        return seq.pop(0)

    commands.status_fn = status_twice
    rc = run_auto(_options(tmp_path), commands=commands, sleep_fn=lambda seconds: None)
    assert rc == 0
    assert calls == ["restore", "status", "status", "copy", "verify"]


def test_run_auto_blocks_copy_when_restore_incomplete(tmp_path) -> None:
    calls: list[str] = []
    never = StatusCounts(restoring=2, total=2)
    rc = run_auto(
        _options(tmp_path, max_polls=2),
        commands=_commands(calls, counts=never),
        sleep_fn=lambda seconds: None,
    )
    assert rc == 1
    assert calls == ["restore", "status", "status"]
    state = AutoState.load(
        tmp_path / "state" / "1234-5678-90-verificar.json", "1234-5678-90", "verificar"
    )
    assert state.status == "failed"
    assert state.error
    assert not (tmp_path / "state" / "1234-5678-90.done").exists()


def test_run_auto_resumes_from_copy_phase(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    AutoState(serial="1234-5678-90", profile="verificar", phase="copy", status="running").save(
        state_dir / "1234-5678-90-verificar.json"
    )
    calls: list[str] = []
    rc = run_auto(_options(tmp_path), commands=_commands(calls), sleep_fn=lambda seconds: None)
    assert rc == 0
    assert calls == ["copy", "verify"]


def test_run_auto_done_is_idempotent(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    AutoState(serial="1234-5678-90", profile="verificar", phase="done", status="done").save(
        state_dir / "1234-5678-90-verificar.json"
    )
    calls: list[str] = []
    rc = run_auto(_options(tmp_path), commands=_commands(calls), sleep_fn=lambda seconds: None)
    assert rc == 0
    assert calls == []


def test_run_batch_processes_every_serial(tmp_path) -> None:
    seen: list[str] = []

    def fake_auto(opts: AutoOptions, **kwargs: object) -> int:
        seen.append(opts.serial)
        return 0

    rc = run_batch(
        ["1234-5678-90", "1234-5678-91"],
        _options(tmp_path),
        max_parallel=2,
        runner=fake_auto,
    )
    assert rc == 0
    assert sorted(seen) == ["1234-5678-90", "1234-5678-91"]


def test_parser_defaults_match_auto_options() -> None:
    from s3_glacier_restore.cli import build_parser

    auto_args = build_parser().parse_args(["auto", "--serial", "1234-5678-90"])
    batch_args = build_parser().parse_args(["batch", "1234-5678-90", "1234-5678-91"])
    restore_args = build_parser().parse_args(["restore", "--serial", "1234-5678-90"])
    assert auto_args.max_list == AutoOptions.max_list == 700000
    assert batch_args.max_list == 700000
    assert auto_args.workers == batch_args.workers == 20
    assert restore_args.max_list == 20000  # standalone commands keep their own default


def test_options_from_uri_args_infers_label(tmp_path) -> None:
    from s3_glacier_restore.auto import _options_from_args
    from s3_glacier_restore.cli import build_parser

    args = build_parser().parse_args(
        [
            "auto",
            "--src",
            "s3://src-bkt/1234-5678-90/",
            "--dst",
            "s3://dst-bkt/1234-5678-90/",
        ]
    )
    opts = _options_from_args(args)
    assert opts.serial == "1234-5678-90"
    assert opts.src_bucket == "src-bkt"
    assert opts.src_prefix == "1234-5678-90/"
    assert opts.dst_bucket == "dst-bkt"
    assert opts.dst_prefix == "1234-5678-90/"


def test_run_batch_accepts_uri_pairs(tmp_path) -> None:
    seen: list[AutoOptions] = []

    def fake_auto(opts: AutoOptions, **kwargs: object) -> int:
        seen.append(opts)
        return 0

    rc = run_batch(
        ["s3://src-bkt/backups/2024=s3://dst-bkt/entrega/2024"],
        _options(tmp_path),
        max_parallel=2,
        runner=fake_auto,
    )

    assert rc == 0
    assert len(seen) == 1
    assert seen[0].serial == "2024"
    assert seen[0].src_bucket == "src-bkt"
    assert seen[0].src_prefix == "backups/2024/"
    assert seen[0].dst_bucket == "dst-bkt"
    assert seen[0].dst_prefix == "entrega/2024/"


def test_run_batch_plain_labels_scope_under_base(tmp_path) -> None:
    seen: list[AutoOptions] = []

    def fake_auto(opts: AutoOptions, **kwargs: object) -> int:
        seen.append(opts)
        return 0

    rc = run_batch(["job-a", "job-b"], _options(tmp_path), max_parallel=2, runner=fake_auto)

    assert rc == 0
    assert sorted(o.serial for o in seen) == ["job-a", "job-b"]
    assert all(o.src_prefix == f"{o.serial}/" for o in seen)


def test_batch_requires_valid_serials(tmp_path) -> None:
    import pytest

    with pytest.raises(ValueError):
        run_batch(["etiqueta/invalida"], _options(tmp_path), runner=lambda o, **k: 0)

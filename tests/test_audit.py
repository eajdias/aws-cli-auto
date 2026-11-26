"""Audit logger: schema, redaction, JSONL output."""

from __future__ import annotations

import json

import pytest

from s3_glacier_restore.audit import AuditLogger, sanitize


def test_sanitize_removes_key_material() -> None:
    fake_key = "AKIA" + "0" * 16  # format-like fake assembled at runtime, never a literal key
    assert sanitize(f"key {fake_key} here") == "key AKIA***REDACTED here"
    assert sanitize('secret_key": "abc123secret"') == 'secret_key": "***REDACTED"'
    assert sanitize(None) is None
    assert sanitize("clean message") == "clean message"


def test_logger_writes_jsonl(tmp_path) -> None:
    logger = AuditLogger(
        tmp_path,
        "run1",
        "1234-5678-90",
        "verificar",
        account="123",
        actor="arn:aws:iam::123:user/x",
    )
    logger.log("INFO", "plan", "s3:ListObjectsV2", "s3://bkt/1/", "ok", extra={"scanned": 3})
    lines = tmp_path.joinpath("run1.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2  # header + event
    event = json.loads(lines[1])
    assert event["run_id"] == "run1"
    assert event["step"] == "plan"
    assert event["result"] == "ok"
    assert event["extra"] == {"scanned": 3}
    assert event["ts"].endswith("Z")


def test_logger_is_thread_safe(tmp_path) -> None:
    import threading

    logger = AuditLogger(tmp_path, "run1", "s", "p")
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                logger.log("INFO", "restore", "s3:RestoreObject", f"s3://b/k{n}-{i}", "ok")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    lines = tmp_path.joinpath("run1.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1 + 8 * 50  # header + events
    assert all(json.loads(line)["run_id"] == "run1" for line in lines)
    assert logger.counts == {"ok": 8 * 50}


def test_logger_rejects_invalid_result(tmp_path) -> None:
    logger = AuditLogger(tmp_path, "run1", "s", "p")
    with pytest.raises(ValueError):
        logger.log("INFO", "plan", "a", "r", "maybe")


def test_logger_counts(tmp_path) -> None:
    logger = AuditLogger(tmp_path, "run1", "s", "p")
    logger.log("INFO", "copy", "s3:CopyObject", "s3://b/k", "ok")
    logger.log("ERROR", "copy", "s3:CopyObject", "s3://b/k2", "fail", "AccessDenied", "denied")
    assert logger.counts == {"ok": 1, "fail": 1}

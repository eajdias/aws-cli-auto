"""Worker helpers: single-object restore/head without AWS."""

from __future__ import annotations

from botocore.exceptions import ClientError

from s3_glacier_restore.cli import _copy_one, _head_one, _restore_one, build_parser
from s3_glacier_restore.config import RestoreConfig
from s3_glacier_restore.s3ops import S3ObjectRef


class FakeS3:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.restored: list[str] = []

    def restore_object(self, **kwargs: object) -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        self.restored.append(str(kwargs["Key"]))
        return {}

    def head_object(self, **kwargs: object) -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        return {"StorageClass": "DEEP_ARCHIVE", "Restore": 'ongoing-request="true"'}


def _cfg() -> RestoreConfig:
    return RestoreConfig(
        serial="1234-5678-90",
        profile="verificar",
        src_bucket="src-test",
        dst_bucket="dst-test",
    )


def test_restore_one_ok() -> None:
    s3 = FakeS3()
    obj = S3ObjectRef(key="1/a.png", size=10, storage_class="DEEP_ARCHIVE")
    _, outcome, code, _, _ = _restore_one(s3, _cfg(), obj)
    assert outcome == "ok"
    assert code is None
    assert s3.restored == ["1/a.png"]


def test_restore_one_skip_when_already_in_progress() -> None:
    err = ClientError(
        {"Error": {"Code": "RestoreAlreadyInProgress", "Message": "busy"}},
        "RestoreObject",
    )
    s3 = FakeS3(fail_with=err)
    obj = S3ObjectRef(key="1/a.png", size=10, storage_class="DEEP_ARCHIVE")
    _, outcome, code, _, _ = _restore_one(s3, _cfg(), obj)
    assert outcome == "skip"
    assert code == "RestoreAlreadyInProgress"


def test_restore_one_fail_on_access_denied() -> None:
    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}},
        "RestoreObject",
    )
    s3 = FakeS3(fail_with=err)
    obj = S3ObjectRef(key="1/a.png", size=10, storage_class="DEEP_ARCHIVE")
    _, outcome, code, _, _ = _restore_one(s3, _cfg(), obj)
    assert outcome == "fail"
    assert code == "AccessDenied"


def test_head_one_restoring() -> None:
    s3 = FakeS3()
    obj = S3ObjectRef(key="1/a.png", size=10, storage_class="DEEP_ARCHIVE")
    _, state, code, _ = _head_one(s3, _cfg(), obj)
    assert state == "restoring"
    assert code is None


class FakeCopyS3(FakeS3):
    def __init__(self, restore_header: str | None) -> None:
        super().__init__()
        self.restore_header = restore_header
        self.copied: list[str] = []

    def head_object(self, **kwargs: object) -> dict:
        if self.restore_header is None:
            return {"StorageClass": "DEEP_ARCHIVE"}
        return {"StorageClass": "DEEP_ARCHIVE", "Restore": self.restore_header}

    def copy_object(self, **kwargs: object) -> dict:
        self.copied.append(str(kwargs["Key"]))
        return {}


def test_copy_one_copies_restored_deep_archive() -> None:
    """Regression: a restored Deep Archive keeps its class and MUST be copied."""
    s3 = FakeCopyS3('ongoing-request="false", expiry-date="Fri, 01 Jan 2027 00:00:00 GMT"')
    outcome, code, _, _, reason = _copy_one(s3, _cfg(), "1234-5678-90/a.png", "DEEP_ARCHIVE")
    assert (outcome, code, reason) == ("ok", None, "copied")
    assert s3.copied == ["1234-5678-90/a.png"]


def test_copy_one_skips_deep_archive_without_restore() -> None:
    s3 = FakeCopyS3(None)
    outcome, _, msg, _, reason = _copy_one(s3, _cfg(), "1234-5678-90/a.png", "DEEP_ARCHIVE")
    assert outcome == "skip"
    assert reason == "restore not complete"
    assert "restore_state=none" in (msg or "")
    assert s3.copied == []


def test_copy_one_skips_deep_archive_still_restoring() -> None:
    s3 = FakeCopyS3('ongoing-request="true"')
    outcome, _, _, _, reason = _copy_one(s3, _cfg(), "1234-5678-90/a.png", "DEEP_ARCHIVE")
    assert outcome == "skip"
    assert reason == "restore not complete"
    assert s3.copied == []


def test_copy_one_copies_standard_without_head() -> None:
    s3 = FakeCopyS3(None)
    outcome, _, _, _, reason = _copy_one(s3, _cfg(), "1234-5678-90/a.png", "STANDARD")
    assert (outcome, reason) == ("ok", "copied")
    assert s3.copied == ["1234-5678-90/a.png"]


def test_cli_parses_copy_workers() -> None:
    args = build_parser().parse_args(
        ["copy", "--serial", "1234-5678-90", "--profile", "verificar", "--workers", "20"]
    )
    assert args.workers == 20

"""Unit tests: no AWS calls. S3 is faked in-memory."""

from __future__ import annotations

from s3_glacier_restore.s3ops import (
    dst_key_for,
    is_archived,
    is_downloadable,
    normalize_class,
    parse_restore_header,
    run_census,
)


class FakeS3:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls = 0

    def list_objects_v2(self, **kwargs: object) -> dict:
        token = kwargs.get("ContinuationToken")
        idx = int(token) if token is not None else 0
        page = dict(self.pages[idx])
        self.calls += 1
        if idx + 1 < len(self.pages):
            page["NextContinuationToken"] = str(idx + 1)
        return page


def test_normalize_class_folder_marker() -> None:
    assert normalize_class(None) == "FOLDER_MARKER"
    assert normalize_class("") == "FOLDER_MARKER"
    assert normalize_class("STANDARD") == "STANDARD"
    assert normalize_class("DEEP_ARCHIVE") == "DEEP_ARCHIVE"


def test_is_archived() -> None:
    assert is_archived("DEEP_ARCHIVE")
    assert is_archived("GLACIER")
    assert not is_archived("GLACIER_IR")
    assert not is_archived("STANDARD")
    assert not is_archived("STANDARD_IA")
    assert not is_archived("FOLDER_MARKER")


def test_parse_restore_header() -> None:
    assert parse_restore_header(None) == "none"
    assert parse_restore_header("") == "none"
    assert parse_restore_header('ongoing-request="true"') == "restoring"
    assert parse_restore_header('ongoing-request="false", expiry-date="x"') == "restored"


def test_is_downloadable() -> None:
    # Readable classes are always downloadable.
    assert is_downloadable("STANDARD", "none")
    assert is_downloadable("STANDARD_IA", "none")
    assert is_downloadable("GLACIER_IR", "none")
    assert is_downloadable("FOLDER_MARKER", "none")
    # Archived without restore is the trap: HEAD returns 200 but GET fails.
    assert not is_downloadable("DEEP_ARCHIVE", "none")
    assert not is_downloadable("GLACIER", "none")
    assert not is_downloadable("DEEP_ARCHIVE", "restoring")
    # Only a completed restore unlocks GET.
    assert is_downloadable("DEEP_ARCHIVE", "restored")
    assert is_downloadable("GLACIER", "restored")


def test_dst_key_for() -> None:
    assert dst_key_for("1/x.png", "1/", "1/") == "1/x.png"
    assert dst_key_for("1/a/b.png", "1/", "1/") == "1/a/b.png"
    assert dst_key_for("other.png", "1/", "1/") == "1/other.png"


def test_census_groups_and_collects_archived() -> None:
    s3 = FakeS3(
        [
            {
                "Contents": [
                    {"Key": "1/a.png", "Size": 10, "StorageClass": "STANDARD"},
                    {"Key": "1/b.png", "Size": 20, "StorageClass": "DEEP_ARCHIVE"},
                    {"Key": "1/", "Size": 0},
                ]
            },
            {
                "Contents": [
                    {"Key": "1/c.png", "Size": 30, "StorageClass": "GLACIER_IR"},
                ]
            },
        ]
    )
    census = run_census(s3, "bkt", "1/", 100)
    assert census.ok
    assert census.scanned == 4
    assert census.bytes_total == 60
    assert census.by_class == {
        "STANDARD": 1,
        "DEEP_ARCHIVE": 1,
        "FOLDER_MARKER": 1,
        "GLACIER_IR": 1,
    }
    assert [o.key for o in census.archived] == ["1/b.png"]


def test_census_respects_max_items() -> None:
    s3 = FakeS3(
        [
            {
                "Contents": [
                    {"Key": f"1/{i}.png", "Size": 1, "StorageClass": "STANDARD"} for i in range(5)
                ]
            },
            {
                "Contents": [
                    {"Key": f"1/x{i}.png", "Size": 1, "StorageClass": "STANDARD"} for i in range(5)
                ]
            },
        ]
    )
    census = run_census(s3, "bkt", "1/", 3)
    assert census.scanned == 3


def test_census_records_error() -> None:
    class Boom:
        def list_objects_v2(self, **kwargs: object) -> dict:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListObjectsV2"
            )

    census = run_census(Boom(), "bkt", "1/", 10)
    assert not census.ok
    assert census.error_code == "AccessDenied"
    assert census.scanned == 0

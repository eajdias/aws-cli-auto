"""Pure S3 operations. Pagination is manual; no query projections."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Protocol

from .aws_factory import split_aws_error

ARCHIVED_CLASSES = frozenset({"DEEP_ARCHIVE", "GLACIER"})
FOLDER_MARKER = "FOLDER_MARKER"


class S3Like(Protocol):
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def restore_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def copy_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...


def normalize_class(raw: str | None) -> str:
    """list-objects-v2 omits StorageClass for 0-byte folder markers."""
    return raw if raw else FOLDER_MARKER


def is_archived(storage_class: str) -> bool:
    return storage_class in ARCHIVED_CLASSES


def is_folder_marker(storage_class: str) -> bool:
    return storage_class == FOLDER_MARKER


@dataclass
class S3ObjectRef:
    key: str
    size: int
    storage_class: str


@dataclass
class Census:
    scanned: int = 0
    bytes_total: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    archived: list[S3ObjectRef] = field(default_factory=list)
    ok: bool = True
    error_code: str | None = None
    error_msg: str | None = None
    truncated: bool = False


def run_census(
    s3: S3Like,
    bucket: str,
    prefix: str,
    max_items: int,
    on_page: Any = None,
) -> Census:
    """List up to max_items keys, grouping by storage class."""
    census = Census()
    token: str | None = None
    while True:
        params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            params["ContinuationToken"] = token
        try:
            page = s3.list_objects_v2(**params)
        except Exception as exc:
            census.ok = False
            census.error_code, census.error_msg = split_aws_error(exc)
            return census
        for obj in page.get("Contents", []) or []:
            if obj is None:
                continue
            cls = normalize_class(obj.get("StorageClass"))
            key = str(obj.get("Key", ""))
            size = int(obj.get("Size", 0) or 0)
            census.by_class[cls] = census.by_class.get(cls, 0) + 1
            census.scanned += 1
            census.bytes_total += size
            if is_archived(cls):
                census.archived.append(S3ObjectRef(key=key, size=size, storage_class=cls))
            if census.scanned >= max_items:
                census.truncated = True
                break
        if on_page is not None:
            on_page(census.scanned)
        if census.scanned >= max_items:
            break
        token = page.get("NextContinuationToken")
        if not token:
            break
    return census


def parse_restore_header(restore: str | None) -> str:
    """Map the S3 Restore header to restored|restoring|none."""
    if not restore:
        return "none"
    if 'ongoing-request="true"' in restore:
        return "restoring"
    if 'ongoing-request="false"' in restore:
        return "restored"
    return "none"


def is_downloadable(storage_class: str, restore_state: str) -> bool:
    """An archived object only allows GET after a completed restore.

    HEAD on Deep Archive returns 200 even when the object is unreadable,
    so the storage class (not just the Restore header) decides.
    """
    return not is_archived(storage_class) or restore_state == "restored"


def head_restore_state(
    s3: S3Like, bucket: str, key: str
) -> tuple[str, dict[str, Any] | None, tuple[str, str] | None]:
    """Return (state, head, error). State is restored|restoring|none|error."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code, msg = split_aws_error(exc)
        return "error", None, (code, msg)
    return parse_restore_header(head.get("Restore")), head, None


@dataclass
class StatusCounts:
    """Aggregate restore state across archived objects."""

    restored: int = 0
    restoring: int = 0
    not_requested: int = 0
    failed: int = 0
    total: int = 0

    @property
    def complete(self) -> bool:
        return (
            self.total > 0 and self.restoring == 0 and self.not_requested == 0 and self.failed == 0
        )

    def summary(self) -> str:
        return (
            f"restaurados={self.restored} em_andamento={self.restoring} "
            f"nao_solicitados={self.not_requested} falhas={self.failed} de {self.total}"
        )


def collect_status(
    s3: S3Like,
    bucket: str,
    refs: list[S3ObjectRef],
    workers: int = 8,
    on_result: Callable[[S3ObjectRef, str, str | None, str | None], None] | None = None,
) -> StatusCounts:
    """HEAD every archived key and aggregate the result (thread-safe counters)."""
    counts = StatusCounts(total=len(refs))

    def record(ref: S3ObjectRef, state: str, code: str | None, msg: str | None) -> None:
        if state == "error":
            counts.failed += 1
        elif state == "restored":
            counts.restored += 1
        elif state == "restoring":
            counts.restoring += 1
        else:
            counts.not_requested += 1
        if on_result is not None:
            on_result(ref, state, code, msg)

    def head(ref: S3ObjectRef) -> tuple[S3ObjectRef, str, str | None, str | None]:
        state, _, err = head_restore_state(s3, bucket, ref.key)
        if err is not None:
            return (ref, "error", err[0], err[1])
        return (ref, state, None, None)

    if workers <= 1:
        for ref in refs:
            record(*head(ref))
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="status") as ex:
            for fut in as_completed([ex.submit(head, ref) for ref in refs]):
                record(*fut.result())
    return counts


def dst_key_for(src_key: str, src_prefix: str, dst_prefix: str) -> str:
    if src_key.startswith(src_prefix):
        return dst_prefix + src_key[len(src_prefix) :]
    return dst_prefix + src_key


def timed(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, int, tuple[str, str] | None]:
    """Run fn, return (result, duration_ms, error)."""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        duration = int((time.perf_counter() - start) * 1000)
        code, msg = split_aws_error(exc)
        return None, duration, (code, msg)
    duration = int((time.perf_counter() - start) * 1000)
    return result, duration, None

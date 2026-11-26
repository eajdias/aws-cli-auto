"""Read-only source-vs-destination parity check (generic bucket/prefix pairs).

Lists both scopes fully (up to ``max_items``) and compares relative key sets:
missing in destination, extra in destination and size divergences, with the
age bounds of the missing keys. Restore-state sampling of missing keys lives
in the CLI layer, next to the other HEAD-based checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class S3Like(Protocol):
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...


# relative key -> (size, last modified)
Listing = dict[str, tuple[int, datetime | None]]


@dataclass
class ParityResult:
    src_objects: int = 0
    src_bytes: int = 0
    dst_objects: int = 0
    dst_bytes: int = 0
    missing: dict[str, tuple[int, datetime | None]] = field(default_factory=dict)
    extra: dict[str, tuple[int, datetime | None]] = field(default_factory=dict)
    diverged: dict[str, tuple[tuple[int, datetime | None], tuple[int, datetime | None]]] = field(
        default_factory=dict
    )
    src_truncated: bool = False
    dst_truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.missing and not self.extra and not self.diverged

    @property
    def missing_bytes(self) -> int:
        return sum(size for size, _ in self.missing.values())

    @property
    def missing_oldest(self) -> datetime | None:
        dates = [modified for _, modified in self.missing.values() if modified is not None]
        return min(dates) if dates else None

    @property
    def missing_newest(self) -> datetime | None:
        dates = [modified for _, modified in self.missing.values() if modified is not None]
        return max(dates) if dates else None

    def summary(self) -> str:
        return (
            f"ausentes={len(self.missing)} extras={len(self.extra)} "
            f"divergentes={len(self.diverged)}"
        )


def compare_listings(src: Listing, dst: Listing) -> ParityResult:
    """Compare two relative-key listings (pure, no I/O)."""
    result = ParityResult(
        src_objects=len(src),
        src_bytes=sum(size for size, _ in src.values()),
        dst_objects=len(dst),
        dst_bytes=sum(size for size, _ in dst.values()),
    )
    for key, meta in src.items():
        other = dst.get(key)
        if other is None:
            result.missing[key] = meta
        elif other[0] != meta[0]:
            result.diverged[key] = (meta, other)
    for key, meta in dst.items():
        if key not in src:
            result.extra[key] = meta
    return result


def list_scope(s3: S3Like, bucket: str, prefix: str, max_items: int) -> tuple[Listing, bool]:
    """List ``prefix`` fully; returns (relative-key map, truncated)."""
    out: Listing = {}
    token: str | None = None
    truncated = False
    while True:
        params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            params["ContinuationToken"] = token
        page = s3.list_objects_v2(**params)
        for obj in page.get("Contents", []) or []:
            if obj is None:
                continue
            key = str(obj.get("Key", ""))
            if key.endswith("/"):
                continue
            rel = key[len(prefix) :] if key.startswith(prefix) else key
            out[rel] = (int(obj.get("Size", 0) or 0), obj.get("LastModified"))
            if len(out) >= max_items:
                truncated = True
                break
        if truncated:
            break
        token = page.get("NextContinuationToken")
        if not token:
            break
    return out, truncated

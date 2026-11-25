"""Live prefix discovery and sampled HEAD checks for the client UI."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .locations import normalize_root
from .s3ops import (
    FOLDER_MARKER,
    head_restore_state,
    is_downloadable,
    normalize_class,
)


class PaginatorLike(Protocol):
    def paginate(self, **kwargs: Any) -> Iterable[dict[str, Any]]: ...


class S3Like(Protocol):
    def get_paginator(self, name: str) -> PaginatorLike: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass
class PrefixStats:
    prefix: str
    objects: int = 0
    bytes: int = 0


@dataclass
class PrefixListing:
    prefixes: list[PrefixStats] = field(default_factory=list)
    objects: int = 0
    bytes_total: int = 0
    elapsed_ms: int = 0
    truncated: bool = False


@dataclass
class HeadSample:
    prefix: str
    key: str
    storage_class: str
    state: str
    downloadable: bool


@dataclass
class HeadSampleResult:
    ok: bool = True
    samples: list[HeadSample] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


def _relative_prefix(raw: str, root: str) -> str:
    rel = (raw or "").strip("/")
    base = normalize_root(root).rstrip("/")
    if rel == base:
        return ""
    prefix = normalize_root(root)
    if prefix and rel.startswith(prefix):
        rel = rel[len(prefix) :]
    return f"{rel}/" if rel else ""


def list_prefixes(s3: S3Like, bucket: str, root: str, max_list: int = 700000) -> PrefixListing:
    """Discover first-level folders, then count objects in one full listing pass.

    ``root`` is the run scope inside the bucket: a legacy serial
    (``"1234-5678-90"``), a serial prefix (``"1234-5678-90/"``) or any generic
    prefix (``"backups/2024/"``). "" scopes to the whole bucket.
    """
    started = time.perf_counter()
    base = normalize_root(root)
    listing = PrefixListing()

    discovered: set[str] = set()
    discovery = s3.get_paginator("list_objects_v2")
    for page in discovery.paginate(Bucket=bucket, Prefix=base, Delimiter="/"):
        for entry in page.get("CommonPrefixes", []) or []:
            if not entry:
                continue
            name = str(entry.get("Prefix", ""))
            if name.startswith(base):
                rel = name[len(base) :]
                if rel:
                    discovered.add(rel)

    counts: dict[str, PrefixStats] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=base):
        for obj in page.get("Contents", []) or []:
            if obj is None:
                continue
            key = str(obj.get("Key", ""))
            storage_class = normalize_class(obj.get("StorageClass"))
            if not key.startswith(base) or key.endswith("/") or storage_class == FOLDER_MARKER:
                continue
            rel = key[len(base) :]
            prefix = f"{rel.split('/', 1)[0]}/" if "/" in rel else ""
            if prefix and discovered and prefix not in discovered:
                continue
            stat = counts.setdefault(prefix, PrefixStats(prefix=prefix))
            size = int(obj.get("Size", 0) or 0)
            stat.objects += 1
            stat.bytes += size
            listing.objects += 1
            listing.bytes_total += size
            if listing.objects >= max_list:
                listing.truncated = True
                break
        if listing.truncated:
            break

    listing.prefixes = [counts[name] for name in sorted(counts)]
    listing.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return listing


def sample_heads(
    s3: S3Like,
    bucket: str,
    root: str,
    prefixes: list[str],
    sample_size: int = 5,
) -> HeadSampleResult:
    """HEAD a small sample per selected prefix before allowing download."""
    result = HeadSampleResult()
    base = normalize_root(root)

    for raw in prefixes:
        prefix = _relative_prefix(raw, root)
        full_prefix = f"{base}{prefix}"
        keys: list[str] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []) or []:
                if obj is None:
                    continue
                key = str(obj.get("Key", ""))
                storage_class = normalize_class(obj.get("StorageClass"))
                if key.endswith("/") or storage_class == FOLDER_MARKER:
                    continue
                rel = key[len(base) :] if key.startswith(base) else key
                if not prefix and "/" in rel:
                    continue
                keys.append(key)
                if len(keys) >= sample_size:
                    break
            if len(keys) >= sample_size:
                break

        if not keys:
            result.ok = False
            result.failures.append({"prefix": prefix, "key": "", "reason": "no objects found"})
            continue

        for key in keys:
            state, head, error = head_restore_state(s3, bucket, key)
            storage_class = normalize_class((head or {}).get("StorageClass"))
            downloadable = error is None and is_downloadable(storage_class, state)
            result.samples.append(
                HeadSample(
                    prefix=prefix,
                    key=key,
                    storage_class=storage_class,
                    state=state,
                    downloadable=downloadable,
                )
            )
            if downloadable:
                continue
            if error is not None:
                code, msg = error
                reason = f"{code}: {msg}"
            elif state == "restoring":
                reason = "restore ongoing"
            else:
                reason = "archived, cannot download"
            result.ok = False
            result.failures.append({"prefix": prefix, "key": key, "reason": reason})

    return result

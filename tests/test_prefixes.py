"""Unit tests for live prefix discovery and sampled HEAD checks."""

from __future__ import annotations

from s3_glacier_restore.prefixes import (
    HeadSampleResult,
    PrefixListing,
    list_prefixes,
    sample_heads,
)

SERIAL = "1234-5678-90"
SERIAL_PREFIX = f"{SERIAL}/"


class FakePaginator:
    def __init__(self, s3: FakeS3) -> None:
        self.s3 = s3

    def paginate(self, **kwargs: object) -> object:
        self.s3.calls.append(kwargs)
        pages = self.s3.pages_for(kwargs)
        yield from pages


class FakeS3:
    def __init__(
        self,
        discovery_pages: list[dict[str, object]],
        count_pages: list[dict[str, object]],
        sample_pages: dict[str, list[dict[str, object]]] | None = None,
        heads: dict[str, dict[str, object]] | None = None,
        root: str = SERIAL_PREFIX,
    ) -> None:
        self.discovery_pages = discovery_pages
        self.count_pages = count_pages
        self.sample_pages = sample_pages or {}
        self.heads = heads or {}
        self.root = root
        self.calls: list[dict[str, object]] = []
        self.head_calls: list[str] = []

    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_objects_v2"
        return FakePaginator(self)

    def pages_for(self, kwargs: dict[str, object]) -> list[dict[str, object]]:
        if kwargs.get("Delimiter") == "/":
            return self.discovery_pages
        prefix = str(kwargs.get("Prefix", ""))
        if prefix == self.root:
            return self.count_pages
        return self.sample_pages.get(prefix, [])

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.head_calls.append(key)
        if key not in self.heads:
            raise KeyError(key)
        return self.heads[key]


def _standard(key: str, size: int) -> dict[str, object]:
    return {"Key": key, "Size": size, "StorageClass": "STANDARD"}


def test_list_prefixes_aggregates_objects_and_ignores_markers() -> None:
    s3 = FakeS3(
        discovery_pages=[
            {
                "CommonPrefixes": [
                    {"Prefix": f"{SERIAL_PREFIX}Images/"},
                    {"Prefix": f"{SERIAL_PREFIX}Reports/"},
                ]
            }
        ],
        count_pages=[
            {
                "Contents": [
                    _standard(f"{SERIAL_PREFIX}root.txt", 1),
                    _standard(f"{SERIAL_PREFIX}Images/a.png", 10),
                    {"Key": f"{SERIAL_PREFIX}Images/", "Size": 0},
                    {
                        "Key": f"{SERIAL_PREFIX}Reports/r.pdf",
                        "Size": 20,
                        "StorageClass": "STANDARD_IA",
                    },
                ]
            }
        ],
    )

    result = list_prefixes(s3, "bucket-test", SERIAL)

    assert isinstance(result, PrefixListing)
    assert [(p.prefix, p.objects, p.bytes) for p in result.prefixes] == [
        ("", 1, 1),
        ("Images/", 1, 10),
        ("Reports/", 1, 20),
    ]
    assert result.objects == 3
    assert result.bytes_total == 31
    assert result.truncated is False
    assert result.elapsed_ms >= 0


def test_list_prefixes_marks_truncated_at_max_list() -> None:
    s3 = FakeS3(
        discovery_pages=[{"CommonPrefixes": [{"Prefix": f"{SERIAL_PREFIX}Images/"}]}],
        count_pages=[
            {
                "Contents": [
                    _standard(f"{SERIAL_PREFIX}root.txt", 1),
                    _standard(f"{SERIAL_PREFIX}Images/a.png", 10),
                    _standard(f"{SERIAL_PREFIX}Images/b.png", 20),
                ]
            }
        ],
    )

    result = list_prefixes(s3, "bucket-test", SERIAL, max_list=2)

    assert result.objects == 2
    assert result.bytes_total == 11
    assert result.truncated is True
    assert [p.prefix for p in result.prefixes] == ["", "Images/"]


def test_list_prefixes_supports_generic_root() -> None:
    root = "backups/2024/"
    s3 = FakeS3(
        discovery_pages=[
            {
                "CommonPrefixes": [
                    {"Prefix": f"{root}Images/"},
                    {"Prefix": f"{root}Reports/"},
                ]
            }
        ],
        count_pages=[
            {
                "Contents": [
                    _standard(f"{root}Images/a.png", 10),
                    _standard(f"{root}Reports/r.pdf", 20),
                ]
            }
        ],
        root=root,
    )

    result = list_prefixes(s3, "bucket-test", "backups/2024")

    assert [(p.prefix, p.objects) for p in result.prefixes] == [
        ("Images/", 1),
        ("Reports/", 1),
    ]
    assert result.objects == 2
    prefixes = [call["Prefix"] for call in s3.calls]
    assert all(str(prefix).startswith(root) for prefix in prefixes)


def test_sample_heads_ok_with_five_standard_objects() -> None:
    keys = [f"{SERIAL_PREFIX}Images/f{i}.png" for i in range(5)]
    s3 = FakeS3(
        discovery_pages=[],
        count_pages=[],
        sample_pages={f"{SERIAL_PREFIX}Images/": [{"Contents": [_standard(k, 1) for k in keys]}]},
        heads={k: {"StorageClass": "STANDARD"} for k in keys},
    )

    result = sample_heads(s3, "bucket-test", SERIAL, ["Images/"], sample_size=5)

    assert isinstance(result, HeadSampleResult)
    assert result.ok is True
    assert result.failures == []
    assert [s.key for s in result.samples] == keys
    assert all(s.downloadable for s in result.samples)
    assert s3.head_calls == keys


def test_sample_heads_rejects_archived_without_restore() -> None:
    archived = f"{SERIAL_PREFIX}Reports/r1"
    readable = f"{SERIAL_PREFIX}Reports/r2"
    s3 = FakeS3(
        discovery_pages=[],
        count_pages=[],
        sample_pages={
            f"{SERIAL_PREFIX}Reports/": [
                {
                    "Contents": [
                        {"Key": archived, "Size": 5, "StorageClass": "DEEP_ARCHIVE"},
                        _standard(readable, 5),
                    ]
                }
            ]
        },
        heads={
            archived: {"StorageClass": "DEEP_ARCHIVE"},
            readable: {"StorageClass": "STANDARD"},
        },
    )

    result = sample_heads(s3, "bucket-test", SERIAL, ["Reports/"], sample_size=2)

    assert result.ok is False
    assert len(result.failures) == 1
    assert result.failures[0]["prefix"] == "Reports/"
    assert result.failures[0]["key"] == archived
    assert "archived" in result.failures[0]["reason"]
    archived_sample = next(s for s in result.samples if s.key == archived)
    assert archived_sample.downloadable is False
    assert archived_sample.state == "none"


def test_sample_heads_fails_for_prefix_without_keys() -> None:
    s3 = FakeS3(discovery_pages=[], count_pages=[], sample_pages={})

    result = sample_heads(s3, "bucket-test", SERIAL, ["Empty/"])

    assert result.ok is False
    assert result.samples == []
    assert result.failures[0]["prefix"] == "Empty/"
    assert result.failures[0]["key"] == ""

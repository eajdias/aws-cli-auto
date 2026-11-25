"""S3 location parsing and label inference (generic buckets/prefixes)."""

from __future__ import annotations

import pytest

from s3_glacier_restore.locations import (
    infer_label,
    parse_s3_location,
    sanitize_label,
)


def test_parse_full_uri_with_serial_prefix() -> None:
    location = parse_s3_location("s3://destino-bkt/1234-5678-90/")

    assert location.bucket == "destino-bkt"
    assert location.prefix == "1234-5678-90/"


def test_parse_bare_bucket_has_empty_prefix() -> None:
    location = parse_s3_location("meu-bucket")

    assert location.bucket == "meu-bucket"
    assert location.prefix == ""


def test_parse_uri_without_prefix() -> None:
    location = parse_s3_location("s3://meu-bucket")

    assert location.bucket == "meu-bucket"
    assert location.prefix == ""


def test_parse_bucket_slash_prefix_without_scheme() -> None:
    location = parse_s3_location("bkt/backups/2024")

    assert location.bucket == "bkt"
    assert location.prefix == "backups/2024/"


def test_parse_rejects_empty_and_scheme_only() -> None:
    for raw in ("", "   ", "s3://", "s3:///prefixo/"):
        with pytest.raises(ValueError):
            parse_s3_location(raw)


def test_infer_label_prefers_explicit_value() -> None:
    assert infer_label("minha-etiqueta", "1234-5678-90/") == "minha-etiqueta"


def test_infer_label_detects_serial_in_prefix() -> None:
    assert infer_label("", "1234-5678-90/") == "1234-5678-90"
    assert infer_label("", "backups/1234-5678-90/") == "1234-5678-90"


def test_infer_label_falls_back_to_last_segment() -> None:
    assert infer_label("", "backups/2024/") == "2024"
    assert infer_label("", "cliente-a/") == "cliente-a"


def test_infer_label_requires_a_prefix_or_explicit_value() -> None:
    with pytest.raises(ValueError):
        infer_label("", "")


def test_infer_label_rejects_invalid_explicit_value() -> None:
    with pytest.raises(ValueError):
        infer_label("etiqueta invalida/com barra", "1234-5678-90/")


def test_sanitize_label_replaces_unsafe_runs() -> None:
    assert sanitize_label("a b/c") == "a-b-c"
    assert sanitize_label("  2024  ") == "2024"

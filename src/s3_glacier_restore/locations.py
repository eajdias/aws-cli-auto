"""S3 location parsing and run-label inference.

The pipeline works on shared buckets: every run is scoped to a bucket +
prefix pair such as ``s3://<bucket>/<prefixo>/``. When the last prefix
segment matches the legacy serial pattern it is reused as the run label;
otherwise the label is either explicit (``--serial``/``--label``) or the
last prefix segment, sanitized for file names, logs and reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERIAL_RE = re.compile(r"^[0-9]{4}-[0-9]{4}-[0-9]{2}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,61}[A-Za-z0-9]$|^[A-Za-z0-9]$")


@dataclass(frozen=True)
class S3Location:
    bucket: str
    prefix: str  # "" (whole bucket) or "a/b/" with a trailing slash


def parse_s3_location(raw: str) -> S3Location:
    """Parse ``s3://bucket/prefix/``, ``bucket/prefix/`` or a bare bucket."""
    text = (raw or "").strip()
    if text.lower().startswith("s3://"):
        text = text[len("s3://") :]
    if not text or text.startswith("/"):
        raise ValueError("local S3 vazio: informe bucket ou s3://bucket/prefixo/")
    text = text.strip("/")
    if not text:
        raise ValueError("local S3 vazio: informe bucket ou s3://bucket/prefixo/")
    bucket, _, rest = text.partition("/")
    if not _BUCKET_RE.match(bucket):
        raise ValueError(f"bucket invalido: {bucket!r}")
    prefix = rest.strip("/")
    if prefix:
        if ".." in prefix.split("/"):
            raise ValueError(f"prefixo invalido: {prefix!r}")
        prefix += "/"
    return S3Location(bucket=bucket, prefix=prefix)


def normalize_root(root: str) -> str:
    """Normalize a bucket root: "" (whole bucket) or "a/b/" with trailing slash."""
    stripped = (root or "").strip().strip("/")
    return f"{stripped}/" if stripped else ""


def last_segment(prefix: str) -> str:
    """Return the last path segment of a normalized prefix ("" if none)."""
    stripped = (prefix or "").strip("/")
    if not stripped:
        return ""
    return stripped.split("/")[-1]


def sanitize_label(raw: str) -> str:
    """Make an arbitrary string safe for run ids, file names and logs."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (raw or "").strip()).strip("-")
    return cleaned


def infer_label(explicit: str, *prefixes: str) -> str:
    """Resolve the run label: explicit value wins, else last prefix segment."""
    wanted = (explicit or "").strip()
    if wanted:
        if not LABEL_RE.match(wanted):
            raise ValueError(f"etiqueta invalida (letras, numeros, . _ -): {explicit!r}")
        return wanted
    for prefix in prefixes:
        segment = last_segment(prefix)
        if segment:
            label = segment if LABEL_RE.match(segment) else sanitize_label(segment)
            if label:
                return label
    raise ValueError("etiqueta ausente: informe --serial ou use prefixo nao vazio")

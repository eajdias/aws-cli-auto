"""Pipeline configuration. No secrets here - only names and policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .locations import LABEL_RE, SERIAL_RE  # noqa: F401 (reexportado p/ compatibilidade)

DEFAULT_REGION = "sa-east-1"
DEFAULT_STORAGE_CLASS = "STANDARD"

VALID_TIERS = {"Standard", "Bulk"}
VALID_STORAGE_CLASSES = {"STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR"}
VALID_LOG_LEVELS = {"OFF", "BASIC", "TRANSFER"}


def make_run_id(serial: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{serial}-{stamp}-{uuid4().hex[:8]}"


def _normalize_prefix(prefix: str) -> str:
    """Normalize to "" (whole bucket) or "a/b/" with a trailing slash."""
    stripped = (prefix or "").strip().strip("/")
    return f"{stripped}/" if stripped else ""


@dataclass
class RestoreConfig:
    """Run label (ex-serial) + explicit bucket/prefix pairs.

    ``serial`` is kept as the attribute name for compatibility, but it is a
    free-form run label now. Prefixes default to ``<label>/`` (legacy layout)
    and may point anywhere (``backups/2024/``) for generic buckets.
    """

    serial: str
    profile: str = "default"
    region: str = DEFAULT_REGION
    src_bucket: str = ""
    dst_bucket: str = ""
    src_prefix: str = ""
    dst_prefix: str = ""
    dest_storage_class: str = DEFAULT_STORAGE_CLASS
    restore_days: int = 7
    restore_tier: str = "Standard"
    max_list: int = 20000
    dry_run: bool = False
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    report_dir: Path = field(default_factory=lambda: Path("reports"))
    run_id: str = ""

    def __post_init__(self) -> None:
        if not LABEL_RE.match((self.serial or "").strip()):
            raise ValueError(f"etiqueta invalida (letras, numeros, . _ -): {self.serial!r}")
        self.serial = self.serial.strip()
        if not self.src_bucket.strip():
            raise ValueError("bucket de origem ausente: informe --src ou S3_RESTORE_SRC_BUCKET")
        if not self.dst_bucket.strip():
            raise ValueError("bucket de destino ausente: informe --dst ou S3_RESTORE_DST_BUCKET")
        self.src_prefix = _normalize_prefix(self.src_prefix) or f"{self.serial}/"
        self.dst_prefix = _normalize_prefix(self.dst_prefix) or f"{self.serial}/"
        if self.restore_tier not in VALID_TIERS:
            raise ValueError(f"invalid restore tier: {self.restore_tier}")
        if self.dest_storage_class not in VALID_STORAGE_CLASSES:
            raise ValueError(f"invalid storage class: {self.dest_storage_class}")
        if self.max_list <= 0:
            raise ValueError("max_list must be positive")
        if not self.run_id:
            self.run_id = make_run_id(self.serial)

    def src_uri(self, key: str = "") -> str:
        return f"s3://{self.src_bucket}/{key or self.src_prefix}"

    def dst_uri(self, key: str = "") -> str:
        return f"s3://{self.dst_bucket}/{key or self.dst_prefix}"

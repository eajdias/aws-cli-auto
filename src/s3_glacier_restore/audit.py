"""Audit trail: JSONL event log with run correlation and secret redaction."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_RESULTS = {"ok", "fail", "skip", "blocked", "dry_run"}
VALID_LEVELS = {"INFO", "WARN", "ERROR"}

_ACCESS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_SECRET_RE = re.compile(r"(?i)(secret[_-]?key[\"'\s:=]+)([^\s\",}]+)")
_PASSWORD_RE = re.compile(r"(?i)(password[\"'\s:=]+)([^\s\",}]+)")


def sanitize(text: str | None) -> str | None:
    """Remove key material from a log string. Never let secrets reach disk."""
    if not text:
        return text
    redacted = _ACCESS_KEY_RE.sub("AKIA***REDACTED", text)
    redacted = _SECRET_RE.sub(r"\1***REDACTED", redacted)
    redacted = _PASSWORD_RE.sub(r"\1***REDACTED", redacted)
    return redacted


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class AuditEvent:
    ts: str
    level: str
    run_id: str
    serial: str
    profile: str
    account: str
    actor: str
    step: str
    action: str
    resource: str
    result: str
    error_code: str | None = None
    error_msg: str | None = None
    duration_ms: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    """Append-only JSONL audit log. One event per line, flushed immediately."""

    def __init__(
        self,
        log_dir: Path,
        run_id: str,
        serial: str,
        profile: str,
        account: str = "",
        actor: str = "",
    ) -> None:
        self.log_dir = log_dir
        self.run_id = run_id
        self.serial = serial
        self.profile = profile
        self.account = account
        self.actor = actor
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{run_id}.jsonl"
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        # Write run header so every file is self-describing.
        header = AuditEvent(
            ts=utc_now_iso(),
            level="INFO",
            run_id=run_id,
            serial=serial,
            profile=profile,
            account=account,
            actor=actor,
            step="run",
            action="run:start",
            resource=f"serial:{serial}",
            result="ok",
        )
        self._write(header, count=False)

    def set_identity(self, account: str, actor: str) -> None:
        self.account = account
        self.actor = actor

    def log(
        self,
        level: str,
        step: str,
        action: str,
        resource: str,
        result: str,
        error_code: str | None = None,
        error_msg: str | None = None,
        duration_ms: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if level not in VALID_LEVELS:
            raise ValueError(f"invalid level: {level}")
        if result not in VALID_RESULTS:
            raise ValueError(f"invalid result: {result}")
        event = AuditEvent(
            ts=utc_now_iso(),
            level=level,
            run_id=self.run_id,
            serial=self.serial,
            profile=self.profile,
            account=self.account,
            actor=self.actor,
            step=step,
            action=action,
            resource=sanitize(resource) or "",
            result=result,
            error_code=error_code,
            error_msg=sanitize(error_msg),
            duration_ms=duration_ms,
            extra=extra or {},
        )
        self._write(event)
        return event

    def _write(self, event: AuditEvent, count: bool = True) -> None:
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if count:
                self._counts[event.result] = self._counts.get(event.result, 0) + 1

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

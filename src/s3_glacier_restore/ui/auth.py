"""Local admin gate. Stores only a salted digest, never the password itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

OVERRIDE_FILE = "admin-auth.json"


def _default_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "s3-glacier-restore"


def _override_path(appdata_dir: Path | None) -> Path:
    return Path(appdata_dir or _default_appdata()) / OVERRIDE_FILE


def _read_override(path: Path) -> tuple[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    salt = str(payload.get("salt", ""))
    digest = str(payload.get("digest", ""))
    if len(salt) != 32 or len(digest) != 64:
        return None
    try:
        bytes.fromhex(salt)
        bytes.fromhex(digest)
    except ValueError:
        return None
    return digest, salt


def _configured_digest_and_salt(appdata_dir: Path | None = None) -> tuple[str, str]:
    try:
        from . import _build_config
    except ImportError:
        digest, salt = "", ""
    else:
        digest = str(getattr(_build_config, "ADMIN_PASSWORD_SHA256", ""))
        salt = str(getattr(_build_config, "ADMIN_SALT", ""))
    override = _read_override(_override_path(appdata_dir))
    if override is not None:
        digest, salt = override
    env_digest = os.environ.get("S3_RESTORE_ADMIN_PASSWORD_SHA256", "")
    env_salt = os.environ.get("S3_RESTORE_ADMIN_SALT", "")
    if env_digest and env_salt:
        digest, salt = env_digest, env_salt
    return digest, salt


def password_digest(password: str, salt: str) -> str:
    return hashlib.sha256(bytes.fromhex(salt) + password.encode()).hexdigest()


def set_admin_password(password: str, appdata_dir: Path | None = None) -> None:
    """Persist a local override; only the salt and digest touch the disk."""
    if not password.strip():
        raise ValueError("senha do admin nao pode ser vazia")
    salt = secrets.token_hex(16)
    digest = password_digest(password, salt)
    path = _override_path(appdata_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp.write_text(json.dumps({"salt": salt, "digest": digest}), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def verify_admin_password(password: str, appdata_dir: Path | None = None) -> bool:
    expected, salt = _configured_digest_and_salt(appdata_dir)
    if not expected or not salt:
        return False
    return hmac.compare_digest(password_digest(password, salt), expected)

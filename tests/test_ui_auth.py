"""Admin gate: build digest, local override and environment precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s3_glacier_restore.ui import auth


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_RESTORE_ADMIN_SALT", raising=False)
    monkeypatch.delenv("S3_RESTORE_ADMIN_PASSWORD_SHA256", raising=False)


def test_local_override_stores_digest_only_and_verifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)
    password = "pw-local-" + "3" * 10

    auth.set_admin_password(password, tmp_path)

    text = (tmp_path / "admin-auth.json").read_text(encoding="utf-8")
    assert password not in text
    assert auth.verify_admin_password(password, tmp_path)
    assert not auth.verify_admin_password("wrong", tmp_path)


def test_local_override_rejects_empty_password(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        auth.set_admin_password("   ", tmp_path)
    assert not (tmp_path / "admin-auth.json").exists()


def test_environment_digest_wins_over_local_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)
    auth.set_admin_password("local-password", tmp_path)
    salt = "ab" * 16
    monkeypatch.setenv("S3_RESTORE_ADMIN_SALT", salt)
    monkeypatch.setenv(
        "S3_RESTORE_ADMIN_PASSWORD_SHA256", auth.password_digest("env-password", salt)
    )

    assert auth.verify_admin_password("env-password", tmp_path)
    assert not auth.verify_admin_password("local-password", tmp_path)


def test_corrupt_override_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "admin-auth.json").write_text(
        json.dumps({"salt": "not-hex", "digest": "00"}), encoding="utf-8"
    )
    salt = "cd" * 16
    monkeypatch.setenv("S3_RESTORE_ADMIN_SALT", salt)
    monkeypatch.setenv("S3_RESTORE_ADMIN_PASSWORD_SHA256", auth.password_digest("fallback", salt))

    assert auth.verify_admin_password("fallback", tmp_path)

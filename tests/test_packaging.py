"""Build-time checks for the single-file client; no real PyInstaller run here."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from s3_glacier_restore.ui import auth, entry, resources

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
SPEC = ROOT / "s3-download-client.spec"


def _build_client() -> ModuleType:
    path = SCRIPTS / "build_client.py"
    spec = importlib.util.spec_from_file_location("build_client", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_client"] = module
    spec.loader.exec_module(module)
    return module


def test_build_help_exposes_no_client_runtime_flags() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_client.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for forbidden in ("--serial", "--bucket", "--verified-run-id", "--manifest"):
        assert forbidden not in result.stdout


def test_write_build_config_never_stores_password_in_clear(tmp_path: Path) -> None:
    module = _build_client()
    password = "pw-smoke-" + "7" * 12
    path = tmp_path / "_build_config.py"

    module.write_build_config(password, path)
    text = path.read_text(encoding="utf-8")

    assert password not in text
    salt = module._constant(text, "ADMIN_SALT")
    digest = module._constant(text, "ADMIN_PASSWORD_SHA256")
    assert len(bytes.fromhex(salt)) == 16
    assert digest == hashlib.sha256(bytes.fromhex(salt) + password.encode()).hexdigest()


def test_write_build_config_rejects_empty_password(tmp_path: Path) -> None:
    module = _build_client()
    path = tmp_path / "_build_config.py"
    with pytest.raises(ValueError):
        module.write_build_config("   ", path)
    assert not path.exists()


def test_password_digest_uses_salt_bytes() -> None:
    salt = "ab" * 16
    assert (
        auth.password_digest("pw", salt) == hashlib.sha256(bytes.fromhex(salt) + b"pw").hexdigest()
    )


def test_build_uses_default_admin_password_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _build_client()
    captured: list[str] = []
    monkeypatch.delenv("S3_RESTORE_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(module, "build", lambda password: captured.append(password) or 0)

    assert module.main([]) == 0
    assert captured == [module.DEFAULT_ADMIN_PASSWORD]
    assert module.DEFAULT_ADMIN_PASSWORD == "eajdias"


def test_build_password_can_be_overridden_by_flag_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _build_client()
    captured: list[str] = []
    monkeypatch.setattr(module, "build", lambda password: captured.append(password) or 0)

    assert module.main(["--admin-password", "from-flag"]) == 0
    monkeypatch.setenv("S3_RESTORE_ADMIN_PASSWORD", "from-env")
    assert module.main([]) == 0
    assert captured == ["from-flag", "from-env"]


def test_spec_embeds_only_html_and_existing_files() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "manifest.json" not in text
    assert "index.html" in text
    assert "console=False" in text
    assert "upx=False" in text
    assert "s3-download-client" in text
    assert "pathex=" in text
    for name in ("scripts/exe_entry.py", "src/s3_glacier_restore/ui/index.html"):
        assert (ROOT / name).is_file(), name


def test_frozen_resource_path_points_into_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(ROOT / "bundle"), raising=False)
    assert resources.resource_path("index.html") == (
        ROOT / "bundle" / "s3_glacier_restore" / "ui" / "index.html"
    )
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert resources.resource_path("index.html") == (
        ROOT / "src" / "s3_glacier_restore" / "ui" / "index.html"
    )


def test_exe_entry_routes_internal_child_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_serve(**_kwargs: object) -> int:
        raise AssertionError("serve() chamado no caminho __child")

    monkeypatch.setattr(entry, "serve", fake_serve)
    monkeypatch.setattr(
        "s3_glacier_restore.cli.main", lambda argv: calls.append(list(argv or [])) or 7
    )

    assert entry.main(["__child", "validate", "--bucket", "b"]) == 7
    assert calls == [["__child", "validate", "--bucket", "b"]]


def test_exe_entry_routes_admin_commands_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_serve(**_kwargs: object) -> int:
        raise AssertionError("serve() chamado no caminho de comando admin")

    monkeypatch.setattr(entry, "serve", fake_serve)
    monkeypatch.setattr(
        "s3_glacier_restore.cli.main", lambda argv: calls.append(list(argv or [])) or 7
    )

    assert entry.main(["auto", "--serial", "1234-5678-90"]) == 7
    assert calls == [["auto", "--serial", "1234-5678-90"]]


def test_exe_entry_keeps_directory_argument_as_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path | None] = []

    def fake_cli_main(_argv: list[str]) -> int:
        raise AssertionError("cli.main() chamado no caminho de appdata")

    monkeypatch.setattr(entry, "serve", lambda appdata_dir=None: calls.append(appdata_dir) or 0)
    monkeypatch.setattr("s3_glacier_restore.cli.main", fake_cli_main)

    assert entry.main([str(tmp_path)]) == 0
    assert calls == [tmp_path]


def test_entry_cli_commands_match_parser() -> None:
    import argparse

    from s3_glacier_restore.cli import build_parser

    subparsers = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparsers) == 1
    assert set(subparsers[0].choices) == entry._CLI_COMMANDS


def test_signing_without_configuration_warns_explicitly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _build_client()
    assert module.sign_if_configured(Path("dist/client.exe"), {}) is False
    assert "SmartScreen" in capsys.readouterr().out

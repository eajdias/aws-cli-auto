"""Build one reusable client EXE; the admin password is stored only as a digest."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from s3_glacier_restore.ui.auth import password_digest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "s3_glacier_restore"
CONFIG_PATH = SRC / "ui" / "_build_config.py"
SPEC_PATH = ROOT / "s3-download-client.spec"
EXE_PATH = ROOT / "dist" / "s3-download-client.exe"
DEFAULT_ADMIN_PASSWORD = "eajdias"  # local UI lock, not an AWS credential


def _constant(text: str, name: str) -> str:
    match = re.search(rf'^{name} = "([0-9a-f]+)"$', text, re.MULTILINE)
    if match is None:
        raise ValueError(f"constante ausente no config de build: {name}")
    return match.group(1)


def write_build_config(password: str, path: Path) -> None:
    """Write salt + digest only; never write the password itself."""
    if not password.strip():
        raise ValueError("senha do admin nao pode ser vazia")
    salt = secrets.token_hex(16)
    digest = password_digest(password, salt)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'ADMIN_SALT = "{salt}"\nADMIN_PASSWORD_SHA256 = "{digest}"\n',
        encoding="ascii",
    )


def sign_if_configured(exe: Path, env: Mapping[str, str]) -> bool:
    tool = env.get("S3_RESTORE_SIGNTOOL", "")
    subject = env.get("S3_RESTORE_CERT_SUBJ", "")
    if not tool or not subject:
        print("AVISO: exe nao assinado; o SmartScreen pode bloquear a primeira execucao.")
        return False
    subprocess.run(
        [tool, "sign", "/n", subject, "/fd", "SHA256", str(exe)],
        check=True,
    )
    print("Exe assinado com certificado configurado.")
    return True


def run_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_PATH)],
        cwd=ROOT,
        check=True,
    )


def build(admin_password: str) -> int:
    write_build_config(admin_password, CONFIG_PATH)
    run_pyinstaller()
    sign_if_configured(EXE_PATH, os.environ)
    if not EXE_PATH.is_file():
        raise RuntimeError(f"exe nao gerado: {EXE_PATH}")
    print(f"Executavel: {EXE_PATH.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera um unico EXE reutilizavel para todos os clientes."
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="senha do painel admin (padrao embutido; ou S3_RESTORE_ADMIN_PASSWORD no ambiente)",
    )
    args = parser.parse_args(argv)
    from_env = os.environ.get("S3_RESTORE_ADMIN_PASSWORD", "")
    if args.admin_password is not None:
        password = args.admin_password
    else:
        password = from_env or DEFAULT_ADMIN_PASSWORD
        if not from_env:
            print(
                "AVISO: usando a senha padrao do build; "
                "altere no painel admin ou passe --admin-password."
            )
    try:
        return build(password)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

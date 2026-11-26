"""Unit tests for UI child-process orchestration; no AWS calls."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from s3_glacier_restore.ui import runner

SERIAL = "1234-5678-90"
BUCKET = "bucket-test"
DST = f"s3://{BUCKET}/{SERIAL}/"


def _credentials() -> tuple[str, str, str]:
    access = "AKIA" + "0" * 16
    secret = "0" * 40
    token = "token-" + "1" * 20
    return access, secret, token


def _block(with_token: bool = False) -> str:
    access, secret, token = _credentials()
    lines = [
        f"AWS_ACCESS_KEY_ID: {access}",
        f"AWS_SECRET_ACCESS_KEY: {secret}",
        "AWS_DEFAULT_REGION: sa-east-1",
        "AWS_DEFAULT_OUTPUT: json",
    ]
    if with_token:
        lines.append(f"AWS_SESSION_TOKEN: {token}")
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _clean_runs() -> None:
    runner._RUNS.clear()


class FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.StringIO("\n".join(lines) + "\n")
        self.returncode: int | None = None
        self.terminated = False

    def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True


def test_validate_argv_has_no_secret_and_env_has_child_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    access, secret, _ = _credentials()

    def fake_run(cmd: list[str], **kwargs: object) -> FakeCompleted:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return FakeCompleted('{"ok":true,"region":"sa-east-1","arn":"arn:test"}\n')

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.validate(_block(), DST, SERIAL)

    assert result["ok"] is True
    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert isinstance(env, dict)
    assert all(access not in arg and secret not in arg for arg in cmd)
    assert cmd[cmd.index("--root") + 1] == f"{SERIAL}/"
    assert cmd[cmd.index("--label") + 1] == SERIAL
    assert env["AWS_ACCESS_KEY_ID"] == access
    assert env["AWS_SECRET_ACCESS_KEY"] == secret
    assert env["AWS_DEFAULT_REGION"] == "sa-east-1"
    assert env["AWS_DEFAULT_OUTPUT"] == "json"
    assert "AWS_PROFILE" not in env


def test_child_env_drops_local_profile_and_credential_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overrides = (
        "AWS_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    )
    for key in overrides:
        monkeypatch.setenv(key, "herdado")
    monkeypatch.setenv("APP_DATA_KEEP", "mantido")

    env = runner._child_env(_block())

    access, secret, _ = _credentials()
    assert env["AWS_ACCESS_KEY_ID"] == access
    assert env["AWS_SECRET_ACCESS_KEY"] == secret
    assert env["APP_DATA_KEEP"] == "mantido"
    for key in overrides:
        assert key not in env

    with_token = runner._child_env(_block(with_token=True))
    assert "AWS_SESSION_TOKEN" in with_token


def test_sample_heads_passes_prefixes_and_sample_size(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> FakeCompleted:
        captured["cmd"] = cmd
        return FakeCompleted('{"ok":true,"samples":[],"failures":[]}\n')

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.sample_heads(
        _block(with_token=True), DST, SERIAL, ["Images/", "Database/"], sample_size=3
    )

    assert result["ok"] is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--sample-size") + 1] == "3"
    assert cmd.count("--prefix") == 2
    assert "Images/" in cmd and "Database/" in cmd
    assert cmd[cmd.index("--root") + 1] == f"{SERIAL}/"


def test_validate_infers_label_from_generic_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> FakeCompleted:
        captured["cmd"] = cmd
        return FakeCompleted('{"ok":true,"region":"sa-east-1","arn":"arn:test"}\n')

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.validate(_block(), "s3://dst-bkt/entrega/cliente-a", "")

    assert result["ok"] is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--bucket") + 1] == "dst-bkt"
    assert cmd[cmd.index("--root") + 1] == "entrega/cliente-a/"
    assert cmd[cmd.index("--label") + 1] == "cliente-a"


def test_start_download_spawns_without_credentials_in_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    process = FakeProcess(
        [
            "Alvos: 2",
            "DOWNLOAD: baixados=2 pulados=0 falhas=0 (0.00 GB) -> out",
            "Trilha: logs/done-run.jsonl",
        ]
    )
    access, secret, _ = _credentials()

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProcess:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return process

    monkeypatch.setattr(runner, "_exe_path", lambda: "client.exe")
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    run_id = runner.start_download(
        _block(),
        DST,
        SERIAL,
        "sa-east-1",
        ["Images/"],
        str(tmp_path / "out"),
        appdata_dir=tmp_path,
    )

    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert isinstance(env, dict)
    assert all(access not in arg and secret not in arg for arg in cmd)
    assert "--skip-head" in cmd
    assert cmd[cmd.index("--max-list") + 1] == "700000"
    assert cmd[cmd.index("--label") + 1] == SERIAL
    assert cmd[cmd.index("--root") + 1] == f"{SERIAL}/"
    assert run_id in cmd
    assert env["AWS_ACCESS_KEY_ID"] == access
    assert "AWS_PROFILE" not in env

    for _ in range(100):
        if runner.progress(run_id)["exit_code"] is not None:
            break
        time.sleep(0.01)
    progress = runner.progress(run_id)
    assert progress["downloaded"] == 2
    assert progress["skipped"] == 0
    assert progress["failed"] == 0
    assert progress["exit_code"] == 0
    assert progress["audit_run_id"] == "done-run"


def test_admin_run_rejects_download_and_child(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    for command in ("download", "__child"):
        with pytest.raises(ValueError):
            runner.admin_run(command, [])

    assert calls == []


def test_admin_run_inherits_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    process = FakeProcess(["OK"])
    monkeypatch.setattr(runner, "_exe_path", lambda: "client.exe")

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProcess:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    run_id = runner.admin_run("verify", ["--serial", SERIAL])

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd == ["client.exe", "verify", "--serial", SERIAL]
    assert captured["env"] is None
    assert isinstance(run_id, str) and run_id


def test_cancel_terminates_running_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = FakeProcess(["iniciado"])
    monkeypatch.setattr(runner, "_exe_path", lambda: "client.exe")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: process)

    run_id = runner.start_download(
        _block(), DST, SERIAL, "sa-east-1", [], str(tmp_path / "out"), appdata_dir=tmp_path
    )

    assert runner.cancel(run_id) is True
    assert process.terminated is True

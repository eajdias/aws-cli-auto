"""HTTP-level tests for the local UI server; no AWS calls."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from s3_glacier_restore.ui import auth, server

SERIAL = "1234-5678-90"
DST = "s3://bucket-test/1234-5678-90/"


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def validate(self, block: str, dst: str, label: str) -> dict[str, object]:
        self.calls.append(("validate", (block, dst, label)))
        if "AWS_DEFAULT_OUTPUT" not in block:
            return {"ok": False, "errors": ["AWS_DEFAULT_OUTPUT ausente"]}
        return {"ok": True, "region": "sa-east-1", "arn": "arn:test"}

    def list_prefixes(self, block: str, dst: str, label: str) -> dict[str, object]:
        self.calls.append(("list_prefixes", (block, dst, label)))
        return {"prefixes": [{"prefix": "Images/", "objects": 2, "bytes": 5}]}

    def sample_heads(
        self,
        block: str,
        dst: str,
        label: str,
        prefixes: list[str],
        sample_size: int,
    ) -> dict[str, object]:
        self.calls.append(("sample_heads", (block, dst, label, prefixes, sample_size)))
        return {"ok": False, "samples": [], "failures": [{"prefix": "Images/", "key": "k"}]}

    def start_download(
        self,
        block: str,
        dst: str,
        label: str,
        region: str,
        prefixes: list[str],
        dest: str,
        max_list: int = 700000,
        appdata_dir: Path | None = None,
    ) -> str:
        self.calls.append(("start_download", (block, dst, label, region, prefixes, dest)))
        return "run-1"

    def admin_run(self, command: str, args: list[str]) -> str:
        self.calls.append(("admin_run", (command, tuple(args))))
        if command in {"download", "__child"}:
            raise ValueError("comando nao permitido")
        return "admin-1"

    def progress(self, run_id: str) -> dict[str, object]:
        self.calls.append(("progress", (run_id,)))
        return {
            "status": "done",
            "lines": ["DOWNLOAD: baixados=2 pulados=0 falhas=0"],
            "downloaded": 2,
            "skipped": 0,
            "failed": 0,
            "exit_code": 0,
        }

    def cancel(self, run_id: str) -> bool:
        self.calls.append(("cancel", (run_id,)))
        return True


@contextmanager
def _running(fake: FakeRunner, appdata: Path) -> Iterator[tuple[int, FakeRunner]]:
    httpd = server.build_server(appdata_dir=appdata, port=0, runner=fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], fake
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(
    port: int,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, object], str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    if host is None:
        conn.request(method, path, body=payload, headers=headers)
    else:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders(payload)
    response = conn.getresponse()
    raw = response.read().decode("utf-8")
    parsed = json.loads(raw) if raw else {}
    conn.close()
    return response.status, parsed, raw


def _block() -> str:
    return "\n".join(
        [
            "AWS_ACCESS_KEY_ID: " + "AKIA" + "0" * 16,
            "AWS_SECRET_ACCESS_KEY: " + "0" * 40,
            "AWS_DEFAULT_REGION: sa-east-1",
            "AWS_DEFAULT_OUTPUT: json",
        ]
    )


def test_health_returns_json_with_no_store_headers(tmp_path: Path) -> None:
    with _running(FakeRunner(), tmp_path) as (port, _):
        status, payload, _ = _request(port, "GET", "/api/health")

    assert status == 200
    assert payload == {"ok": True}


def test_bad_host_is_rejected_before_body_processing(tmp_path: Path) -> None:
    runner = FakeRunner()
    with _running(runner, tmp_path) as (port, _):
        status, payload, _ = _request(
            port, "POST", "/api/health", {"block": _block()}, host="evil.example"
        )

    assert status == 403
    assert payload["ok"] is False
    assert runner.calls == []


def test_admin_unlock_uses_hash_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password = "pw-" + "9" * 12
    salt = "ab" * 16
    monkeypatch.setenv("S3_RESTORE_ADMIN_SALT", salt)
    monkeypatch.setenv("S3_RESTORE_ADMIN_PASSWORD_SHA256", auth.password_digest(password, salt))

    with _running(FakeRunner(), tmp_path) as (port, _):
        bad_status, bad, _ = _request(port, "POST", "/api/admin/unlock", {"password": "wrong"})
        good_status, good, _ = _request(port, "POST", "/api/admin/unlock", {"password": password})

    assert bad_status == 200 and bad == {"ok": False}
    assert good_status == 200 and good == {"ok": True}


def test_admin_password_can_be_changed_and_unlocks_with_new_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("S3_RESTORE_ADMIN_SALT", raising=False)
    monkeypatch.delenv("S3_RESTORE_ADMIN_PASSWORD_SHA256", raising=False)
    old = "old-" + "1" * 10
    new = "new-" + "2" * 10
    auth.set_admin_password(old, tmp_path)

    with _running(FakeRunner(), tmp_path) as (port, _):
        change_status, change, _ = _request(
            port, "POST", "/api/admin/password", {"password": new, "confirm": new}
        )
        new_status, new_payload, _ = _request(port, "POST", "/api/admin/unlock", {"password": new})
        old_status, old_payload, _ = _request(port, "POST", "/api/admin/unlock", {"password": old})

    assert change_status == 200 and change == {"ok": True}
    assert new_status == 200 and new_payload == {"ok": True}
    assert old_status == 200 and old_payload == {"ok": False}


def test_admin_password_change_rejects_mismatch(tmp_path: Path) -> None:
    with _running(FakeRunner(), tmp_path) as (port, _):
        status, payload, _ = _request(
            port, "POST", "/api/admin/password", {"password": "new", "confirm": "other"}
        )

    assert status == 400
    assert payload["ok"] is False
    assert not (tmp_path / "admin-auth.json").exists()


def test_admin_password_change_rejects_empty_password(tmp_path: Path) -> None:
    with _running(FakeRunner(), tmp_path) as (port, _):
        status, payload, _ = _request(
            port, "POST", "/api/admin/password", {"password": "  ", "confirm": "  "}
        )

    assert status == 400
    assert payload["ok"] is False


def test_admin_run_rejects_download_without_spawning(tmp_path: Path) -> None:
    runner = FakeRunner()
    with _running(runner, tmp_path) as (port, _):
        status, payload, _ = _request(
            port, "POST", "/api/admin/run", {"command": "download", "args": []}
        )

    assert status == 400
    assert payload["ok"] is False
    assert runner.calls == [("admin_run", ("download", ()))]


def test_invalid_block_error_has_no_secret(tmp_path: Path) -> None:
    secret = "0" * 40
    with _running(FakeRunner(), tmp_path) as (port, _):
        status, payload, raw = _request(
            port,
            "POST",
            "/api/validate-credentials",
            {"block": "AWS_ACCESS_KEY_ID: x\n", "dst": "b", "label": SERIAL},
        )

    assert status == 400
    assert payload["ok"] is False
    assert secret not in raw


def test_download_rejects_insufficient_space_before_spawn(tmp_path: Path) -> None:
    runner = FakeRunner()
    with _running(runner, tmp_path) as (port, _):
        status, payload, _ = _request(
            port,
            "POST",
            "/api/download",
            {
                "block": _block(),
                "dst": "bucket-test",
                "label": SERIAL,
                "region": "sa-east-1",
                "prefixes": ["Images/"],
                "dest": str(tmp_path / "out"),
                "required_bytes": 10**15,
            },
        )

    assert status == 409
    assert "espaco" in str(payload).lower()
    assert runner.calls == []


def test_download_starts_child_and_progress_is_available(tmp_path: Path) -> None:
    runner = FakeRunner()
    with _running(runner, tmp_path) as (port, _):
        status, payload, raw = _request(
            port,
            "POST",
            "/api/download",
            {
                "block": _block(),
                "dst": DST,
                "label": SERIAL,
                "region": "sa-east-1",
                "prefixes": ["Images/"],
                "dest": str(tmp_path / "out"),
                "required_bytes": 1,
            },
        )
        progress_status, progress, _ = _request(port, "GET", "/api/progress?run_id=run-1")

    assert status == 200
    assert payload == {"ok": True, "run_id": "run-1"}
    assert "0" * 40 not in raw
    assert runner.calls[0][0] == "start_download"
    assert progress_status == 200
    assert progress["downloaded"] == 2
    assert progress["exit_code"] == 0

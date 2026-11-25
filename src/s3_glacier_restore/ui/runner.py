"""Spawn child processes; credentials live only in the child environment."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

from ..config import make_run_id
from ..credentials import CredentialBlockError, parse_env_block
from ..locations import infer_label, parse_s3_location

_RUNS: dict[str, dict[str, object]] = {}
_LOCK = threading.RLock()
_ADMIN_COMMANDS = frozenset(
    {"perms", "plan", "restore", "status", "copy", "verify", "parity", "auto", "batch"}
)
_PROGRESS_RE = re.compile(
    r"DOWNLOAD:\s+baixados[=: ]+(\d+)\s+pulados[=: ]+(\d+)\s+falhas[=: ]+(\d+)"
)
_TRAIL_RE = re.compile(r"Trilha:\s+logs[\\/]([^\s]+\.jsonl)")
_CHILD_CREDENTIAL_OVERRIDES = (
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


def _exe_path() -> str:
    return sys.executable


def _default_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "s3-glacier-restore"


def _child_env(block: str, region: str = "") -> dict[str, str]:
    creds = parse_env_block(block)
    env = dict(os.environ)
    for key in _CHILD_CREDENTIAL_OVERRIDES:
        env.pop(key, None)
    env.update(creds.as_child_env())
    if region:
        env["AWS_DEFAULT_REGION"] = region
    del creds
    return env


def _json_child(block: str, args: list[str]) -> dict[str, object]:
    try:
        env = _child_env(block)
    except CredentialBlockError as exc:
        return {"ok": False, "errors": [str(exc)]}
    cmd = [_exe_path(), "__child", *args]
    try:
        completed = subprocess.run(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
        )
    except OSError as exc:
        return {"ok": False, "errors": [type(exc).__name__]}
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {"ok": False, "errors": ["saida vazia"]}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "errors": ["saida invalida"]}
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["saida invalida"]}
    return payload


def validate(block: str, dst: str, label: str = "") -> dict[str, object]:
    bucket, prefix, run_label = _resolve_dst(dst, label)
    return _json_child(
        block, ["validate", "--bucket", bucket, "--root", prefix, "--label", run_label]
    )


def list_prefixes(block: str, dst: str, label: str = "") -> dict[str, object]:
    bucket, prefix, run_label = _resolve_dst(dst, label)
    return _json_child(
        block,
        [
            "list-prefixes",
            "--bucket",
            bucket,
            "--root",
            prefix,
            "--label",
            run_label,
            "--max-list",
            "700000",
        ],
    )


def sample_heads(
    block: str,
    dst: str,
    label: str,
    prefixes: list[str],
    sample_size: int = 5,
) -> dict[str, object]:
    bucket, prefix, run_label = _resolve_dst(dst, label)
    args = ["sample-heads", "--bucket", bucket, "--root", prefix, "--label", run_label]
    for item in prefixes:
        args.extend(["--prefix", item])
    args.extend(["--sample-size", str(sample_size)])
    return _json_child(block, args)


def _new_state(process: subprocess.Popen[str]) -> dict[str, object]:
    return {
        "status": "running",
        "lines": [],
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "exit_code": None,
        "audit_run_id": "",
        "process": process,
    }


def _read_process(run_id: str, process: subprocess.Popen[str]) -> None:
    stream = process.stdout
    if stream is not None:
        for raw in stream:
            line = raw.rstrip("\r\n")
            with _LOCK:
                state = _RUNS[run_id]
                lines = cast(list[str], state["lines"])
                lines.append(line)
                if len(lines) > 200:
                    del lines[:-200]
                match = _PROGRESS_RE.search(line)
                if match is not None:
                    state["downloaded"] = int(match.group(1))
                    state["skipped"] = int(match.group(2))
                    state["failed"] = int(match.group(3))
                trail = _TRAIL_RE.search(line)
                if trail is not None and not state["audit_run_id"]:
                    state["audit_run_id"] = trail.group(1).removesuffix(".jsonl")
    code = process.wait()
    with _LOCK:
        state = _RUNS[run_id]
        state["exit_code"] = code
        state["status"] = "done" if code == 0 else "failed"


def _flag(cmd: list[str], *names: str) -> str:
    for name in names:
        if name in cmd:
            idx = cmd.index(name)
            if idx + 1 < len(cmd):
                return cmd[idx + 1]
    return ""


def _admin_label(cmd: list[str]) -> str:
    """Label for admin run ids: explicit flag wins, else URI inference."""
    explicit = _flag(cmd, "--serial", "--label")
    src_raw, dst_raw = _flag(cmd, "--src"), _flag(cmd, "--dst")
    try:
        if src_raw or dst_raw:
            src = parse_s3_location(src_raw) if src_raw else None
            dst = parse_s3_location(dst_raw) if dst_raw else None
            return infer_label(
                explicit,
                *(p.prefix for p in (src, dst) if p is not None),
            )
        if explicit:
            return infer_label(explicit)
    except ValueError:
        pass
    return explicit or "admin"


def _spawn(cmd: list[str], env: dict[str, str] | None) -> tuple[str, subprocess.Popen[str]]:
    process = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        shell=False,
    )
    run_id = make_run_id("admin" if cmd[1] in _ADMIN_COMMANDS else _admin_label(cmd))
    with _LOCK:
        _RUNS[run_id] = _new_state(process)
    threading.Thread(target=_read_process, args=(run_id, process), daemon=True).start()
    return run_id, process


def _resolve_dst(dst: str, label: str) -> tuple[str, str, str]:
    """Split a ``s3://bucket/prefixo/`` URI and resolve the run label."""
    location = parse_s3_location(dst)
    return location.bucket, location.prefix, infer_label(label, location.prefix)


def start_download(
    block: str,
    dst: str,
    label: str,
    region: str,
    prefixes: list[str],
    dest: str,
    max_list: int = 700000,
    appdata_dir: Path | None = None,
) -> str:
    appdata = appdata_dir or _default_appdata()
    log_dir = appdata / "logs"
    report_dir = appdata / "reports"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    bucket, prefix, run_label = _resolve_dst(dst, label)
    run_id = make_run_id(run_label)
    env = _child_env(block, region)
    cmd = [
        _exe_path(),
        "__child",
        "download",
        "--bucket",
        bucket,
        "--root",
        prefix,
        "--label",
        run_label,
        "--region",
        region,
        "--profile",
        "env",
        "--max-list",
        str(max_list),
        "--log-dir",
        str(log_dir),
        "--report-dir",
        str(report_dir),
        "--dest",
        dest,
        "--run-id",
        run_id,
        "--skip-head",
    ]
    for prefix in prefixes:
        cmd.extend(["--prefix", prefix])
    process = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        shell=False,
    )
    with _LOCK:
        _RUNS[run_id] = _new_state(process)
    threading.Thread(target=_read_process, args=(run_id, process), daemon=True).start()
    del env
    return run_id


def admin_run(command: str, args: list[str]) -> str:
    if command not in _ADMIN_COMMANDS or command in {"download", "__child"}:
        raise ValueError("comando nao permitido")
    cmd = [_exe_path(), command, *[str(arg) for arg in args]]
    run_id, _ = _spawn(cmd, None)
    return run_id


def progress(run_id: str) -> dict[str, object]:
    with _LOCK:
        state = _RUNS.get(run_id)
        if state is None:
            return {
                "status": "unknown",
                "lines": [],
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "exit_code": None,
                "audit_run_id": "",
            }
        lines = cast(list[str], state["lines"])
        return {
            "status": state["status"],
            "lines": list(lines),
            "downloaded": state["downloaded"],
            "skipped": state["skipped"],
            "failed": state["failed"],
            "exit_code": state["exit_code"],
            "audit_run_id": state["audit_run_id"],
        }


def cancel(run_id: str) -> bool:
    with _LOCK:
        state = _RUNS.get(run_id)
        if state is None:
            return False
        process = cast("subprocess.Popen[str]", state["process"])
        state["status"] = "cancelled"
    try:
        process.terminate()
    except OSError:
        return False
    return True

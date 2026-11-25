"""Local HTTP API for the admin/client interfaces. Binds to loopback only."""

from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import parse_qs, urlsplit

from ..credentials import CredentialBlockError
from . import auth, picker
from . import runner as default_runner
from .resources import resource_path


class RunnerLike(Protocol):
    def validate(self, block: str, dst: str, label: str) -> dict[str, object]: ...

    def list_prefixes(self, block: str, dst: str, label: str) -> dict[str, object]: ...

    def sample_heads(
        self,
        block: str,
        dst: str,
        label: str,
        prefixes: list[str],
        sample_size: int,
    ) -> dict[str, object]: ...

    def start_download(
        self,
        block: str,
        dst: str,
        label: str,
        region: str,
        prefixes: list[str],
        dest: str,
        max_list: int,
        appdata_dir: Path | None,
    ) -> str: ...

    def admin_run(self, command: str, args: list[str]) -> str: ...

    def progress(self, run_id: str) -> dict[str, object]: ...

    def cancel(self, run_id: str) -> bool: ...


@dataclass
class UIState:
    appdata: Path
    runner: RunnerLike


class UIServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        ui: UIState,
    ) -> None:
        self.ui = ui
        super().__init__(server_address, handler)


def _default_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "s3-glacier-restore"


def build_server(
    appdata_dir: Path | None = None,
    port: int = 8765,
    runner: RunnerLike | None = None,
) -> UIServer:
    appdata = Path(appdata_dir) if appdata_dir else _default_appdata()
    appdata.mkdir(parents=True, exist_ok=True)
    state = UIState(appdata=appdata, runner=runner or default_runner)
    return UIServer(("127.0.0.1", port), UIRequestHandler, ui=state)


def serve(appdata_dir: Path | None = None, port: int = 8765) -> int:
    """Bind the first free port in 8765-8775, open the browser and block."""
    appdata = Path(appdata_dir) if appdata_dir else _default_appdata()
    appdata.mkdir(parents=True, exist_ok=True)
    for candidate in range(port, 8776):
        try:
            httpd = build_server(appdata_dir=appdata, port=candidate)
        except OSError:
            continue
        (appdata / "ui.port").write_text(str(candidate), encoding="ascii")
        url = f"http://127.0.0.1:{candidate}/"
        webbrowser.open(url)
        print(f"UI: {url}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return 0
    print("ERRO: nenhuma porta livre entre 8765 e 8775.", file=sys.stderr)
    return 2


class UIRequestHandler(BaseHTTPRequestHandler):
    server_version = "s3-download-client/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        """Never log request bodies, headers, or pasted credentials."""

    @property
    def ui(self) -> UIState:
        return cast(UIServer, self.server).ui

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline' 'self'; "
            "script-src 'unsafe-inline' 'self'",
        )

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._common_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "arquivo ausente"})
            return
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self._common_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        parsed = urlsplit(f"//{host}")
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return False
        if parsed.port is None:
            return True
        return parsed.port == cast(UIServer, self.server).server_address[1]

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("json invalido")
        return payload

    def _drain_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)

    def do_GET(self) -> None:
        if not self._host_ok():
            self._drain_body()
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "host invalido"})
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/progress":
            run_id = parse_qs(parsed.query).get("run_id", [""])[0]
            if not run_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "run_id ausente"})
                return
            self._send_json(HTTPStatus.OK, self.ui.runner.progress(run_id))
            return
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if path in static:
            name, content_type = static[path]
            self._send_file(resource_path(name), content_type)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "rota nao encontrada"})

    def do_POST(self) -> None:
        if not self._host_ok():
            self._drain_body()
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "host invalido"})
            return
        try:
            body = self._read_json()
        except (ValueError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "json invalido"})
            return
        path = urlsplit(self.path).path
        try:
            status, payload = self._dispatch(path, body)
        except CredentialBlockError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": [str(exc)]})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "erro interno"}
            )
            return
        self._send_json(status, payload)

    def _dispatch(self, path: str, body: dict[str, object]) -> tuple[HTTPStatus, dict[str, object]]:
        api = self.ui.runner
        if path == "/api/admin/unlock":
            ok = auth.verify_admin_password(str(body.get("password", "")), self.ui.appdata)
            return HTTPStatus.OK, {"ok": ok}
        if path == "/api/admin/password":
            password = str(body.get("password", ""))
            confirm = str(body.get("confirm", ""))
            if password != confirm:
                return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "senhas nao conferem"}
            auth.set_admin_password(password, self.ui.appdata)
            return HTTPStatus.OK, {"ok": True}
        if path == "/api/admin/run":
            command = str(body.get("command", ""))
            args = [str(arg) for arg in cast(list[object], body.get("args", []))]
            return HTTPStatus.OK, {"ok": True, "run_id": api.admin_run(command, args)}
        if path == "/api/validate-credentials":
            result = api.validate(
                str(body.get("block", "")), str(body.get("dst", "")), str(body.get("label", ""))
            )
            if result.get("ok") is False:
                return HTTPStatus.BAD_REQUEST, result
            return HTTPStatus.OK, result
        if path == "/api/list-prefixes":
            result = api.list_prefixes(
                str(body.get("block", "")), str(body.get("dst", "")), str(body.get("label", ""))
            )
            if "errors" in result:
                return HTTPStatus.BAD_REQUEST, result
            return HTTPStatus.OK, result
        if path == "/api/sample-heads":
            result = api.sample_heads(
                str(body.get("block", "")),
                str(body.get("dst", "")),
                str(body.get("label", "")),
                [str(p) for p in cast(list[object], body.get("prefixes", []))],
                int(body.get("sample_size", 5)),
            )
            if "errors" in result:
                return HTTPStatus.BAD_REQUEST, result
            return HTTPStatus.OK, result
        if path == "/api/check-destination":
            return HTTPStatus.OK, self._disk_info(
                str(body.get("path", "")), int(body.get("required_bytes", 0))
            )
        if path == "/api/pick-folder":
            return HTTPStatus.OK, {"path": picker.pick_folder()}
        if path == "/api/download":
            dest = str(body.get("dest", ""))
            required = int(body.get("required_bytes", 0))
            disk = self._disk_info(dest, required)
            if not disk["ok"]:
                return HTTPStatus.CONFLICT, {"ok": False, "error": "espaco insuficiente no destino"}
            run_id = api.start_download(
                str(body.get("block", "")),
                str(body.get("dst", "")),
                str(body.get("label", "")),
                str(body.get("region", "")),
                [str(p) for p in cast(list[object], body.get("prefixes", []))],
                dest,
                int(body.get("max_list", 700000)),
                self.ui.appdata,
            )
            return HTTPStatus.OK, {"ok": True, "run_id": run_id}
        if path == "/api/cancel":
            return HTTPStatus.OK, {"ok": api.cancel(str(body.get("run_id", "")))}
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "rota nao encontrada"}

    def _disk_info(self, dest: str, required_bytes: int) -> dict[str, object]:
        target = Path(dest)
        probe = target if target.exists() else target.parent
        if not probe.exists():
            raise ValueError("destino invalido")
        free = shutil.disk_usage(str(probe)).free
        return {
            "exists": target.exists(),
            "free_bytes": free,
            "ok": free >= required_bytes * 1.10,
        }

"""One VTK/Trame process per browser visit.

Each full page load (including refresh) gets a fresh worker so the UI looks
like the first visit. Different browsers — including users on other machines —
are routed to different workers, so camera, channels, and tools stay private.

Workers bind to 127.0.0.1 only. The launcher is the single public HTTP/WS port.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiohttp import web

COOKIE = "bioset_sid"
WORKER_IDLE_TIMEOUT_S = 60
WORKER_START_TIMEOUT_S = 90
DEFAULT_MAX_SESSIONS = 8
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-encoding",
    "content-length",
}


def _log(msg: str) -> None:
    print(msg, file=sys.__stderr__, flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _is_websocket(request: web.Request) -> bool:
    return request.headers.get("Upgrade", "").lower() == "websocket"


def _is_document(request: web.Request) -> bool:
    if request.method != "GET" or _is_websocket(request):
        return False
    return request.path in ("/", "/index.html")


def _is_navigation(request: web.Request) -> bool:
    """True for a real tab open / refresh, not a background HTML refetch."""
    if not _is_document(request):
        return False
    mode = request.headers.get("Sec-Fetch-Mode", "")
    dest = request.headers.get("Sec-Fetch-Dest", "")
    if mode or dest:
        return mode == "navigate" and dest == "document"
    # urllib / old clients: treat document GETs as navigation.
    return True


def _filter_request_headers(headers) -> dict[str, str]:
    out = {}
    for key, value in headers.items():
        if key.lower() not in HOP_BY_HOP:
            out[key] = value
    return out


def _filter_response_headers(headers) -> dict[str, str]:
    out = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP or lower == "set-cookie":
            continue
        out[key] = value
    return out


@dataclass
class WorkerSession:
    sid: str
    port: int
    proc: subprocess.Popen
    log_path: Path
    log_file: object = None
    created_at: float = field(default_factory=time.time)
    html_ready: bool = False

    def alive(self) -> bool:
        return self.proc.poll() is None

    def read_log(self, tail: int = 4000) -> str:
        try:
            data = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return data[-tail:]

    def terminate(self) -> None:
        if not self.alive():
            return
        try:
            self.proc.kill()
        except OSError:
            pass


class SessionHub:
    def __init__(self, max_sessions: int, worker_timeout: int):
        self.max_sessions = max_sessions
        self.worker_timeout = worker_timeout
        self.sessions: dict[str, WorkerSession] = {}
        self._lock = asyncio.Lock()

    def _reap(self) -> None:
        dead = [sid for sid, sess in self.sessions.items() if not sess.alive()]
        for sid in dead:
            self.sessions.pop(sid, None)

    def _worker_cmd(self, port: int) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "bioset.app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--server",
            "--timeout",
            str(self.worker_timeout),
            "--logs",
        ]
        if "--profile" in sys.argv:
            cmd.append("--profile")
            try:
                idx = sys.argv.index("--profile")
                if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
                    cmd.append(sys.argv[idx + 1])
            except ValueError:
                pass
        return cmd

    def _spawn_process(self, port: int, log_file) -> subprocess.Popen:
        env = os.environ.copy()
        env["BIOSET_WORKER"] = "1"
        kwargs = {
            "env": env,
            "cwd": os.getcwd(),
            "stdout": log_file,
            "stderr": log_file,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(self._worker_cmd(port), **kwargs)

    async def spawn(self) -> WorkerSession:
        async with self._lock:
            self._reap()
            if len(self.sessions) >= self.max_sessions:
                raise web.HTTPServiceUnavailable(
                    text="All BioSET sessions are in use. Close another tab and retry.",
                    content_type="text/plain",
                )
            port = _free_port()
            sid = uuid.uuid4().hex
            log_path = Path(tempfile.gettempdir()) / f"bioset-session-{sid}.log"
            log_file = log_path.open("w", encoding="utf-8", buffering=1)
            try:
                proc = self._spawn_process(port, log_file)
            except Exception:
                log_file.close()
                raise
            session = WorkerSession(
                sid=sid, port=port, proc=proc, log_path=log_path, log_file=log_file
            )
            self.sessions[sid] = session

        deadline = time.time() + WORKER_START_TIMEOUT_S
        while time.time() < deadline:
            if not session.alive():
                async with self._lock:
                    self.sessions.pop(sid, None)
                detail = session.read_log()
                _log(f"[bioset] worker {sid[:8]} exited early:\n{detail}")
                raise web.HTTPBadGateway(
                    text="BioSET worker exited before it became ready.",
                    content_type="text/plain",
                )
            if _port_open(port):
                _log(f"[bioset] session {sid[:8]} on 127.0.0.1:{port}")
                return session
            await asyncio.sleep(0.15)

        async with self._lock:
            self.sessions.pop(sid, None)
        session.terminate()
        detail = session.read_log()
        _log(f"[bioset] worker {sid[:8]} start timeout:\n{detail}")
        raise web.HTTPGatewayTimeout(
            text="BioSET worker did not start in time.",
            content_type="text/plain",
        )

    def get(self, sid: str | None) -> WorkerSession | None:
        if not sid:
            return None
        session = self.sessions.get(sid)
        if session is None or not session.alive():
            if sid in self.sessions:
                self.sessions.pop(sid, None)
            return None
        return session

    def drop(self, sid: str | None) -> None:
        session = self.sessions.pop(sid, None) if sid else None
        if session is not None:
            session.terminate()

    def shutdown(self) -> None:
        for sid in list(self.sessions):
            self.drop(sid)


def _attach_cookie(response: web.StreamResponse, sid: str) -> None:
    response.set_cookie(COOKIE, sid, path="/", samesite="Lax")


async def _proxy_websocket(request: web.Request, session: WorkerSession) -> web.WebSocketResponse:
    client_ws = web.WebSocketResponse(max_msg_size=50_000_000, heartbeat=30)
    await client_ws.prepare(request)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.ws_connect(
            f"ws://127.0.0.1:{session.port}/ws",
            max_msg_size=50_000_000,
            heartbeat=30,
        ) as upstream:

            async def client_to_worker():
                async for msg in client_ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await upstream.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await upstream.send_bytes(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break

            async def worker_to_client():
                async for msg in upstream:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await client_ws.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await client_ws.send_bytes(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break

            try:
                await asyncio.gather(client_to_worker(), worker_to_client())
            except (aiohttp.ClientError, ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                if not client_ws.closed:
                    await client_ws.close()
    return client_ws


async def _proxy_http(
    request: web.Request,
    session: WorkerSession,
    http: aiohttp.ClientSession,
) -> web.Response:
    url = f"http://127.0.0.1:{session.port}{request.path_qs}"
    body = await request.read()
    async with http.request(
        request.method,
        url,
        headers=_filter_request_headers(request.headers),
        data=body if body else None,
        allow_redirects=False,
    ) as upstream:
        payload = await upstream.read()
        response = web.Response(
            body=payload,
            status=upstream.status,
            headers=_filter_response_headers(upstream.headers),
        )
        _attach_cookie(response, session.sid)
        return response


def create_app(hub: SessionHub) -> web.Application:
    app = web.Application(client_max_size=50_000_000)

    async def handle(request: web.Request):
        cookie_sid = request.cookies.get(COOKIE)
        existing = hub.get(cookie_sid)

        if request.path.rstrip("/") == "/ws" and not _is_websocket(request):
            return web.Response(status=426, text="Upgrade Required")

        if _is_navigation(request):
            if existing is not None and not existing.html_ready:
                target = existing
            else:
                if existing is not None:
                    hub.drop(cookie_sid)
                target = await hub.spawn()
        elif existing is not None:
            target = existing
        elif _is_document(request):
            target = await hub.spawn()
        else:
            raise web.HTTPNotFound(text="No BioSET session. Reload the page.")

        if _is_websocket(request):
            return await _proxy_websocket(request, target)
        response = await _proxy_http(request, target, request.app["http"])
        if _is_document(request) and getattr(response, "status", 0) == 200:
            target.html_ready = True
        return response

    async def on_startup(_app):
        _app["http"] = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
        )

    async def on_cleanup(_app):
        http = _app.get("http")
        if http is not None and not http.closed:
            await http.close()
        hub.shutdown()

    app.router.add_route("*", "/", handle)
    app.router.add_route("*", "/{path:.*}", handle)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def _parse_launcher_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="BioSET multi-user launcher (one VTK process per browser visit).",
        add_help=False,
    )
    parser.add_argument("--host", default=os.environ.get("TRAME_DEFAULT_HOST", "localhost"))
    parser.add_argument("-p", "--port", type=int, default=8080)
    parser.add_argument("--server", action="store_true", default=False)
    parser.add_argument("--logs", action="store_true", default=False)
    parser.add_argument("--single", action="store_true", default=False)
    parser.add_argument(
        "--timeout",
        type=int,
        default=WORKER_IDLE_TIMEOUT_S,
        help="Seconds after disconnect before a worker exits (default: 5).",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=int(os.environ.get("BIOSET_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)),
        help=f"Max simultaneous browser sessions (default: {DEFAULT_MAX_SESSIONS}).",
    )
    args, _ = parser.parse_known_args(argv)
    return args


def run_launcher(argv: list[str] | None = None) -> None:
    args = _parse_launcher_args(argv)
    hub = SessionHub(max_sessions=args.max_sessions, worker_timeout=max(1, args.timeout))
    app = create_app(hub)
    display_host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{args.port}/"
    _log(f"[bioset] Multi-user launcher at {url}")
    _log("[bioset] Each refresh starts a new session; each browser is isolated.")
    if not args.server:
        webbrowser.open(url)
    web.run_app(app, host=args.host, port=args.port, print=None)

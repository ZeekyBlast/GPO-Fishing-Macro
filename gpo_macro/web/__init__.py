"""The browser shell: the engine's frames over Server-Sent Events, its
commands over POST.

`python main.py --serve` starts the engine and this server on 127.0.0.1 and
opens the page. The page is the same shell the C# window is - dashboard,
calibration, settings - drawn from the same hello frame and the same ticks,
so whatever the engine can do, the browser can do, on any OS. There is no
overlay: calibration picks are made on a capture of the game's own client
area, which makes every coordinate window-relative by construction.

Two things keep it to this machine. The server binds loopback only, and
every command and the event stream need a per-launch token, which is baked
into the served page: a web page you happen to have open cannot read that
page across origins, so it cannot learn the token, so it cannot drive the
mouse through localhost. The Origin header is checked as well.
"""

from __future__ import annotations

import hmac
import json
import logging
import queue
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import APP_NAME, __version__
from ..config import ConfigStore
from ..rpc import Engine

log = logging.getLogger("gpo.web")

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
PING_SECONDS = 5.0
CLIENT_QUEUE = 512          # frames; a page that cannot keep up loses ticks, not the session


class Broadcaster:
    """The engine's sink in serve mode: every frame goes to every open page."""

    def __init__(self) -> None:
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def __call__(self, message: dict) -> None:
        line = json.dumps(message, default=str)
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(line)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        client: queue.Queue = queue.Queue(maxsize=CLIENT_QUEUE)
        with self._lock:
            self._clients.add(client)
        return client

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(client)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], engine: Engine,
                 broadcaster: Broadcaster, token: str):
        super().__init__(address, Handler)
        self.engine = engine
        self.broadcaster = broadcaster
        self.token = token
        self.stopping = threading.Event()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode()
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.stopping.set()
        self.shutdown()


class Handler(BaseHTTPRequestHandler):
    server: WebServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    # ------------------------------------------------------------ guards

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        offered = self.headers.get("X-Token") or (query.get("token") or [""])[0]
        return bool(offered) and hmac.compare_digest(offered, self.server.token)

    def _origin_ok(self) -> bool:
        """A request with no Origin is the browser talking to its own page.
        One with an Origin has to be this server's."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parts = urlsplit(origin)
        return (parts.hostname in ("127.0.0.1", "localhost", "::1")
                and parts.port == self.server.server_address[1])

    # ----------------------------------------------------------- replies

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, status: int, text: str) -> None:
        self._json(status, {"ok": False, "error": text})

    def _file(self, name: str) -> None:
        path = (STATIC / name).resolve()
        if STATIC not in path.parents or not path.is_file() or path.suffix not in CONTENT_TYPES:
            self._refuse(404, "not found")
            return
        data = path.read_bytes()
        if name == "index.html":
            data = data.replace(b"__TOKEN__", self.server.token.encode("ascii"))
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[path.suffix])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- GET

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/events":
            if not self._origin_ok():
                self._refuse(403, "wrong origin")
            elif not self._authorised(parse_qs(parts.query)):
                self._refuse(401, "token required")
            else:
                self._stream()
            return
        name = "index.html" if parts.path in ("", "/") else parts.path.lstrip("/")
        self._file(name)

    def _stream(self) -> None:
        """hello, the log so far, then every frame as it happens."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        engine, server = self.server.engine, self.server
        client = server.broadcaster.subscribe()
        try:
            self._event(json.dumps(engine.hello_frame(), default=str))
            for frame in engine.recent_event_frames():
                self._event(json.dumps(frame, default=str))
            while not server.stopping.is_set():
                try:
                    self._event(client.get(timeout=PING_SECONDS))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            server.broadcaster.unsubscribe(client)

    def _event(self, data: str) -> None:
        self.wfile.write(b"data: " + data.encode("utf-8") + b"\n\n")
        self.wfile.flush()

    # --------------------------------------------------------------- POST

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/cmd":
            self._refuse(404, "not found")
            return
        if not self._origin_ok():
            self._refuse(403, "wrong origin")
            return
        if not self._authorised(parse_qs(parts.query)):
            self._refuse(401, "token required")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError("not an object")
        except (ValueError, json.JSONDecodeError) as exc:
            self._refuse(400, f"bad JSON: {exc}")
            return
        self._json(200, self.server.engine.dispatch(request))


def start_server(store: ConfigStore, host: str = "127.0.0.1",
                 port: int = 8790) -> tuple[WebServer, Engine, str, str]:
    """Engine plus server, running; returns (server, engine, url, token).
    Port 0 picks a free one, which the self-check uses."""
    token = secrets.token_urlsafe(32)
    broadcaster = Broadcaster()
    engine = Engine(store, sink=broadcaster)
    server = WebServer((host, port), engine, broadcaster, token)
    engine.start()
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    return server, engine, server.url, token


def serve(store: ConfigStore, host: str = "127.0.0.1", port: int = 8790,
          open_browser: bool = True) -> int:
    """`main.py --serve`: run until Ctrl+C or the page asks to quit."""
    server, engine, url, _token = start_server(store, host, port)
    print(f"{APP_NAME} v{__version__} - the window is at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        while engine._running.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.stop()
        engine.shutdown()
    return 0

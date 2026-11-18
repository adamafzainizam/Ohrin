"""Local-only HTTP entry point for the Ohrin passage workbench."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import assets, ffmpeg, paths
from .analysis import analyze_audio
from .instrument_inference import predict_or_unavailable
from .setup_job import Busy, SetupJob


WEB = paths.resource_root() / "web"
MAX_UPLOAD = 50 * 1024 * 1024
FILES = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/state.mjs": "state.mjs", "/eq.mjs": "eq.mjs", "/eq-ui.js": "eq-ui.js", "/timeline.mjs": "timeline.mjs", "/lanes-model.mjs": "lanes-model.mjs", "/peaks.mjs": "peaks.mjs", "/input-rules.mjs": "input-rules.mjs", "/store.mjs": "store.mjs", "/transport.js": "transport.js", "/inspector.js": "inspector.js",
         "/lanes/overview.js": "lanes/overview.js",
         "/lanes/ruler.js": "lanes/ruler.js",
         "/lanes/waveform.js": "lanes/waveform.js",
         "/lanes/energy.js": "lanes/energy.js",
         "/lanes/instruments.js": "lanes/instruments.js",
         "/lanes/playhead.js": "lanes/playhead.js",
         "/tokens.css": "tokens.css", "/theme.mjs": "theme.mjs", "/desktop.mjs": "desktop.mjs",
         "/setup.mjs": "setup.mjs", "/setup-ui.js": "setup-ui.js",
         "/fonts/Geist-Variable.woff2": "fonts/Geist-Variable.woff2",
         "/fonts/GeistMono-Variable.woff2": "fonts/GeistMono-Variable.woff2"}


def analyze_upload(data: bytes, start: float, end: float) -> dict:
    if start < 0 or end <= start:
        raise ValueError("Select a valid passage.")
    if not 0 < len(data) <= MAX_UPLOAD:
        raise ValueError("Choose an audio file smaller than 50 MB.")
    with tempfile.NamedTemporaryFile(prefix="ohrin-", suffix=".audio", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        result = analyze_audio(Path(tmp.name), start, end)
        passage = result["passage"]
        result["instruments"] = predict_or_unavailable(Path(tmp.name), passage["start_s"], passage["end_s"])
        if result["instruments"]["available"]:
            result["limitations"] = [t for t in result["limitations"] if not t.startswith("Instrument identity")]
            result["limitations"].append("Instrument candidates are uncertain model outputs, not confirmed parts.")
        return result


SAFELISTED_TYPES = {"text/plain", "application/x-www-form-urlencoded", "multipart/form-data"}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".woff2": "font/woff2",
}


def content_type_for(path: str) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _allowed_hosts(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self):
        host = self.headers.get("Host")
        return host is not None and host.lower() in self._allowed_hosts()

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin.lower() in {f"http://{h}" for h in self._allowed_hosts()}

    def _content_type_ok(self):
        media = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        return bool(media) and media not in SAFELISTED_TYPES

    def _reject_host(self):
        body = b"Forbidden host."
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            self._reject_host()
            return
        path = urlsplit(self.path).path
        if path == "/api/setup":
            data_dir = paths.user_data_dir()
            todo = assets.missing(data_dir)
            self._json(200, {
                "mode": self.server.mode,
                "data_dir": str(data_dir),
                "ffmpeg": ffmpeg.status(),
                "torch": importlib.util.find_spec("torch") is not None,
                "assets": assets.asset_status(data_dir),
                "downloadable": all(e["url"] for e in todo),
                "download_bytes": sum(e["size"] for e in todo),
                "hosts": sorted({e["host"] for e in todo}),
            })
            return
        if path == "/api/setup/progress":
            self._json(200, self.server.setup_job.snapshot())
            return
        if path not in FILES:
            self.send_error(404)
            return
        data = (WEB / FILES[path]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(FILES[path]))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._host_ok():
            self._reject_host()
            return
        url = urlsplit(self.path)
        handler = self._post_routes().get(url.path)
        if handler is None:
            self.send_error(404)
            return
        if not self._origin_ok() or not self._content_type_ok():
            self._json(403, {"error": "Cross-origin requests are not allowed."})
            return
        handler(url)

    def _post_routes(self):
        routes = {"/api/analyze": self._post_analyze, "/api/setup/download": self._post_setup_download,
                  "/api/setup/cancel": self._post_setup_cancel}
        if self.server.mode == "browser":
            routes["/api/quit"] = self._post_quit
        return routes

    def _read_small_body(self, limit=1024):
        try:
            size = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            size = 0
        if size > limit or size < 0:
            return None
        return self.rfile.read(size) if size else b""

    def _post_setup_download(self, url):
        raw = self._read_small_body()
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or set(body) != {"consent"} or body["consent"] is not True:
            self._json(400, {"error": "Consent is required to download."})
            return
        if not assets.downloadable(paths.user_data_dir()):
            self._json(400, {"error": "The instrument model is not published yet."})
            return
        try:
            self.server.setup_job.start()
        except Busy:
            self._json(409, {"error": "A download is already running."})
            return
        self._json(202, self.server.setup_job.snapshot())

    def _post_setup_cancel(self, url):
        self._read_small_body()
        self.server.setup_job.cancel()
        self._json(200, self.server.setup_job.snapshot())

    def _post_quit(self, url):
        try:
            size = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            size = 0
        size = min(max(size, 0), 1024)
        if size > 0:
            self.rfile.read(size)
        self.server.quit_event.set()
        self._json(200, {"ok": True})

    def _post_analyze(self, url):
        try:
            params = parse_qs(url.query)
            start = float(params["start"][0])
            end = float(params["end"][0])
            if start < 0 or end <= start:
                raise ValueError("Select a valid passage.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_UPLOAD:
                raise ValueError("Choose an audio file smaller than 50 MB.")
            data = self.rfile.read(size)
            if len(data) != size:
                raise ValueError("The upload was incomplete.")
            result = analyze_upload(data, start, end)
            self._json(200, result)
        except (KeyError, IndexError, ValueError, OverflowError) as exc:
            self._json(400, {"error": str(exc) or "Invalid analysis request."})


def reuse_address_for(platform):
    return platform != "win32"


class OhrinServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = reuse_address_for(sys.platform)

    def __init__(self, address, handler, mode="dev", opener=None):
        self.mode = mode
        self.setup_job = SetupJob(paths.user_data_dir, **({"opener": opener} if opener else {}))
        self.quit_event = threading.Event()
        super().__init__(address, handler)

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(port=8765, *, mode="dev", opener=None):
    return OhrinServer(("127.0.0.1", port), Handler, mode=mode, opener=opener)


def bind_preferred(preferred, *, mode):
    """Try the preferred port, then any free one. Returns (server, fell_back)."""
    try:
        return make_server(preferred, mode=mode), False
    except OSError:
        return make_server(0, mode=mode), True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run Ohrin locally")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(port=args.port)
    print(f"Ohrin: http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

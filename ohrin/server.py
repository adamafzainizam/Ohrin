"""Local-only HTTP entry point for the Ohrin passage workbench."""

import json
import mimetypes
import os
from pathlib import Path
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .analysis import analyze_audio
from .instrument_inference import predict_or_unavailable


WEB = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 50 * 1024 * 1024
FILES = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/state.mjs": "state.mjs", "/eq.mjs": "eq.mjs", "/eq-ui.js": "eq-ui.js"}


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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in FILES:
            self.send_error(404)
            return
        data = (WEB / FILES[path]).read_bytes()
        content_type = "text/javascript" if path.endswith((".js", ".mjs")) else mimetypes.guess_type(FILES[path])[0]
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type or 'text/plain'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path != "/api/analyze":
            self.send_error(404)
            return
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


def make_server(host="127.0.0.1", port=8765):
    return ThreadingHTTPServer((host, port), Handler)


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

"""Headless self-test of an Ohrin install. Opens no window, downloads nothing, writes only the optional report."""

import http.client
import importlib.util
import io
import json
import sys
import threading
import wave
from pathlib import Path

import numpy as np

from . import ffmpeg, paths, server

EXPECTED_SETUP_KEYS = {"mode", "data_dir", "ffmpeg", "torch"}


def ok(detail=""):
    return {"status": "ok", "detail": detail}


def fail(detail):
    return {"status": "fail", "detail": detail}


def skipped(detail):
    return {"status": "skipped", "detail": detail}


def tone_wav(seconds=2.0, rate=22050):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * 220 * t) * 12000).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())
    return buffer.getvalue()


def fetch(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def check_files(port):
    bad = []
    for url, name in server.FILES.items():
        try:
            status, ctype, _ = fetch(port, "GET", url)
        except Exception as exc:
            bad.append(f"{url} ({type(exc).__name__})")
            continue
        if status != 200 or ctype != server.content_type_for(name):
            bad.append(f"{url} (status {status}, {ctype})")
    return fail("; ".join(bad)) if bad else ok(f"{len(server.FILES)} files")


def check_setup(port):
    status, _, body = fetch(port, "GET", "/api/setup")
    if status != 200:
        return fail(f"status {status}")
    data = json.loads(body)
    missing = EXPECTED_SETUP_KEYS - set(data)
    return fail(f"missing keys {sorted(missing)}") if missing else ok()


def check_analysis(port):
    if not ffmpeg.status()["found"]:
        return skipped("FFmpeg not found")
    status, _, body = fetch(port, "POST", "/api/analyze?start=0&end=2", tone_wav(),
                            {"Content-Type": "application/octet-stream"})
    if status != 200:
        return fail(f"status {status}: {body[:200].decode('utf-8', 'replace')}")
    return ok() if "windows" in json.loads(body) else fail("no windows in result")


def check_torch():
    required = paths.is_frozen()
    if importlib.util.find_spec("torch") is None:
        return fail("torch is required in a packaged build") if required else skipped("torch not installed")
    try:
        import torch
        from .instruments import make_model
        from .vggish import VGGish
        with torch.no_grad():
            embedding = VGGish().eval()(torch.zeros(1, 1, 96, 64))
            logits = make_model("mlp").eval()(torch.zeros(1, 128))
        if tuple(embedding.shape) != (1, 128) or tuple(logits.shape) != (1, 4):
            return fail(f"unexpected shapes {tuple(embedding.shape)} {tuple(logits.shape)}")
        return ok(f"torch {torch.__version__}")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}")


def probe_webview():
    present = importlib.util.find_spec("webview") is not None
    detail = {"pywebview": present}
    if sys.platform.startswith("linux"):
        try:
            import gi
            gi.require_version("WebKit2", "4.1")
            detail["webkit2"] = True
        except Exception as exc:
            detail["webkit2"] = False
            detail["webkit2_error"] = f"{type(exc).__name__}: {exc}"
    return {"status": "ok" if present else "absent", "detail": detail}


def guarded(check, *args):
    try:
        return check(*args)
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}")


def run(report_path=None):
    checks = {}
    srv = None
    thread = None
    try:
        srv = server.make_server(0, mode="dev")
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        port = srv.server_address[1]
        checks["server"] = ok(f"127.0.0.1:{port}")
        checks["files"] = guarded(check_files, port)
        checks["setup"] = guarded(check_setup, port)
        checks["analysis"] = guarded(check_analysis, port)
    except Exception as exc:
        checks.setdefault("server", fail(f"{type(exc).__name__}: {exc}"))
        for name in ("files", "setup", "analysis"):
            checks.setdefault(name, fail("server did not start"))
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        if thread is not None:
            thread.join(5)
    checks["torch"] = guarded(check_torch)
    checks["webview"] = guarded(probe_webview)
    gating = ("server", "files", "setup", "analysis", "torch")
    passed = all(checks[name]["status"] != "fail" for name in gating)
    report = {"ok": passed, "frozen": paths.is_frozen(), "platform": sys.platform, "checks": checks}
    line = json.dumps(report)
    if sys.stdout is not None:
        print(line, flush=True)
    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(line, encoding="utf-8")
    return 0 if passed else 1

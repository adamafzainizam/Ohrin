import hashlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ohrin import assets
from ohrin.setup_job import SetupJob
from tests.test_server import request, start_server

A = b"alpha-bytes" * 10
B = b"beta-bytes" * 10


def entry(name, data, subdir="vggish", url="https://example.invalid/x"):
    return {"name": name, "subdir": subdir, "url": url, "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "host": "example.invalid"}


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """Serves bytes by URL; optionally blocks the first read until `gate` is set."""

    def __init__(self, bodies, gate=None):
        self.bodies = bodies
        self.gate = gate
        self.started = threading.Event()
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        outer = self

        class R(FakeResponse):
            def read(self, n=-1):
                outer.started.set()
                if outer.gate is not None:
                    outer.gate.wait(5)
                return super().read(n)
        return R(self.bodies[url])


class SetupApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"OHRIN_DATA_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.table = [entry("a.bin", A, url="https://example.invalid/a"), entry("b.bin", B, "models", url="https://example.invalid/b")]
        p = mock.patch.object(assets, "ASSETS", self.table)
        p.start()
        self.addCleanup(p.stop)
        self.opener = Opener({"https://example.invalid/a": A, "https://example.invalid/b": B})

    def serve(self, opener=None):
        from ohrin.server import make_server
        server = make_server(port=0, opener=opener or self.opener)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.setup_job.cancel()
            server.setup_job.join(5)
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(stop)
        self.server = server
        self.port = server.server_address[1]
        return server

    def req(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        status, _, data = request(self.port, method, path, h, body)
        return status, (json.loads(data) if data and data[:1] in b"{[" else data)

    def consent(self):
        return self.req("POST", "/api/setup/download", b'{"consent": true}')

    def wait_state(self, states, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            snap = self.server.setup_job.snapshot()
            if snap["state"] in states:
                return snap
            time.sleep(0.01)
        self.fail(f"state never reached {states}: {self.server.setup_job.snapshot()}")


class SetupApiTests(SetupApiBase):
    def test_setup_download_requires_consent(self):
        self.serve()
        for body in [b"", b"{}", b'{"consent": false}', b'{"consent": "yes"}', b"not json", b'{"consent": true, "x": 1}', b"[true]"]:
            status, _ = self.req("POST", "/api/setup/download", body)
            self.assertEqual(status, 400, body)
        status, _ = self.req("POST", "/api/setup/download", b" " * 2000)
        self.assertEqual(status, 400)
        self.assertEqual(self.opener.calls, [])
        self.assertEqual(self.server.setup_job.snapshot()["state"], "idle")

    def test_setup_download_rejects_safelisted_content_type(self):
        self.serve()
        for path in ["/api/setup/download", "/api/setup/cancel"]:
            for ct in ["text/plain", "application/x-www-form-urlencoded", None]:
                headers = {"Host": f"127.0.0.1:{self.port}"}
                if ct:
                    headers["Content-Type"] = ct
                status, _, _ = request(self.port, "POST", path, headers, b'{"consent": true}')
                self.assertEqual(status, 403, (path, ct))
            status, _, _ = request(self.port, "POST", path, {"Host": f"127.0.0.1:{self.port}", "Origin": "http://evil.example",
                                                             "Content-Type": "application/json"}, b'{"consent": true}')
            self.assertEqual(status, 403)
            status, _, _ = request(self.port, "POST", path, {"Host": "evil.example", "Content-Type": "application/json"}, b'{"consent": true}')
            self.assertEqual(status, 403)
        self.assertEqual(self.opener.calls, [])

    def test_setup_download_conflict_409(self):
        gate = threading.Event()
        self.serve(Opener({"https://example.invalid/a": A, "https://example.invalid/b": B}, gate))
        self.assertEqual(self.consent()[0], 202)
        self.assertEqual(self.consent()[0], 409)
        gate.set()
        self.wait_state({"done"})

    def test_setup_progress_reaches_done_and_status_verified(self):
        self.serve()
        status, snap = self.consent()
        self.assertEqual(status, 202)
        self.assertIn(snap["state"], {"downloading", "done"})
        self.wait_state({"done"})
        status, snap = self.req("GET", "/api/setup/progress")
        self.assertEqual((status, snap["state"], snap["error"]), (200, "done", None))
        _, setup = self.req("GET", "/api/setup")
        self.assertTrue(all(a["verified"] for a in setup["assets"]))
        self.assertEqual(setup["download_bytes"], 0)
        self.assertEqual((Path(self.tmp.name) / "vggish" / "a.bin").read_bytes(), A)
        self.assertEqual((Path(self.tmp.name) / "models" / "b.bin").read_bytes(), B)

    def test_checksum_mismatch_reports_error_and_leaves_no_file(self):
        bad = Opener({"https://example.invalid/a": b"x" * len(A), "https://example.invalid/b": B})
        self.serve(bad)
        self.consent()
        snap = self.wait_state({"error"})
        self.assertIn("Checksum mismatch", snap["error"])
        self.assertEqual(list(Path(self.tmp.name).rglob("a.bin*")), [])

    def test_cancel_removes_partial_and_reports_cancelled(self):
        gate = threading.Event()
        opener = Opener({"https://example.invalid/a": A, "https://example.invalid/b": B}, gate)
        self.serve(opener)
        self.consent()
        self.assertTrue(opener.started.wait(5))
        status, snap = self.req("POST", "/api/setup/cancel", b"{}")
        self.assertEqual(status, 200)
        gate.set()
        self.wait_state({"cancelled"})
        self.assertEqual(list(Path(self.tmp.name).rglob("*.part")), [])
        self.assertEqual(list(Path(self.tmp.name).rglob("a.bin")), [])
        status, snap = self.req("POST", "/api/setup/cancel", b"{}")
        self.assertEqual((status, snap["state"]), (200, "cancelled"))

    def test_done_resets_model_cache(self):
        self.serve()
        with mock.patch("ohrin.instrument_inference.reset_model_cache") as reset:
            self.consent()
            self.wait_state({"done"})
        reset.assert_called_once()

    def test_unpublished_head_returns_400(self):
        self.table.append(entry("head.pt", b"head", "models", url=None))
        self.serve()
        status, body = self.consent()
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "The instrument model is not published yet.")
        self.assertEqual(self.opener.calls, [])
        _, setup = self.req("GET", "/api/setup")
        self.assertFalse(setup["downloadable"])

    def test_get_setup_lists_assets_and_bytes(self):
        self.serve()
        (Path(self.tmp.name) / "vggish").mkdir()
        (Path(self.tmp.name) / "vggish" / "a.bin").write_bytes(A)
        status, setup = self.req("GET", "/api/setup")
        self.assertEqual(status, 200)
        by = {a["name"]: a for a in setup["assets"]}
        self.assertTrue(by["a.bin"]["verified"])
        self.assertFalse(by["b.bin"]["present"])
        self.assertEqual(setup["download_bytes"], len(B))
        self.assertTrue(setup["downloadable"])
        self.assertEqual(setup["hosts"], ["example.invalid"])
        self.assertIn("mode", setup)

    def test_app_shutdown_cancels_running_job(self):
        from ohrin import app
        gate = threading.Event()
        opener = Opener({"https://example.invalid/a": A, "https://example.invalid/b": B}, gate)
        from ohrin import server as srv
        real = srv.make_server
        holder = {}

        def make(port=0, *, mode="dev", opener_=None):
            holder["s"] = real(port, mode=mode, opener=opener)
            return holder["s"]

        def open_browser(url):
            holder["s"].setup_job.start()
            opener.started.wait(5)
            holder["s"].quit_event.set()
            threading.Timer(0.2, gate.set).start()

        with mock.patch.object(srv, "make_server", make):
            code = app.main(["--browser", "--port", "0", "--data-dir", self.tmp.name], open_browser=open_browser)
        self.assertEqual(code, 0)
        gate.set()
        self.assertIn(holder["s"].setup_job.snapshot()["state"], {"cancelled"})
        self.assertEqual(list(Path(self.tmp.name).rglob("*.part")), [])


if __name__ == "__main__":
    unittest.main()

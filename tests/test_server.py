import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ohrin.server import WEB, FILES, analyze_upload, content_type_for, make_server
from tests.test_analysis import write_tone


def start_server(case):
    server = make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(5)

    case.addCleanup(stop)
    return server


def request(port, method, path, headers, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


class HostOriginGuardTests(unittest.TestCase):
    def setUp(self):
        self.port = start_server(self).server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.url = "/api/analyze?start=0&end=4"

    def post(self, headers, body=b"abc"):
        with mock.patch("ohrin.server.analyze_upload", return_value={"ok": True}) as m:
            out = request(self.port, "POST", self.url, headers, body)
        return out, m

    def test_get_good_host_ok_with_nosniff(self):
        status, headers, _ = request(self.port, "GET", "/", {"Host": self.host})
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_get_localhost_host_ok_and_case_insensitive(self):
        self.assertEqual(request(self.port, "GET", "/", {"Host": f"localhost:{self.port}"})[0], 200)
        self.assertEqual(request(self.port, "GET", "/", {"Host": f"LocalHost:{self.port}"})[0], 200)

    def test_get_bad_hosts_rejected(self):
        for host in [f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", "localhost"]:
            status, _, body = request(self.port, "GET", "/", {"Host": host})
            self.assertEqual(status, 403, host)
            self.assertNotIn(b"Ohrin", body)

    def test_get_missing_host_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("GET", "/", skip_host=True, skip_accept_encoding=True)
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 403)

    def test_post_foreign_origin_rejected_with_json(self):
        for origin in ["http://evil.example", "null"]:
            (status, headers, body), m = self.post({"Host": self.host, "Origin": origin})
            self.assertEqual(status, 403, origin)
            self.assertEqual(json.loads(body), {"error": "Cross-origin requests are not allowed."})
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            m.assert_not_called()

    def test_post_matching_origin_passes_guard(self):
        for origin in [f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"]:
            (status, headers, body), m = self.post({"Host": self.host, "Origin": origin})
            self.assertEqual(status, 200, origin)
            self.assertEqual(json.loads(body), {"ok": True})
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            m.assert_called_once()

    def test_post_without_origin_passes_guard(self):
        (status, _, _), m = self.post({"Host": self.host})
        self.assertEqual(status, 200)
        m.assert_called_once()

    def test_post_bad_host_rejected_even_with_matching_origin(self):
        (status, _, _), m = self.post({"Host": "evil.example", "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 403)
        m.assert_not_called()


class ServerBoundaryTests(unittest.TestCase):
    def test_browser_ui_is_available_to_serve(self):
        page = (WEB / "index.html").read_text()
        self.assertIn("Ohrin", page)
        self.assertIn("Open an audio file", page)

    def test_every_files_entry_exists(self):
        for path, name in FILES.items():
            self.assertTrue((WEB / name).is_file(), f"{path} -> {name}")

    def test_content_types(self):
        js = "text/javascript; charset=utf-8"
        self.assertEqual(content_type_for("a.js"), js)
        self.assertEqual(content_type_for("a.mjs"), js)
        self.assertEqual(content_type_for("a.css"), "text/css; charset=utf-8")
        self.assertEqual(content_type_for("a.html"), "text/html; charset=utf-8")
        self.assertEqual(content_type_for("a.json"), "application/json; charset=utf-8")
        self.assertEqual(content_type_for("a.svg"), "image/svg+xml; charset=utf-8")
        self.assertEqual(content_type_for("a.woff2"), "font/woff2")

    def test_eq_static_routes(self):
        server = start_server(self)
        port = server.server_address[1]
        for path, expected in [("/eq.mjs", 200), ("/eq-ui.js", 200), ("/unknown-eq.js", 404)]:
            status, headers, body = request(port, "GET", path, {"Host": f"127.0.0.1:{port}"})
            self.assertEqual(status, expected)
            if expected == 200:
                self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
                self.assertTrue(body)

    def _analyze(self, instruments):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            write_tone(path)
            with mock.patch("ohrin.server.predict_or_unavailable", return_value=instruments):
                return analyze_upload(path.read_bytes(), 0, 4)

    def test_upload_analysis_returns_measured_level_change(self):
        result = self._analyze({"available": False, "reason": "x"})
        self.assertEqual(result["passage"], {"start_s": 0.0, "end_s": 4.0})
        self.assertGreater(result["windows"][0]["rms_dbfs"] - result["windows"][3]["rms_dbfs"], 15)

    def test_unavailable_instruments_keep_identity_limitation(self):
        result = self._analyze({"available": False, "reason": "x"})
        self.assertIs(result["instruments"]["available"], False)
        self.assertTrue(any(t.startswith("Instrument identity") for t in result["limitations"]))

    def test_available_instruments_swap_limitation(self):
        stub = {"available": True, "model": "stub", "classes": [], "segments": [], "caveat": "c"}
        result = self._analyze(stub)
        self.assertFalse(any(t.startswith("Instrument identity") for t in result["limitations"]))
        self.assertTrue(any("uncertain model outputs" in t for t in result["limitations"]))

    def test_prediction_runtime_error_still_yields_analysis_result(self):
        import contextlib, io
        import ohrin.instrument_inference as inference
        model = mock.Mock()
        model.predict.side_effect = RuntimeError("tensor exploded")
        with mock.patch.object(inference, "get_model", return_value=(model, None)), \
                contextlib.redirect_stderr(io.StringIO()):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "tone.wav"
                write_tone(path)
                result = analyze_upload(path.read_bytes(), 0, 4)
        self.assertIs(result["instruments"]["available"], False)
        self.assertIn("windows", result)

    def test_rejects_bad_selection_and_bad_audio(self):
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", -1, 4)
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", 0, 4)


if __name__ == "__main__":
    unittest.main()

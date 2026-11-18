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


class TempFileLifecycleTests(unittest.TestCase):
    def test_temp_file_is_closed_before_analysis_and_removed_after(self):
        seen = {}

        def fake_analyze(path, start, end):
            seen["path"] = path
            # Windows cannot open a file another handle holds; reopening for write models that.
            with open(path, "r+b") as handle:
                seen["data"] = handle.read()
            return {"passage": {"start_s": 0, "end_s": 4}, "limitations": []}

        def fake_predict(path, start, end):
            seen["exists_for_predict"] = Path(path).exists()
            return {"available": False}

        with mock.patch("ohrin.server.analyze_audio", fake_analyze), \
                mock.patch("ohrin.server.predict_or_unavailable", fake_predict):
            analyze_upload(b"payload", 0, 4)
        self.assertEqual(seen["data"], b"payload")
        self.assertTrue(seen["exists_for_predict"])
        self.assertFalse(seen["path"].exists())

    def test_temp_file_removed_when_analysis_raises(self):
        seen = {}

        def boom(path, start, end):
            seen["path"] = path
            raise ValueError("bad")

        with mock.patch("ohrin.server.analyze_audio", boom):
            with self.assertRaises(ValueError):
                analyze_upload(b"payload", 0, 4)
        self.assertFalse(seen["path"].exists())


class CrossSiteGetTests(unittest.TestCase):
    def setUp(self):
        self.port = start_server(self).server_address[1]
        self.host = f"127.0.0.1:{self.port}"

    def test_cross_site_api_get_is_forbidden(self):
        for path in ("/api/setup", "/api/setup/progress"):
            status, _, _ = request(self.port, "GET", path, {"Host": self.host, "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 403, path)

    def test_same_origin_none_and_absent_are_allowed(self):
        for extra in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}):
            status, _, _ = request(self.port, "GET", "/api/setup", {"Host": self.host, **extra})
            self.assertEqual(status, 200, extra)


class HostOriginGuardTests(unittest.TestCase):
    def setUp(self):
        self.port = start_server(self).server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.url = "/api/analyze?start=0&end=4"

    def post(self, headers, body=b"abc"):
        if "Content-Type" not in headers and not getattr(self, "_no_ct", False):
            headers = {**headers, "Content-Type": "application/octet-stream"}
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

    def test_post_safelisted_or_missing_content_type_rejected(self):
        for ctype in ["text/plain", "text/plain; charset=UTF-8", "Application/X-WWW-Form-Urlencoded",
                      "multipart/form-data; boundary=x", " text/plain ", None]:
            self._no_ct = ctype is None
            headers = {"Host": self.host}
            if ctype is not None:
                headers["Content-Type"] = ctype
            (status, _, body), m = self.post(headers)
            self.assertEqual(status, 403, ctype)
            self.assertEqual(json.loads(body), {"error": "Cross-origin requests are not allowed."})
            m.assert_not_called()

    def test_post_audio_content_types_pass(self):
        for ctype in ["application/octet-stream", "audio/wav"]:
            (status, _, _), m = self.post({"Host": self.host, "Content-Type": ctype})
            self.assertEqual(status, 200, ctype)
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

    def test_page_declares_an_inline_icon_so_favicon_is_not_requested(self):
        self.assertIn('<link rel="icon" href="data:,">', (WEB / "index.html").read_text())

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


def start_mode_server(case, mode):
    server = make_server(port=0, mode=mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(5)

    case.addCleanup(stop)
    return server


class ServerLifecycleTests(unittest.TestCase):
    def test_make_server_binds_loopback_only(self):
        server = start_mode_server(self, "dev")
        self.assertEqual(server.server_address[0], "127.0.0.1")
        with self.assertRaises(TypeError):
            make_server(host="0.0.0.0", port=0)

    def test_bind_preferred_uses_preferred_when_free(self):
        import socket
        from ohrin.server import bind_preferred
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        server, fell_back = bind_preferred(free, mode="dev")
        self.addCleanup(server.server_close)
        self.assertFalse(fell_back)
        self.assertEqual(server.server_address[1], free)

    def test_bind_preferred_falls_back_when_port_busy(self):
        import socket
        from ohrin.server import bind_preferred
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        server, fell_back = bind_preferred(busy, mode="window")
        self.addCleanup(server.server_close)
        self.assertTrue(fell_back)
        self.assertNotEqual(server.server_address[1], busy)
        self.assertEqual(server.mode, "window")

    def test_reuse_address_disabled_on_windows(self):
        from ohrin.server import OhrinServer, reuse_address_for
        self.assertFalse(reuse_address_for("win32"))
        self.assertTrue(reuse_address_for("linux"))
        self.assertTrue(reuse_address_for("darwin"))
        import sys
        self.assertEqual(OhrinServer.allow_reuse_address, reuse_address_for(sys.platform))
        self.assertTrue(OhrinServer.daemon_threads)

    def test_server_has_mode_and_quit_event(self):
        server = start_mode_server(self, "window")
        self.assertEqual(server.mode, "window")
        self.assertFalse(server.quit_event.is_set())

    def test_guard_applies_to_every_post_route(self):
        server = start_mode_server(self, "browser")
        port = server.server_address[1]
        host = f"127.0.0.1:{port}"
        routes = ("/api/analyze", "/api/quit", "/api/heartbeat", "/api/setup/download", "/api/setup/cancel")
        for route in routes:
            with self.subTest(route=route, case="text/plain"):
                status, _, _ = request(port, "POST", route, {"Host": host, "Content-Type": "text/plain"}, b"{}")
                self.assertEqual(status, 403)
            with self.subTest(route=route, case="foreign origin"):
                headers = {"Host": host, "Content-Type": "application/json", "Origin": "http://evil.example"}
                status, _, _ = request(port, "POST", route, headers, b"{}")
                self.assertEqual(status, 403)
            with self.subTest(route=route, case="no content type"):
                status, _, _ = request(port, "POST", route, {"Host": host}, b"{}")
                self.assertEqual(status, 403)
        self.assertFalse(server.quit_event.is_set())

    def test_quit_404_outside_browser_mode(self):
        for mode in ("dev", "window"):
            server = start_mode_server(self, mode)
            port = server.server_address[1]
            headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
            status, _, _ = request(port, "POST", "/api/quit", headers, b"{}")
            self.assertEqual(status, 404)
            self.assertFalse(server.quit_event.is_set())

    def test_quit_sets_event_in_browser_mode(self):
        server = start_mode_server(self, "browser")
        port = server.server_address[1]
        headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
        status, _, body = request(port, "POST", "/api/quit", headers, b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertTrue(server.quit_event.is_set())

    def test_setup_get_shape_and_no_torch_import(self):
        import sys
        server = start_mode_server(self, "window")
        port = server.server_address[1]
        before = "torch" in sys.modules
        with mock.patch("importlib.util.find_spec", return_value=None) as spec:
            status, headers, body = request(port, "GET", "/api/setup", {"Host": f"127.0.0.1:{port}"})
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data = json.loads(body)
        self.assertEqual(set(data), {"mode", "data_dir", "ffmpeg", "torch", "assets", "downloadable", "download_bytes", "hosts"})
        self.assertEqual(data["mode"], "window")
        self.assertIsInstance(data["data_dir"], str)
        self.assertEqual(set(data["ffmpeg"]), {"found", "ffmpeg", "ffprobe", "hint"})
        self.assertIs(data["torch"], False)
        spec.assert_any_call("torch")
        self.assertEqual("torch" in sys.modules, before)

    def test_setup_get_rejects_foreign_host(self):
        port = start_mode_server(self, "dev").server_address[1]
        status, _, _ = request(port, "GET", "/api/setup", {"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_analyze_without_ffmpeg_returns_400_json_with_hint(self):
        from ohrin import ffmpeg
        port = start_mode_server(self, "dev").server_address[1]
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            write_tone(wav)
            data = wav.read_bytes()
        headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "audio/wav"}
        with mock.patch.object(ffmpeg, "find_tool", return_value=None):
            status, resp_headers, body = request(port, "POST", "/api/analyze?start=0&end=4", headers, data)
        self.assertEqual(status, 400)
        self.assertIn("application/json", resp_headers["Content-Type"])
        self.assertIn(ffmpeg.install_hint(), json.loads(body)["error"])


class QuitContentLengthTests(unittest.TestCase):
    def test_quit_tolerates_bad_content_length(self):
        for value in ("abc", "-5"):
            server = start_mode_server(self, "browser")
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            self.addCleanup(conn.close)
            conn.putrequest("POST", "/api/quit", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{port}")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", value)
            conn.endheaders()
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 200, value)
            self.assertTrue(server.quit_event.is_set(), value)


if __name__ == "__main__":
    unittest.main()

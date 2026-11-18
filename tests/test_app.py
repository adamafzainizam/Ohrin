import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ohrin import app


def get_status(port, path="/"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        return conn.getresponse().status
    finally:
        conn.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_refused(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except OSError:
        return True
    return False


def url_port(url):
    return int(url.rsplit(":", 1)[1].rstrip("/"))


class AppCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"OHRIN_DATA_DIR": str(self.tmp)})
        env.start()
        self.addCleanup(env.stop)

    def fake_webview(self, on_start=None, start_error=None):
        calls = SimpleNamespace(window=None, start=None)

        def create_window(title, url, **kw):
            calls.window = SimpleNamespace(title=title, url=url, kw=kw)
            return SimpleNamespace()

        def start(**kw):
            calls.start = kw
            if start_error:
                raise start_error
            if on_start:
                on_start(calls)

        return SimpleNamespace(create_window=create_window, start=start, SAVE_DIALOG=15), calls

    def run_main(self, argv, webview=None, load=None, open_browser=None):
        if load is None:
            def load():
                if webview is None:
                    raise ImportError("no webview")
                return webview
        return app.main(argv, load_webview=load, open_browser=open_browser or (lambda url: None))


class WindowModeTests(AppCase):
    def test_window_mode_serves_then_stops_on_close(self):
        seen = {}

        def on_start(calls):
            port = url_port(calls.window.url)
            seen["port"] = port
            seen["status"] = get_status(port)

        webview, calls = self.fake_webview(on_start)
        self.assertEqual(self.run_main(["--port", "0"], webview), 0)
        self.assertEqual(seen["status"], 200)
        self.assertTrue(port_refused(seen["port"]))

    def test_window_loads_bound_loopback_url(self):
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        webview, calls = self.fake_webview()
        self.run_main(["--port", str(busy)], webview)
        self.assertTrue(calls.window.url.startswith("http://127.0.0.1:"))
        self.assertNotEqual(url_port(calls.window.url), busy)
        self.assertEqual(calls.window.kw["width"], 1280)
        self.assertEqual(calls.window.kw["min_size"], (900, 640))
        self.assertIs(calls.window.kw["text_select"], True)

    def test_window_start_uses_persistent_storage_in_data_dir(self):
        webview, calls = self.fake_webview()
        self.run_main(["--port", "0", "--debug"], webview)
        self.assertIs(calls.start["private_mode"], False)
        self.assertEqual(Path(calls.start["storage_path"]), self.tmp / "webview")
        self.assertIs(calls.start["debug"], True)

    def test_linux_forces_gtk_backend(self):
        for platform, expected in (("linux", "gtk"), ("win32", None), ("darwin", None)):
            webview, calls = self.fake_webview()
            with mock.patch.object(sys, "platform", platform):
                self.run_main(["--port", "0"], webview)
            self.assertEqual(calls.start["gui"], expected, platform)

    def test_window_mode_server_mode_is_window(self):
        modes = []
        webview, calls = self.fake_webview(lambda c: modes.append(json.loads(self._setup(c))["mode"]))
        self.run_main(["--port", "0"], webview)
        self.assertEqual(modes, ["window"])

    def _setup(self, calls):
        port = url_port(calls.window.url)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/api/setup", headers={"Host": f"127.0.0.1:{port}"})
            return conn.getresponse().read()
        finally:
            conn.close()

    def test_window_gets_desktop_api_with_window_reference(self):
        webview, calls = self.fake_webview()
        self.run_main(["--port", "0"], webview)
        api = calls.window.kw["js_api"]
        self.assertIsInstance(api, app.DesktopApi)
        self.assertIs(api._window.__class__, SimpleNamespace)


class BrowserFallbackTests(AppCase):
    def browser_run(self, argv, webview=None, load=None):
        opened = []
        box = {}

        def open_browser(url):
            opened.append(url)
            box["port"] = url_port(url)
            box["status"] = get_status(box["port"])
            box["setup"] = json.loads(self._get(box["port"], "/api/setup"))
            box["server"].quit_event.set()

        real = app.bind_preferred

        def spy(*a, **k):
            server, fell = real(*a, **k)
            box["server"] = server
            return server, fell

        with mock.patch.object(app, "bind_preferred", spy):
            code = self.run_main(argv, webview, load, open_browser)
        return code, opened, box

    def _get(self, port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
            return conn.getresponse().read()
        finally:
            conn.close()

    def run_main(self, argv, webview=None, load=None, open_browser=None):
        return super().run_main(argv, webview, load, open_browser)

    def test_import_failure_falls_back_to_browser(self):
        code, opened, box = self.browser_run(["--port", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(len(opened), 1)
        self.assertEqual(box["status"], 200)
        self.assertEqual(box["setup"]["mode"], "browser")
        self.assertTrue(port_refused(box["port"]))

    def test_backend_start_failure_falls_back_to_browser(self):
        webview, calls = self.fake_webview(start_error=RuntimeError("no GTK"))
        code, opened, box = self.browser_run(["--port", "0"], webview)
        self.assertEqual(code, 0)
        self.assertEqual(len(opened), 1)
        self.assertEqual(box["setup"]["mode"], "browser")

    def test_browser_flag_skips_webview(self):
        load = mock.Mock(side_effect=AssertionError("webview must not load"))
        code, opened, box = self.browser_run(["--browser", "--port", "0"], load=load)
        self.assertEqual(code, 0)
        load.assert_not_called()
        self.assertEqual(len(opened), 1)


class ArgsAndLoggingTests(AppCase):
    def test_data_dir_flag_sets_env(self):
        target = self.tmp / "custom"
        webview, calls = self.fake_webview()
        self.run_main(["--port", "0", "--data-dir", str(target)], webview)
        self.assertEqual(os.environ["OHRIN_DATA_DIR"], str(target))
        self.assertEqual(Path(calls.start["storage_path"]), target / "webview")

    def test_log_redirect_when_stdio_missing(self):
        webview, calls = self.fake_webview()
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            try:
                self.run_main(["--port", "0"], webview)
                redirected = (sys.stdout, sys.stderr)
            finally:
                for stream in (sys.stdout, sys.stderr):
                    if stream is not None:
                        stream.close()
        self.assertIsNotNone(redirected[0])
        log = self.tmp / "logs" / "ohrin.log"
        self.assertTrue(log.exists())
        self.assertIn("http://127.0.0.1:", log.read_text(encoding="utf-8"))

    def test_log_truncated_when_over_1mb(self):
        log = self.tmp / "logs" / "ohrin.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(b"x" * (1024 * 1024 + 1))
        webview, calls = self.fake_webview()
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            try:
                self.run_main(["--port", "0"], webview)
            finally:
                for stream in (sys.stdout, sys.stderr):
                    if stream is not None:
                        stream.close()
        self.assertLess(log.stat().st_size, 1024 * 1024)

    def test_stdio_untouched_when_present(self):
        out, err = sys.stdout, sys.stderr
        webview, calls = self.fake_webview()
        self.run_main(["--port", "0"], webview)
        self.assertIs(sys.stdout, out)
        self.assertIs(sys.stderr, err)
        self.assertFalse((self.tmp / "logs" / "ohrin.log").exists())


class DesktopApiTests(unittest.TestCase):
    def make(self, result, webview=None):
        webview = webview or SimpleNamespace(SAVE_DIALOG=15)
        window = SimpleNamespace(calls=[])

        def create_file_dialog(kind, **kw):
            window.calls.append((kind, kw))
            return result

        window.create_file_dialog = create_file_dialog
        api = app.DesktopApi()
        api._window = window
        patcher = mock.patch.object(app, "_webview_module", lambda: webview)
        patcher.start()
        self.addCleanup(patcher.stop)
        return api, window

    def test_desktop_api_exposes_only_save_note(self):
        public = [n for n in dir(app.DesktopApi()) if not n.startswith("_")]
        self.assertEqual(public, ["save_note"])

    def test_save_note_writes_utf8_and_reports_cancel(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "note.json"
            api, window = self.make(str(target))
            text = json.dumps({"note": "café ♪"}, ensure_ascii=False)
            self.assertEqual(api.save_note(text), {"saved": True})
            self.assertEqual(target.read_bytes(), text.encode("utf-8"))
            self.assertEqual(window.calls[0][0], 15)
            self.assertEqual(window.calls[0][1]["save_filename"], "ohrin-passage-note.json")
            api2, _ = self.make(None)
            self.assertEqual(api2.save_note(text), {"saved": False})

    def test_save_note_prefers_filedialog_enum(self):
        webview = SimpleNamespace(FileDialog=SimpleNamespace(SAVE=7), SAVE_DIALOG=15)
        api, window = self.make(None, webview)
        api.save_note("{}")
        self.assertEqual(window.calls[0][0], 7)

    def test_save_note_accepts_tuple_result(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "n.json"
            for wrap in (tuple, list):
                api, _ = self.make(wrap([str(target)]))
                self.assertEqual(api.save_note('{"a":1}'), {"saved": True})
            self.assertEqual(target.read_text(encoding="utf-8"), '{"a":1}')

    def test_save_note_rejects_non_json_and_oversize(self):
        api, window = self.make("/nowhere.json")
        for bad in ("not json", None, 5, b"{}", "{" + " " * (5 * 1024 * 1024 + 1) + "}"):
            with self.assertRaises(ValueError, msg=repr(bad)[:30]):
                api.save_note(bad)
        self.assertEqual(window.calls, [])


if __name__ == "__main__":
    unittest.main()

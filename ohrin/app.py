"""Desktop launcher: serves the workbench locally and shows it in a native window, or in the browser."""

import argparse
import json
import http.client
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from . import paths
from .server import bind_preferred

DESKTOP_PORT = 38765
MAX_NOTE_BYTES = 5 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
NOTE_FILENAME = "ohrin-passage-note.json"
HEARTBEAT_TIMEOUT_S = 45

_WEBVIEW = None


def _import_webview():
    import webview
    return webview


def _webview_module():
    return _WEBVIEW


class DesktopApi:
    """Exposed to the page through pywebview. Keep every attribute but save_note private."""

    def __init__(self):
        self._window = None
        self._origin = None

    def save_note(self, text):
        if not isinstance(text, str):
            raise ValueError("The note must be text.")
        if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
            raise ValueError("The note is too large.")
        json.loads(text)
        current = self._window.get_current_url()
        origin = self._origin
        if not origin or not isinstance(current, str) or not (current == origin or current.startswith(origin + "/")):
            raise ValueError("Saving is only allowed from the Ohrin window.")
        webview = _webview_module()
        file_dialog = getattr(webview, "FileDialog", None)
        kind = file_dialog.SAVE if file_dialog is not None else webview.SAVE_DIALOG
        result = self._window.create_file_dialog(kind, save_filename=NOTE_FILENAME)
        if isinstance(result, (tuple, list)):
            result = result[0] if result else None
        if not result:
            return {"saved": False}
        Path(result).write_text(text, encoding="utf-8")
        return {"saved": True}


def redirect_stdio_if_missing():
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "ohrin.log"
    if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
        log.write_bytes(b"")
    stream = open(log, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _port(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port: {text!r}")
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return value


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="ohrin.app", description="Ohrin desktop app")
    parser.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    parser.add_argument("--data-dir", metavar="PATH")
    parser.add_argument("--port", type=_port, default=DESKTOP_PORT, help="0 picks a random free port")
    parser.add_argument("--debug", action="store_true", help="enable the web inspector in the window")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-report", metavar="PATH")
    return parser.parse_args(argv)


def log(message):
    print(f"Ohrin: {message}", flush=True)


def find_running(port):
    """Return the URL of an Ohrin already serving on `port`, or None. Needs a JSON /api/setup answer containing "mode"."""
    if not port:
        return None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        conn.request("GET", "/api/setup", headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        if response.status != 200:
            return None
        data = json.loads(response.read(1024 * 1024))
        if isinstance(data, dict) and "mode" in data:
            return f"http://127.0.0.1:{port}/"
    except (OSError, ValueError, http.client.HTTPException):
        pass
    finally:
        conn.close()
    return None


def _destroy_windows(webview):
    for window in list(getattr(webview, "windows", None) or []):
        try:
            window.destroy()
        except Exception:
            pass


def run_window(server, url, load_webview, debug):
    """Returns True if a window was shown and closed, False if the caller should fall back.

    `server` is None when the window attaches to an Ohrin that is already running."""
    global _WEBVIEW
    try:
        webview = load_webview()
    except ImportError as exc:
        log(f"pywebview is not available ({exc}); using the browser")
        return False
    previous = None
    installed = False
    try:
        _WEBVIEW = webview
        if server is not None:
            server.mode = "window"
        api = DesktopApi()
        api._origin = url.rstrip("/")
        api._window = webview.create_window("Ohrin", url, width=1280, height=860, min_size=(900, 640), js_api=api,
                                      text_select=True)
        try:
            previous = signal.signal(signal.SIGINT, lambda signum, frame: _destroy_windows(webview))
            installed = True
        except (ValueError, OSError):
            pass
        unsupported = []

        def check_renderer():
            # pywebview sets webview.renderer when start() picks a backend. On Windows without WebView2 it
            # silently uses MSHTML, which cannot run ES modules.
            if getattr(webview, "renderer", None) == "mshtml":
                unsupported.append(True)
                _destroy_windows(webview)

        webview.start(
            func=check_renderer,
            gui="gtk" if sys.platform.startswith("linux") else None,
            private_mode=False,
            storage_path=str(paths.webview_storage_dir()),
            debug=debug,
        )
        if unsupported:
            log("the window uses the legacy MSHTML renderer (WebView2 is missing); using the browser")
            return False
        return True
    except Exception as exc:
        log(f"the window could not start ({exc}); using the browser")
        return False
    finally:
        _WEBVIEW = None
        if installed:
            try:
                signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)
            except (ValueError, OSError):
                pass


def wait_until_done(server, *, timeout=HEARTBEAT_TIMEOUT_S, interval=0.5):
    """Block until /api/quit, or until the page has sent heartbeats and then gone silent for `timeout` seconds."""
    while not server.quit_event.wait(interval):
        seen = server.last_heartbeat
        if seen is not None and server.clock() - seen > timeout:
            log("the browser tab is gone; stopping")
            return


def main(argv=None, *, load_webview=_import_webview, open_browser=webbrowser.open):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.data_dir:
        os.environ["OHRIN_DATA_DIR"] = args.data_dir
    redirect_stdio_if_missing()
    if args.self_test:
        from . import selftest
        return selftest.run(args.self_test_report)
    running = find_running(args.port)
    if running:
        log(f"already running at {running}")
        if args.browser or not run_window(None, running, load_webview, args.debug):
            open_browser(running)
        return 0
    server, fell_back = bind_preferred(args.port, mode="browser" if args.browser else "window")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        log(url)
        if fell_back:
            log(f"port {args.port} is busy; using another port, so the saved theme will not carry over")
        if args.browser or not run_window(server, url, load_webview, args.debug):
            server.mode = "browser"
            open_browser(url)
            try:
                wait_until_done(server)
            except KeyboardInterrupt:
                pass
    finally:
        try:
            server.setup_job.close()
            server.setup_job.join(5)
        finally:
            try:
                server.shutdown()
            finally:
                server.server_close()
                thread.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

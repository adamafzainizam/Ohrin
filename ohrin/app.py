"""Desktop launcher: serves the workbench locally and shows it in a native window, or in the browser."""

import argparse
import json
import os
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

    def save_note(self, text):
        if not isinstance(text, str):
            raise ValueError("The note must be text.")
        if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
            raise ValueError("The note is too large.")
        json.loads(text)
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


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="ohrin.app", description="Ohrin desktop app")
    parser.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    parser.add_argument("--data-dir", metavar="PATH")
    parser.add_argument("--port", type=int, default=DESKTOP_PORT, help="0 picks a random free port")
    parser.add_argument("--debug", action="store_true", help="enable the web inspector in the window")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-report", metavar="PATH")
    return parser.parse_args(argv)


def log(message):
    print(f"Ohrin: {message}", flush=True)


def run_window(server, url, load_webview, debug):
    """Returns True if a window was shown and closed, False if the caller should fall back."""
    global _WEBVIEW
    try:
        webview = load_webview()
    except ImportError as exc:
        log(f"pywebview is not available ({exc}); using the browser")
        return False
    try:
        _WEBVIEW = webview
        server.mode = "window"
        api = DesktopApi()
        api._window = webview.create_window("Ohrin", url, width=1280, height=860, min_size=(900, 640), js_api=api,
                                      text_select=True)
        webview.start(
            gui="gtk" if sys.platform.startswith("linux") else None,
            private_mode=False,
            storage_path=str(paths.webview_storage_dir()),
            debug=debug,
        )
        return True
    except Exception as exc:
        log(f"the window could not start ({exc}); using the browser")
        return False
    finally:
        _WEBVIEW = None


def main(argv=None, *, load_webview=_import_webview, open_browser=webbrowser.open):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.data_dir:
        os.environ["OHRIN_DATA_DIR"] = args.data_dir
    redirect_stdio_if_missing()
    if args.self_test:
        from . import selftest
        return selftest.run(args.self_test_report)
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
                while not server.quit_event.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

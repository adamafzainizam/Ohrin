"""Background download of the missing model files, with progress, cancel and a single run at a time."""
import threading
import urllib.request
from pathlib import Path

from . import assets


class Busy(RuntimeError):
    pass


class SetupJob:
    def __init__(self, data_dir_fn, opener=urllib.request.urlopen):
        self._data_dir_fn = data_dir_fn
        self._opener = opener
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {"state": "idle", "file": None, "bytes": 0, "total": 0, "error": None}

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _set(self, **changes):
        with self._lock:
            self._state.update(changes)

    def start(self):
        with self._lock:
            if self._state["state"] == "downloading":
                raise Busy("A download is already running.")
            self._cancel = threading.Event()
            self._state = {"state": "downloading", "file": None, "bytes": 0, "total": 0, "error": None}
            self._thread = threading.Thread(target=self._run, args=(self._cancel,), daemon=True)
            thread = self._thread
        thread.start()

    def cancel(self):
        with self._lock:
            self._cancel.set()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, cancel):
        try:
            data_dir = Path(self._data_dir_fn())
            todo = assets.missing(data_dir)
            for entry in todo:
                self._set(file=entry["name"], bytes=0, total=entry["size"])
                assets.verify_or_download(
                    entry["url"], data_dir / entry["subdir"] / entry["name"], entry["sha256"],
                    opener=self._opener, force=True, max_bytes=entry["size"] + assets.SIZE_SLACK,
                    cancel=cancel.is_set, progress=lambda done: self._set(bytes=done),
                )
            from . import instrument_inference
            instrument_inference.reset_model_cache()
            self._set(state="done", file=None)
        except assets.Cancelled:
            self._set(state="cancelled", error=None)
        except assets.ChecksumMismatch:
            self._set(state="error", error="Checksum mismatch, file discarded.")
        except assets.SizeExceeded as exc:
            self._set(state="error", error=str(exc))
        except Exception as exc:
            self._set(state="error", error=f"Download failed: {' '.join(str(exc).split())[:160]}")

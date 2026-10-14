"""Instrument-presence candidates for an uploaded passage.

Pipeline: FFmpeg decode -> VGGish embeddings (ohrin.vggish) -> mean of up to
ten 0.96 s embeddings per ~10 s segment -> OpenMIC-trained head. The heads were
fitted and evaluated on OpenMIC clips; nothing here validates them on full
commercial mixes, so results are shown as uncertain model outputs.
"""
import sys
import traceback
from pathlib import Path

import numpy as np

# torch (and the modules that import it) are loaded lazily so the core
# workbench imports and runs without PyTorch installed.

ROOT = Path(__file__).resolve().parent.parent
SEGMENT_EXAMPLES = 10          # 10 x 0.96 s, the unit the heads were trained on
EXAMPLE_SECONDS = 0.96
MIN_SEGMENT_EXAMPLES = 3       # skip tails shorter than ~2.9 s


def decode_mono_16k(*args, **kwargs):
    from .vggish import decode_mono_16k as decode
    return decode(*args, **kwargs)


class InstrumentModel:
    def __init__(self, embed, head, classes, thresholds, name):
        self.embed, self.head = embed, head
        self.classes, self.thresholds, self.name = list(classes), np.asarray(thresholds, dtype=float), name

    @classmethod
    def load(cls, weights_dir=None, checkpoint=None, kind="mlp"):
        import torch
        from .instruments import make_model
        from .vggish import Extractor

        weights_dir = Path(weights_dir or ROOT / "data" / "vggish")
        checkpoint = Path(checkpoint or ROOT / "outputs" / f"openmic-{kind}.pt")
        for needed in (weights_dir / "vggish-10086976.pth", weights_dir / "vggish_pca_params.npz", checkpoint):
            if not needed.is_file():
                raise FileNotFoundError(needed.name)
        state = torch.load(checkpoint, map_location="cpu", weights_only=False)
        head = make_model(state["kind"])
        head.load_state_dict(state["state_dict"])
        head.eval()
        extractor = Extractor(weights_dir)
        return cls(extractor.embed, head, state["classes"], state["thresholds"], f"OpenMIC {state['kind']} head")

    def _probabilities(self, embeddings):
        import torch
        x = torch.tensor(embeddings.astype(np.float32) / 255.0)
        with torch.no_grad():
            return torch.sigmoid(self.head(x)).numpy()

    def predict(self, path, start_s, end_s):
        samples = decode_mono_16k(path, start=start_s, duration=end_s - start_s)
        embeddings = self.embed(samples)
        if len(embeddings) == 0:
            raise ValueError("The passage is too short for instrument analysis.")
        groups = [embeddings[i:i + SEGMENT_EXAMPLES] for i in range(0, len(embeddings), SEGMENT_EXAMPLES)]
        groups = [g for g in groups if len(g) >= MIN_SEGMENT_EXAMPLES] or [embeddings]
        probs = self._probabilities(np.stack([g.astype(float).mean(axis=0) for g in groups]))
        segments, cursor = [], 0
        for g, p in zip(groups, probs):
            lo = start_s + cursor * EXAMPLE_SECONDS
            cursor += len(g)
            segments.append({
                "start_s": round(lo, 2), "end_s": round(start_s + cursor * EXAMPLE_SECONDS, 2),
                "probabilities": {c: round(float(v), 3) for c, v in zip(self.classes, p)},
            })
        overall = probs.mean(axis=0)
        return {
            "available": True, "model": self.name,
            "classes": [{"name": c, "probability": round(float(v), 3), "threshold": round(float(t), 3),
                         "above_threshold": bool(v >= t)}
                        for c, v, t in zip(self.classes, overall, self.thresholds)],
            "segments": segments,
            "caveat": ("Model output from an OpenMIC-trained head on VGGish embeddings. Evaluated on OpenMIC clips only; "
                       "not validated on full mixes. Presence is not a transcription or an isolated part."),
        }


_cached = {}


def _short(exc, limit=160):
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _log_failure(what):
    print(f"ohrin: {what} failed:", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


def get_model():
    """Load once; return (model, None) or (None, reason)."""
    if "result" not in _cached:
        try:
            _cached["result"] = (InstrumentModel.load(), None)
        except ImportError as exc:
            _log_failure("model load")
            top = (getattr(exc, "name", None) or "").split(".")[0]
            if top == "torch":
                reason = "PyTorch is not installed; instrument candidates are unavailable. See README."
            elif "torch" in str(exc).lower():
                reason = (f"PyTorch failed to import: {_short(exc)}; "
                          "instrument candidates are unavailable. Fix it and restart the server.")
            else:
                reason = (f"A dependency could not be imported ({type(exc).__name__}: {_short(exc)}); "
                          "instrument candidates are unavailable. Fix it and restart the server.")
            _cached["result"] = (None, reason)
        except FileNotFoundError as exc:
            _log_failure("model load")
            _cached["result"] = (None, f"Missing local file: {exc}. See docs/model-card.md.")
        except Exception as exc:  # corrupt or incompatible weights/checkpoint: report, do not drop the request
            _log_failure("model load")
            _cached["result"] = (None, f"The instrument model could not be loaded ({type(exc).__name__}: {_short(exc)}). "
                                       "Check the weights and checkpoint, then restart the server.")
    return _cached["result"]


def predict_or_unavailable(path, start_s, end_s):
    model, reason = get_model()
    if model is None:
        return {"available": False, "reason": reason}
    try:
        return model.predict(path, start_s, end_s)
    except (ValueError, OSError) as exc:
        return {"available": False, "reason": str(exc) or "Instrument analysis failed."}
    except Exception:  # e.g. torch RuntimeError / out of memory: keep the response a valid analysis result
        _log_failure("prediction")
        return {"available": False, "reason": "Instrument analysis failed unexpectedly; see the server log."}

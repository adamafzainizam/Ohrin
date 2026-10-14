"""VGGish embedding extractor compatible with OpenMIC-2018's stored features.

Front end follows Google's AudioSet reference (mel_features / vggish_params):
16 kHz mono, 25 ms periodic Hann window, 10 ms hop, 512-point FFT, 64 HTK mel
bands (125-7500 Hz), log(mel + 0.01), non-overlapping 96-frame (0.96 s)
examples. The network is the torchvggish port; postprocessing is the AudioSet
PCA-whiten + clip to [-2, 2] + 8-bit quantisation. Compatibility with
OpenMIC is measured by experiments/validate_vggish.py, not assumed.
"""
import subprocess
from pathlib import Path

import numpy as np
import torch
from torch import nn

SAMPLE_RATE = 16000
WINDOW = 400
HOP = 160
FFT = 512
N_MEL = 64
MEL_MIN_HZ, MEL_MAX_HZ = 125.0, 7500.0
LOG_OFFSET = 0.01
EXAMPLE_FRAMES = 96

_MEL_BREAK_HZ, _MEL_HIGH_Q = 700.0, 1127.0


def _hz_to_mel(hz):
    return _MEL_HIGH_Q * np.log(1.0 + np.asarray(hz, dtype=np.float64) / _MEL_BREAK_HZ)


def mel_matrix():
    bins = FFT // 2 + 1
    spec_mel = _hz_to_mel(np.linspace(0.0, SAMPLE_RATE / 2.0, bins))[:, None]
    edges = np.linspace(_hz_to_mel(MEL_MIN_HZ), _hz_to_mel(MEL_MAX_HZ), N_MEL + 2)
    weights = np.zeros((bins, N_MEL))
    for i in range(N_MEL):
        lo, centre, hi = edges[i:i + 3]
        lower = (spec_mel[:, 0] - lo) / (centre - lo)
        upper = (hi - spec_mel[:, 0]) / (hi - centre)
        weights[:, i] = np.maximum(0.0, np.minimum(lower, upper))
    weights[0, :] = 0.0  # Google zeroes the DC bin.
    return weights


def decode_mono_16k(path, start=None, duration=None):
    cmd = ['ffmpeg', '-v', 'error', '-nostdin']
    if start is not None:
        cmd += ['-ss', str(start)]
    if duration is not None:
        cmd += ['-t', str(duration)]
    cmd += ['-i', str(path), '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 'f32le', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype='<f4').astype(np.float64)


def log_mel_examples(samples):
    """Return an (n, 96, 64) array of log-mel examples."""
    if len(samples) < WINDOW:
        return np.zeros((0, EXAMPLE_FRAMES, N_MEL), dtype=np.float32)
    n_frames = 1 + (len(samples) - WINDOW) // HOP
    idx = np.arange(WINDOW)[None, :] + HOP * np.arange(n_frames)[:, None]
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WINDOW) / WINDOW)
    spectrum = np.abs(np.fft.rfft(samples[idx] * window, FFT))
    log_mel = np.log(spectrum @ mel_matrix() + LOG_OFFSET)
    n_examples = n_frames // EXAMPLE_FRAMES
    return log_mel[:n_examples * EXAMPLE_FRAMES].reshape(n_examples, EXAMPLE_FRAMES, N_MEL).astype(np.float32)


class VGGish(nn.Module):
    def __init__(self):
        super().__init__()
        cfg, layers, channels = [64, 'M', 128, 'M', 256, 256, 'M', 512, 512, 'M'], [], 1
        for v in cfg:
            if v == 'M':
                layers.append(nn.MaxPool2d(2, 2))
            else:
                layers += [nn.Conv2d(channels, v, 3, padding=1), nn.ReLU(inplace=True)]
                channels = v
        self.features = nn.Sequential(*layers)
        self.embeddings = nn.Sequential(
            nn.Linear(512 * 4 * 6, 4096), nn.ReLU(True),
            nn.Linear(4096, 4096), nn.ReLU(True),
            nn.Linear(4096, 128), nn.ReLU(True))

    def forward(self, x):
        x = self.features(x)
        x = x.permute(0, 2, 3, 1).contiguous().flatten(1)  # NHWC flatten as in TF
        return self.embeddings(x)


class Extractor:
    def __init__(self, weights_dir):
        weights_dir = Path(weights_dir)
        self.model = VGGish().eval()
        state = torch.load(weights_dir / 'vggish-10086976.pth', map_location='cpu', weights_only=True)
        self.model.load_state_dict(state)
        pca = np.load(weights_dir / 'vggish_pca_params.npz')
        self.pca_matrix = pca['pca_eigen_vectors']
        self.pca_means = pca['pca_means'].reshape(-1, 1)

    def raw_embeddings(self, examples):
        if len(examples) == 0:
            return np.zeros((0, 128), dtype=np.float32)
        with torch.no_grad():
            return self.model(torch.from_numpy(examples)[:, None]).numpy()

    def postprocess(self, embeddings):
        whitened = (self.pca_matrix @ (embeddings.T.astype(np.float64) - self.pca_means)).T
        clipped = np.clip(whitened, -2.0, 2.0)
        return ((clipped + 2.0) * (255.0 / 4.0)).astype(np.uint8)

    def embed(self, samples):
        return self.postprocess(self.raw_embeddings(log_mel_examples(samples)))

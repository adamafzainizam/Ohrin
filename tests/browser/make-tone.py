"""Write a 240 s, 44.1 kHz mono 16-bit WAV of synthetic tones for browser checks.

Usage: python tests/browser/make-tone.py /path/outside/repo/ohrin-tone.wav
The output is generated test data; never commit it.
"""
import math
import struct
import sys
import wave

SR = 44100
DURATION_S = 240
NOTES_HZ = (220.0, 277.18, 329.63, 440.0)  # A3, C#4, E4, A4, one per 2.4 s


def main(path):
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SR)
        block = SR // 10
        for first in range(0, SR * DURATION_S, block):
            frames = bytearray()
            for n in range(first, min(first + block, SR * DURATION_S)):
                t = n / SR
                freq = NOTES_HZ[int(t / 2.4) % len(NOTES_HZ)]
                swell = 0.5 + 0.5 * math.sin(2 * math.pi * t / 19.2)
                value = 0.25 * (0.4 + 0.6 * swell) * math.sin(2 * math.pi * freq * t)
                frames += struct.pack("<h", int(value * 32767))
            out.writeframes(bytes(frames))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make-tone.py OUTPUT.wav")
    main(sys.argv[1])

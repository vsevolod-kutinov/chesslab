#!/usr/bin/env python3
"""Generates move sounds into chesslab/assets/.

Our own samples rather than third-party files, so nothing breaks when packages
are removed. A wooden click = a short noise transient plus a pair of decaying
sine waves. Run manually if you want to change the timbre.
"""

from __future__ import annotations

import array
import math
import random
import wave
from pathlib import Path

RATE = 44100
ASSETS = Path(__file__).resolve().parent.parent / "chesslab" / "assets"


def render(duration: float, tones: list[tuple[float, float]], tau: float,
           noise: float, peak: float) -> array.array:
    """tones are (frequency, weight) pairs. tau is the decay constant in seconds."""
    samples = array.array("h")
    total = int(RATE * duration)
    weight_sum = sum(weight for _, weight in tones) or 1.0
    noise_len = int(RATE * 0.004)  # transient at the very start

    for index in range(total):
        t = index / RATE
        envelope = math.exp(-t / tau)

        value = sum(
            weight * math.sin(2 * math.pi * freq * t) for freq, weight in tones
        ) / weight_sum

        if index < noise_len:
            value += noise * random.uniform(-1.0, 1.0) * (1 - index / noise_len)

        # soft limiting so the transient does not clip
        value = math.tanh(value * 1.4)
        samples.append(int(max(-1.0, min(1.0, value * envelope * peak)) * 32767))

    # smooth fade at the end: otherwise the cut-off is audible as a click
    fade = int(RATE * 0.006)
    for index in range(min(fade, len(samples))):
        position = len(samples) - 1 - index
        samples[position] = int(samples[position] * index / fade)

    return samples


def save(name: str, samples: array.array) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    path = ASSETS / f"{name}.wav"
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())
    print(f"{path.name}: {len(samples) / RATE * 1000:.0f} ms, "
          f"{path.stat().st_size / 1024:.1f} KB")


def main() -> None:
    random.seed(7)  # so that a rebuild gives the same sound

    # ordinary move — a dry high click
    save("move", render(0.075, [(880, 1.0), (1760, 0.35)], tau=0.017,
                        noise=0.30, peak=0.55))

    # capture — lower and denser, clearly distinguishable by ear
    save("capture", render(0.110, [(520, 1.0), (780, 0.5), (260, 0.3)],
                           tau=0.030, noise=0.55, peak=0.70))

    # check — a short two-tone trill
    first = render(0.055, [(990, 1.0)], tau=0.020, noise=0.10, peak=0.5)
    second = render(0.075, [(1320, 1.0)], tau=0.024, noise=0.10, peak=0.55)
    save("check", first + second)


if __name__ == "__main__":
    main()

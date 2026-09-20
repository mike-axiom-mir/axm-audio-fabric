"""Render a deterministic procedural zap into ./out for a quick smoke test."""
from pathlib import Path
import json

from axm_audio import render_wav

cue = {
    "schema": "axm.audio-cue/v1",
    "atom": {
        "schema": "axm.audio-atom/v1",
        "id": "procedural-zap-v1",
        "sample_rate": 44100,
        "duration_ms": 280,
        "source": {
            "waveform": "sine",
            "frequency_hz": 980,
            "frequency_end_hz": 140,
            "noise_mix": 0.18,
            "seed": 20260920,
        },
        "envelope": {
            "attack_ms": 3,
            "decay_ms": 55,
            "sustain_level": 0.32,
            "release_ms": 130,
        },
        "effects": [
            {"type": "drive", "amount": 1.7},
            {"type": "lowpass", "cutoff_hz": 3200},
        ],
    },
    "gain": 0.75,
    "position": {"x": 4, "y": 0, "z": 2},
    "listener": {"x": 0, "y": 0, "z": 0},
    "max_distance": 25,
}

out = Path("out")
receipt = render_wav(cue, out / "procedural-zap.wav", out / "procedural-zap.receipt.json")
print(json.dumps(receipt, indent=2, sort_keys=True))

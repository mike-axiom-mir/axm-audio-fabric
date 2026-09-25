"""Dependency-light deterministic audio core for AXM Audio Fabric.

Truth boundary: this module can prove structural validity, deterministic replay
within this implementation, signal statistics, and WAV integrity. It cannot
prove that rendered audio sounds good.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import wave
from pathlib import Path
from typing import Any, Iterable


AUDIO_ATOM_SCHEMA = "axm.audio-atom/v1"
AUDIO_CUE_SCHEMA = "axm.audio-cue/v1"
RECEIPT_SCHEMA = "axm.audio-render-receipt/v1"


class AudioRecipeError(ValueError):
    """Raised when a canonical audio recipe is structurally invalid."""


def canonical_json(value: Any) -> str:
    """Stable compact JSON used for recipe identity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise AudioRecipeError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise AudioRecipeError(f"{name} must be >= {minimum}")
    return value


def validate_atom(atom: dict[str, Any]) -> None:
    if not isinstance(atom, dict):
        raise AudioRecipeError("audio atom must be an object")
    if atom.get("schema") != AUDIO_ATOM_SCHEMA:
        raise AudioRecipeError(f"schema must be {AUDIO_ATOM_SCHEMA!r}")
    if not isinstance(atom.get("id"), str) or not atom["id"].strip():
        raise AudioRecipeError("id must be a non-empty string")
    sr = atom.get("sample_rate", 44100)
    if not isinstance(sr, int) or isinstance(sr, bool) or sr < 8000 or sr > 192000:
        raise AudioRecipeError("sample_rate must be an integer between 8000 and 192000")
    _number(atom.get("duration_ms"), "duration_ms", minimum=1.0)

    source = atom.get("source")
    if not isinstance(source, dict):
        raise AudioRecipeError("source must be an object")
    waveform = source.get("waveform")
    if waveform not in {"sine", "square", "triangle", "noise"}:
        raise AudioRecipeError("source.waveform must be sine, square, triangle, or noise")
    if waveform != "noise":
        _number(source.get("frequency_hz"), "source.frequency_hz", minimum=1.0)
        if "frequency_end_hz" in source:
            _number(source["frequency_end_hz"], "source.frequency_end_hz", minimum=1.0)
    if "noise_mix" in source:
        nm = _number(source["noise_mix"], "source.noise_mix", minimum=0.0)
        if nm > 1.0:
            raise AudioRecipeError("source.noise_mix must be <= 1")
    if "seed" in source and not isinstance(source["seed"], int):
        raise AudioRecipeError("source.seed must be an integer")

    env = atom.get("envelope", {})
    if not isinstance(env, dict):
        raise AudioRecipeError("envelope must be an object")
    for key in ("attack_ms", "decay_ms", "release_ms"):
        _number(env.get(key, 0.0), f"envelope.{key}", minimum=0.0)
    sustain = _number(env.get("sustain_level", 1.0), "envelope.sustain_level", minimum=0.0)
    if sustain > 1.0:
        raise AudioRecipeError("envelope.sustain_level must be <= 1")

    effects = atom.get("effects", [])
    if not isinstance(effects, list):
        raise AudioRecipeError("effects must be an array")
    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            raise AudioRecipeError(f"effects[{i}] must be an object")
        kind = effect.get("type")
        if kind == "lowpass":
            _number(effect.get("cutoff_hz"), f"effects[{i}].cutoff_hz", minimum=1.0)
        elif kind == "drive":
            _number(effect.get("amount"), f"effects[{i}].amount", minimum=0.0)
        elif kind == "gain":
            _number(effect.get("amount"), f"effects[{i}].amount", minimum=0.0)
        else:
            raise AudioRecipeError(f"unsupported effect type: {kind!r}")


def validate_cue(cue: dict[str, Any]) -> None:
    if not isinstance(cue, dict) or cue.get("schema") != AUDIO_CUE_SCHEMA:
        raise AudioRecipeError(f"cue.schema must be {AUDIO_CUE_SCHEMA!r}")
    validate_atom(cue.get("atom"))
    _number(cue.get("gain", 1.0), "cue.gain", minimum=0.0)
    if "position" in cue:
        for field in ("position", "listener"):
            point = cue.get(field, {"x": 0, "y": 0, "z": 0})
            if not isinstance(point, dict):
                raise AudioRecipeError(f"{field} must be an object")
            for axis in ("x", "y", "z"):
                _number(point.get(axis, 0), f"{field}.{axis}")
        _number(cue.get("max_distance", 30.0), "cue.max_distance", minimum=0.001)


def _waveform_sample(waveform: str, phase: float, rng: random.Random) -> float:
    if waveform == "sine":
        return math.sin(phase)
    if waveform == "square":
        return 1.0 if math.sin(phase) >= 0.0 else -1.0
    if waveform == "triangle":
        return (2.0 / math.pi) * math.asin(math.sin(phase))
    return rng.uniform(-1.0, 1.0)


def _apply_envelope(samples: list[float], sample_rate: int, env: dict[str, Any]) -> list[float]:
    n = len(samples)
    if not n:
        return samples
    attack = min(n, round(float(env.get("attack_ms", 0.0)) * sample_rate / 1000.0))
    decay = min(max(0, n - attack), round(float(env.get("decay_ms", 0.0)) * sample_rate / 1000.0))
    release = min(n, round(float(env.get("release_ms", 0.0)) * sample_rate / 1000.0))
    sustain = float(env.get("sustain_level", 1.0))

    out: list[float] = []
    release_start = max(0, n - release)
    for i, sample in enumerate(samples):
        if attack and i < attack:
            level = (i + 1) / attack
        elif decay and i < attack + decay:
            t = (i - attack + 1) / decay
            level = 1.0 + (sustain - 1.0) * t
        else:
            level = sustain

        if release and i >= release_start:
            remain = n - i - 1
            level *= max(0.0, remain / release)

        out.append(sample * level)
    return out


def _lowpass(samples: list[float], sample_rate: int, cutoff_hz: float) -> list[float]:
    dt = 1.0 / sample_rate
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    y = 0.0
    out = []
    for x in samples:
        y += alpha * (x - y)
        out.append(y)
    return out


def render_atom(atom: dict[str, Any]) -> tuple[list[float], int]:
    """Render a validated mono audio atom into float samples in [-1, 1]."""
    validate_atom(atom)
    sample_rate = int(atom.get("sample_rate", 44100))
    frames = max(1, round(float(atom["duration_ms"]) * sample_rate / 1000.0))
    source = atom["source"]
    waveform = source["waveform"]
    start_hz = float(source.get("frequency_hz", 440.0))
    end_hz = float(source.get("frequency_end_hz", start_hz))
    noise_mix = float(source.get("noise_mix", 0.0))
    seed = int(source.get("seed", 0))
    rng = random.Random(seed)

    phase = 0.0
    samples: list[float] = []
    for i in range(frames):
        t = i / max(1, frames - 1)
        hz = start_hz + (end_hz - start_hz) * t
        phase += 2.0 * math.pi * hz / sample_rate
        base = _waveform_sample(waveform, phase, rng)
        if waveform != "noise" and noise_mix:
            noise = rng.uniform(-1.0, 1.0)
            base = base * (1.0 - noise_mix) + noise * noise_mix
        samples.append(base)

    for effect in atom.get("effects", []):
        kind = effect["type"]
        if kind == "lowpass":
            samples = _lowpass(samples, sample_rate, float(effect["cutoff_hz"]))
        elif kind == "drive":
            amount = float(effect["amount"])
            denom = math.tanh(max(amount, 1e-12))
            samples = [math.tanh(s * amount) / denom if amount else s for s in samples]
        elif kind == "gain":
            amount = float(effect["amount"])
            samples = [s * amount for s in samples]

    samples = _apply_envelope(samples, sample_rate, atom.get("envelope", {}))
    return [max(-1.0, min(1.0, s)) for s in samples], sample_rate


def _spatial_gains(cue: dict[str, Any]) -> tuple[float, float]:
    gain = float(cue.get("gain", 1.0))
    if "position" not in cue:
        center = gain / math.sqrt(2.0)
        return center, center

    p = cue["position"]
    l = cue.get("listener", {"x": 0, "y": 0, "z": 0})
    dx = float(p.get("x", 0)) - float(l.get("x", 0))
    dy = float(p.get("y", 0)) - float(l.get("y", 0))
    dz = float(p.get("z", 0)) - float(l.get("z", 0))
    max_distance = float(cue.get("max_distance", 30.0))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    attenuation = max(0.0, 1.0 - distance / max_distance)
    pan = max(-1.0, min(1.0, dx / max_distance))
    angle = (pan + 1.0) * math.pi / 4.0
    return gain * attenuation * math.cos(angle), gain * attenuation * math.sin(angle)


def render_cue(cue: dict[str, Any]) -> tuple[list[tuple[float, float]], int]:
    """Render a runtime cue to stereo using distance attenuation + equal-power pan."""
    validate_cue(cue)
    mono, sample_rate = render_atom(cue["atom"])
    left_gain, right_gain = _spatial_gains(cue)
    stereo = [
        (
            max(-1.0, min(1.0, s * left_gain)),
            max(-1.0, min(1.0, s * right_gain)),
        )
        for s in mono
    ]
    return stereo, sample_rate


def pcm16_bytes(stereo: Iterable[tuple[float, float]]) -> bytes:
    payload = bytearray()
    for index, (left, right) in enumerate(stereo):
        left = _number(left, f"PCM frame {index} left")
        right = _number(right, f"PCM frame {index} right")
        li = round(max(-1.0, min(1.0, left)) * 32767.0)
        ri = round(max(-1.0, min(1.0, right)) * 32767.0)
        payload.extend(struct.pack("<hh", li, ri))
    return bytes(payload)


def build_receipt(cue: dict[str, Any], stereo: list[tuple[float, float]], sample_rate: int) -> dict[str, Any]:
    payload = pcm16_bytes(stereo)
    flat = [v for pair in stereo for v in pair]
    peak = max((abs(v) for v in flat), default=0.0)
    rms = math.sqrt(sum(v * v for v in flat) / len(flat)) if flat else 0.0
    return {
        "schema": RECEIPT_SCHEMA,
        "cue_sha256": canonical_sha256(cue),
        "pcm16_sha256": hashlib.sha256(payload).hexdigest(),
        "sample_rate": sample_rate,
        "channels": 2,
        "frames": len(stereo),
        "duration_seconds": len(stereo) / sample_rate,
        "peak": peak,
        "rms": rms,
        "truth_boundary": "Structural/signal evidence only; no listening-quality claim.",
    }


def render_wav(cue: dict[str, Any], wav_path: str | Path, receipt_path: str | Path | None = None) -> dict[str, Any]:
    """Render a cue to 16-bit stereo WAV and optionally write a JSON receipt."""
    stereo, sample_rate = render_cue(cue)
    payload = pcm16_bytes(stereo)
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(payload)

    receipt = build_receipt(cue, stereo, sample_rate)
    if receipt_path is not None:
        receipt_path = Path(receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt

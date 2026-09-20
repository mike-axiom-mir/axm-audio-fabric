"""Deterministic layered SFX composition for AXM Audio Fabric.

This module combines existing ``axm.audio-cue/v1`` cues into one explicit
layered sound recipe. It proves structural validity, deterministic mixing, and
signal/render evidence only. It does not prove perceptual or production
quality.
"""
from __future__ import annotations

import hashlib
import json
import math
import wave
from pathlib import Path
from typing import Any

from .core import AudioRecipeError, canonical_sha256, pcm16_bytes, render_cue, validate_cue


LAYERED_CUE_SCHEMA = "axm.audio-layered-cue/v1"
LAYERED_RECEIPT_SCHEMA = "axm.audio-layered-render-receipt/v1"


def _finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AudioRecipeError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise AudioRecipeError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AudioRecipeError(f"{name} must be >= {minimum}")
    return value


def validate_layered_cue(recipe: dict[str, Any]) -> None:
    """Validate an explicit layered SFX recipe."""
    if not isinstance(recipe, dict):
        raise AudioRecipeError("layered cue must be an object")
    if recipe.get("schema") != LAYERED_CUE_SCHEMA:
        raise AudioRecipeError(f"schema must be {LAYERED_CUE_SCHEMA!r}")
    if not isinstance(recipe.get("id"), str) or not recipe["id"].strip():
        raise AudioRecipeError("id must be a non-empty string")

    _finite_number(recipe.get("master_gain", 1.0), "master_gain", minimum=0.0)
    layers = recipe.get("layers")
    if not isinstance(layers, list) or not layers:
        raise AudioRecipeError("layers must be a non-empty array")

    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise AudioRecipeError(f"layers[{index}] must be an object")
        validate_cue(layer.get("cue"))
        _finite_number(layer.get("offset_ms", 0.0), f"layers[{index}].offset_ms", minimum=0.0)
        _finite_number(layer.get("gain", 1.0), f"layers[{index}].gain", minimum=0.0)


def _render_layered_mix(
    recipe: dict[str, Any],
) -> tuple[list[tuple[float, float]], int, float, int]:
    validate_layered_cue(recipe)

    sample_rate: int | None = None
    rendered: list[tuple[int, float, list[tuple[float, float]]]] = []
    total_frames = 0

    for index, layer in enumerate(recipe["layers"]):
        stereo, layer_rate = render_cue(layer["cue"])
        if sample_rate is None:
            sample_rate = layer_rate
        elif layer_rate != sample_rate:
            raise AudioRecipeError(
                f"layers[{index}] sample_rate {layer_rate} does not match {sample_rate}; "
                "resampling is not implemented in layered cue v1"
            )

        offset_frames = round(float(layer.get("offset_ms", 0.0)) * layer_rate / 1000.0)
        layer_gain = float(layer.get("gain", 1.0))
        rendered.append((offset_frames, layer_gain, stereo))
        total_frames = max(total_frames, offset_frames + len(stereo))

    assert sample_rate is not None
    mix = [[0.0, 0.0] for _ in range(total_frames)]
    for offset_frames, layer_gain, stereo in rendered:
        for frame_index, (left, right) in enumerate(stereo):
            target = offset_frames + frame_index
            mix[target][0] += left * layer_gain
            mix[target][1] += right * layer_gain

    master_gain = float(recipe.get("master_gain", 1.0))
    preclip_peak = 0.0
    clipped_channel_sample_count = 0
    output: list[tuple[float, float]] = []
    for left, right in mix:
        left *= master_gain
        right *= master_gain
        preclip_peak = max(preclip_peak, abs(left), abs(right))
        if abs(left) > 1.0:
            clipped_channel_sample_count += 1
        if abs(right) > 1.0:
            clipped_channel_sample_count += 1
        output.append((max(-1.0, min(1.0, left)), max(-1.0, min(1.0, right))))

    return output, sample_rate, preclip_peak, clipped_channel_sample_count


def render_layered_cue(recipe: dict[str, Any]) -> tuple[list[tuple[float, float]], int]:
    """Render a layered SFX recipe to deterministic clipped stereo floats."""
    stereo, sample_rate, _, _ = _render_layered_mix(recipe)
    return stereo, sample_rate


def render_layered_wav(
    recipe: dict[str, Any],
    wav_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render a layered cue and return objective replay/signal evidence."""
    stereo, sample_rate, preclip_peak, clipped_channel_sample_count = _render_layered_mix(recipe)
    payload = pcm16_bytes(stereo)

    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(payload)

    flat = [value for pair in stereo for value in pair]
    peak = max((abs(value) for value in flat), default=0.0)
    rms = math.sqrt(sum(value * value for value in flat) / len(flat)) if flat else 0.0
    receipt = {
        "schema": LAYERED_RECEIPT_SCHEMA,
        "layered_cue_sha256": canonical_sha256(recipe),
        "pcm16_sha256": hashlib.sha256(payload).hexdigest(),
        "sample_rate": sample_rate,
        "channels": 2,
        "frames": len(stereo),
        "duration_seconds": len(stereo) / sample_rate,
        "peak": peak,
        "rms": rms,
        "preclip_peak": preclip_peak,
        "clipped_channel_sample_count": clipped_channel_sample_count,
        "truth_boundary": "Structural/signal evidence only; no listening-quality claim.",
    }

    if receipt_path is not None:
        receipt_path = Path(receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt

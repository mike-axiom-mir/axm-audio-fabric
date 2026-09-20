"""Deterministic Sound Mixer plan execution for AXM Audio Fabric.

This module is a narrow execution bridge. Sound Mixer owns edit/timeline/mix
intent; Audio Fabric only realizes an accepted ``axm.sound-mix-plan/v1`` into
a rendered WAV. v1 resolves explicit ``audio-fabric-render`` sources from
caller-supplied receipt + WAV evidence, requires one shared sample rate, and
rejects unsupported automation rather than silently ignoring it.

Truth boundary: this module can prove plan/source identity, deterministic
sample execution, rendered bytes, and bounded signal statistics. It cannot
prove that the mix sounds good or is production-suitable.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any
import wave

from .core import AudioRecipeError, canonical_sha256, pcm16_bytes


MIX_PLAN_SCHEMA = "axm.sound-mix-plan/v1"
MIX_RENDER_RECEIPT_SCHEMA = "axm.audio-mix-render-receipt/v1"
AUDIO_FABRIC_RENDER_SOURCE_KIND = "audio-fabric-render"
PCM16_STEREO_ARTIFACT_KIND = "audio/wav-pcm16-stereo"


def _finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AudioRecipeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise AudioRecipeError(f"{name} must be a finite number")
    if minimum is not None and result < minimum:
        raise AudioRecipeError(f"{name} must be >= {minimum}")
    return result


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AudioRecipeError(f"{name} must be a non-empty string")
    return value


def _ms_to_frames(milliseconds: float, sample_rate: int) -> int:
    value = (Decimal(str(milliseconds)) * Decimal(sample_rate)) / Decimal(1000)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def validate_mix_plan(plan: dict[str, Any]) -> None:
    """Validate Mixer-owned render intent without silently changing semantics."""
    if not isinstance(plan, dict):
        raise AudioRecipeError("mix plan must be an object")
    if plan.get("schema") != MIX_PLAN_SCHEMA:
        raise AudioRecipeError(f"plan.schema must be {MIX_PLAN_SCHEMA!r}")
    _non_empty_string(plan.get("projectId"), "plan.projectId")
    revision = plan.get("projectRevision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise AudioRecipeError("plan.projectRevision must be a non-negative integer")
    sample_rate = plan.get("sampleRate")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate < 8000 or sample_rate > 192000:
        raise AudioRecipeError("plan.sampleRate must be an integer between 8000 and 192000")
    events = plan.get("events")
    if not isinstance(events, list):
        raise AudioRecipeError("plan.events must be an array")
    automation = plan.get("automation")
    if not isinstance(automation, list):
        raise AudioRecipeError("plan.automation must be an array")

    placement_ids: set[str] = set()
    for index, event in enumerate(events):
        label = f"plan.events[{index}]"
        if not isinstance(event, dict):
            raise AudioRecipeError(f"{label} must be an object")
        placement_id = _non_empty_string(event.get("placementId"), f"{label}.placementId")
        if placement_id in placement_ids:
            raise AudioRecipeError(f"duplicate placementId {placement_id!r}")
        placement_ids.add(placement_id)
        _non_empty_string(event.get("trackId"), f"{label}.trackId")
        source = event.get("source")
        if not isinstance(source, dict):
            raise AudioRecipeError(f"{label}.source must be an object")
        _non_empty_string(source.get("kind"), f"{label}.source.kind")
        _non_empty_string(source.get("ref"), f"{label}.source.ref")

        _finite_number(event.get("startMs"), f"{label}.startMs", minimum=0.0)
        _finite_number(event.get("sourceOffsetMs"), f"{label}.sourceOffsetMs", minimum=0.0)
        duration = _finite_number(event.get("durationMs"), f"{label}.durationMs", minimum=0.0)
        if duration <= 0.0:
            raise AudioRecipeError(f"{label}.durationMs must be > 0")
        fade_in = _finite_number(event.get("fadeInMs"), f"{label}.fadeInMs", minimum=0.0)
        fade_out = _finite_number(event.get("fadeOutMs"), f"{label}.fadeOutMs", minimum=0.0)
        if fade_in + fade_out > duration + 1e-9:
            raise AudioRecipeError(f"{label} combined fades cannot exceed durationMs")
        _finite_number(event.get("clipGain"), f"{label}.clipGain", minimum=0.0)
        _finite_number(event.get("trackGain"), f"{label}.trackGain", minimum=0.0)
        pan = _finite_number(event.get("pan"), f"{label}.pan")
        if pan < -1.0 or pan > 1.0:
            raise AudioRecipeError(f"{label}.pan must be between -1 and 1")
        if not isinstance(event.get("loop"), bool):
            raise AudioRecipeError(f"{label}.loop must be boolean")
        if not isinstance(event.get("audible"), bool):
            raise AudioRecipeError(f"{label}.audible must be boolean")


def _source_receipt_artifact(receipt: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise AudioRecipeError("source receipt must be an object")
    receipt_schema = _non_empty_string(receipt.get("schema"), "source receipt.schema")
    artifact = receipt.get("rendered_artifact")
    if not isinstance(artifact, dict):
        raise AudioRecipeError("source receipt.rendered_artifact must be an object")
    if artifact.get("kind") != PCM16_STEREO_ARTIFACT_KIND:
        raise AudioRecipeError(
            f"source artifact.kind must be {PCM16_STEREO_ARTIFACT_KIND!r} in mix-plan v1"
        )
    content_sha256 = _non_empty_string(artifact.get("content_sha256"), "source artifact.content_sha256")
    if len(content_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in content_sha256):
        raise AudioRecipeError("source artifact.content_sha256 must be lowercase sha256 hex")
    if artifact.get("id") != f"sha256:{content_sha256}":
        raise AudioRecipeError("source artifact.id must match content_sha256")
    if source.get("ref") != artifact["id"]:
        raise AudioRecipeError("Mixer source.ref does not match supplied Audio Fabric receipt artifact")

    sample_rate = artifact.get("sample_rate")
    channels = artifact.get("channels")
    frames = artifact.get("frames")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise AudioRecipeError("source artifact.sample_rate must be a positive integer")
    if channels != 2:
        raise AudioRecipeError("mix-plan v1 requires stereo Audio Fabric source artifacts")
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        raise AudioRecipeError("source artifact.frames must be a positive integer")

    provenance = source.get("provenance")
    if not isinstance(provenance, dict):
        raise AudioRecipeError("Mixer audio-fabric-render source requires provenance")
    if provenance.get("receiptSchema") != receipt_schema:
        raise AudioRecipeError("Mixer source provenance receiptSchema does not match supplied receipt")
    provenance_artifact = provenance.get("artifact")
    if not isinstance(provenance_artifact, dict):
        raise AudioRecipeError("Mixer source provenance.artifact must be an object")
    expected = {
        "kind": artifact["kind"],
        "contentSha256": content_sha256,
        "sampleRate": sample_rate,
        "channels": channels,
        "frames": frames,
    }
    for key, value in expected.items():
        if provenance_artifact.get(key) != value:
            raise AudioRecipeError(f"Mixer source provenance.artifact.{key} disagrees with supplied receipt")
    return artifact


def _read_source_wav(
    source: dict[str, Any],
    evidence: dict[str, Any],
    expected_sample_rate: int,
) -> tuple[list[tuple[float, float]], dict[str, Any], dict[str, Any]]:
    if source.get("kind") != AUDIO_FABRIC_RENDER_SOURCE_KIND:
        raise AudioRecipeError(
            f"unsupported Mixer source.kind {source.get('kind')!r}; mix-plan v1 resolves only "
            f"{AUDIO_FABRIC_RENDER_SOURCE_KIND!r}"
        )
    if not isinstance(evidence, dict):
        raise AudioRecipeError("source evidence must be an object with wav_path and receipt")
    wav_path_raw = evidence.get("wav_path")
    if not isinstance(wav_path_raw, (str, Path)):
        raise AudioRecipeError("source evidence.wav_path must be a path")
    receipt = evidence.get("receipt")
    artifact = _source_receipt_artifact(receipt, source)
    if artifact["sample_rate"] != expected_sample_rate:
        raise AudioRecipeError(
            f"source sample_rate {artifact['sample_rate']} does not match mix plan {expected_sample_rate}; "
            "resampling is not implemented in mix-plan v1"
        )

    wav_path = Path(wav_path_raw)
    try:
        with wave.open(str(wav_path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            payload = handle.readframes(frame_count)
    except (OSError, wave.Error) as exc:
        raise AudioRecipeError(f"cannot read source WAV {wav_path}: {exc}") from exc

    if channels != 2 or sample_width != 2:
        raise AudioRecipeError("mix-plan v1 source WAV must be 16-bit stereo PCM")
    if sample_rate != artifact["sample_rate"]:
        raise AudioRecipeError("source WAV sample rate disagrees with receipt")
    if frame_count != artifact["frames"]:
        raise AudioRecipeError("source WAV frame count disagrees with receipt")
    pcm_sha256 = hashlib.sha256(payload).hexdigest()
    if pcm_sha256 != artifact["content_sha256"]:
        raise AudioRecipeError("source WAV PCM hash disagrees with receipt artifact identity")

    samples: list[tuple[float, float]] = []
    for left, right in struct.iter_unpack("<hh", payload):
        samples.append(
            (
                max(-1.0, min(1.0, left / 32767.0)),
                max(-1.0, min(1.0, right / 32767.0)),
            )
        )
    return samples, receipt, artifact


def _balance_gains(pan: float) -> tuple[float, float]:
    """Linear stereo balance: center is identity; hard pan mutes the far side."""
    if pan > 0.0:
        return 1.0 - pan, 1.0
    if pan < 0.0:
        return 1.0, 1.0 + pan
    return 1.0, 1.0


def _fade_gain(frame_index: int, duration_frames: int, fade_in_frames: int, fade_out_frames: int) -> float:
    gain = 1.0
    if fade_in_frames > 0:
        gain *= min(1.0, frame_index / fade_in_frames)
    if fade_out_frames > 0:
        remaining = duration_frames - frame_index - 1
        gain *= min(1.0, max(0, remaining) / fade_out_frames)
    return gain


def render_mix_plan_wav(
    plan: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    wav_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute a Sound Mixer plan against explicit Audio Fabric artifacts.

    ``sources`` is keyed by ``source.ref``. Each value must contain ``wav_path``
    plus the exact Audio Fabric receipt that identifies those PCM bytes.
    """
    validate_mix_plan(plan)
    if plan["automation"]:
        raise AudioRecipeError(
            "mix-plan v1 does not execute automation yet; refusing to ignore Mixer automation intent"
        )
    if not plan["events"]:
        raise AudioRecipeError("mix-plan v1 requires at least one placement event")
    if not isinstance(sources, dict):
        raise AudioRecipeError("sources must be an object keyed by Audio Fabric artifact id")

    sample_rate = plan["sampleRate"]
    resolved_sources: dict[str, tuple[list[tuple[float, float]], dict[str, Any], dict[str, Any]]] = {}
    for event in plan["events"]:
        source = event["source"]
        source_ref = source["ref"]
        if source_ref not in sources:
            raise AudioRecipeError(f"missing source evidence for {source_ref!r}")
        if source_ref not in resolved_sources:
            resolved_sources[source_ref] = _read_source_wav(source, sources[source_ref], sample_rate)
        else:
            _source_receipt_artifact(resolved_sources[source_ref][1], source)

    resolved_events: list[dict[str, Any]] = []
    total_frames = 0
    for event in plan["events"]:
        start_frame = _ms_to_frames(float(event["startMs"]), sample_rate)
        source_offset_frame = _ms_to_frames(float(event["sourceOffsetMs"]), sample_rate)
        duration_frames = _ms_to_frames(float(event["durationMs"]), sample_rate)
        if duration_frames <= 0:
            raise AudioRecipeError(
                f"placement {event['placementId']!r} duration resolves to zero frames at {sample_rate} Hz"
            )
        samples = resolved_sources[event["source"]["ref"]][0]
        if source_offset_frame >= len(samples):
            raise AudioRecipeError(f"placement {event['placementId']!r} sourceOffsetMs is outside source artifact")
        if not event["loop"] and source_offset_frame + duration_frames > len(samples):
            raise AudioRecipeError(
                f"placement {event['placementId']!r} extends beyond source artifact without loop=true"
            )
        fade_in_frames = _ms_to_frames(float(event["fadeInMs"]), sample_rate)
        fade_out_frames = _ms_to_frames(float(event["fadeOutMs"]), sample_rate)
        total_frames = max(total_frames, start_frame + duration_frames)
        resolved_events.append(
            {
                "event": event,
                "start_frame": start_frame,
                "source_offset_frame": source_offset_frame,
                "duration_frames": duration_frames,
                "fade_in_frames": fade_in_frames,
                "fade_out_frames": fade_out_frames,
            }
        )

    mix = [[0.0, 0.0] for _ in range(total_frames)]
    execution_events: list[dict[str, Any]] = []
    for resolved in resolved_events:
        event = resolved["event"]
        source_ref = event["source"]["ref"]
        source_samples = resolved_sources[source_ref][0]
        start_frame = resolved["start_frame"]
        source_offset_frame = resolved["source_offset_frame"]
        duration_frames = resolved["duration_frames"]
        fade_in_frames = resolved["fade_in_frames"]
        fade_out_frames = resolved["fade_out_frames"]
        effective_gain = float(event["clipGain"]) * float(event["trackGain"])
        left_balance, right_balance = _balance_gains(float(event["pan"]))

        if event["audible"]:
            for frame_index in range(duration_frames):
                if event["loop"]:
                    source_index = (source_offset_frame + frame_index) % len(source_samples)
                else:
                    source_index = source_offset_frame + frame_index
                left, right = source_samples[source_index]
                envelope = _fade_gain(frame_index, duration_frames, fade_in_frames, fade_out_frames)
                target = start_frame + frame_index
                mix[target][0] += left * effective_gain * left_balance * envelope
                mix[target][1] += right * effective_gain * right_balance * envelope

        execution_events.append(
            {
                "placement_id": event["placementId"],
                "track_id": event["trackId"],
                "source_ref": source_ref,
                "start_frame": start_frame,
                "source_offset_frame": source_offset_frame,
                "duration_frames": duration_frames,
                "loop": event["loop"],
                "fade_in_frames": fade_in_frames,
                "fade_out_frames": fade_out_frames,
                "effective_gain": effective_gain,
                "pan": float(event["pan"]),
                "audible": event["audible"],
            }
        )

    preclip_peak = 0.0
    clipped_channel_sample_count = 0
    stereo: list[tuple[float, float]] = []
    for left, right in mix:
        preclip_peak = max(preclip_peak, abs(left), abs(right))
        if abs(left) > 1.0:
            clipped_channel_sample_count += 1
        if abs(right) > 1.0:
            clipped_channel_sample_count += 1
        stereo.append((max(-1.0, min(1.0, left)), max(-1.0, min(1.0, right))))

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
    pcm_sha256 = hashlib.sha256(payload).hexdigest()
    source_receipts = []
    for source_ref in sorted(resolved_sources):
        _, source_receipt, artifact = resolved_sources[source_ref]
        source_receipts.append(
            {
                "source_ref": source_ref,
                "receipt_schema": source_receipt["schema"],
                "receipt_sha256": canonical_sha256(source_receipt),
                "artifact_content_sha256": artifact["content_sha256"],
                "frames": artifact["frames"],
            }
        )

    receipt = {
        "schema": MIX_RENDER_RECEIPT_SCHEMA,
        "mix_plan_sha256": canonical_sha256(plan),
        "project_id": plan["projectId"],
        "project_revision": plan["projectRevision"],
        "source_artifacts": source_receipts,
        "execution": {
            "millisecond_to_frame_rounding": "decimal half-even",
            "pan_law": "linear stereo balance; center identity, hard pan mutes far side",
            "automation": "unsupported in v1; non-empty automation is rejected",
            "events": execution_events,
        },
        "rendered_artifact": {
            "kind": PCM16_STEREO_ARTIFACT_KIND,
            "id": f"sha256:{pcm_sha256}",
            "content_sha256": pcm_sha256,
            "sample_rate": sample_rate,
            "channels": 2,
            "frames": len(stereo),
            "duration_seconds": len(stereo) / sample_rate,
        },
        "signal": {
            "peak": peak,
            "rms": rms,
            "preclip_peak": preclip_peak,
            "clipped_channel_sample_count": clipped_channel_sample_count,
        },
        "truth_boundary": (
            "Deterministic Mixer-plan execution + source identity + rendered-byte/signal evidence only; "
            "no listening-quality, creative-acceptance, runtime-playback, or production-suitability claim."
        ),
    }
    if receipt_path is not None:
        receipt_path = Path(receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt

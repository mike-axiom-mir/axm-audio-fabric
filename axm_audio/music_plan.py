"""Explicit Music Maker render-plan realization for AXM Audio Fabric.

This module is the narrow bridge from structured musical intent into Audio
Fabric realization. It does not compose music, infer instruments, choose voice
performances, or own mixer placement. Every note must carry an explicit
caller-provided binding to a validated Audio Fabric realization profile.

Truth boundary: deterministic translation, rendered bytes, and signal receipts
are objective evidence. They are not evidence that the music sounds good.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN
import json
from pathlib import Path
from typing import Any

from .core import AUDIO_ATOM_SCHEMA, AUDIO_CUE_SCHEMA, AudioRecipeError, canonical_sha256, validate_atom
from .layered import LAYERED_CUE_SCHEMA, render_layered_wav


MUSIC_RENDER_PLAN_SCHEMA = "axm.music-audio-render-plan/v1"
REALIZATION_PROFILE_SCHEMA = "axm.audio-realization-profile/v1"
MUSIC_RENDER_RECEIPT_SCHEMA = "axm.audio-music-render-receipt/v1"


def _finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AudioRecipeError(f"{name} must be a finite number")
    value = float(value)
    if value != value or value in {float("inf"), float("-inf")}:
        raise AudioRecipeError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AudioRecipeError(f"{name} must be >= {minimum}")
    return value


def tick_to_milliseconds(tick: int, tempo_bpm: int | float, ppq: int) -> float:
    """Convert section-relative ticks to milliseconds at fixed 1 ns resolution.

    Decimal conversion from the canonical string form avoids silently depending
    on binary-float arithmetic for the source tempo. The result is rounded to
    nine decimal places in milliseconds (one picosecond in seconds), which is
    far below sample resolution at supported Audio Fabric sample rates while
    giving the contract one explicit deterministic rounding rule.
    """
    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        raise AudioRecipeError("tick must be a non-negative integer")
    if not isinstance(ppq, int) or isinstance(ppq, bool) or ppq <= 0:
        raise AudioRecipeError("ppq must be a positive integer")
    tempo = _finite_number(tempo_bpm, "tempo_bpm", minimum=0.000001)
    value = (Decimal(tick) * Decimal(60000)) / (Decimal(str(tempo)) * Decimal(ppq))
    return float(value.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_EVEN))


def validate_realization_profile(profile: dict[str, Any]) -> None:
    """Validate one explicit instrument realization profile."""
    if not isinstance(profile, dict):
        raise AudioRecipeError("realization profile must be an object")
    if profile.get("schema") != REALIZATION_PROFILE_SCHEMA:
        raise AudioRecipeError(f"profile.schema must be {REALIZATION_PROFILE_SCHEMA!r}")
    if not isinstance(profile.get("id"), str) or not profile["id"].strip():
        raise AudioRecipeError("profile.id must be a non-empty string")
    if profile.get("kind") != "instrument":
        raise AudioRecipeError("profile.kind must be 'instrument' in v1")
    waveform = profile.get("waveform")
    if waveform not in {"sine", "square", "triangle"}:
        raise AudioRecipeError("profile.waveform must be sine, square, or triangle in v1")
    sample_rate = profile.get("sample_rate", 44100)
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise AudioRecipeError("profile.sample_rate must be an integer")
    gain = _finite_number(profile.get("gain", 1.0), "profile.gain", minimum=0.0)
    if gain > 8.0:
        raise AudioRecipeError("profile.gain must be <= 8")

    # Reuse the Audio Atom validator for envelope/effect/sample-rate semantics.
    probe = {
        "schema": AUDIO_ATOM_SCHEMA,
        "id": "profile-validation-probe",
        "sample_rate": sample_rate,
        "duration_ms": 10.0,
        "source": {"waveform": waveform, "frequency_hz": 440.0},
        "envelope": deepcopy(profile.get("envelope", {})),
        "effects": deepcopy(profile.get("effects", [])),
    }
    validate_atom(probe)


def _profile_index(profiles: list[dict[str, Any]] | dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if isinstance(profiles, dict):
        values = list(profiles.values())
    elif isinstance(profiles, list):
        values = profiles
    else:
        raise AudioRecipeError("profiles must be an array or object map")

    indexed: dict[str, dict[str, Any]] = {}
    for profile in values:
        validate_realization_profile(profile)
        profile_id = profile["id"]
        if profile_id in indexed:
            raise AudioRecipeError(f"duplicate realization profile id {profile_id!r}")
        indexed[profile_id] = deepcopy(profile)
    return indexed


def _validate_plan_header(plan: dict[str, Any]) -> tuple[float, int]:
    if not isinstance(plan, dict):
        raise AudioRecipeError("music render plan must be an object")
    if plan.get("schema") != MUSIC_RENDER_PLAN_SCHEMA:
        raise AudioRecipeError(f"plan.schema must be {MUSIC_RENDER_PLAN_SCHEMA!r}")
    if plan.get("target") != "axm-audio-fabric":
        raise AudioRecipeError("plan.target must be 'axm-audio-fabric'")
    if plan.get("timing_scope") != "section_relative_ticks":
        raise AudioRecipeError("plan.timing_scope must be 'section_relative_ticks'")
    tempo = _finite_number(plan.get("tempo_bpm"), "plan.tempo_bpm", minimum=0.000001)
    ppq = plan.get("ppq")
    if not isinstance(ppq, int) or isinstance(ppq, bool) or ppq <= 0:
        raise AudioRecipeError("plan.ppq must be a positive integer")
    if not isinstance(plan.get("project_id"), str) or not plan["project_id"].strip():
        raise AudioRecipeError("plan.project_id must be a non-empty string")
    if not isinstance(plan.get("state_id"), str) or not plan["state_id"].strip():
        raise AudioRecipeError("plan.state_id must be a non-empty string")
    if not isinstance(plan.get("stems"), list):
        raise AudioRecipeError("plan.stems must be an array")
    return tempo, ppq


def build_music_layered_cue(
    plan: dict[str, Any],
    profiles: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Translate a fully bound Music Maker note plan into one layered audio cue.

    v1 intentionally realizes only note/instrument events. Voice performance is
    rejected rather than synthesized or selected implicitly.
    """
    tempo, ppq = _validate_plan_header(plan)
    indexed_profiles = _profile_index(profiles)
    layers: list[dict[str, Any]] = []
    schedule: list[dict[str, Any]] = []

    for stem_index, stem in enumerate(plan["stems"]):
        if not isinstance(stem, dict):
            raise AudioRecipeError(f"plan.stems[{stem_index}] must be an object")
        stem_id = stem.get("stem_id")
        if not isinstance(stem_id, str) or not stem_id.strip():
            raise AudioRecipeError(f"plan.stems[{stem_index}].stem_id must be a non-empty string")
        events = stem.get("events")
        if not isinstance(events, list):
            raise AudioRecipeError(f"stem {stem_id!r} events must be an array")

        for event_index, event in enumerate(events):
            label = f"stem {stem_id!r} event[{event_index}]"
            if not isinstance(event, dict):
                raise AudioRecipeError(f"{label} must be an object")
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise AudioRecipeError(f"{label}.event_id must be a non-empty string")
            if event.get("kind") != "note":
                raise AudioRecipeError(
                    f"{label} kind {event.get('kind')!r} is not realizable by music-plan v1; "
                    "voice performance requires an explicit future Audio Fabric contract"
                )

            start_tick = event.get("start_tick")
            duration_ticks = event.get("duration_ticks")
            if not isinstance(start_tick, int) or isinstance(start_tick, bool) or start_tick < 0:
                raise AudioRecipeError(f"{label}.start_tick must be a non-negative integer")
            if not isinstance(duration_ticks, int) or isinstance(duration_ticks, bool) or duration_ticks <= 0:
                raise AudioRecipeError(f"{label}.duration_ticks must be a positive integer")

            realization = event.get("realization")
            if not isinstance(realization, dict):
                raise AudioRecipeError(f"{label}.realization must be an object")
            if realization.get("status") != "bound":
                raise AudioRecipeError(f"{label} realization must be explicitly bound")
            if realization.get("kind") != "instrument":
                raise AudioRecipeError(f"{label} realization.kind must be 'instrument'")
            binding_ref = realization.get("binding_ref")
            if not isinstance(binding_ref, str) or not binding_ref.strip():
                raise AudioRecipeError(f"{label} bound realization requires binding_ref")
            if binding_ref not in indexed_profiles:
                raise AudioRecipeError(f"{label} references unknown binding {binding_ref!r}")
            profile = indexed_profiles[binding_ref]

            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise AudioRecipeError(f"{label}.payload must be an object")
            pitch = payload.get("pitch_midi")
            velocity = payload.get("velocity")
            if not isinstance(pitch, int) or isinstance(pitch, bool) or pitch < 0 or pitch > 127:
                raise AudioRecipeError(f"{label}.payload.pitch_midi must be an integer 0..127")
            if not isinstance(velocity, int) or isinstance(velocity, bool) or velocity < 0 or velocity > 127:
                raise AudioRecipeError(f"{label}.payload.velocity must be an integer 0..127")

            offset_ms = tick_to_milliseconds(start_tick, tempo, ppq)
            duration_ms = tick_to_milliseconds(duration_ticks, tempo, ppq)
            if duration_ms < 1.0:
                raise AudioRecipeError(
                    f"{label} resolves to {duration_ms} ms; Audio Fabric v1 atoms require >= 1 ms"
                )
            frequency_hz = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
            atom = {
                "schema": AUDIO_ATOM_SCHEMA,
                "id": f"music:{plan['project_id']}:{event_id}",
                "sample_rate": int(profile.get("sample_rate", 44100)),
                "duration_ms": duration_ms,
                "source": {
                    "waveform": profile["waveform"],
                    "frequency_hz": frequency_hz,
                },
                "envelope": deepcopy(profile.get("envelope", {})),
                "effects": deepcopy(profile.get("effects", [])),
            }
            validate_atom(atom)
            event_gain = float(profile.get("gain", 1.0)) * (velocity / 127.0)
            cue = {"schema": AUDIO_CUE_SCHEMA, "atom": atom, "gain": 1.0}
            layers.append({"cue": cue, "offset_ms": offset_ms, "gain": event_gain})
            schedule.append(
                {
                    "stem_id": stem_id,
                    "event_id": event_id,
                    "binding_ref": binding_ref,
                    "start_tick": start_tick,
                    "duration_ticks": duration_ticks,
                    "offset_ms": offset_ms,
                    "duration_ms": duration_ms,
                    "pitch_midi": pitch,
                    "frequency_hz": frequency_hz,
                    "velocity": velocity,
                    "event_gain": event_gain,
                }
            )

    if not layers:
        raise AudioRecipeError("music render plan contains no realizable note events")

    plan_hash = canonical_sha256(plan)
    recipe = {
        "schema": LAYERED_CUE_SCHEMA,
        "id": f"music-plan:{plan['project_id']}:{plan['state_id']}:{plan_hash[:16]}",
        "master_gain": 1.0,
        "layers": layers,
    }
    return recipe, schedule


def render_music_plan_wav(
    plan: dict[str, Any],
    profiles: list[dict[str, Any]] | dict[str, dict[str, Any]],
    wav_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render one explicitly bound Music Maker plan and return a stable receipt."""
    indexed_profiles = _profile_index(profiles)
    recipe, schedule = build_music_layered_cue(plan, indexed_profiles)
    layered_receipt = render_layered_wav(recipe, wav_path)
    used_binding_refs = sorted({entry["binding_ref"] for entry in schedule})
    binding_receipts = [
        {
            "binding_ref": binding_ref,
            "profile_sha256": canonical_sha256(indexed_profiles[binding_ref]),
        }
        for binding_ref in used_binding_refs
    ]
    pcm_sha256 = layered_receipt["pcm16_sha256"]
    artifact_id = f"sha256:{pcm_sha256}"
    receipt = {
        "schema": MUSIC_RENDER_RECEIPT_SCHEMA,
        "music_plan_sha256": canonical_sha256(plan),
        "project_id": plan["project_id"],
        "project_sha256": plan.get("project_sha256"),
        "state_id": plan["state_id"],
        "section_id": plan.get("section_id"),
        "timing": {
            "tempo_bpm": plan["tempo_bpm"],
            "ppq": plan["ppq"],
            "scope": "section_relative_ticks",
            "rounding": "decimal milliseconds, 9 fractional digits, half-even",
        },
        "bindings": binding_receipts,
        "event_schedule": schedule,
        "rendered_artifact": {
            "kind": "audio/wav-pcm16-stereo",
            "id": artifact_id,
            "content_sha256": pcm_sha256,
            "sample_rate": layered_receipt["sample_rate"],
            "channels": layered_receipt["channels"],
            "frames": layered_receipt["frames"],
            "duration_seconds": layered_receipt["duration_seconds"],
        },
        "signal": {
            "peak": layered_receipt["peak"],
            "rms": layered_receipt["rms"],
            "preclip_peak": layered_receipt["preclip_peak"],
            "clipped_channel_sample_count": layered_receipt["clipped_channel_sample_count"],
        },
        "layered_cue_sha256": layered_receipt["layered_cue_sha256"],
        "truth_boundary": (
            "Explicit binding + deterministic translation/render evidence only; "
            "no listening-quality, musical-quality, or production-suitability claim."
        ),
    }
    if receipt_path is not None:
        path = Path(receipt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt

"""Explicit Gameplay Ability -> Audio Fabric runtime cue bridge.

Gameplay Ability Fabric owns authored action timing and emits deterministic
runtime cue requests. Audio Fabric owns sound realization. This module validates
one externally supplied audio request, requires an explicit caller-provided
binding to an existing ``axm.audio-cue/v1`` recipe, renders that recipe through
the existing Audio Fabric core, and returns evidence that preserves both sides
of the boundary.

No sound design is inferred from cue names. A rendered WAV proves execution and
signal properties only; it does not prove device playback, listening quality,
game feel, or production suitability.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import (
    AUDIO_CUE_SCHEMA,
    AudioRecipeError,
    canonical_sha256,
    render_wav,
    validate_cue,
)


GAMEPLAY_RUNTIME_CUE_REQUEST_SCHEMA = "axm.game-ability-runtime-cue-request/v1"
GAMEPLAY_AUDIO_BINDING_SCHEMA = "axm.audio-gameplay-cue-binding/v1"
GAMEPLAY_AUDIO_EXECUTION_RECEIPT_SCHEMA = "axm.audio-gameplay-runtime-receipt/v1"
PCM16_STEREO_ARTIFACT_KIND = "audio/wav-pcm16-stereo"


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AudioRecipeError(f"{name} must be a non-empty string")
    return value


def _lower_sha256(value: Any, name: str) -> str:
    text = _non_empty_string(value, name)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise AudioRecipeError(f"{name} must be lowercase sha256 hex")
    return text


def validate_gameplay_audio_request(request: dict[str, Any]) -> str:
    """Validate one Gameplay Ability Fabric audio request and return authored cue id."""
    if not isinstance(request, dict):
        raise AudioRecipeError("gameplay runtime request must be an object")
    if request.get("schema") != GAMEPLAY_RUNTIME_CUE_REQUEST_SCHEMA:
        raise AudioRecipeError(
            f"request.schema must be {GAMEPLAY_RUNTIME_CUE_REQUEST_SCHEMA!r}"
        )
    if request.get("type") != "audio-cue-request" or request.get("cueType") != "audio":
        raise AudioRecipeError("Gameplay runtime request must be an audio-cue-request")

    _non_empty_string(request.get("dispatchKey"), "request.dispatchKey")
    _non_empty_string(request.get("actionInstanceId"), "request.actionInstanceId")
    _non_empty_string(request.get("abilityId"), "request.abilityId")
    _non_empty_string(request.get("trackId"), "request.trackId")
    _non_empty_string(request.get("eventId"), "request.eventId")

    event = request.get("event")
    if not isinstance(event, dict):
        raise AudioRecipeError("request.event must be an object")
    if event.get("id") != request["eventId"]:
        raise AudioRecipeError("request.event.id must match request.eventId")
    if event.get("track") != request["trackId"]:
        raise AudioRecipeError("request.event.track must match request.trackId")
    if event.get("type") != "audio":
        raise AudioRecipeError("request.event.type must be audio")
    authored_cue = _non_empty_string(event.get("cue"), "request.event.cue")

    receipt = request.get("receipt")
    if not isinstance(receipt, dict):
        raise AudioRecipeError("request.receipt must be an object")
    receipt_sha256 = _lower_sha256(receipt.get("sha256"), "request.receipt.sha256")
    if receipt.get("deterministic") is not True:
        raise AudioRecipeError("request.receipt.deterministic must be true")

    body = dict(request)
    body.pop("receipt", None)
    if canonical_sha256(body) != receipt_sha256:
        raise AudioRecipeError("Gameplay runtime request receipt mismatch")
    return authored_cue


def validate_gameplay_audio_binding(
    binding: dict[str, Any], *, authored_cue: str | None = None
) -> None:
    """Validate an explicit caller binding without inferring any sound design."""
    if not isinstance(binding, dict):
        raise AudioRecipeError("gameplay audio binding must be an object")
    if binding.get("schema") != GAMEPLAY_AUDIO_BINDING_SCHEMA:
        raise AudioRecipeError(
            f"binding.schema must be {GAMEPLAY_AUDIO_BINDING_SCHEMA!r}"
        )
    bound_cue = _non_empty_string(binding.get("authored_cue"), "binding.authored_cue")
    if authored_cue is not None and bound_cue != authored_cue:
        raise AudioRecipeError(
            "explicit gameplay audio binding does not match request.event.cue"
        )
    if not isinstance(binding.get("provenance"), dict):
        raise AudioRecipeError("binding.provenance must be an object")
    audio_cue = binding.get("audio_cue")
    if not isinstance(audio_cue, dict):
        raise AudioRecipeError("binding.audio_cue must be an object")
    if audio_cue.get("schema") != AUDIO_CUE_SCHEMA:
        raise AudioRecipeError(f"binding.audio_cue.schema must be {AUDIO_CUE_SCHEMA!r}")
    validate_cue(audio_cue)


def render_gameplay_audio_request(
    request: dict[str, Any],
    binding: dict[str, Any],
    wav_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render one explicitly bound Gameplay Ability audio request.

    The returned execution id is a content identity for the receipt body. It is
    suitable for carrying back as ``externalReceipt.source.receipt`` through
    Gameplay Ability Fabric's own receipt-binding API.
    """
    authored_cue = validate_gameplay_audio_request(request)
    validate_gameplay_audio_binding(binding, authored_cue=authored_cue)

    audio_cue = binding["audio_cue"]
    base_receipt = render_wav(audio_cue, wav_path)
    artifact_id = f"sha256:{base_receipt['pcm16_sha256']}"

    body = {
        "schema": GAMEPLAY_AUDIO_EXECUTION_RECEIPT_SCHEMA,
        "status": "executed",
        "gameplay_request": {
            "schema": request["schema"],
            "request_sha256": request["receipt"]["sha256"],
            "dispatch_key": request["dispatchKey"],
            "action_instance_id": request["actionInstanceId"],
            "ability_id": request["abilityId"],
            "track_id": request["trackId"],
            "event_id": request["eventId"],
            "event_time": request["eventTime"],
            "authored_cue": authored_cue,
        },
        "binding": {
            "schema": binding["schema"],
            "binding_sha256": canonical_sha256(binding),
            "provenance": binding["provenance"],
        },
        "recipe": {
            "schema": AUDIO_CUE_SCHEMA,
            "cue_sha256": canonical_sha256(audio_cue),
        },
        "rendered_artifact": {
            "kind": PCM16_STEREO_ARTIFACT_KIND,
            "id": artifact_id,
            "content_sha256": base_receipt["pcm16_sha256"],
            "sample_rate": base_receipt["sample_rate"],
            "channels": base_receipt["channels"],
            "frames": base_receipt["frames"],
            "duration_seconds": base_receipt["duration_seconds"],
        },
        "signal": {
            "peak": base_receipt["peak"],
            "rms": base_receipt["rms"],
        },
        "truth_boundary": (
            "Exact request + explicit binding + deterministic offline render evidence only; "
            "no audible-device-playback, listening-quality, game-feel, or production claim."
        ),
    }
    receipt = {
        **body,
        "execution_id": f"sha256:{canonical_sha256(body)}",
    }

    if receipt_path is not None:
        path = Path(receipt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return receipt

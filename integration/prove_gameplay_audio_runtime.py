#!/usr/bin/env python3
"""Prove Gameplay Ability runtime request -> Audio Fabric -> bound external receipt.

This uses the pinned Gameplay Ability Fabric public API to emit and re-bind the
request. Audio Fabric receives only the explicit request plus a caller-authored
binding; no cue-name-to-sound inference occurs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axm_audio import (  # noqa: E402
    AUDIO_ATOM_SCHEMA,
    AUDIO_CUE_SCHEMA,
    GAMEPLAY_AUDIO_BINDING_SCHEMA,
    render_gameplay_audio_request,
)


OUT = Path(os.environ.get("AXM_AUDIO_PROOF_OUT", "out/gameplay-audio-runtime"))
GAMEPLAY_ROOT = Path(
    os.environ.get(
        "AXM_GAMEPLAY_ABILITY_ROOT",
        "integration/vendor/axm-gameplay-ability-fabric",
    )
)
GAMEPLAY_REF = os.environ.get(
    "AXM_GAMEPLAY_ABILITY_REF",
    "654abf664a1d3e0d097ff1bb158b8e97416da5e9",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_node(script_name: str) -> None:
    env = os.environ.copy()
    env["AXM_GAMEPLAY_ABILITY_ROOT"] = str(GAMEPLAY_ROOT)
    env["AXM_AUDIO_PROOF_OUT"] = str(OUT)
    subprocess.run(
        ["node", str(REPO_ROOT / "integration" / script_name)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Actual Gameplay Ability Fabric API emits the deterministic request.
    run_node("build_gameplay_audio_request.mjs")
    request = json.loads((OUT / "gameplay-request.json").read_text(encoding="utf-8"))

    # This is an explicit caller-owned binding. The authored string
    # "blade-impact" is not interpreted by Audio Fabric.
    binding = {
        "schema": GAMEPLAY_AUDIO_BINDING_SCHEMA,
        "authored_cue": "blade-impact",
        "provenance": {
            "kind": "caller-supplied",
            "source": "cross-repo proof fixture",
            "inference": False,
            "gameplay_ability_ref": GAMEPLAY_REF,
        },
        "audio_cue": {
            "schema": AUDIO_CUE_SCHEMA,
            "gain": 0.72,
            "position": {"x": 1.5, "y": 0.0, "z": 0.0},
            "listener": {"x": 0.0, "y": 0.0, "z": 0.0},
            "max_distance": 14.0,
            "atom": {
                "schema": AUDIO_ATOM_SCHEMA,
                "id": "proof-explicit-blade-impact",
                "sample_rate": 44100,
                "duration_ms": 135.0,
                "source": {
                    "waveform": "triangle",
                    "frequency_hz": 210.0,
                    "frequency_end_hz": 78.0,
                    "noise_mix": 0.22,
                    "seed": 17022,
                },
                "envelope": {
                    "attack_ms": 1.0,
                    "decay_ms": 38.0,
                    "sustain_level": 0.24,
                    "release_ms": 60.0,
                },
                "effects": [
                    {"type": "drive", "amount": 1.7},
                    {"type": "lowpass", "cutoff_hz": 3400.0},
                    {"type": "gain", "amount": 0.72},
                ],
            },
        },
    }
    write_json(OUT / "explicit-binding.json", binding)

    first = render_gameplay_audio_request(
        request,
        binding,
        OUT / "audio-first.wav",
        OUT / "audio-first.receipt.json",
    )
    replay = render_gameplay_audio_request(
        request,
        binding,
        OUT / "audio-replay.wav",
        OUT / "audio-replay.receipt.json",
    )

    if first != replay:
        raise RuntimeError("Audio Fabric gameplay runtime receipt replay mismatch")
    if (OUT / "audio-first.wav").read_bytes() != (OUT / "audio-replay.wav").read_bytes():
        raise RuntimeError("Audio Fabric gameplay runtime WAV replay mismatch")

    external_receipt = {
        "requestSha256": request["receipt"]["sha256"],
        "dispatchKey": request["dispatchKey"],
        "status": "executed",
        "source": {
            "system": "axm-audio-fabric",
            "receipt": first["execution_id"],
        },
        "details": {
            "renderedArtifactId": first["rendered_artifact"]["id"],
            "bindingSha256": first["binding"]["binding_sha256"],
            "recipeSha256": first["recipe"]["cue_sha256"],
            "evidenceLevel": "deterministic-offline-render",
        },
    }
    write_json(OUT / "audio-external-receipt.json", external_receipt)

    # Actual Gameplay Ability Fabric API verifies the request hash and exact
    # dispatch key when binding Audio Fabric's external receipt.
    run_node("bind_gameplay_audio_receipt.mjs")
    bound = json.loads((OUT / "gameplay-bound-receipt.json").read_text(encoding="utf-8"))

    if bound["requestSha256"] != request["receipt"]["sha256"]:
        raise RuntimeError("bound Gameplay receipt request hash mismatch")
    if bound["dispatchKey"] != request["dispatchKey"]:
        raise RuntimeError("bound Gameplay receipt dispatch key mismatch")
    if bound["external"]["source"]["receipt"] != first["execution_id"]:
        raise RuntimeError("bound Gameplay receipt lost Audio Fabric execution identity")
    if bound["external"]["status"] != "executed":
        raise RuntimeError("bound Gameplay receipt did not preserve executed status")

    proof = {
        "schema": "axm.audio-gameplay-runtime-proof/v1",
        "gameplay_ability_ref": GAMEPLAY_REF,
        "request": {
            "request_sha256": request["receipt"]["sha256"],
            "dispatch_key": request["dispatchKey"],
            "ability_id": request["abilityId"],
            "event_id": request["eventId"],
            "authored_cue": request["event"]["cue"],
        },
        "audio": {
            "execution_id": first["execution_id"],
            "binding_sha256": first["binding"]["binding_sha256"],
            "recipe_sha256": first["recipe"]["cue_sha256"],
            "rendered_artifact_id": first["rendered_artifact"]["id"],
            "rendered_content_sha256": first["rendered_artifact"]["content_sha256"],
            "sample_rate": first["rendered_artifact"]["sample_rate"],
            "frames": first["rendered_artifact"]["frames"],
            "peak": first["signal"]["peak"],
            "rms": first["signal"]["rms"],
        },
        "gameplay_bound_receipt": {
            "receipt_sha256": bound["receipt"]["sha256"],
            "external_status": bound["external"]["status"],
            "external_system": bound["external"]["source"]["system"],
            "external_receipt": bound["external"]["source"]["receipt"],
        },
        "deterministic_replay": {
            "receipt_equal": first == replay,
            "wav_bytes_equal": (
                (OUT / "audio-first.wav").read_bytes()
                == (OUT / "audio-replay.wav").read_bytes()
            ),
        },
        "truth_boundary": (
            "TESTED: exact Gameplay Ability request receipt + dispatch-key continuity, "
            "explicit caller binding, deterministic Audio Fabric offline render/replay, "
            "and external receipt binding through Gameplay Ability Fabric. "
            "NOT TESTED: audible device playback, listening quality, game feel, "
            "or production suitability."
        ),
    }
    write_json(OUT / "proof.json", proof)
    print(json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    main()

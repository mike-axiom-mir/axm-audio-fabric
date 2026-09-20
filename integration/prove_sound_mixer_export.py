"""Prove actual Sound Mixer plan -> Audio Fabric deterministic export.

The caller supplies a checked-out Sound Mixer repository and exact expected
commit. This script creates one real Audio Fabric music render artifact, asks
Sound Mixer's public APIs to place that exact receipt into canonical Mixer state
and build ``axm.sound-mix-plan/v1``, then feeds the generated plan back into
Audio Fabric for deterministic render/replay.

Passing establishes cross-repository plan execution, source identity, rendered
bytes, and signal evidence for this pinned fixture only. It does not establish
playback, game-runtime execution, listening quality, or production suitability.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

AUDIO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUDIO_ROOT))

from axm_audio import canonical_sha256, render_mix_plan_wav, render_music_plan_wav


PROOF_SCHEMA = "axm.audio-sound-mixer-export-proof/v1"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def execute_proof() -> dict:
    mixer_root_raw = os.environ.get("AXM_SOUND_MIXER_ROOT")
    expected_mixer_ref = os.environ.get("AXM_SOUND_MIXER_REF")
    if not mixer_root_raw:
        raise RuntimeError("AXM_SOUND_MIXER_ROOT must point to a checked-out Sound Mixer repository")
    if not expected_mixer_ref:
        raise RuntimeError("AXM_SOUND_MIXER_REF must name the exact Sound Mixer commit under test")

    mixer_root = Path(mixer_root_raw).resolve()
    if not (mixer_root / "src" / "mixer-core.mjs").is_file():
        raise RuntimeError(f"Sound Mixer source not found under {mixer_root}")
    observed_mixer_ref = _git_head(mixer_root)
    if observed_mixer_ref != expected_mixer_ref:
        raise RuntimeError(
            f"Sound Mixer checkout mismatch: expected {expected_mixer_ref}, observed {observed_mixer_ref}"
        )

    out_dir = Path(os.environ.get("AXM_AUDIO_PROOF_OUT", "out/sound-mixer-export"))
    out_dir.mkdir(parents=True, exist_ok=True)

    source_plan = {
        "schema": "axm.music-audio-render-plan/v1",
        "target": "axm-audio-fabric",
        "timing_scope": "section_relative_ticks",
        "tempo_bpm": 120,
        "ppq": 480,
        "project_id": "mixer-export-source",
        "state_id": "proof",
        "stems": [
            {
                "stem_id": "music",
                "events": [
                    {
                        "event_id": "proof-note",
                        "kind": "note",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "realization": {
                            "status": "bound",
                            "kind": "instrument",
                            "binding_ref": "mixer-export-triangle",
                        },
                        "payload": {"pitch_midi": 64, "velocity": 104},
                    }
                ],
            }
        ],
    }
    profile = {
        "schema": "axm.audio-realization-profile/v1",
        "id": "mixer-export-triangle",
        "kind": "instrument",
        "waveform": "triangle",
        "sample_rate": 44100,
        "gain": 0.2,
        "envelope": {},
        "effects": [],
    }
    source_wav = out_dir / "source.wav"
    source_receipt_path = out_dir / "source.receipt.json"
    source_receipt = render_music_plan_wav(source_plan, [profile], source_wav, source_receipt_path)

    mix_plan_path = out_dir / "sound-mixer-plan.json"
    subprocess.run(
        [
            "node",
            str(AUDIO_ROOT / "integration" / "build_sound_mixer_plan.mjs"),
            str(source_receipt_path),
            str(mix_plan_path),
        ],
        check=True,
        env={**os.environ, "AXM_SOUND_MIXER_ROOT": str(mixer_root)},
    )
    mix_plan = json.loads(mix_plan_path.read_text(encoding="utf-8"))
    if mix_plan.get("schema") != "axm.sound-mix-plan/v1":
        raise AssertionError("Sound Mixer did not produce axm.sound-mix-plan/v1")
    if len(mix_plan.get("events", [])) != 1:
        raise AssertionError("expected one placement event from pinned Sound Mixer fixture")
    source_ref = source_receipt["rendered_artifact"]["id"]
    if mix_plan["events"][0]["source"]["ref"] != source_ref:
        raise AssertionError("Sound Mixer plan did not preserve Audio Fabric artifact identity")

    sources = {source_ref: {"wav_path": source_wav, "receipt": source_receipt}}
    first_wav = out_dir / "mix-first.wav"
    replay_wav = out_dir / "mix-replay.wav"
    first_receipt_path = out_dir / "mix-first.receipt.json"
    replay_receipt_path = out_dir / "mix-replay.receipt.json"
    first_receipt = render_mix_plan_wav(mix_plan, sources, first_wav, first_receipt_path)
    replay_receipt = render_mix_plan_wav(mix_plan, sources, replay_wav, replay_receipt_path)

    first_bytes = first_wav.read_bytes()
    replay_bytes = replay_wav.read_bytes()
    if first_receipt != replay_receipt:
        raise AssertionError("Mixer-plan replay receipt differs from first render receipt")
    if first_bytes != replay_bytes:
        raise AssertionError("Mixer-plan replay WAV bytes differ from first render WAV bytes")
    mix_plan_sha256 = canonical_sha256(mix_plan)
    if first_receipt["mix_plan_sha256"] != mix_plan_sha256:
        raise AssertionError("Audio Fabric export receipt does not bind exact Sound Mixer plan hash")
    if first_receipt["source_artifacts"][0]["source_ref"] != source_ref:
        raise AssertionError("Audio Fabric export receipt does not bind exact source artifact identity")

    proof = {
        "schema": PROOF_SCHEMA,
        "sound_mixer": {
            "repository": "mike-axiom-mir/axm-sound-mixer",
            "commit": observed_mixer_ref,
            "mix_plan_sha256": mix_plan_sha256,
            "project_id": mix_plan["projectId"],
            "project_revision": mix_plan["projectRevision"],
        },
        "audio_fabric_source": {
            "receipt_schema": source_receipt["schema"],
            "artifact": source_receipt["rendered_artifact"],
        },
        "audio_fabric_export": {
            "receipt_schema": first_receipt["schema"],
            "artifact": first_receipt["rendered_artifact"],
            "wav_sha256": _sha256_bytes(first_bytes),
        },
        "replay": {
            "receipt_identical": True,
            "wav_bytes_identical": True,
            "artifact_identity_identical": (
                first_receipt["rendered_artifact"]["id"]
                == replay_receipt["rendered_artifact"]["id"]
            ),
        },
        "signal": first_receipt["signal"],
        "truth_boundary": (
            "Pinned actual Sound Mixer API -> generated mix plan -> Audio Fabric deterministic export/replay "
            "evidence only; no listening-quality, playback, game-runtime, or production-suitability claim."
        ),
    }
    _write_json(out_dir / "proof.json", proof)
    return proof


def main() -> int:
    out_dir = Path(os.environ.get("AXM_AUDIO_PROOF_OUT", "out/sound-mixer-export"))
    try:
        proof = execute_proof()
    except Exception as exc:
        _write_json(
            out_dir / "failure.json",
            {
                "schema": "axm.audio-sound-mixer-export-proof-failure/v1",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        raise
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

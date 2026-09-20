"""Execute the smallest real Music Maker -> Audio Fabric render proof.

This is evidence infrastructure, not a product dependency. The caller provides a
checked-out Music Maker repository and the exact commit expected for that source.
The script uses Music Maker's public ``build_render_plan`` API, feeds that exact
plan into Audio Fabric, renders twice, and requires identical canonical receipts
and WAV bytes.

Passing this proof establishes cross-repository structural execution,
deterministic plan translation, and deterministic rendered bytes for this pinned
fixture. It does not establish listening quality or production suitability.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

from axm_audio import canonical_sha256, render_music_plan_wav


PROOF_SCHEMA = "axm.audio-cross-repo-music-proof/v1"


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
    music_root_raw = os.environ.get("AXM_MUSIC_MAKER_ROOT")
    expected_music_ref = os.environ.get("AXM_MUSIC_MAKER_REF")
    if not music_root_raw:
        raise RuntimeError("AXM_MUSIC_MAKER_ROOT must point to a checked-out Music Maker repository")
    if not expected_music_ref:
        raise RuntimeError("AXM_MUSIC_MAKER_REF must name the exact Music Maker commit under test")

    music_root = Path(music_root_raw).resolve()
    if not (music_root / "axm_music" / "__init__.py").is_file():
        raise RuntimeError(f"Music Maker package not found under {music_root}")
    observed_music_ref = _git_head(music_root)
    if observed_music_ref != expected_music_ref:
        raise RuntimeError(
            f"Music Maker checkout mismatch: expected {expected_music_ref}, observed {observed_music_ref}"
        )

    sys.path.insert(0, str(music_root))
    from axm_music import build_render_plan  # imported from the pinned external checkout

    project_path = music_root / "examples" / "three_state_score.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    realization_bindings = [
        {
            "scope": "stem",
            "key": "motif",
            "binding_ref": "cross-repo-proof-triangle",
            "provenance": {
                "origin": "cross-repo-integration-proof",
                "music_maker_commit": observed_music_ref,
            },
        }
    ]
    plan = build_render_plan(project, "exploration", realization_bindings)
    if plan["truth_boundary"]["event_count"] != 4:
        raise AssertionError("expected the pinned exploration fixture to contain exactly four note events")
    if not plan["truth_boundary"]["realizations_bound"]:
        raise AssertionError("Music Maker did not mark the proof phrase as fully bound")

    profile = {
        "schema": "axm.audio-realization-profile/v1",
        "id": "cross-repo-proof-triangle",
        "kind": "instrument",
        "waveform": "triangle",
        "sample_rate": 44100,
        "gain": 0.25,
        "envelope": {},
        "effects": [],
    }

    out_dir = Path(os.environ.get("AXM_AUDIO_PROOF_OUT", "out/music-maker-cross-repo"))
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "music-maker-plan.json"
    first_wav = out_dir / "render-first.wav"
    replay_wav = out_dir / "render-replay.wav"
    first_receipt_path = out_dir / "render-first.receipt.json"
    replay_receipt_path = out_dir / "render-replay.receipt.json"
    _write_json(plan_path, plan)

    first_receipt = render_music_plan_wav(plan, [profile], first_wav, first_receipt_path)
    replay_receipt = render_music_plan_wav(plan, [profile], replay_wav, replay_receipt_path)

    first_bytes = first_wav.read_bytes()
    replay_bytes = replay_wav.read_bytes()
    if first_receipt != replay_receipt:
        raise AssertionError("replay receipt differs from first render receipt")
    if first_bytes != replay_bytes:
        raise AssertionError("replay WAV bytes differ from first render WAV bytes")

    plan_sha256 = canonical_sha256(plan)
    if first_receipt["music_plan_sha256"] != plan_sha256:
        raise AssertionError("Audio Fabric receipt does not bind the exact Music Maker plan hash")
    artifact = first_receipt["rendered_artifact"]
    if artifact["id"] != f"sha256:{artifact['content_sha256']}":
        raise AssertionError("rendered artifact identity does not match its PCM content hash")

    proof = {
        "schema": PROOF_SCHEMA,
        "music_maker": {
            "repository": "mike-axiom-mir/axm-music-maker",
            "commit": observed_music_ref,
            "fixture": "examples/three_state_score.json",
            "state_id": "exploration",
            "plan_sha256": plan_sha256,
        },
        "audio_fabric": {
            "repository": "mike-axiom-mir/axm-audio-fabric",
            "receipt_schema": first_receipt["schema"],
            "rendered_artifact": artifact,
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
            "Pinned cross-repo source/API execution + deterministic plan/render/replay evidence only; "
            "no listening-quality, musical-quality, browser-playback, game-runtime, or production claim."
        ),
    }
    _write_json(out_dir / "proof.json", proof)
    return proof


def main() -> int:
    out_dir = Path(os.environ.get("AXM_AUDIO_PROOF_OUT", "out/music-maker-cross-repo"))
    try:
        proof = execute_proof()
    except Exception as exc:
        _write_json(
            out_dir / "failure.json",
            {
                "schema": "axm.audio-cross-repo-music-proof-failure/v1",
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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from axm_audio import AudioRecipeError
from axm_audio.music_plan import (
    MUSIC_RENDER_PLAN_SCHEMA,
    MUSIC_RENDER_RECEIPT_SCHEMA,
    REALIZATION_PROFILE_SCHEMA,
    build_music_layered_cue,
    render_music_plan_wav,
    tick_to_milliseconds,
)


class MusicPlanRealizerTests(unittest.TestCase):
    def profile(self) -> dict:
        return {
            "schema": REALIZATION_PROFILE_SCHEMA,
            "id": "instrument.basic-sine",
            "kind": "instrument",
            "waveform": "sine",
            "sample_rate": 16000,
            "gain": 0.25,
            "envelope": {
                "attack_ms": 2,
                "decay_ms": 5,
                "sustain_level": 0.8,
                "release_ms": 10,
            },
            "effects": [],
        }

    def plan(self) -> dict:
        def note(event_id: str, start_tick: int, duration_ticks: int, pitch: int, velocity: int) -> dict:
            return {
                "event_id": event_id,
                "clip_id": "clip-a",
                "kind": "note",
                "start_tick": start_tick,
                "duration_ticks": duration_ticks,
                "provenance": {"source": "test-fixture"},
                "payload": {"pitch_midi": pitch, "velocity": velocity},
                "realization": {
                    "status": "bound",
                    "kind": "instrument",
                    "binding_scope": "stem",
                    "binding_key": "stem-a",
                    "target": "axm-audio-fabric",
                    "binding_ref": "instrument.basic-sine",
                },
            }

        return {
            "schema": MUSIC_RENDER_PLAN_SCHEMA,
            "target": "axm-audio-fabric",
            "project_id": "music-proof",
            "project_sha256": "fixture-project-sha",
            "project_provenance": {"source": "test-fixture"},
            "state_id": "exploration",
            "section_id": "section-a",
            "tempo_bpm": 120,
            "ppq": 480,
            "meter": {"numerator": 4, "denominator": 4},
            "section_length_ticks": 1920,
            "timing_scope": "section_relative_ticks",
            "stems": [
                {
                    "stem_id": "stem-a",
                    "role": "lead",
                    "clip_id": "clip-a",
                    "clip_provenance": {"source": "test-fixture"},
                    "playback_policy": "once_at_section_start",
                    "clip_length_ticks": 960,
                    "uncovered_tail_ticks": 960,
                    "events": [
                        note("note-a", 0, 240, 69, 127),
                        note("note-b", 480, 240, 72, 96),
                    ],
                }
            ],
            "truth_boundary": {
                "audio_rendered": False,
                "realizations_bound": True,
                "looping_inferred": False,
                "mix_decisions_inferred": False,
            },
        }

    def test_tick_conversion_has_explicit_deterministic_rounding(self) -> None:
        self.assertEqual(tick_to_milliseconds(0, 120, 480), 0.0)
        self.assertEqual(tick_to_milliseconds(240, 120, 480), 250.0)
        self.assertEqual(tick_to_milliseconds(480, 120, 480), 500.0)
        self.assertEqual(tick_to_milliseconds(1, 120, 480), 1.041666667)

    def test_bound_plan_becomes_explicit_layered_cue(self) -> None:
        recipe, schedule = build_music_layered_cue(self.plan(), [self.profile()])
        self.assertEqual(recipe["schema"], "axm.audio-layered-cue/v1")
        self.assertEqual(len(recipe["layers"]), 2)
        self.assertEqual(recipe["layers"][0]["offset_ms"], 0.0)
        self.assertEqual(recipe["layers"][1]["offset_ms"], 500.0)
        self.assertEqual(recipe["layers"][0]["cue"]["atom"]["source"]["frequency_hz"], 440.0)
        self.assertEqual(schedule[0]["binding_ref"], "instrument.basic-sine")
        self.assertEqual(schedule[0]["duration_ms"], 250.0)

    def test_render_is_replayable_and_exposes_stable_artifact_identity(self) -> None:
        plan = self.plan()
        profile = self.profile()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt_a = render_music_plan_wav(plan, [profile], root / "a.wav", root / "a.json")
            receipt_b = render_music_plan_wav(plan, [profile], root / "b.wav", root / "b.json")
            self.assertEqual(receipt_a, receipt_b)
            self.assertEqual((root / "a.wav").read_bytes(), (root / "b.wav").read_bytes())
            self.assertEqual(receipt_a["schema"], MUSIC_RENDER_RECEIPT_SCHEMA)
            self.assertEqual(
                receipt_a["rendered_artifact"]["id"],
                "sha256:" + receipt_a["rendered_artifact"]["content_sha256"],
            )
            persisted = json.loads((root / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, receipt_a)
            self.assertIn("no listening-quality", receipt_a["truth_boundary"])

    def test_unbound_realization_is_rejected(self) -> None:
        plan = self.plan()
        plan["stems"][0]["events"][0]["realization"] = {
            "status": "unbound",
            "kind": "instrument",
            "binding_scope": "stem",
            "binding_key": "stem-a",
            "target": "axm-audio-fabric",
        }
        with self.assertRaisesRegex(AudioRecipeError, "explicitly bound"):
            build_music_layered_cue(plan, [self.profile()])

    def test_unknown_binding_is_rejected(self) -> None:
        plan = self.plan()
        plan["stems"][0]["events"][0]["realization"]["binding_ref"] = "instrument.missing"
        with self.assertRaisesRegex(AudioRecipeError, "unknown binding"):
            build_music_layered_cue(plan, [self.profile()])

    def test_voice_is_not_silently_synthesized(self) -> None:
        plan = self.plan()
        event = plan["stems"][0]["events"][0]
        event["kind"] = "voice"
        event["payload"] = {"speaker_id": "npc-a", "text": "hello"}
        event["realization"] = {
            "status": "bound",
            "kind": "voice_performance",
            "binding_ref": "voice.take-a",
        }
        with self.assertRaisesRegex(AudioRecipeError, "voice performance requires"):
            build_music_layered_cue(plan, [self.profile()])


if __name__ == "__main__":
    unittest.main()

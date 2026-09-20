import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from axm_audio import (
    AudioRecipeError,
    canonical_sha256,
    render_mix_plan_wav,
    render_music_plan_wav,
)


class MixPlanRenderTests(unittest.TestCase):
    def _source(self, root: Path):
        plan = {
            "schema": "axm.music-audio-render-plan/v1",
            "target": "axm-audio-fabric",
            "timing_scope": "section_relative_ticks",
            "tempo_bpm": 120,
            "ppq": 480,
            "project_id": "mix-source-project",
            "state_id": "proof",
            "stems": [
                {
                    "stem_id": "music",
                    "events": [
                        {
                            "event_id": "note-1",
                            "kind": "note",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "realization": {
                                "status": "bound",
                                "kind": "instrument",
                                "binding_ref": "proof-sine",
                            },
                            "payload": {"pitch_midi": 69, "velocity": 100},
                        }
                    ],
                }
            ],
        }
        profile = {
            "schema": "axm.audio-realization-profile/v1",
            "id": "proof-sine",
            "kind": "instrument",
            "waveform": "sine",
            "sample_rate": 44100,
            "gain": 0.2,
            "envelope": {},
            "effects": [],
        }
        wav_path = root / "source.wav"
        receipt = render_music_plan_wav(plan, [profile], wav_path)
        artifact = receipt["rendered_artifact"]
        source = {
            "kind": "audio-fabric-render",
            "ref": artifact["id"],
            "provenance": {
                "receiptSchema": receipt["schema"],
                "receiptRef": "unit:source.receipt.json",
                "musicPlanSha256": receipt["music_plan_sha256"],
                "projectId": receipt["project_id"],
                "projectSha256": receipt.get("project_sha256"),
                "stateId": receipt["state_id"],
                "sectionId": receipt.get("section_id"),
                "artifact": {
                    "kind": artifact["kind"],
                    "contentSha256": artifact["content_sha256"],
                    "sampleRate": artifact["sample_rate"],
                    "channels": artifact["channels"],
                    "frames": artifact["frames"],
                },
            },
        }
        return wav_path, receipt, source

    def _mix_plan(self, source, *, audible=True, sample_rate=44100):
        return {
            "schema": "axm.sound-mix-plan/v1",
            "projectId": "proof-mix",
            "projectRevision": 4,
            "sampleRate": sample_rate,
            "events": [
                {
                    "placementId": "music-clip",
                    "trackId": "music",
                    "source": deepcopy(source),
                    "startMs": 10,
                    "sourceOffsetMs": 20,
                    "durationMs": 100,
                    "loop": False,
                    "fadeInMs": 10,
                    "fadeOutMs": 20,
                    "clipGain": 0.5,
                    "trackGain": 0.8,
                    "pan": 0.25,
                    "audible": audible,
                }
            ],
            "automation": [],
        }

    def test_deterministic_render_binds_plan_and_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_wav, source_receipt, source = self._source(root)
            plan = self._mix_plan(source)
            evidence = {source["ref"]: {"wav_path": source_wav, "receipt": source_receipt}}

            first = render_mix_plan_wav(plan, evidence, root / "first.wav", root / "first.json")
            replay = render_mix_plan_wav(plan, evidence, root / "replay.wav", root / "replay.json")

            self.assertEqual(first, replay)
            self.assertEqual((root / "first.wav").read_bytes(), (root / "replay.wav").read_bytes())
            self.assertEqual(first["mix_plan_sha256"], canonical_sha256(plan))
            self.assertEqual(first["source_artifacts"][0]["source_ref"], source["ref"])
            self.assertEqual(first["rendered_artifact"]["sample_rate"], 44100)
            self.assertEqual(first["rendered_artifact"]["frames"], 4851)
            self.assertGreater(first["signal"]["rms"], 0.0)
            self.assertIn("no listening-quality", first["truth_boundary"])
            persisted = json.loads((root / "first.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, first)

    def test_inaudible_event_preserves_timeline_as_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_wav, source_receipt, source = self._source(root)
            plan = self._mix_plan(source, audible=False)
            receipt = render_mix_plan_wav(
                plan,
                {source["ref"]: {"wav_path": source_wav, "receipt": source_receipt}},
                root / "muted.wav",
            )
            self.assertEqual(receipt["rendered_artifact"]["frames"], 4851)
            self.assertEqual(receipt["signal"]["peak"], 0.0)
            self.assertEqual(receipt["signal"]["rms"], 0.0)

    def test_rejects_rate_mismatch_instead_of_resampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_wav, source_receipt, source = self._source(root)
            plan = self._mix_plan(source, sample_rate=48000)
            with self.assertRaisesRegex(AudioRecipeError, "resampling is not implemented"):
                render_mix_plan_wav(
                    plan,
                    {source["ref"]: {"wav_path": source_wav, "receipt": source_receipt}},
                    root / "bad.wav",
                )

    def test_rejects_unsupported_source_and_automation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_wav, source_receipt, source = self._source(root)
            plan = self._mix_plan(source)
            plan["events"][0]["source"]["kind"] = "sample-library"
            with self.assertRaisesRegex(AudioRecipeError, "resolves only 'audio-fabric-render'"):
                render_mix_plan_wav(
                    plan,
                    {source["ref"]: {"wav_path": source_wav, "receipt": source_receipt}},
                    root / "unsupported.wav",
                )

            plan = self._mix_plan(source)
            plan["automation"] = [
                {
                    "schema": "axm.mix-automation/v1",
                    "id": "auto-1",
                    "target": "track:music:gain",
                    "interpolation": "linear",
                    "points": [{"timeMs": 0, "value": 1}],
                }
            ]
            with self.assertRaisesRegex(AudioRecipeError, "refusing to ignore Mixer automation intent"):
                render_mix_plan_wav(
                    plan,
                    {source["ref"]: {"wav_path": source_wav, "receipt": source_receipt}},
                    root / "automation.wav",
                )

    def test_rejects_pcm_bytes_that_do_not_match_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_wav, source_receipt, source = self._source(root)
            with wave.open(str(source_wav), "rb") as handle:
                params = handle.getparams()
                payload = bytearray(handle.readframes(handle.getnframes()))
            payload[20] ^= 0x01
            tampered = root / "tampered.wav"
            with wave.open(str(tampered), "wb") as handle:
                handle.setparams(params)
                handle.writeframes(bytes(payload))

            with self.assertRaisesRegex(AudioRecipeError, "PCM hash disagrees"):
                render_mix_plan_wav(
                    self._mix_plan(source),
                    {source["ref"]: {"wav_path": tampered, "receipt": source_receipt}},
                    root / "bad.wav",
                )


if __name__ == "__main__":
    unittest.main()

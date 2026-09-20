import copy
import tempfile
import unittest
from pathlib import Path

from axm_audio import (
    AUDIO_ATOM_SCHEMA,
    AUDIO_CUE_SCHEMA,
    GAMEPLAY_AUDIO_BINDING_SCHEMA,
    GAMEPLAY_RUNTIME_CUE_REQUEST_SCHEMA,
    AudioRecipeError,
    canonical_sha256,
    render_gameplay_audio_request,
    validate_gameplay_audio_binding,
    validate_gameplay_audio_request,
)


def gameplay_request():
    event = {
        "id": "impact-crack",
        "time": 0.22,
        "cue": "blade-impact",
        "track": "audio",
        "type": "audio",
    }
    body = {
        "schema": GAMEPLAY_RUNTIME_CUE_REQUEST_SCHEMA,
        "type": "audio-cue-request",
        "cueType": "audio",
        "dispatchKey": "dispatch-abc",
        "actionInstanceId": "attack-17",
        "abilityId": "arc-slash",
        "trackId": "audio",
        "eventId": "impact-crack",
        "eventTime": 0.22,
        "event": event,
        "context": {"actorId": "player-1"},
        "authority": {
            "externalRuntimeTruthOwner": False,
            "durableWorldStateOwner": False,
            "emitsRequestOnly": True,
        },
    }
    return {
        **body,
        "receipt": {
            "sha256": canonical_sha256(body),
            "deterministic": True,
        },
    }


def explicit_binding():
    return {
        "schema": GAMEPLAY_AUDIO_BINDING_SCHEMA,
        "authored_cue": "blade-impact",
        "provenance": {
            "kind": "caller-supplied",
            "source": "unit-test fixture",
            "inference": False,
        },
        "audio_cue": {
            "schema": AUDIO_CUE_SCHEMA,
            "gain": 0.7,
            "position": {"x": 1.0, "y": 0.0, "z": 0.0},
            "listener": {"x": 0.0, "y": 0.0, "z": 0.0},
            "max_distance": 12.0,
            "atom": {
                "schema": AUDIO_ATOM_SCHEMA,
                "id": "explicit-impact-recipe",
                "sample_rate": 44100,
                "duration_ms": 120.0,
                "source": {
                    "waveform": "triangle",
                    "frequency_hz": 190.0,
                    "frequency_end_hz": 72.0,
                    "noise_mix": 0.18,
                    "seed": 17,
                },
                "envelope": {
                    "attack_ms": 1.0,
                    "decay_ms": 35.0,
                    "sustain_level": 0.25,
                    "release_ms": 55.0,
                },
                "effects": [
                    {"type": "drive", "amount": 1.6},
                    {"type": "lowpass", "cutoff_hz": 3200.0},
                    {"type": "gain", "amount": 0.75},
                ],
            },
        },
    }


class GameplayRuntimeTests(unittest.TestCase):
    def test_exact_request_and_binding_render_deterministically(self):
        request = gameplay_request()
        binding = explicit_binding()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = render_gameplay_audio_request(
                request, binding, root / "first.wav", root / "first.json"
            )
            replay = render_gameplay_audio_request(
                request, binding, root / "replay.wav", root / "replay.json"
            )

            self.assertEqual(first, replay)
            self.assertEqual(
                (root / "first.wav").read_bytes(),
                (root / "replay.wav").read_bytes(),
            )
            self.assertEqual(
                first["gameplay_request"]["request_sha256"],
                request["receipt"]["sha256"],
            )
            self.assertEqual(
                first["gameplay_request"]["dispatch_key"],
                request["dispatchKey"],
            )
            self.assertEqual(
                first["binding"]["binding_sha256"],
                canonical_sha256(binding),
            )
            self.assertEqual(
                first["recipe"]["cue_sha256"],
                canonical_sha256(binding["audio_cue"]),
            )
            self.assertTrue(first["execution_id"].startswith("sha256:"))
            self.assertGreater(first["signal"]["peak"], 0.0)

    def test_request_receipt_tampering_is_rejected(self):
        request = gameplay_request()
        request["eventTime"] = 0.23
        with self.assertRaisesRegex(AudioRecipeError, "receipt mismatch"):
            validate_gameplay_audio_request(request)

    def test_audio_request_only(self):
        request = gameplay_request()
        body = {k: copy.deepcopy(v) for k, v in request.items() if k != "receipt"}
        body["type"] = "camera-cue-request"
        body["cueType"] = "camera"
        request = {
            **body,
            "receipt": {
                "sha256": canonical_sha256(body),
                "deterministic": True,
            },
        }
        with self.assertRaisesRegex(AudioRecipeError, "audio-cue-request"):
            validate_gameplay_audio_request(request)

    def test_binding_must_match_authored_cue_exactly(self):
        binding = explicit_binding()
        binding["authored_cue"] = "slash-windup"
        with self.assertRaisesRegex(AudioRecipeError, "does not match"):
            validate_gameplay_audio_binding(binding, authored_cue="blade-impact")

    def test_binding_requires_provenance(self):
        binding = explicit_binding()
        del binding["provenance"]
        with self.assertRaisesRegex(AudioRecipeError, "provenance"):
            validate_gameplay_audio_binding(binding, authored_cue="blade-impact")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
import wave
from pathlib import Path

from axm_audio import AudioRecipeError, render_atom, render_cue, render_wav


def atom(seed=7):
    return {
        "schema": "axm.audio-atom/v1",
        "id": "test-zap",
        "sample_rate": 16000,
        "duration_ms": 120,
        "source": {
            "waveform": "sine",
            "frequency_hz": 720,
            "frequency_end_hz": 160,
            "noise_mix": 0.22,
            "seed": seed,
        },
        "envelope": {
            "attack_ms": 2,
            "decay_ms": 25,
            "sustain_level": 0.45,
            "release_ms": 55,
        },
        "effects": [
            {"type": "drive", "amount": 1.5},
            {"type": "lowpass", "cutoff_hz": 2400},
        ],
    }


def cue(x=0):
    return {
        "schema": "axm.audio-cue/v1",
        "atom": atom(),
        "gain": 0.8,
        "position": {"x": x, "y": 0, "z": 0},
        "listener": {"x": 0, "y": 0, "z": 0},
        "max_distance": 20,
    }


class AudioCoreTests(unittest.TestCase):
    def test_same_recipe_replays_exact_float_sequence(self):
        first, sr1 = render_atom(atom())
        second, sr2 = render_atom(atom())
        self.assertEqual(sr1, sr2)
        self.assertEqual(first, second)

    def test_seed_changes_noise_component(self):
        first, _ = render_atom(atom(seed=1))
        second, _ = render_atom(atom(seed=2))
        self.assertNotEqual(first, second)

    def test_positional_pan_changes_channel_energy(self):
        left, _ = render_cue(cue(x=-8))
        right, _ = render_cue(cue(x=8))
        left_energy = (sum(abs(l) for l, _ in left), sum(abs(r) for _, r in left))
        right_energy = (sum(abs(l) for l, _ in right), sum(abs(r) for _, r in right))
        self.assertGreater(left_energy[0], left_energy[1])
        self.assertGreater(right_energy[1], right_energy[0])

    def test_wav_and_receipt_are_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            receipt1 = render_wav(cue(3), p / "a.wav", p / "a.json")
            receipt2 = render_wav(cue(3), p / "b.wav", p / "b.json")
            self.assertEqual(receipt1["pcm16_sha256"], receipt2["pcm16_sha256"])
            self.assertEqual((p / "a.wav").read_bytes(), (p / "b.wav").read_bytes())
            with wave.open(str(p / "a.wav"), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 2)
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getnframes(), 1920)

    def test_rejects_unknown_schema(self):
        broken = atom()
        broken["schema"] = "wrong"
        with self.assertRaises(AudioRecipeError):
            render_atom(broken)


if __name__ == "__main__":
    unittest.main()

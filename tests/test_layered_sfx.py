import tempfile
import unittest
import wave
from pathlib import Path

from axm_audio import AudioRecipeError, render_layered_cue, render_layered_wav


def atom(*, sample_rate=8000, duration_ms=100, waveform="sine", frequency_hz=440, seed=0):
    source = {"waveform": waveform, "seed": seed}
    if waveform != "noise":
        source["frequency_hz"] = frequency_hz
    return {
        "schema": "axm.audio-atom/v1",
        "id": f"{waveform}-{frequency_hz}-{seed}",
        "sample_rate": sample_rate,
        "duration_ms": duration_ms,
        "source": source,
    }


def cue(audio_atom, *, x=0, gain=1.0):
    return {
        "schema": "axm.audio-cue/v1",
        "atom": audio_atom,
        "gain": gain,
        "position": {"x": x, "y": 0, "z": 0},
        "listener": {"x": 0, "y": 0, "z": 0},
        "max_distance": 20,
    }


def layered():
    return {
        "schema": "axm.audio-layered-cue/v1",
        "id": "impact-layered-test",
        "master_gain": 0.9,
        "layers": [
            {"cue": cue(atom(waveform="sine", frequency_hz=180)), "offset_ms": 0, "gain": 0.8},
            {"cue": cue(atom(waveform="noise", seed=42), x=3), "offset_ms": 35, "gain": 0.45},
        ],
    }


class LayeredSfxTests(unittest.TestCase):
    def test_same_layered_recipe_replays_exact_float_sequence(self):
        first, sr1 = render_layered_cue(layered())
        second, sr2 = render_layered_cue(layered())
        self.assertEqual(sr1, sr2)
        self.assertEqual(first, second)

    def test_offsets_extend_mix_timeline(self):
        stereo, sample_rate = render_layered_cue(layered())
        self.assertEqual(sample_rate, 8000)
        self.assertEqual(len(stereo), 1080)  # 100 ms + 35 ms at 8 kHz

    def test_rejects_mixed_sample_rates_without_hidden_resampling(self):
        recipe = layered()
        recipe["layers"][1]["cue"]["atom"]["sample_rate"] = 16000
        with self.assertRaisesRegex(AudioRecipeError, "resampling is not implemented"):
            render_layered_cue(recipe)

    def test_receipt_exposes_preclip_overload(self):
        loud = {
            "schema": "axm.audio-layered-cue/v1",
            "id": "deliberate-overload",
            "layers": [
                {"cue": cue(atom(frequency_hz=220)), "gain": 1.0},
                {"cue": cue(atom(frequency_hz=220)), "gain": 1.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            receipt = render_layered_wav(loud, Path(tmp) / "loud.wav")
        self.assertGreater(receipt["preclip_peak"], 1.0)
        self.assertGreater(receipt["clipped_channel_sample_count"], 0)
        self.assertLessEqual(receipt["peak"], 1.0)

    def test_wav_and_receipt_are_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            first = render_layered_wav(layered(), p / "a.wav", p / "a.json")
            second = render_layered_wav(layered(), p / "b.wav", p / "b.json")
            self.assertEqual(first["pcm16_sha256"], second["pcm16_sha256"])
            self.assertEqual((p / "a.wav").read_bytes(), (p / "b.wav").read_bytes())
            with wave.open(str(p / "a.wav"), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 2)
                self.assertEqual(handle.getframerate(), 8000)
                self.assertEqual(handle.getnframes(), 1080)


if __name__ == "__main__":
    unittest.main()

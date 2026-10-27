"""Audio decode and checks (SPEC 6.2) on WAVs from scripts/make_test_wavs.py."""
import importlib.util
import struct
import unittest
from pathlib import Path

import numpy as np

from darkshelter.audio import AudioError, check_audio, decode_wav, public, resample_linear

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mtw = _load_script("make_test_wavs")


def _extensible_wav(x, sr=16000):
    """16-bit PCM wrapped in a WAVE_FORMAT_EXTENSIBLE header with a LIST chunk before data."""
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    guid = struct.pack("<H", 1) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    fmt = struct.pack("<HHIIHH", 0xFFFE, 1, sr, sr * 2, 2, 16) + struct.pack("<HHI", 22, 16, 4) + guid
    lst = b"INFOISFT" + struct.pack("<I", 3) + b"ds\x00"   # odd size -> pad byte
    chunks = (b"fmt " + struct.pack("<I", len(fmt)) + fmt
              + b"LIST" + struct.pack("<I", len(lst)) + lst + b"\x00"
              + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


class DecodeTest(unittest.TestCase):
    def test_16k_mono_roundtrip(self):
        sig = mtw.speech_like_signal(1.0)
        x, sr = decode_wav(mtw.wav_bytes(sig))
        self.assertEqual(sr, 16000)
        self.assertEqual(x.dtype, np.float32)
        self.assertEqual(len(x), 16000)
        self.assertLess(float(np.max(np.abs(x - sig))), 1e-3)

    def test_48k_stereo_resampled_to_16k_mono(self):
        x, sr = decode_wav(mtw.stereo_48k(2.0))
        self.assertEqual(sr, 48000)
        self.assertAlmostEqual(len(x) / 16000.0, 2.0, delta=0.001)
        self.assertTrue(np.all(np.isfinite(x)))

    def test_8k_upsampled(self):
        t = np.arange(8000) / 8000.0
        x, sr = decode_wav(mtw.wav_bytes(0.5 * np.sin(2 * np.pi * 200 * t), sr=8000))
        self.assertEqual(sr, 8000)
        self.assertEqual(len(x), 16000)

    def test_extensible_header_and_odd_chunk(self):
        sig = mtw.speech_like_signal(1.0)
        x, sr = decode_wav(_extensible_wav(sig))
        self.assertEqual((sr, len(x)), (16000, 16000))

    def test_streaming_data_size_zero_takes_rest(self):
        data = bytearray(mtw.wav_bytes(mtw.speech_like_signal(1.0)))
        i = data.find(b"data")
        data[i + 4:i + 8] = b"\x00\x00\x00\x00"
        x, _ = decode_wav(bytes(data))
        self.assertEqual(len(x), 16000)

    def test_non_wav_rejected(self):
        for bad in (b"", b"hello world", b"RIFF\x00\x00\x00\x00WAVEjunk", b"\x00" * 100, b"OggS" + b"\x00" * 60):
            with self.assertRaises(AudioError):
                decode_wav(bad)

    def test_no_data_chunk_rejected(self):
        data = mtw.wav_bytes(np.zeros(100))
        with self.assertRaises(AudioError):
            decode_wav(data[:36])

    def test_resample_identity(self):
        x = np.linspace(-1, 1, 100).astype(np.float32)
        self.assertIs(resample_linear(x, 16000).dtype, np.dtype(np.float32))
        self.assertEqual(len(resample_linear(x, 16000)), 100)


class CheckTest(unittest.TestCase):
    def check(self, wav, max_seconds=30):
        x, _ = decode_wav(wav)
        return check_audio(x, max_seconds)

    def test_silence_rejected(self):
        c = self.check(mtw.silence())
        self.assertFalse(c["ok"])
        self.assertEqual(c["reason"], "silent")

    def test_quiet_tone_rejected_as_silent(self):
        c = self.check(mtw.quiet_tone(dbfs=-60))
        self.assertEqual(c["reason"], "silent")

    def test_too_short_rejected(self):
        c = self.check(mtw.short_tone(0.3))
        self.assertFalse(c["ok"])
        self.assertEqual(c["reason"], "too_short")

    def test_clipped_warned_not_rejected(self):
        c = self.check(mtw.clipped_sine())
        self.assertTrue(c["ok"])
        self.assertIn("clipped", c["warnings"])
        self.assertGreater(c["clip_ratio"], 0.02)

    def test_too_long_truncated(self):
        c = self.check(mtw.long_signal(40.0), max_seconds=30)
        self.assertTrue(c["ok"])
        self.assertIn("too_long", c["warnings"])
        self.assertEqual(c["duration_s"], 40.0)
        self.assertEqual(len(c["x"]), 30 * 16000)

    def test_speech_like_passes(self):
        c = self.check(mtw.speech_like())
        self.assertTrue(c["ok"])
        self.assertEqual(c["warnings"], [])
        self.assertGreater(c["rms_dbfs"], -50)

    def test_public_is_json_safe(self):
        import json
        c = public(self.check(mtw.silence()))
        self.assertNotIn("x", c)
        json.loads(json.dumps(c, allow_nan=False))

    def test_all_generated_wavs_decode(self):
        for name, data in mtw.all_wavs().items():
            x, sr = decode_wav(data)
            self.assertGreater(len(x), 0, name)


if __name__ == "__main__":
    unittest.main()

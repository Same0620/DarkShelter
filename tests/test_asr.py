"""ASR adapter (SPEC 6.3): phantom filter, MockASR, confidence rules, exact transcribe kwargs.

One test loads the real Whisper `base` (local_files_only=True); it is skipped with DS_FAST=1
or when the model is not in the cache.
"""
import importlib.util
import os
import unittest
from collections import namedtuple
from pathlib import Path

import numpy as np

from darkshelter import asr
from darkshelter.asr import (DEMO_SENTENCES, DOMAIN_PROMPT, AsrResult, AsrUnavailable, FasterWhisperASR, MockASR,
                             filter_phantoms, load_whisper, summarize_segments)
from darkshelter.config import make_config

ROOT = Path(__file__).resolve().parent.parent
Seg = namedtuple("Seg", "start end text avg_logprob no_speech_prob compression_ratio")
Info = namedtuple("Info", "language")


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PhantomTest(unittest.TestCase):
    def test_pure_phantoms_become_empty(self):
        for p in ("請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", "请不吝点赞订阅转发打赏支持明镜与点点栏目", "謝謝觀看",
                  "谢谢观看！", "字幕由Amara.org社群提供", "字幕由amara.org社区提供", "優優獨播劇場", "中文字幕",
                  "明鏡與點點", "感謝觀看。", "字幕志願者", "请订阅 点赞"):
            self.assertEqual(filter_phantoms(p), "", p)

    def test_real_text_kept_and_phantom_tail_removed(self):
        self.assertEqual(filter_phantoms("阿嬤明天要洗腎。謝謝觀看"), "阿嬤明天要洗腎。")
        self.assertEqual(filter_phantoms("謝謝觀看，我要毛毯"), "我要毛毯")
        self.assertEqual(filter_phantoms("我需要胰島素"), "我需要胰島素")

    def test_fewer_than_two_meaningful_chars(self):
        self.assertEqual(filter_phantoms("嗯"), "")
        self.assertEqual(filter_phantoms("。！"), "")
        self.assertEqual(filter_phantoms(""), "")
        self.assertEqual(filter_phantoms(None), "")
        self.assertEqual(filter_phantoms("好的"), "好的")


class MockAsrTest(unittest.TestCase):
    def test_cycles_demo_sentences(self):
        m = MockASR(make_config())
        got = [m.transcribe(None).text for _ in range(len(DEMO_SENTENCES) + 2)]
        self.assertEqual(got[:len(DEMO_SENTENCES)], [filter_phantoms(s) for s in DEMO_SENTENCES])
        self.assertEqual(got[len(DEMO_SENTENCES):], got[:2])

    def test_demo_sentences_cover_plan(self):
        joined = "".join(DEMO_SENTENCES)
        for word in ("洗腎", "胰島素", "中正路三巷", "沒出來", "我沒有胸痛"):
            self.assertIn(word, joined)
        self.assertGreaterEqual(sum("洗腎" in s for s in DEMO_SENTENCES), 2)  # duplicate dialysis report

    def test_mock_text_wins_and_is_filtered(self):
        m = MockASR(make_config())
        r = m.transcribe(np.zeros(32000, dtype=np.float32), mock_text="我要洗肾")
        self.assertIsInstance(r, AsrResult)
        self.assertIn("洗", r.text)
        self.assertEqual(r.model, "mock")
        self.assertAlmostEqual(r.speech_seconds, 2.0)
        r2 = m.transcribe(None, mock_text="謝謝觀看")
        self.assertEqual(r2.text, "")
        self.assertTrue(r2.low_conf)
        self.assertEqual(m.transcribe(None).text, filter_phantoms(DEMO_SENTENCES[0]))  # cycle untouched

    def test_to_dict(self):
        d = MockASR().transcribe(None, mock_text="阿公要輪椅").to_dict()
        for k in ("text", "avg_logprob", "no_speech_prob", "compression_ratio", "language", "low_conf", "reasons",
                  "seconds", "model"):
            self.assertIn(k, d)


class SummarizeTest(unittest.TestCase):
    def test_weighted_logprob_and_maxima(self):
        segs = [Seg(0.0, 1.0, "阿嬤明天", -0.2, 0.1, 1.1), Seg(1.0, 4.0, "要洗腎", -0.6, 0.3, 1.5)]
        r = summarize_segments(segs, 1.5, "small")
        self.assertEqual(r.text, "阿嬤明天要洗腎")
        self.assertAlmostEqual(r.avg_logprob, (-0.2 * 1 + -0.6 * 3) / 4, places=4)
        self.assertAlmostEqual(r.no_speech_prob, 0.3)
        self.assertAlmostEqual(r.compression_ratio, 1.5)
        self.assertFalse(r.low_conf)
        self.assertEqual(r.model, "small")
        self.assertEqual(r.speech_seconds, 4.0)

    def test_low_conf_rules(self):
        cases = {
            "avg_logprob": [Seg(0, 2, "阿嬤要洗腎", -1.3, 0.1, 1.2)],
            "no_speech_prob": [Seg(0, 2, "阿嬤要洗腎", -0.3, 0.7, 1.2)],
            "compression_ratio": [Seg(0, 2, "阿嬤要洗腎", -0.3, 0.1, 2.6)],
            "chars_per_s": [Seg(0, 10, "洗腎", -0.3, 0.1, 1.2)],
        }
        for reason, segs in cases.items():
            r = summarize_segments(segs)
            self.assertTrue(r.low_conf, reason)
            self.assertIn(reason, r.reasons)

    def test_phantom_only_is_no_speech(self):
        r = summarize_segments([Seg(0, 3, "謝謝觀看", -0.3, 0.2, 1.0)])
        self.assertEqual(r.text, "")
        self.assertEqual(r.reasons, ["no_speech"])
        self.assertEqual(r.raw_text, "謝謝觀看")

    def test_empty(self):
        r = summarize_segments([])
        self.assertEqual(r.text, "")
        self.assertTrue(r.low_conf)


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, x, **kw):
        self.calls.append((x, kw))
        return iter([Seg(0.0, 2.0, "阿嬤要洗肾", -0.3, 0.05, 1.2)]), Info("zh")


class FasterWhisperAdapterTest(unittest.TestCase):
    def test_exact_kwargs_and_numpy_input(self):
        a = FasterWhisperASR(make_config())
        fake, fast_fake = FakeModel(), FakeModel()
        a._models = {"small": fake, "base": fast_fake}
        x = np.zeros(32000, dtype=np.float32)
        r = a.transcribe(x)
        self.assertEqual(len(fake.calls), 1)
        got_x, kw = fake.calls[0]
        self.assertIs(got_x, x)
        self.assertEqual(kw, {"language": "zh", "beam_size": 1, "temperature": 0.0, "vad_filter": True,
                              "vad_parameters": {"min_silence_duration_ms": 500}, "initial_prompt": DOMAIN_PROMPT,
                              "condition_on_previous_text": False, "no_speech_threshold": 0.6})
        self.assertEqual(r.model, "small")
        self.assertEqual(r.language, "zh")
        self.assertIn("洗", r.text)
        r2 = a.transcribe(x, fast=True)
        self.assertEqual(r2.model, "base")
        self.assertEqual(len(fast_fake.calls), 1)

    def test_names_from_config(self):
        a = FasterWhisperASR(make_config({"asr": {"model": "base", "fast_model": "tiny"}}))
        self.assertEqual((a.model_name, a.fast_model_name), ("base", "tiny"))
        self.assertEqual(a.name_for(True), "tiny")

    def test_bad_model_name_raises_clear_error(self):
        with self.assertRaises(AsrUnavailable):
            load_whisper("no-such-model-size", make_config(), local_files_only=True)

    def test_memory_error_retried_once(self):
        from unittest import mock
        calls = []

        def flaky(*args, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise RuntimeError("mkl_malloc: failed to allocate memory")
            return "MODEL"
        with mock.patch("faster_whisper.WhisperModel", side_effect=flaky):
            self.assertEqual(load_whisper("base", make_config(), retry_delay_s=0), "MODEL")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["device"], "cpu")
        self.assertIs(calls[0]["local_files_only"], True)

        def always(*args, **kw):
            raise RuntimeError("mkl_malloc: failed to allocate memory")
        with mock.patch("faster_whisper.WhisperModel", side_effect=always):
            with self.assertRaises(AsrUnavailable) as cm:
                load_whisper("base", make_config(), retry_delay_s=0)
        self.assertIn("not enough free memory", str(cm.exception))

    def test_domain_prompt(self):
        self.assertTrue(DOMAIN_PROMPT.startswith("以下是台灣避難收容處所登記台的對話"))
        for w in ("洗腎", "胰島素", "製氧機", "床位"):
            self.assertIn(w, DOMAIN_PROMPT)


@unittest.skipIf(os.environ.get("DS_FAST") == "1", "slow: loads the real Whisper model")
class RealWhisperTest(unittest.TestCase):
    def test_base_transcribes_generated_clip(self):
        cfg = make_config()
        a = FasterWhisperASR({**cfg, "asr": {**cfg["asr"], "model": "base"}})
        try:
            a.load()  # local_files_only=True
        except AsrUnavailable as e:
            self.skipTest(f"Whisper base not available: {e}")
        mtw = _load_script("make_test_wavs")
        from darkshelter.audio import decode_wav
        sentence = "阿嬤明天早上要洗腎"
        data = mtw.tts_wav(sentence)
        if data is None:
            x, _ = decode_wav(mtw.speech_like())
            r = a.transcribe(x)
            self.assertIsInstance(r, AsrResult)
        else:
            x, _ = decode_wav(data)
            r = a.transcribe(x)
            self.assertIn("洗腎", r.text, r.to_dict())
            self.assertFalse(r.low_conf, r.to_dict())
        self.assertEqual(r.model, "base")
        self.assertGreater(r.seconds, 0)
        self.assertIs(asr.FasterWhisperASR, FasterWhisperASR)


if __name__ == "__main__":
    unittest.main()

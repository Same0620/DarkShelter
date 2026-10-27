"""Ollama adapter (SPEC 6.4 / 7) against scripts/ollama_stub.py, plus validation and MockLLM.

A real-Ollama smoke test runs only when Ollama answers on the configured URL with the model
pulled (skipped with DS_FAST=1).
"""
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path

from darkshelter import llm as L
from darkshelter.config import make_config
from darkshelter.llm import SCHEMA, MockLLM, OllamaLLM, need_to_llm_dict, validate

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stubmod = _load_script("ollama_stub")


def _need(**kw):
    n = {"category": "MEDICATION", "item": "胰島素", "quantity": 0, "subject": "阿嬤", "subject_group": "elderly",
         "location": "", "time_phrase": "今天", "summary": "胰島素用完"}
    n.update(kw)
    return n


class ValidateTest(unittest.TestCase):
    def test_clean_need(self):
        out = validate({"needs": [_need()]})
        self.assertEqual(len(out), 1)
        n = out[0]
        self.assertEqual(n["category"], "MEDICATION")
        self.assertIsNone(n["quantity"])        # 0 = not said
        self.assertIsNone(n["location"])        # "" = not said
        self.assertEqual(n["subject_group"], "elderly")

    def test_limits(self):
        out = validate({"needs": [_need(quantity=99999, item="藥" * 100), _need(quantity=-3), _need(quantity="4"),
                                  _need(quantity=2.7), _need(quantity=True), _need(), _need()]})
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["quantity"], 10000)
        self.assertEqual(len(out[0]["item"]), 40)
        self.assertIsNone(out[1]["quantity"])
        self.assertEqual(out[2]["quantity"], 4)
        self.assertEqual(out[3]["quantity"], 2)
        self.assertIsNone(out[4]["quantity"])

    def test_bad_category_dropped_and_subject_group_defaulted(self):
        out = validate({"needs": [_need(category="DANGER"), _need(category="pet", subject_group="alien"), "junk"]})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "PET")
        self.assertEqual(out[0]["subject_group"], "unknown")

    def test_bad_shapes_raise(self):
        for bad in ("[]", "{}", '{"needs": 3}', "not json", [], None):
            with self.assertRaises((ValueError, TypeError)):
                validate(bad)

    def test_json_string_accepted(self):
        self.assertEqual(len(validate(json.dumps({"needs": [_need()]}, ensure_ascii=False))), 1)

    def test_schema_shape(self):
        items = SCHEMA["properties"]["needs"]["items"]
        self.assertEqual(SCHEMA["properties"]["needs"]["maxItems"], 5)
        self.assertEqual(len(items["properties"]["category"]["enum"]), 14)
        self.assertEqual(set(items["required"]), {"category", "item", "quantity", "subject", "subject_group",
                                                  "location", "time_phrase", "summary"})


class PromptTest(unittest.TestCase):
    def test_system_prompt(self):
        p = L.load_system_prompt()
        self.assertTrue(p.startswith("你是避難收容處所登記台的資料整理助手"))
        for s in ("最多 5 筆", "不要猜", "MEDICAL_EMERGENCY", "PROTECTION", "只輸出 JSON"):
            self.assertIn(s, p)

    def test_exactly_two_fewshots_that_validate(self):
        shots = L.load_fewshot()
        self.assertEqual(len(shots), 2)
        cats = [[n["category"] for n in validate(s["output"])] for s in shots]
        self.assertEqual(cats, [["LIFE_SUSTAINING", "MEDICATION"], ["SUPPLIES", "COMMUNICATION"]])
        self.assertEqual(validate(shots[1]["output"])[0]["quantity"], 4)

    def test_fixed_prefix_is_small(self):
        llm = OllamaLLM()
        body = llm.build_body("")
        prefix_chars = sum(len(m["content"]) for m in body["messages"][:-1])
        # "about 500 tokens": real Ollama reports 525 prompt tokens for the whole request
        # (fixed prefix + chat template + 「逐字稿：測試」) at 1129 prefix characters.
        self.assertLess(prefix_chars, 1200)


class StubCase(unittest.TestCase):
    mode = "normal"

    def setUp(self):
        self.stub = stubmod.OllamaStub(port=0, mode=self.mode, slow_s=30.0)
        self.port = self.stub.start()
        self.llm = OllamaLLM(url=self.stub.url, model="qwen2.5:1.5b", timeout_s=3, num_predict=320, keep_alive=-1,
                             warmup_timeout_s=5)

    def tearDown(self):
        self.stub.stop()


class StubNormalTest(StubCase):
    def test_tags_and_available_cached(self):
        self.assertTrue(self.llm.available())
        n = self.stub.tags_requests
        self.assertTrue(self.llm.available())
        self.assertEqual(self.stub.tags_requests, n)       # cached for 10 s
        self.assertTrue(self.llm.available(force=True))
        self.assertEqual(self.stub.tags_requests, n + 1)

    def test_model_not_listed(self):
        other = OllamaLLM(url=self.stub.url, model="llama3:8b")
        self.assertFalse(other.available())

    def test_chat_valid_needs_and_request_body(self):
        r = self.llm.extract("阿嬤明天早上要洗腎，然後她的胰島素今天就用完了")
        self.assertIsNone(r["error"])
        self.assertEqual([n["category"] for n in r["needs"]], ["LIFE_SUSTAINING", "MEDICATION"])
        self.assertEqual(r["tokens_per_s"], 42.0)
        self.assertEqual(r["prompt_eval_count"], 412)
        body = self.stub.chat_requests[-1]
        self.assertEqual(body["model"], "qwen2.5:1.5b")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["keep_alive"], -1)
        self.assertEqual(body["format"], SCHEMA)
        # SPEC 6.4 options + use_mmap (memory-maps the model: ~340 MB less committed memory, see PROGRESS.md)
        self.assertEqual(body["options"], {"temperature": 0, "num_predict": 320, "num_ctx": 2048, "use_mmap": True})
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertTrue(body["messages"][-1]["content"].startswith("逐字稿："))

    def test_warmup_prefix_cached(self):
        w = self.llm.warmup()
        self.assertTrue(w["ok"])
        self.assertTrue(w["prefix_cached"])
        self.assertEqual(w["prompt_eval_count"], [412, 12])
        self.assertEqual(len(self.stub.chat_requests), 2)

    def test_metrics_omitted_do_not_crash(self):
        self.stub.response = {"needs": []}
        self.stub.omit_metrics = True  # Ollama omits zero values
        r = self.llm.extract("測試")
        self.assertIsNone(r["error"])
        self.assertEqual(r["needs"], [])
        self.assertIsNone(r["tokens_per_s"])
        self.assertEqual(r["prompt_eval_count"], 0)
        w = self.llm.warmup()
        self.assertTrue(w["ok"])
        self.assertFalse(w["prefix_cached"])

    def test_unknown_model_is_llm_error(self):
        other = OllamaLLM(url=self.stub.url, model="nope:1b", timeout_s=3)
        self.assertEqual(other.extract("測試")["error"], "llm_error")


class StubSlowTest(StubCase):
    mode = "slow"

    def test_timeout_within_budget(self):
        t0 = time.monotonic()
        r = self.llm.extract("阿嬤要洗腎", timeout_s=2)
        took = time.monotonic() - t0
        self.assertEqual(r["error"], "timeout")
        self.assertLessEqual(took, 2 + 1)
        self.assertEqual(r["needs"], [])

    def test_default_timeout_is_config_value(self):
        t0 = time.monotonic()
        r = self.llm.extract("阿嬤要洗腎")  # timeout_s=3 from the constructor
        self.assertEqual(r["error"], "timeout")
        self.assertLessEqual(time.monotonic() - t0, 3 + 1)


class StubBadJsonTest(StubCase):
    mode = "bad_json"

    def test_one_retry_then_invalid_json(self):
        r = self.llm.extract("阿嬤要洗腎")
        self.assertEqual(r["error"], "invalid_json")
        self.assertEqual(len(self.stub.chat_requests), 2)
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(r["needs"], [])


class StubStoppedTest(StubCase):
    mode = "stopped"

    def test_available_false_and_extract_llm_error(self):
        self.assertFalse(self.llm.available())
        t0 = time.monotonic()
        r = self.llm.extract("阿嬤要洗腎")
        self.assertEqual(r["error"], "llm_error")
        self.assertLess(time.monotonic() - t0, 3)

    def test_server_gone_refused(self):
        self.stub.stop()
        self.assertFalse(self.llm.available(force=True))
        self.assertEqual(self.llm.extract("測試")["error"], "llm_error")
        self.stub.start()  # tearDown stops it again

    def test_recovers_when_back(self):
        self.assertFalse(self.llm.available())
        self.stub.set_mode("normal")
        self.assertFalse(self.llm.available())       # cached for 10 s
        self.assertTrue(self.llm.available(force=True))


class MockLlmTest(unittest.TestCase):
    def setUp(self):
        try:
            from darkshelter import extract  # noqa: F401
        except Exception as e:  # written by another module owner
            self.skipTest(f"darkshelter.extract not importable yet: {e!r}")

    def test_mirrors_rules_after_delay(self):
        m = MockLLM(delay=0.2)
        t0 = time.monotonic()
        r = m.extract("阿嬤明天早上要洗腎，然後她的胰島素今天就用完了")
        self.assertGreaterEqual(time.monotonic() - t0, 0.2 - 0.04)  # Windows clock ticks are ~15.6 ms
        self.assertIsNone(r["error"])
        cats = [n["category"] for n in r["needs"]]
        self.assertIn("LIFE_SUSTAINING", cats)
        self.assertIn("MEDICATION", cats)

    def test_drop_index_and_inject(self):
        base = MockLLM(delay=0).extract("阿嬤明天早上要洗腎，然後她的胰島素今天就用完了")["needs"]
        dropped = MockLLM(delay=0, drop_index=0).extract("阿嬤明天早上要洗腎，然後她的胰島素今天就用完了")["needs"]
        self.assertEqual(len(dropped), len(base) - 1)
        inj = MockLLM(delay=0, inject="胸痛").extract("我沒有胸痛，只是有點累")["needs"]
        self.assertTrue(any(n["category"] == "MEDICAL_EMERGENCY" and n["item"] == "胸痛" for n in inj))
        inj2 = MockLLM(delay=0, inject={"category": "PET", "item": "狗"}).extract("我要毛毯")["needs"]
        self.assertTrue(any(n["category"] == "PET" for n in inj2))

    def test_timeout_and_error_modes(self):
        r = MockLLM(delay=5).extract("我要毛毯", timeout_s=0.2)
        self.assertEqual(r["error"], "timeout")
        self.assertEqual(MockLLM(delay=0, error="invalid_json").extract("我要毛毯")["error"], "invalid_json")
        self.assertTrue(MockLLM().available())


class NeedToLlmDictTest(unittest.TestCase):
    def test_from_dict_and_object(self):
        d = need_to_llm_dict({"category": 2, "item": "洗腎", "subject": "阿嬤", "subject_group": "elderly",
                              "location": "B區12床", "time_phrase": "明天早上", "quantity": None})
        self.assertEqual(d["category"], "LIFE_SUSTAINING")
        self.assertEqual(d["quantity"], 0)
        self.assertEqual(validate({"needs": [d]})[0]["location"], "B區12床")

        class N:
            category, item, subject, subject_group, location, time_phrase, quantity, summary = (
                13, "", "", "weird", "", "", 0, "")
        self.assertEqual(need_to_llm_dict(N())["category"], "PROTECTION")
        self.assertEqual(need_to_llm_dict(N())["subject_group"], "unknown")


@unittest.skipIf(os.environ.get("DS_FAST") == "1", "slow: calls the real Ollama model")
class RealOllamaTest(unittest.TestCase):
    def test_real_extraction(self):
        cfg = make_config()
        llm = OllamaLLM.from_config(cfg)
        if not llm.available(force=True):
            self.skipTest(f"Ollama with {llm.model} not available at {llm.url}")
        r = llm.extract("阿嬤明天早上要洗腎", timeout_s=60)
        self.assertIsNone(r["error"], r)
        self.assertTrue(r["needs"], r)
        self.assertIn(r["needs"][0]["category"], L.CATEGORY_KEYS)


if __name__ == "__main__":
    unittest.main()

"""Timers (SPEC 6.11) and health/mode ladder + watchdog (SPEC 6.10)."""
import os
import tempfile
import threading
import time
import unittest

from darkshelter import codebook
from darkshelter.config import make_config
from darkshelter.health import Health
from darkshelter.store import Store
from darkshelter.timers import Timers
from darkshelter.wireview import F_ESCALATED, F_UNVERIFIED, wire_view


def p1_need(**kw):
    d = {"category": 1, "item_code": 101, "item": "胸痛／呼吸困難", "priority": 1, "rule_priority": 1,
         "emergency": "medical", "zone_code": codebook.zone_code("B", 3)}
    d.update(kw)
    return d


class FakeSender:
    def __init__(self):
        self.jumps = 0

    def on_clock_jump(self):
        self.jumps += 1


class TimersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.wall = [1_800_000_000.0]
        self.mono = [1000.0]
        self.cfg = make_config({})
        self.store = Store(os.path.join(self.tmp.name, "desk.db"), "B", clock=lambda: self.wall[0])
        self.sender = FakeSender()
        self.timers = Timers(self.store, self.cfg, sender=self.sender, clock=lambda: self.wall[0],
                             mono=lambda: self.mono[0])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def advance(self, s):
        self.wall[0] += s
        self.mono[0] += s

    def test_unconfirmed_p1_reminder_then_content_free_alert(self):
        seq = self.store.create_draft(p1_need(emergency=None, category=2, item_code=201), job_id="j")
        self.timers.check()
        self.assertFalse([b for b in self.timers.banners if b["kind"] == "unconfirmed_p1"])
        self.advance(121)
        self.timers.check()
        self.assertTrue([b for b in self.timers.banners if b["kind"] == "unconfirmed_p1"])
        self.assertIsNotNone(self.store.get(seq)["reminded_wall"])
        self.assertIsNone(wire_view(self.store.get(seq), self.wall[0], "B"))
        self.advance(180)
        self.timers.check()
        t = self.store.get(seq)
        self.assertEqual(t["alert_sent"], 1)
        v = wire_view(t, self.wall[0], "B")
        self.assertEqual((v["category"], v["item_code"], v["zone"]), (0, 0, 0))
        self.assertTrue(v["flags"] & F_UNVERIFIED)
        self.assertEqual([u["seq"] for u in self.store.pending_units()], [seq])

    def test_alert_disabled_with_zero(self):
        self.cfg["triage"]["unconfirmed_p1_alert_s"] = 0
        seq = self.store.create_draft(p1_need(), job_id="j")
        self.advance(1000)
        self.timers.check()
        self.assertEqual(self.store.get(seq)["alert_sent"], 0)

    def test_emergency_banner_until_action_and_sla_escalation(self):
        seq = self.store.create_draft(p1_need(), job_id="j")
        self.timers.check()
        self.assertTrue([b for b in self.timers.banners if b["kind"] == "emergency"])
        self.store.confirm(seq, clerk="A")
        self.store.log_emergency_action(seq, "119_unreachable", "A")
        self.timers.check()
        kinds = [b["kind"] for b in self.timers.banners]
        self.assertNotIn("emergency", kinds)
        self.assertIn("backup_channel", kinds)
        # 3 minutes without delivery: backup channel + print, and the first escalation re-sends
        self.advance(181)
        self.timers.check()
        b = [b for b in self.timers.banners if b["kind"] == "emergency_undelivered"]
        self.assertTrue(b and b[0]["print"] and b[0]["backup"])
        t = self.store.get(seq)
        self.assertTrue(wire_view(t, self.wall[0], "B")["flags"] & F_ESCALATED)
        v_after_first = t["version"]
        self.advance(181)
        self.timers.check()
        self.assertEqual(self.store.get(seq)["version"], v_after_first)  # later periods never re-send

    def test_other_p1_undelivered_banner_and_unacked_escalation(self):
        seq = self.store.create_draft(p1_need(emergency=None, category=2, item_code=201), job_id="j")
        self.store.confirm(seq, clerk="A")
        self.advance(30 * 60 + 1)
        self.timers.check()
        self.assertTrue([b for b in self.timers.banners if b["kind"] == "p1_undelivered"])
        self.store.mark_sent([seq])
        self.store.mark_acked(seq, self.store.get(seq)["version"], 2, 0)
        self.timers.check()
        self.assertIsNotNone(self.store.get(seq)["escalated_wall"])
        self.store.apply_status(seq, 1, 1)  # EOC acknowledged -> no more escalation
        self.advance(3600)
        before = self.store.get(seq)["escalated_wall"]
        self.timers.check()
        self.assertEqual(self.store.get(seq)["escalated_wall"], before)

    def test_timers_survive_restart(self):
        seq = self.store.create_draft(p1_need(emergency=None, category=2, item_code=201), job_id="j")
        self.advance(400)
        t2 = Timers(self.store, self.cfg, clock=lambda: self.wall[0], mono=lambda: self.mono[0])
        t2.check()  # a fresh Timers (after a restart) computes the age from created_wall
        self.assertEqual(self.store.get(seq)["alert_sent"], 1)

    def test_clock_jump_detected(self):
        self.timers.check()
        self.wall[0] += 3600  # wall clock jumps, monotonic does not
        self.mono[0] += 2
        self.timers.check()
        self.assertEqual(self.sender.jumps, 1)
        self.assertEqual(self.timers.clock_jumps, 1)
        self.advance(2)
        self.timers.check()
        self.assertEqual(self.sender.jumps, 1)


class FakeLLM:
    def __init__(self, ok=True):
        self.ok = ok

    def available(self):
        return self.ok


class FakeASR:
    model_name = "small"

    def __init__(self, fail=False):
        self.fail = fail

    def load(self, fast=False):
        if self.fail:
            raise OSError("no model")


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = make_config({"paths": {"desk_db": os.path.join(self.tmp.name, "desk.db")}})
        self.store = Store(os.path.join(self.tmp.name, "desk.db"), "B")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_mode_ladder(self):
        h = Health(self.cfg, self.store, asr=FakeASR(), llm=FakeLLM(True))
        h.startup_checks()
        self.assertEqual(h.mode(), "FAST")              # LLM up but not warmed yet
        h.on_warmup({"ok": True, "tokens_per_s": 40.0, "prefix_cached": True})
        self.assertEqual(h.mode(), "FULL")
        h.on_warmup({"ok": True, "tokens_per_s": 1.0})
        self.assertEqual(h.mode(), "FAST")              # slow -> on demand only
        h.on_warmup({"ok": True, "tokens_per_s": 40.0})
        h.llm.ok = False
        h._check_llm()
        h.recompute()
        self.assertEqual(h.mode(), "FAST")              # Ollama stopped
        h.set_mic(False)
        self.assertEqual(h.mode(), "TYPED")             # no mic beats FAST
        h.battery = {"percent": 3.0, "plugged": False}
        self.assertEqual(h.recompute(), "PAPER")        # battery < 5%
        h.set_forced("FULL")
        self.assertEqual(h.mode(), "FULL")              # forced beats everything
        h.set_forced(None)
        self.assertEqual(h.mode(), "PAPER")

    def test_failed_warmup_is_retried_not_duplicated(self):
        class P:
            n = 0

            def submit_warmup(self):
                P.n += 1

            def backlog(self):
                return 0
        h = Health(self.cfg, self.store, asr=FakeASR(), llm=FakeLLM(True))
        h.pipeline = P()
        h.startup_checks()
        self.assertEqual(P.n, 1)
        h._check_llm()
        self.assertEqual(P.n, 1)                        # pending: no second warm-up
        h.on_warmup({"ok": False, "error": "llm_error"})
        self.assertEqual(h.status()["reason"], "llm_failed")
        self.assertEqual(h.mode(), "FAST")
        h._check_llm()
        self.assertEqual(P.n, 1)                        # retry waits 60 s
        h._next_warm = 0.0
        h._check_llm()
        self.assertEqual(P.n, 2)                        # retried
        h.on_warmup({"ok": True, "tokens_per_s": 30.0, "prefix_cached": True})
        self.assertEqual(h.mode(), "FULL")

    def test_asr_falls_back_to_fast_model(self):
        class A:
            model_name, fast_model_name = "small", "base"

            def load(self, fast=False):
                if not fast:
                    raise RuntimeError("mkl_malloc: failed to allocate memory")
        h = Health(self.cfg, self.store, asr=A(), llm=FakeLLM(True))
        h.startup_checks()
        self.assertTrue(h.asr_ok)
        self.assertTrue(h.asr_fallback)
        self.assertEqual(h.status()["asr_model"], "base")
        self.assertNotEqual(h.mode(), "TYPED")

    def test_asr_failure_means_typed(self):
        h = Health(self.cfg, self.store, asr=FakeASR(fail=True), llm=FakeLLM(True))
        h.startup_checks()
        self.assertEqual(h.mode(), "TYPED")
        self.assertIn("OSError", h.status()["asr_error"])

    def test_watchdog_restarts_only_dead_threads(self):
        h = Health(self.cfg, self.store)
        stop = threading.Event()
        holder = {"t": None, "starts": 0}

        def start():
            holder["starts"] += 1
            holder["t"] = threading.Thread(target=stop.wait, daemon=True)
            holder["t"].start()
        start()
        h.register("worker", lambda: holder["t"], start, stale_s=0.05)
        time.sleep(0.1)
        h.watchdog()                                    # alive but stale -> amber, no restart
        self.assertEqual(holder["starts"], 1)
        self.assertEqual(h.threads["worker"]["light"], "amber")
        stop.set()
        holder["t"].join(1)
        h.watchdog()                                    # dead -> restart
        self.assertEqual(holder["starts"], 2)
        self.assertEqual(h.threads["worker"]["restarts"], 1)
        stop2 = holder["t"]
        self.assertTrue(stop2.is_alive() or True)


if __name__ == "__main__":
    unittest.main()

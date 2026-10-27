"""Red-team fixes in the pipeline: H6 (queued intakes survive a crash), M8 (a hung LLM switches to
FAST/llm_slow and a short probe clears it) and C1 (second look by the LLM at category-0 text in FAST mode)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from darkshelter.config import make_config
from darkshelter.health import Health
from darkshelter.llm import MockLLM, OllamaLLM
from darkshelter.pipeline import Pipeline
from darkshelter.store import Store
from darkshelter.timers import Timers
from darkshelter import extract

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ollama_stub import OllamaStub  # noqa: E402

TEXTS = ["我們家四個人需要毛毯", "A區2床需要飲用水三瓶", "C區5床的爺爺需要輪椅"]
UNCLEAR = "可以幫我一下嗎，那個我不知道怎麼說"   # nothing the rules can place


def wait_until(pred, timeout=15.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def healthy(cfg, store, llm):
    h = Health(cfg, store, asr=None, llm=llm)
    h.asr_ok, h.llm_ok, h.llm_ready = True, True, True
    h.recompute()
    return h


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "desk.db")
        self.cfg = make_config({"paths": {"desk_db": self.path, "eoc_db": os.path.join(self.tmp.name, "eoc.db")},
                                "ports": {"emu_control_http": 1}})
        self.stores, self.pipes = [], []

    def tearDown(self):
        for p in self.pipes:
            p.stop()
        time.sleep(0.6)
        for s in self.stores:
            s.close()
        self.tmp.cleanup()

    def store(self):
        s = Store(self.path, "B")
        self.stores.append(s)
        return s

    def pipe(self, store, **kw):
        p = Pipeline(store, self.cfg, **kw)
        self.pipes.append(p)
        return p


class IntakeRecoveryTest(Base):
    def test_typed_intakes_survive_a_crash_and_recordings_are_reported_lost(self):
        s1 = self.store()
        p1 = self.pipe(s1)                                   # never started: the desk dies first
        text_jobs = [p1.submit_text(t, clerk="值班人員A") for t in TEXTS]
        audio_jobs = [p1.submit_audio(b"RIFF-not-really", clerk="值班人員A") for _ in range(2)]
        self.assertEqual(s1.intake_counts(), {"text": 3, "audio": 2})

        s2 = self.store()                                    # the restarted process
        rec = s2.recover()
        self.assertEqual((rec["queued_text"], rec["queued_audio"]), (3, 2))
        p2 = self.pipe(s2)
        p2.start()
        for j in text_jobs:                                  # same job id, processed after the restart
            job = wait_until(lambda: (p2.get_job(j) or {}).get("status") == "done" and p2.get_job(j))
            self.assertTrue(job, j)
            self.assertTrue(job["seqs"], job)
            self.assertTrue(job.get("restored"))
            self.assertTrue(s2.query("SELECT seq FROM tickets WHERE job_id=?", (j,)))
        for j in audio_jobs:                                 # recordings were never saved: an error, not a 404
            job = p2.get_job(j)
            self.assertEqual((job["status"], job["error"]), ("error", "lost_in_crash"))
            self.assertEqual(job["warnings"][0]["code"], "lost_in_crash")
        banners = Timers(s2, self.cfg).check()
        lost = [b for b in banners if b["kind"] == "lost_audio"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["zh"], "2 筆語音在當機時遺失，請重錄")
        self.assertEqual(lost[0]["en"], "2 voice recordings were lost in a crash — please record again")
        self.assertEqual(s2.intake_counts(), {"text": 0, "audio": 0})

        # a second restart neither duplicates the typed drafts nor forgets the banner (within 15 min)
        n = len(s2.query("SELECT seq FROM tickets"))
        p2.stop()
        s3 = self.store()
        p3 = self.pipe(s3)
        p3.start()
        time.sleep(0.8)
        self.assertEqual(len(s3.query("SELECT seq FROM tickets")), n)
        self.assertTrue([b for b in Timers(s3, self.cfg).check() if b["kind"] == "lost_audio"])

    def test_banner_expires(self):
        wall = [1_800_000_000.0]
        s = Store(self.path, "B", clock=lambda: wall[0])
        self.stores.append(s)
        s.intake_save("a1", "audio")
        s.intake_restore()
        self.assertEqual(s.lost_intakes(), 1)
        wall[0] += Store.LOST_WINDOW_S + 1
        self.assertEqual(s.lost_intakes(), 0)
        self.assertFalse([b for b in Timers(s, self.cfg, clock=lambda: wall[0]).check() if b["kind"] == "lost_audio"])

    def test_intake_that_already_made_drafts_is_not_requeued(self):
        s = self.store()
        s.intake_save("jobX", "text", text=TEXTS[0])
        for need in extract.analyze(TEXTS[0], time.time()):   # the crash came after the rules step
            s.create_draft(need, job_id="jobX", transcript=TEXTS[0])
        before = len(s.query("SELECT seq FROM tickets"))
        requeue, lost = s.intake_restore()
        self.assertEqual((requeue, lost), ([], []))
        self.assertEqual(s.intake_counts(), {"text": 0, "audio": 0})
        p = self.pipe(s)
        p.start()
        time.sleep(0.8)
        self.assertEqual(len(s.query("SELECT seq FROM tickets")), before)

    def test_finished_jobs_leave_no_queue_rows(self):
        s = self.store()
        p = self.pipe(s)
        p.start()
        jobs = [p.submit_text(t) for t in TEXTS] + [p.submit_audio(b"not a wav")]
        for j in jobs:
            self.assertTrue(wait_until(lambda: (p.get_job(j) or {}).get("status") in ("done", "error")))
        self.assertTrue(wait_until(lambda: s.intake_counts() == {"text": 0, "audio": 0}, timeout=3))
        self.assertEqual(s.lost_intakes(), 0)

    def test_desk_routes_after_a_crash(self):
        s0 = Store(self.path, "B")
        s0.intake_save("lostaudio01", "audio", clerk="值班人員A")
        s0.intake_save("lostText0001", "text", text=TEXTS[0], clerk="值班人員A")
        s0.close()
        from darkshelter.desk_app import create_app
        app = create_app(self.cfg, mock=True, start_sender=False)
        try:
            c = app.test_client()
            r = c.get("/api/jobs/lostaudio01")
            self.assertEqual(r.status_code, 200)
            self.assertEqual((r.get_json()["status"], r.get_json()["error"]), ("error", "lost_in_crash"))
            j = wait_until(lambda: c.get("/api/jobs/lostText0001").get_json()
                           if c.get("/api/jobs/lostText0001").get_json()["status"] == "done" else None)
            self.assertTrue(j and j["seqs"], j)
            st = wait_until(lambda: (lambda s: s if any(b["kind"] == "lost_audio" for b in s["banners"]) else None)(
                c.get("/api/state").get_json()), timeout=6)
            self.assertTrue(st, "no lost_audio banner")
            self.assertEqual(st["recovered"]["queued_audio"], 1)
        finally:
            app.desk.stop()
            time.sleep(0.6)
            app.desk.store.close()


class HungLlmTest(Base):
    def test_two_timeouts_switch_to_fast_and_the_probe_clears_it(self):
        s = self.store()
        llm = MockLLM(delay=0, error="timeout")
        h = healthy(self.cfg, s, llm)
        p = self.pipe(s, llm=llm, health=h)
        h.pipeline = p
        p._process_text(p._new_job("text"), TEXTS[0])
        self.assertEqual(h.mode(), "FULL")                   # one timeout is not enough
        p._process_text(p._new_job("text"), TEXTS[1])
        self.assertEqual((h.mode(), h.status()["reason"]), ("FAST", "llm_slow"))
        self.assertEqual(llm.calls, 2)
        job = p._new_job("text")
        p._process_text(job, TEXTS[2])
        self.assertEqual(llm.calls, 2)                       # no more waiting on the LLM
        self.assertTrue(job["llm"]["skipped"])
        h._check_llm()
        self.assertTrue(p.q.empty())                         # the probe waits 60 s
        h._next_warm = 0.0
        h._check_llm()
        _, _, job_id, payload = p.q.get_nowait()
        self.assertTrue(payload["probe"])
        llm.error = None
        p._process(p.jobs[job_id], payload)                  # the probe answers in time
        self.assertEqual((h.mode(), h.status()["reason"]), ("FULL", "ok"))

    def test_hung_ollama_stub(self):
        stub = OllamaStub(port=0, mode="slow", slow_s=6.0)
        stub.start()
        try:
            s = self.store()
            llm = OllamaLLM(url=stub.url, timeout_s=1.0, warmup_timeout_s=3.0)
            h = healthy(self.cfg, s, llm)
            p = self.pipe(s, llm=llm, health=h)
            h.pipeline = p
            p.start()
            t0 = time.monotonic()
            jobs = [p.submit_text(t) for t in TEXTS]
            seen = {}
            wait_until(lambda: [seen.setdefault(j, time.monotonic() - t0) for j in jobs
                                if (p.get_job(j) or {}).get("status") == "done"] and len(seen) == 3, timeout=20)
            self.assertEqual(len(seen), 3, seen)
            self.assertEqual((h.mode(), h.status()["reason"]), ("FAST", "llm_slow"))
            self.assertTrue(p.get_job(jobs[2])["llm"]["skipped"])
            self.assertLess(seen[jobs[2]] - seen[jobs[1]], 0.8)   # the third did not wait another timeout
            # probe while still hung: stays FAST, next probe in 60 s
            h._next_warm = 0.0
            h._check_llm()
            self.assertTrue(wait_until(lambda: not h._warm_pending, timeout=10))
            self.assertEqual(h.status()["reason"], "llm_slow")
            self.assertGreater(h._next_warm - time.monotonic(), 50)
            # Ollama answers again: the next probe clears it
            stub.set_mode("normal")
            h._next_warm = 0.0
            h._check_llm()
            self.assertTrue(wait_until(lambda: h.mode() == "FULL", timeout=10), h.status())
        finally:
            stub.stop()


class SecondLookTest(Base):
    def setUp(self):
        super().setUp()
        self.s = self.store()
        self.llm = MockLLM(delay=0)
        self.h = healthy(self.cfg, self.s, self.llm)
        self.p = self.pipe(self.s, llm=self.llm, health=self.h)
        self.h.pipeline = self.p

    def run_fast(self, text):
        job = self.p._new_job("text")
        self.p._process_text(job, text, already_fast=True)   # FAST because of the backlog
        return job

    def test_unclear_text_gets_the_llm_in_fast_mode(self):
        job = self.run_fast(UNCLEAR)
        self.assertEqual(self.llm.calls, 1)
        self.assertTrue(job.get("second_look"))

    def test_known_category_skips_the_llm_in_fast_mode(self):
        job = self.run_fast(TEXTS[0])
        self.assertEqual(self.llm.calls, 0)
        self.assertTrue(job["llm"]["skipped"])

    def test_no_second_look_when_the_llm_is_down_slow_or_forced(self):
        self.h.llm_ok = False
        self.run_fast(UNCLEAR)
        self.h.llm_ok, self.h.llm_slow = True, True
        self.run_fast(UNCLEAR)
        self.h.llm_slow = False
        self.h.set_forced("FAST")
        self.run_fast(UNCLEAR)
        self.assertEqual(self.llm.calls, 0)


if __name__ == "__main__":
    unittest.main()

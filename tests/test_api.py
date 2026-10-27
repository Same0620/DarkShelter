"""Desk HTTP API via the Flask test client (mock ASR/LLM, no network threads)."""
import io
import os
import tempfile
import time
import unittest
import wave

from darkshelter.config import make_config
from darkshelter.desk_app import create_app


def wav_bytes(seconds=1.5, freq=440.0, sr=16000, amp=0.3):
    import math
    n = int(seconds * sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(int(amp * 32767 * math.sin(2 * math.pi * freq * i / sr)).to_bytes(2, "little", signed=True)
                               for i in range(n)))
    return buf.getvalue()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cfg = make_config({"paths": {"desk_db": os.path.join(cls.tmp.name, "desk.db"),
                                     "eoc_db": os.path.join(cls.tmp.name, "eoc.db")},
                           "ports": {"emu_control_http": 1}})  # nothing listens there
        cls.app = create_app(cfg, mock=True, start_sender=False)
        cls.desk = cls.app.desk
        cls.c = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.desk.stop()
        time.sleep(0.6)
        cls.desk.store.close()
        cls.tmp.cleanup()

    def wait_job(self, job_id, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.c.get(f"/api/jobs/{job_id}").get_json()
            if j["status"] in ("done", "error"):
                return j
            time.sleep(0.1)
        self.fail(f"job {job_id} did not finish")

    def intake(self, text):
        r = self.c.post("/api/intake/text", json={"text": text, "clerk": "值班人員A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = self.wait_job(r.get_json()["job_id"])
        self.assertEqual(j["status"], "done", j)
        return j

    def drafts_of(self, job):
        st = self.c.get("/api/state").get_json()
        return [d for d in st["drafts"] if d["seq"] in job["seqs"]]

    def test_healthz_and_state(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)
        st = self.c.get("/api/state").get_json()
        for k in ("drafts", "board", "health", "census", "bytes", "banners", "jobs", "sender"):
            self.assertIn(k, st)
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        r.close()
        js = self.c.get("/static/desk/app.js")
        self.assertIn("javascript", js.headers["Content-Type"])
        js.close()

    def test_checkin_two_yes_two_drafts(self):
        r = self.c.post("/api/checkin", json={"size": 3, "zone": "B12", "elderly": 1, "clerk": "A",
                                              "screening": {"q1": True, "q2": True, "q3": False}})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(len(d["drafts"]), 2)
        st = self.c.get("/api/state").get_json()
        mine = [t for t in st["drafts"] if t["seq"] in d["drafts"]]
        self.assertEqual({t["category"] for t in mine}, {2, 3})
        self.assertTrue(all(t["zone_label"] == "B區12床" for t in mine))
        self.assertTrue(all(t["missing_info"] for t in mine))
        self.assertGreaterEqual(st["census"]["people"], 3)

    def test_intake_confirm_board(self):
        job = self.intake("阿嬤明天早上要洗腎")
        drafts = self.drafts_of(job)
        self.assertTrue(drafts)
        t = [d for d in drafts if d["category"] == 2][0]
        self.assertEqual(t["priority"], 1)
        r = self.c.post(f"/api/tickets/{t['seq']}/confirm", json={"priority": 1, "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        board = self.c.get("/api/state").get_json()["board"]["tickets"]
        row = [b for b in board if b["seq"] == t["seq"]][0]
        self.assertIn("洗腎", row["wire_summary"])
        self.assertEqual(row["wire_state"]["icon"], "⏳")

    def test_lowering_needs_reason_409(self):
        job = self.intake("阿公已經兩天沒洗腎了")
        t = [d for d in self.drafts_of(job) if d["priority"] == 1][0]
        r = self.c.post(f"/api/tickets/{t['seq']}/confirm", json={"priority": 2, "clerk": "A"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "reason_required")
        self.assertIn("zh", r.get_json())

    def test_stale_version_409(self):
        job = self.intake("我們需要毛毯")
        t = self.drafts_of(job)[0]
        self.c.post(f"/api/tickets/{t['seq']}/confirm", json={"clerk": "A"})
        r1 = self.c.post(f"/api/tickets/{t['seq']}/update", json={"fields": {"quantity": 3}, "expected_version": 1})
        self.assertEqual(r1.status_code, 200)
        r2 = self.c.post(f"/api/tickets/{t['seq']}/update", json={"fields": {"quantity": 4}, "expected_version": 1})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.get_json()["error"], "stale_version")

    def test_worried_and_emergency_action(self):
        job = self.intake("我想要手機充電")
        t = self.drafts_of(job)[0]
        self.assertEqual(t["priority"], 3)
        r = self.c.post(f"/api/tickets/{t['seq']}/worried", json={"clerk": "A"})
        self.assertEqual(r.get_json()["ticket"]["priority"], 1)
        job = self.intake("我爸胸口很痛，很喘")
        em = [d for d in self.drafts_of(job) if d["emergency"]][0]
        self.assertEqual(em["priority"], 1)
        r = self.c.post(f"/api/tickets/{em['seq']}/emergency_action", json={"action": "called_119", "clerk": "A"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["ticket"]["emergency_action"], "called_119")
        bad = self.c.post(f"/api/tickets/{em['seq']}/emergency_action", json={"action": "nope"})
        self.assertEqual(bad.status_code, 409)

    def test_protection_hidden_from_board_and_export(self):
        job = self.intake("我先生會打我，我不敢回家")
        prot = [d for d in self.drafts_of(job) if d["category"] == 13]
        self.assertTrue(prot, self.drafts_of(job))
        seq = prot[0]["seq"]
        self.c.post(f"/api/tickets/{seq}/confirm", json={"clerk": "A"})
        st = self.c.get("/api/state").get_json()
        self.assertNotIn(seq, [b["seq"] for b in st["board"]["tickets"]])
        self.assertGreaterEqual(st["board"]["hidden_count"], 1)
        csv_text = self.c.get("/export.csv").get_data(as_text=True)
        self.assertNotIn(f"B-{seq:03d}", csv_text)
        self.assertNotIn("打我", csv_text)
        self.assertNotIn("保護", csv_text)

    def test_discard_restore_and_merge(self):
        j1 = self.intake("C區5床需要輪椅")
        a = self.drafts_of(j1)[0]
        self.assertEqual(self.c.post(f"/api/tickets/{a['seq']}/discard", json={"clerk": "A"}).status_code, 200)
        self.assertEqual(self.c.post(f"/api/tickets/{a['seq']}/restore", json={"clerk": "A"}).status_code, 200)
        j2 = self.intake("C區5床也要輪椅")
        b = self.drafts_of(j2)[0]
        r = self.c.post(f"/api/tickets/{b['seq']}/merge", json={"target": a["seq"], "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["ticket"]["status"], "merged")

    def test_audio_intake_mock(self):
        r = self.c.post("/api/intake/audio?clerk=A&mock_text=我需要飲用水", data=wav_bytes(1.5),
                        content_type="audio/wav")
        j = self.wait_job(r.get_json()["job_id"])
        self.assertEqual(j["status"], "done")
        self.assertGreaterEqual(j["duration_s"], 1.4)
        self.assertTrue(j["seqs"])
        silent = self.c.post("/api/intake/audio?clerk=A", data=wav_bytes(1.5, amp=0.0), content_type="audio/wav")
        js = self.wait_job(silent.get_json()["job_id"])
        self.assertEqual(js["seqs"], [])
        self.assertIn("silent", [w["code"] for w in js["warnings"]])

    def test_body_limit(self):
        r = self.c.post("/api/intake/audio", data=b"0" * (5 * 1024 * 1024 + 10), content_type="audio/wav")
        self.assertEqual(r.status_code, 413)

    def test_mode_and_mic(self):
        r = self.c.post("/api/mode", json={"mode": "PAPER"})
        self.assertEqual(r.get_json()["health"]["mode"], "PAPER")
        self.c.post("/api/mode", json={"mode": None})
        r = self.c.post("/api/mic", json={"ok": False})
        self.assertEqual(r.get_json()["mode"], "TYPED")
        self.c.post("/api/mic", json={"ok": True})


if __name__ == "__main__":
    unittest.main()

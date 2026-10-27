"""Desk HTTP API via the Flask test client (mock ASR/LLM, no network threads)."""
import io
import json
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

    AUDIO_HDR = {"X-DarkShelter": "1"}

    def test_audio_intake_mock(self):
        r = self.c.post("/api/intake/audio?clerk=A&mock_text=我需要飲用水", data=wav_bytes(1.5),
                        content_type="audio/wav", headers=self.AUDIO_HDR)
        j = self.wait_job(r.get_json()["job_id"])
        self.assertEqual(j["status"], "done")
        self.assertGreaterEqual(j["duration_s"], 1.4)
        self.assertTrue(j["seqs"])
        silent = self.c.post("/api/intake/audio?clerk=A", data=wav_bytes(1.5, amp=0.0), content_type="audio/wav",
                             headers=self.AUDIO_HDR)
        js = self.wait_job(silent.get_json()["job_id"])
        self.assertEqual(js["seqs"], [])
        self.assertIn("silent", [w["code"] for w in js["warnings"]])

    def test_body_limit(self):
        r = self.c.post("/api/intake/audio", data=b"0" * (5 * 1024 * 1024 + 10), content_type="audio/wav",
                        headers=self.AUDIO_HDR)
        self.assertEqual(r.status_code, 413)

    # ------------------------------------------------------------ red-team regressions (REDTEAM.md)
    def draft_seq(self, text="A區2床需要飲用水三瓶"):
        return self.drafts_of(self.intake(text))[0]["seq"]

    def test_csrf_simple_requests_refused(self):
        """H2: cross-site 'simple' requests (form, text/plain, foreign Origin) change nothing."""
        seq = self.draft_seq()
        url = f"/api/tickets/{seq}/confirm"
        r = self.c.post(url + "?clerk=attacker", data="{}", content_type="text/plain")
        self.assertEqual(r.status_code, 415, r.get_data(as_text=True))
        r = self.c.post(url, data={"clerk": "attacker"})  # form-encoded
        self.assertEqual(r.status_code, 415)
        r = self.c.post(url, json={"clerk": "A"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "cross_origin")
        r = self.c.post(url, json={"clerk": "A"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/mic", data="{}", content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.desk.store.get(seq)["status"], "draft")
        r = self.c.post("/api/intake/audio", data=wav_bytes(1.0), content_type="audio/wav")  # no X-DarkShelter
        self.assertEqual(r.status_code, 403)
        # same-origin JSON still works, and the clerk is never read from the query string
        r = self.c.post(url + "?clerk=attacker", json={}, headers={"Origin": "http://localhost",
                                                                   "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.desk.store.get(seq)["confirmed_by"], "")

    def test_host_header_checked(self):
        """M5: DNS rebinding — only loopback names (and this server's port) are served."""
        self.assertEqual(self.c.get("/api/state", base_url="http://evil.example:8000").status_code, 400)
        self.assertEqual(self.c.get("/api/state", base_url="http://evil.example").status_code, 400)
        r = self.c.get("/api/state", base_url="http://127.0.0.1:1234", environ_overrides={"SERVER_PORT": "8000"})
        self.assertEqual(r.status_code, 400)
        for base in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000", "http://localhost"):
            self.assertEqual(self.c.get("/healthz", base_url=base).status_code, 200, base)

    def test_round2_no_framing(self):
        """Round 2: no other site may frame the desk (clickjacking a 結案 click past the guard)."""
        for path in ("/", "/api/state", "/export.csv"):
            r = self.c.get(path)
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(r.headers.get("Content-Security-Policy"), "frame-ancestors 'none'", path)
        r = self.c.get("/api/state", base_url="http://evil.example:8000")  # refused responses too
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")

    def test_round2_guard_bypasses_and_false_blocks(self):
        """Round 2 probes: odd Origins and Hosts are refused; what real Chrome and urllib send is not."""
        seq = self.draft_seq()
        url = f"/api/tickets/{seq}/confirm"
        r = self.c.post(url, json={"clerk": "A"}, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (403, "cross_origin"))  # sandboxed iframe, file://
        r = self.c.post(url, data='--x\r\nContent-Disposition: form-data; name="clerk"\r\n\r\nA\r\n--x--\r\n',
                        content_type="multipart/form-data; boundary=x")
        self.assertEqual(r.status_code, 415)
        for host in ("127.0.0.1.:8000", "localhost.:8000", "127.1:8000", "0x7f000001:8000", "[::ffff:127.0.0.1]:8000",
                     "evil.example@127.0.0.1:8000", "127.0.0.1:8000@evil.example", "127.0.0.1:8000:8000", ""):
            r = self.c.get("/api/state", environ_overrides={"HTTP_HOST": host})
            self.assertEqual(r.status_code, 400, host)
        self.assertEqual(self.desk.store.get(seq)["status"], "draft")
        # not blocked: Chrome's same-origin headers at both addresses, a charset parameter, lower-case header names
        for base in ("http://127.0.0.1:8000", "http://localhost:8000"):
            r = self.c.post("/api/intake/text", base_url=base, json={"text": "我想要手機充電"},
                            headers={"Origin": base, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"})
            self.assertEqual(r.status_code, 200, (base, r.get_data(as_text=True)))
            r = self.c.post("/api/intake/audio", base_url=base, data=wav_bytes(1.0), content_type="audio/wav",
                            headers={"Origin": base, "Sec-Fetch-Site": "same-origin", "x-darkshelter": "1"})
            self.assertEqual(r.status_code, 200, (base, r.get_data(as_text=True)))
        r = self.c.post("/api/intake/text", data=json.dumps({"text": "我想要手機充電"}),
                        content_type="application/json; charset=utf-8")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_deadline_text_never_from_client(self):
        """M2: a client-supplied deadline_text is ignored; the text is derived from deadline_at."""
        seq = self.draft_seq()
        r = self.c.post(f"/api/tickets/{seq}/update",
                        json={"fields": {"deadline_text": "王小明 0912 345 678 A 123 456 789"}, "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        tv = r.get_json()["ticket"]
        self.assertNotIn("王小明", tv["deadline_text"] + tv["wire_summary"])
        self.assertNotIn("0912", tv["wire_summary"])
        dl = time.time() + 3 * 3600
        r = self.c.post(f"/api/tickets/{seq}/confirm", json={"fields": {"deadline_at": dl}, "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        tv = r.get_json()["ticket"]
        want = time.strftime("%m/%d %H:%M", time.localtime(dl))
        self.assertEqual(tv["deadline_text"], want)
        self.assertIn(want, tv["wire_summary"])

    def test_out_of_range_fields_rejected(self):
        """M6 + L4: bad codes, ranges and types give 400 (never 200, never 500)."""
        seq = self.draft_seq()
        url = f"/api/tickets/{seq}/update"
        bad = [{"category": 999}, {"category": "1"}, {"item_code": 999999}, {"quantity": -5},
               {"quantity": 10 ** 12}, {"quantity": 1.5}, {"zone_code": "abc"}, {"zone_code": -1},
               {"deadline_at": "tomorrow"}, {"subject_group": "alien"}, {"missing_info": "x"}, {"location": 5}]
        for f in bad:
            r = self.c.post(url, json={"fields": f, "clerk": "A"})
            self.assertEqual(r.status_code, 400, f"{f}: {r.status_code} {r.get_data(as_text=True)}")
            self.assertIn("error", r.get_json())
        r = self.c.post(url, json={"fields": "abc", "clerk": "A"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post(url, json={"fields": {}, "expected_version": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post(url, json=["not", "an", "object"])
        self.assertEqual(r.status_code, 400)
        for p in ("1", 1.5, True, "abc"):
            r = self.c.post(f"/api/tickets/{seq}/confirm", json={"priority": p, "clerk": "A"})
            self.assertEqual(r.status_code, 400, f"priority {p!r}: {r.get_data(as_text=True)}")
        r = self.c.post(f"/api/tickets/{seq}/merge", json={"target": "x", "clerk": "A"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/intake/text", json={"text": "需要毛毯", "household_id": "abc"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/intake/text", json={"text": ["需要毛毯"]})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/checkin", json={"size": 2, "elderly": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.desk.store.get(seq)["status"], "draft")
        # valid edits still work
        r = self.c.post(url, json={"fields": {"quantity": 4, "quantity_approx": True}, "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["ticket"]["quantity"], 4)

    def test_csv_formula_cells_quoted(self):
        """L1: a cell that starts with = + - @ is prefixed with ' in /export.csv."""
        import csv as _csv
        seq = self.draft_seq("我需要毛毯")
        payload = '=HYPERLINK("http://evil.example","x")'
        r = self.c.post(f"/api/tickets/{seq}/confirm",
                        json={"fields": {"location": payload, "zone_code": 65535}, "clerk": "A"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.c.get("/export.csv")
        text = r.get_data().decode("utf-8-sig")
        r.close()
        rows = list(_csv.reader(io.StringIO(text)))
        self.assertTrue(any(payload in cell for row in rows for cell in row), "payload should still be visible")
        for row in rows[1:]:
            for cell in row:
                self.assertFalse(cell.startswith(("=", "+", "-", "@", "\t", "\r")), cell)

    def test_mode_and_mic(self):
        r = self.c.post("/api/mode", json={"mode": "PAPER"})
        self.assertEqual(r.get_json()["health"]["mode"], "PAPER")
        self.c.post("/api/mode", json={"mode": None})
        r = self.c.post("/api/mic", json={"ok": False})
        self.assertEqual(r.get_json()["mode"], "TYPED")
        self.c.post("/api/mic", json={"ok": True})

    def test_privacy_view_default_in_state(self):
        """L8: the desk page takes its privacy-view default from the config."""
        st = self.c.get("/api/state").get_json()
        self.assertIs(st["desk"]["privacy_view_default"], True)
        js = self.c.get("/static/desk/app.js")
        self.assertIn("privacy_view_default", js.get_data(as_text=True))
        js.close()


class EocGuardTest(unittest.TestCase):
    """H2 + M5 on the EOC app."""

    @classmethod
    def setUpClass(cls):
        from darkshelter.eoc_app import create_app as eoc_app
        from darkshelter.net.eoc_store import EocStore
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.cfg = make_config({"ports": {"eoc_http": 8100}})
        cls.store = EocStore(os.path.join(cls.tmp.name, "eoc.db"))
        cls.c = eoc_app(cls.cfg, cls.store).test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.store.close()
        except Exception:
            pass
        cls.tmp.cleanup()

    def test_guards(self):
        url = "/api/tickets/7/1/status"
        self.assertEqual(self.c.post(url, data='{"status": 2}', content_type="text/plain").status_code, 415)
        r = self.c.post(url, json={"status": 2}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.post(url, json=[2]).status_code, 400)
        self.assertEqual(self.c.post(url, json={"status": 2}).status_code, 404)  # unknown ticket, but allowed
        self.assertEqual(self.c.get("/api/state", base_url="http://evil.example:8100").status_code, 400)
        self.assertEqual(self.c.get("/api/state", base_url="http://127.0.0.1:8100").status_code, 200)

    def test_round2_no_framing_and_null_origin(self):
        for path in ("/", "/api/state"):
            r = self.c.get(path, base_url="http://127.0.0.1:8100")
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(r.headers.get("Content-Security-Policy"), "frame-ancestors 'none'", path)
        r = self.c.post("/api/tickets/7/1/status", json={"status": 3}, headers={"Origin": "null"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/tickets/7/1/status", base_url="http://localhost:8100", json={"status": 2},
                        headers={"Origin": "http://localhost:8100", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 404)  # passes the guard; the ticket just doesn't exist


if __name__ == "__main__":
    unittest.main()

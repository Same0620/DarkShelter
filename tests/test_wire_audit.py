"""Wire audit (SPEC §11, acceptance check 6).

For every ticket of the text test set (plus privacy cases), the packed DATA_SUMMARY bytes contain no phone or ID
digits, no protection details and no free-text subject. Also:
- content-free alert -> discard -> the bytes carry no item and no content summary
- never sent + discarded -> nothing is sent
"""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from darkshelter import codebook
from darkshelter.config import make_config, hmac_key
from darkshelter.extract import analyze
from darkshelter.net import codec
from darkshelter.store import Store
from darkshelter.wireview import build_summary, wire_view

ROOT = Path(__file__).resolve().parent.parent
NOW = time.mktime((2026, 10, 28, 10, 0, 0, 0, 0, -1))

PRIVACY_CASES = [
    "我是王小明，身分證A123456789，電話0912-345-678，我阿嬤在B區12床明天早上要洗腎",
    "我先生陳大同一直打我，我不敢回家，電話0987654321",
    "我女兒林小美找不到了，她穿紅色外套，手機0911222333",
    "他叫張阿土，愛滋病的藥沒帶出來，在C區3床",
    "我老婆懷孕八個月，破水了，我們在A區5床，我的證號B287654321",
    "我爸還在中正路三巷的家裡沒出來，電話0922-111-222",
    "家暴，他打我，我需要保護令",
]


def vocabulary():
    cb = codebook.load()
    words = set()
    for c in cb["categories"]:
        words.add(c["zh"])
        words.update(re.split(r"[（）、，／]", c["zh"]))
    for i in cb["items"]:
        words.add(i["zh"])
        words.update(re.split(r"[（）、，／]", i["zh"]))
    for g in cb["subject_groups"]:
        words.add(g["zh"])
    return {w for w in words if w}


def load_texts():
    texts = list(PRIVACY_CASES)
    p = ROOT / "eval" / "text_cases.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                texts.append(json.loads(line)["text"])
    return texts


def digits_runs(text, n=6):
    return set(re.findall(r"\d{%d,}" % n, re.sub(r"[-\s]", "", text)))


class WireAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = make_config({})
        self.key = hmac_key(self.cfg)
        self.store = Store(os.path.join(self.tmp.name, "desk.db"), "B", clock=lambda: NOW)
        self.vocab = vocabulary()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def pack(self, view):
        data, n = codec.pack_summary([view], 200, self.key, 7, 1, 1)
        self.assertEqual(n, 1)
        self.assertLessEqual(len(data), 200)
        return data

    def test_every_ticket_is_clean_on_the_wire(self):
        texts = load_texts()
        self.assertGreaterEqual(len(texts), 50)
        checked = 0
        for text in texts:
            for need in analyze(text, NOW):
                seq = self.store.create_draft(need, job_id="audit", transcript=text)
                t = self.store.confirm(seq, clerk="audit")
                v = wire_view(t, NOW, "B")
                self.assertIsNotNone(v)
                data = self.pack(v)
                msg = codec.unpack(data, {7: self.key})
                self.assertEqual(msg.records[0]["summary"], v["summary"])
                # no phone / ID digits
                for run in digits_runs(text):
                    self.assertNotIn(run.encode("ascii"), data, f"digits {run} leaked: {text}")
                self.assertIsNone(re.search(rb"[A-Za-z][1289]\d{8}", data), text)
                # no free-text subject (unless the words are codebook vocabulary anyway)
                subj = (t.get("subject") or "").strip()
                if subj and subj not in self.vocab and not any(subj in w for w in self.vocab):
                    self.assertNotIn(subj.encode("utf-8"), data, f"subject {subj!r} leaked: {text}")
                # transcript never leaves; names never leave
                self.assertNotIn(text.encode("utf-8"), data)
                for name in ("王小明", "陳大同", "林小美", "張阿土"):
                    self.assertNotIn(name.encode("utf-8"), data)
                if t["category"] == codebook.PROTECTION:
                    rec = msg.records[0]
                    self.assertEqual((rec["item_code"], rec["quantity"], rec["zone"]), (0, 0, 0))
                    self.assertIn("保護個案", rec["summary"])
                    for w in ("打", "家暴", "保護令", "先生", "不敢回家"):
                        self.assertNotIn(w.encode("utf-8"), data, f"protection detail {w}")
                if "sensitive" in (t.get("flags") or []):
                    rec = msg.records[0]
                    self.assertEqual(rec["item_code"], 0)
                    for w in ("愛滋", "HIV", "PrEP", "精神科", "美沙冬"):
                        self.assertNotIn(w.encode("utf-8"), data)
                checked += 1
        self.assertGreater(checked, 50)

    def test_alert_then_discard_carries_no_content(self):
        text = "阿嬤B區12床明天早上要洗腎"
        need = [n for n in analyze(text, NOW) if n.priority == 1][0]
        seq = self.store.create_draft(need, job_id="a", transcript=text)
        self.store.mark_alert_sent(seq)
        t = self.store.get(seq)
        content_summary = build_summary(dict(t, confirmed_wall=NOW), "B")
        alert = wire_view(t, NOW, "B")
        self.assertEqual((alert["item_code"], alert["category"], alert["quantity"], alert["zone"]), (0, 0, 0, 0))
        self.store.mark_sent([seq])
        self.store.discard(seq, "A", "誤報")
        v = wire_view(self.store.get(seq), NOW, "B")
        self.assertIsNotNone(v)  # the withdrawal of an announced alert still goes out
        data = self.pack(v)
        rec = codec.unpack(data, {7: self.key}).records[0]
        self.assertEqual((rec["item_code"], rec["category"], rec["quantity"], rec["zone"]), (0, 0, 0, 0))
        self.assertNotIn(content_summary.encode("utf-8"), data)
        for w in ("洗腎", "B區12床", "阿嬤", "長者"):
            self.assertNotIn(w.encode("utf-8"), data)

    def test_never_sent_and_discarded_is_never_sent(self):
        need = analyze("我們需要毛毯", NOW)[0]
        seq = self.store.create_draft(need, job_id="n", transcript="我們需要毛毯")
        self.store.confirm(seq, clerk="A")
        self.assertEqual([v["seq"] for v in self.store.pending_units()], [seq])
        self.store.discard(seq, "A")
        self.assertIsNone(wire_view(self.store.get(seq), NOW, "B"))
        self.assertEqual(self.store.pending_units(), [])

    def test_unconfirmed_draft_never_leaves(self):
        need = analyze("我需要飲用水", NOW)[0]
        seq = self.store.create_draft(need, job_id="n", transcript="我需要飲用水")
        self.assertIsNone(wire_view(self.store.get(seq), NOW, "B"))
        self.assertEqual(self.store.pending_units(), [])


class RedTeamWireTest(unittest.TestCase):
    """Regressions for REDTEAM.md M2 (deadline text) and M3 (protection sibling located by its zone)."""

    B12 = codebook.parse_location("B區12床")[0]
    BLANKET = codebook.item_by_zh("毛毯")["code"]

    def ticket(self, **kw):
        t = {"seq": 2, "version": 1, "status": "confirmed", "confirmed_wall": NOW - 60, "created_wall": NOW - 120,
             "priority": 3, "category": 8, "item_code": self.BLANKET, "quantity": 0, "zone_code": self.B12,
             "location": "B區12床", "subject_group": "unknown", "transcript": "", "flags": []}
        t.update(kw)
        return t

    def test_protection_sibling_hides_location(self):
        text = "B區12床的王小姐被先生家暴，她也需要毛毯"
        v = wire_view(self.ticket(transcript=text), NOW, "B")
        self.assertEqual(v["zone"], 0)
        self.assertNotIn("B區12床", v["summary"])
        self.assertIn("毛毯", v["summary"])
        # the same need without a protection cue keeps its bed
        v = wire_view(self.ticket(transcript="B區12床的王小姐需要毛毯"), NOW, "B")
        self.assertEqual(v["zone"], self.B12)
        self.assertIn("B區12床", v["summary"])
        # a P1 rescue at an outside address still goes out (life first), with no protection wording
        v = wire_view(self.ticket(priority=1, category=4, item_code=0, zone_code=codebook.ZONE_EXTERNAL,
                                  location="中正路三巷", transcript="我先生打我，我女兒還被困在中正路三巷"), NOW, "B")
        self.assertEqual(v["zone"], codebook.ZONE_EXTERNAL)
        self.assertIn("中正路三巷", v["summary"])
        for w in ("打", "家暴", "保護"):
            self.assertNotIn(w, v["summary"])

    def test_protection_sibling_end_to_end(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        store = Store(os.path.join(tmp.name, "desk.db"), "B")
        try:
            text = "B區12床的王小姐被先生家暴，她也需要毛毯"
            seqs = [store.create_draft(n, job_id="j", transcript=text) for n in analyze(text, NOW)]
            self.assertGreaterEqual(len(seqs), 2)
            key = hmac_key(make_config({}))
            for seq in seqs:
                store.confirm(seq, clerk="A")
                v = wire_view(store.get(seq), NOW, "B")
                data, n = codec.pack_summary([v], 200, key, 7, 1, 1)
                self.assertEqual(n, 1)
                self.assertEqual(v["zone"], 0, v)
                self.assertNotIn("B區12床", v["summary"])
                self.assertNotIn("B區12床".encode("utf-8"), data)
        finally:
            store.close()
            tmp.cleanup()

    def test_deadline_text_rebuilt_from_deadline_at(self):
        dl = NOW + 2 * 3600
        v = wire_view(self.ticket(deadline_text="王小明 0912 345 678 A 123 456 789", deadline_at=dl), NOW, "B")
        self.assertIn(time.strftime("%m/%d %H:%M", time.localtime(dl)), v["summary"])
        for w in ("王小明", "0912", "345", "A 123"):
            self.assertNotIn(w, v["summary"])
        v = wire_view(self.ticket(deadline_text="王小明 0912345678", deadline_at=None), NOW, "B")
        self.assertNotIn("王小明", v["summary"])
        self.assertNotIn("0912", v["summary"])

    def test_clerk_typed_outside_location_loses_the_name(self):
        """H3: a location the clerk typed by hand is trimmed to its place part before it goes out."""
        for typed, place in (("王小明家中正路三巷", "中正路三巷"), ("我叫陳志明中山路五號", "中山路五號"),
                             ("李大同家中正路三巷 0912 345 678", "中正路三巷")):
            v = wire_view(self.ticket(priority=1, category=4, item_code=0, zone_code=codebook.ZONE_EXTERNAL,
                                      location=typed, transcript=""), NOW, "B")
            self.assertIn(place, v["summary"])
            for w in ("王小明", "陳志明", "李大同", "我叫", "0912"):
                self.assertNotIn(w, v["summary"])
        # a place that doesn't parse as an address still goes out as typed
        v = wire_view(self.ticket(priority=1, category=4, item_code=0, zone_code=codebook.ZONE_EXTERNAL,
                                  location="活動中心後門", transcript=""), NOW, "B")
        self.assertIn("活動中心後門", v["summary"])


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

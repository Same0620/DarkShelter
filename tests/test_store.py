"""Store: R-T transition table, R-V version rule, send/ack bookkeeping, recovery."""
import os
import sqlite3
import tempfile
import time
import unittest

from darkshelter import codebook
from darkshelter.store import Conflict, StaleVersion, Store
from darkshelter.wireview import F_CLOSED, F_MULTI, wire_view


def need(**kw):
    d = {"category": 2, "item": "洗腎", "item_code": 201, "subject_group": "elderly",
         "zone_code": codebook.zone_code("B", 12), "priority": 1, "rule_priority": 1,
         "deadline_at": time.time() + 20 * 3600, "deadline_text": "10/28 08:00"}
    d.update(kw)
    return d


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "desk.db")
        self.now = [1_800_000_000.0]
        self.s = Store(self.path, "B", clock=lambda: self.now[0])

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def draft(self, **kw):
        return self.s.create_draft(need(**kw), job_id="j", transcript="測試")

    def confirmed(self, **kw):
        seq = self.draft(**kw)
        self.s.confirm(seq, clerk="A")
        return seq


class TestTransitions(StoreTestBase):
    def test_draft_has_no_wire_view_and_version_zero(self):
        seq = self.draft()
        t = self.s.get(seq)
        self.assertEqual(t["version"], 0)
        self.assertIsNone(wire_view(t, self.now[0], "B"))

    def test_confirm_bumps_once(self):
        seq = self.confirmed()
        self.assertEqual(self.s.get(seq)["version"], 1)
        self.assertEqual(self.s.get(seq)["status"], "confirmed")

    def test_lowering_needs_reason(self):
        seq = self.draft()
        with self.assertRaises(Conflict) as cm:
            self.s.confirm(seq, priority=2, clerk="A")
        self.assertEqual(cm.exception.error, "reason_required")
        t = self.s.confirm(seq, priority=2, clerk="A", reason="家屬說已安排")
        self.assertEqual(t["priority"], 2)
        # raising never needs a reason
        seq2 = self.draft(priority=3, rule_priority=3, category=8, item_code=801)
        self.assertEqual(self.s.confirm(seq2, priority=1, clerk="A")["priority"], 1)

    def test_illegal_transitions_raise_conflict(self):
        seq = self.confirmed()
        with self.assertRaises(Conflict):
            self.s.confirm(seq, clerk="A")              # confirm twice
        with self.assertRaises(Conflict):
            self.s.mark_alert_sent(seq)                 # alert after confirm
        d = self.draft()
        with self.assertRaises(Conflict):
            self.s.resolve(d, "A")                      # resolve a draft
        with self.assertRaises(Conflict):
            self.s.restore(d, "A")                      # restore an open ticket
        self.s.resolve(seq, "A")
        for op in (lambda: self.s.update_fields(seq, {"quantity": 2}, "A"),
                   lambda: self.s.raise_worried(seq, "A"),
                   lambda: self.s.discard(seq, "A", "x"),
                   lambda: self.s.resolve(seq, "A"),
                   lambda: self.s.log_emergency_action(seq, "medic_told", "A"),
                   lambda: self.s.merge(seq, d, "A"),
                   lambda: self.s.escalate(seq, 60)):
            with self.assertRaises(Conflict):
                op()

    def test_confirm_after_merge_conflicts(self):
        a, b = self.draft(), self.draft()
        self.s.merge(a, b, "A")
        with self.assertRaises(Conflict):
            self.s.confirm(a, clerk="A")

    def test_merge_announced_draft_into_draft_conflicts(self):
        # an alerted (announced, version > 0) draft may only merge into a confirmed ticket
        a = self.draft()
        self.s.mark_alert_sent(a)
        self.assertGreater(self.s.get(a)["version"], 0)
        b = self.draft()
        with self.assertRaises(Conflict):
            self.s.merge(a, b, "A")
        c = self.confirmed()
        self.s.merge(a, c, "A")
        self.assertEqual(self.s.get(a)["status"], "merged")

    def test_merge_into_self_and_closed(self):
        a = self.draft()
        with self.assertRaises(Conflict):
            self.s.merge(a, a, "A")
        b = self.confirmed()
        self.s.resolve(b, "A")
        with self.assertRaises(Conflict):
            self.s.merge(a, b, "A")

    def test_mark_alert_sent_rules(self):
        p3 = self.draft(priority=3, rule_priority=3, category=8, item_code=801)
        with self.assertRaises(Conflict):
            self.s.mark_alert_sent(p3)
        p1 = self.draft()
        self.s.mark_alert_sent(p1)
        with self.assertRaises(Conflict):
            self.s.mark_alert_sent(p1)  # only once

    def test_discard_reason_for_p1(self):
        p1 = self.draft()
        with self.assertRaises(Conflict):
            self.s.discard(p1, "A")
        self.s.discard(p1, "A", "重複登記")
        p3 = self.draft(priority=3, rule_priority=3, category=8, item_code=801)
        self.s.discard(p3, "A")  # no reason needed

    def test_stale_version(self):
        seq = self.confirmed()
        self.s.update_fields(seq, {"quantity": 2}, "A", expected_version=1)
        with self.assertRaises(StaleVersion):
            self.s.update_fields(seq, {"quantity": 3}, "A", expected_version=1)

    def test_apply_ai_fields(self):
        d = self.draft(priority=2, rule_priority=2, category=3, item_code=306)
        self.s.update_fields(d, {"quantity": 1}, "A")  # clerk-edited
        self.s.apply_ai_fields(d, {"quantity": 5, "subject": "阿嬤", "priority": 1}, {"subject": "llm"}, ["ai_only"])
        t = self.s.get(d)
        self.assertEqual(t["quantity"], 1)       # clerk wins
        self.assertEqual(t["subject"], "阿嬤")
        self.assertEqual(t["priority"], 1)       # AI may raise
        self.s.apply_ai_fields(d, {"priority": 3}, {}, [])
        self.assertEqual(self.s.get(d)["priority"], 1)  # never lower
        c = self.confirmed()
        before = self.s.get(c)
        self.s.apply_ai_fields(c, {"quantity": 9}, {}, [])
        after = self.s.get(c)
        self.assertEqual(after["quantity"], before["quantity"])
        self.assertEqual(after["ai_suggestion"]["fields"]["quantity"], 9)
        self.assertEqual(after["version"], before["version"])
        self.s.resolve(c, "A")
        self.s.apply_ai_fields(c, {"quantity": 7}, {}, [])  # closed: no-op, no Conflict


class TestVersionRule(StoreTestBase):
    def test_no_bump_on_noop_writes(self):
        seq = self.confirmed()
        v = self.s.get(seq)["version"]
        self.s.update_fields(seq, {"subject": "阿嬤"}, "A")         # free text never on the wire
        self.s.log_emergency_action(seq, "medic_told", "A")
        self.s.raise_worried(seq, "A")                                # already P1
        self.s.mark_sent([seq])
        self.s.mark_acked(seq, v, 2, 1)
        self.s.apply_status(seq, 2, 1)
        self.assertEqual(self.s.get(seq)["version"], v)

    def test_one_bump_per_wire_change(self):
        seq = self.confirmed()
        self.s.update_fields(seq, {"quantity": 3}, "A")
        self.assertEqual(self.s.get(seq)["version"], 2)
        self.s.update_fields(seq, {"quantity": 3}, "A")
        self.assertEqual(self.s.get(seq)["version"], 2)
        self.s.update_fields(seq, {"location": "C區3床"}, "A")
        self.assertEqual(self.s.get(seq)["version"], 3)

    def test_closed_bit_iff_closed_status(self):
        seq = self.confirmed()
        self.s.mark_sent([seq])
        t = self.s.get(seq)
        self.assertFalse(wire_view(t, self.now[0], "B")["flags"] & F_CLOSED)
        self.s.resolve(seq, "A")
        t = self.s.get(seq)
        self.assertTrue(wire_view(t, self.now[0], "B")["flags"] & F_CLOSED)
        self.assertEqual(t["version"], 2)

    def test_never_sent_closed_is_never_sent(self):
        seq = self.confirmed()
        self.s.discard(seq, "A", "誤登")
        self.assertIsNone(wire_view(self.s.get(seq), self.now[0], "B"))
        self.assertEqual(self.s.pending_units(), [])

    def test_escalation_only_first_bumps(self):
        seq = self.confirmed()
        self.s.mark_sent([seq])
        self.s.mark_acked(seq, 1, 2, 0)
        self.assertIsNotNone(self.s.escalate(seq, 60))
        self.assertEqual(self.s.get(seq)["version"], 2)
        self.now[0] += 30
        self.assertIsNone(self.s.escalate(seq, 60))       # within the SLA period: no-op
        self.now[0] += 61
        self.assertIsNotNone(self.s.escalate(seq, 60))    # later period: escalates again
        self.assertEqual(self.s.get(seq)["version"], 2)   # ... but no re-send
        self.s.apply_status(seq, 1, 1)
        self.now[0] += 120
        self.assertIsNone(self.s.escalate(seq, 60))       # acknowledged: never

    def test_merge_and_restore(self):
        dst = self.confirmed(category=8, item_code=801, priority=3, rule_priority=3, quantity=4, deadline_at=None)
        src = self.draft(category=8, item_code=801, priority=2, rule_priority=2, quantity=2, deadline_at=None)
        self.s.merge(src, dst, "A")
        d = self.s.get(dst)
        self.assertEqual(d["report_count"], 2)
        self.assertEqual(d["quantity"], 6)              # supplies add up
        self.assertEqual(d["priority"], 2)              # min
        self.assertTrue(wire_view(d, self.now[0], "B")["flags"] & F_MULTI)
        self.s.restore(src, "A")
        d = self.s.get(dst)
        self.assertEqual(d["report_count"], 1)
        self.assertEqual(d["quantity"], 4)
        self.assertEqual(self.s.get(src)["status"], "draft")

    def test_person_specific_quantities_do_not_add(self):
        dst = self.confirmed(quantity=1)
        src = self.draft(quantity=1)
        self.s.merge(src, dst, "A")
        self.assertEqual(self.s.get(dst)["quantity"], 1)


class TestAckAndPending(StoreTestBase):
    def test_mark_acked_rules(self):
        seq = self.confirmed()
        self.s.update_fields(seq, {"quantity": 2}, "A")   # v2
        self.s.mark_acked(seq, 1, 2, 0)
        t = self.s.get(seq)
        self.assertEqual((t["acked_version"], t["acked_level"]), (1, 2))
        self.assertIsNotNone(t["delivered_wall"])
        self.s.mark_acked(seq, 5, 2, 0)                   # > version: ignored
        self.assertEqual(self.s.get(seq)["acked_version"], 1)
        self.s.mark_acked(seq, 2, 1, 2)
        t = self.s.get(seq)
        self.assertEqual((t["acked_version"], t["acked_level"], t["eoc_status"]), (2, 1, 2))
        self.s.mark_acked(seq, 2, 2, 1)                   # same version, higher level; status keeps max
        t = self.s.get(seq)
        self.assertEqual((t["acked_level"], t["eoc_status"]), (2, 2))
        self.s.mark_acked(seq, 1, 2, 0)                   # older: ignored
        self.assertEqual(self.s.get(seq)["acked_version"], 2)

    def test_pending_order_and_exclusion(self):
        p3 = self.confirmed(category=8, item_code=801, priority=3, rule_priority=3, deadline_at=None)
        p1_late = self.confirmed(deadline_at=self.now[0] + 7200)
        p1_soon = self.confirmed(deadline_at=self.now[0] + 600)
        order = [v["seq"] for v in self.s.pending_units()]
        self.assertEqual(order, [p1_soon, p1_late, p3])
        self.assertEqual([v["seq"] for v in self.s.pending_units(exclude=[p1_soon])], [p1_late, p3])
        self.s.mark_pack_failed(p3, self.s.get(p3)["version"])
        self.assertNotIn(p3, [v["seq"] for v in self.s.pending_units()])
        self.s.update_fields(p3, {"quantity": 3}, "A")   # version changes -> eligible again
        self.assertIn(p3, [v["seq"] for v in self.s.pending_units()])

    def test_status_seq_applied(self):
        seq = self.confirmed()
        self.s.apply_status_many([(seq, 1, 1), (seq, 2, 2)])
        self.assertEqual(self.s.census()["status_seq_applied"], 2)
        self.assertEqual(self.s.get(seq)["eoc_status"], 2)
        self.s.apply_status_many([(seq, 3, 2)])           # already applied seq: ignored
        self.assertEqual(self.s.get(seq)["eoc_status"], 2)


class TestDurability(StoreTestBase):
    def test_autoincrement_never_reuses(self):
        a = self.draft()
        b = self.draft()
        self.s.conn().execute("DELETE FROM tickets WHERE seq=?", (b,))
        self.s.close()
        s2 = Store(self.path, "B")
        c = s2.create_draft(need(), job_id="j")
        self.assertGreater(c, b)
        self.assertGreater(b, a)
        s2.close()

    def test_confirm_survives_reopen(self):
        seq = self.confirmed()
        self.s.close()
        raw = sqlite3.connect(self.path)
        status, version = raw.execute("SELECT status, version FROM tickets WHERE seq=?", (seq,)).fetchone()
        raw.close()
        self.assertEqual((status, version), ("confirmed", 1))
        s2 = Store(self.path, "B")
        self.assertEqual(len(s2.pending_units()), 1)
        rec = s2.recover()
        self.assertEqual(rec["unsent"], 1)
        s2.close()

    def test_boot_id_persists_and_wraps(self):
        b1 = self.s.new_boot()
        self.s.close()
        s2 = Store(self.path, "B")
        b2 = s2.new_boot()
        self.assertEqual(b2, (b1 + 1) % 256)
        s2.set_meta("boot_id", 255)
        self.assertEqual(s2.new_boot(), 0)
        s2.close()

    def test_every_write_logs_an_event(self):
        seq = self.confirmed()
        types = [e["type"] for e in self.s.events(20, seq=seq)]
        self.assertIn("draft_created", types)
        self.assertIn("confirm", types)

    def test_checkin_census(self):
        self.s.checkin({"size": 4, "zone": "B12", "elderly": 1, "infants": 1, "children": 1}, "A")
        hid = self.s.checkin({"size": 2, "pregnant": 1, "disabled": 1}, "A")
        c = self.s.census()
        self.assertEqual((c["people"], c["elderly"], c["infant_child"], c["pregnant"], c["disabled"]), (6, 1, 2, 1, 1))
        self.s.checkout(hid, "A")
        self.assertEqual(self.s.census()["people"], 4)
        with self.assertRaises(Conflict):
            self.s.checkout(hid, "A")


if __name__ == "__main__":
    unittest.main()

"""Network end-to-end (SPEC 11 'Network'): ordering, equality, P1-behind-P3 bound, clock jump,
Windows UDP reset, status loop, census-first, pack failures.

Slow cases are skipped with DS_FAST=1.
"""
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from darkshelter import codebook  # noqa: E402
from darkshelter.net import codec  # noqa: E402
from darkshelter.net.harness import (InProcRig, acked_count, free_port, make_need, net_config,  # noqa: E402
                                     ordering_violations, seed_tickets, wait_until)
from darkshelter.net.receiver import Receiver  # noqa: E402
from darkshelter.net.eoc_store import EocStore  # noqa: E402
from darkshelter.net.sender import Sender  # noqa: E402
from darkshelter.store import Store  # noqa: E402
from darkshelter.config import eoc_keys  # noqa: E402
from darkshelter.wireview import wire_view  # noqa: E402

FAST = os.environ.get("DS_FAST") == "1"
CLERK = "測試員"


def tmpdir():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def unique_rows(eoc):
    keys = [(r["desk_id"], r["seq"]) for r in eoc.board()]
    return len(keys) == len(set(keys))


@unittest.skipIf(FAST, "slow network scenario (DS_FAST=1)")
class SixtyTickets(unittest.TestCase):
    def test_60_tickets_lora_flapping_good(self):
        with tmpdir() as tmp:
            rig = InProcRig(tmp, seed=21, profile="DOWN")
            try:
                seqs, prios = seed_tickets(rig.desk, n=60, seed=21, p1=10, p2=20)
                p1 = [s for s in seqs if prios[s] == 1]
                rig.start()
                time.sleep(1.0)                          # link DOWN: probes only
                rig.emu.set_profile("LORA_RELAY")
                rig.sender.reset_probe()
                self.assertTrue(wait_until(lambda: acked_count(rig.desk) > 0, 30), "no ack on LORA_RELAY")
                first_change_id = rig.eoc.max_rx_id()
                # after the first acks: edit 5, discard 2, resolve 2 (P2/P3 tickets)
                rng = random.Random(21)
                pool = [s for s in seqs if prios[s] != 1]
                rng.shuffle(pool)
                for s in pool[:5]:
                    t = rig.desk.get(s)
                    rig.desk.update_fields(s, {"quantity": int(t["quantity"] or 0) + 3}, CLERK,
                                           expected_version=t["version"])
                for s in pool[5:7]:
                    rig.desk.discard(s, CLERK, reason="測試：重複")
                for s in pool[7:9]:
                    rig.desk.resolve(s, CLERK)
                time.sleep(15)
                rig.emu.set_profile("FLAPPING")
                time.sleep(20)
                rig.emu.set_profile("GOOD")
                rig.sender.reset_probe()
                self.assertTrue(wait_until(lambda: not rig.desk.pending_units(), 120), "pending_units never emptied")
                c = rig.compare()
                self.assertTrue(c["equal"], c)
                self.assertTrue(unique_rows(rig.eoc))
                rows = rig.eoc.rx_log()
                before = [r for r in rows if r["id"] <= first_change_id]
                self.assertEqual(ordering_violations(before, p1), [])
                self.assertEqual(ordering_violations(rows, p1), [])   # also holds over the whole run
                s = rig.sender.stats()
                print(f"\n  60 tickets: desk={c['desk']} eoc={c['eoc']} dup_count={c['dup_count']} "
                      f"datagrams_up={s['datagrams_up']} retransmissions={s['retransmissions']} "
                      f"bytes_up={s['bytes_up']} ordering_violations=0", file=sys.stderr)
            finally:
                rig.stop()


@unittest.skipIf(FAST, "slow network scenario (DS_FAST=1)")
class P1BehindP3(unittest.TestCase):
    def test_p1_behind_200_p3(self):
        rate_bps, loss, delay_ms, jitter_ms, mtu = 1200, 0.20, 400, 200, 200
        with tmpdir() as tmp:
            rig = InProcRig(tmp, seed=5, profile="DOWN")
            try:
                seed_tickets(rig.desk, n=200, seed=5, p1=0, p2=0)
                rig.start()
                rig.emu.set_custom(rate_bps=rate_bps, loss=loss, delay_ms=delay_ms, jitter_ms=jitter_ms, mtu=mtu,
                                   seed=5)
                rig.sender.reset_probe()
                self.assertTrue(wait_until(lambda: acked_count(rig.desk) >= 8, 60), "P3 traffic never flowed")
                need = make_need(1, 2, 201, "elderly", 0, None, random.Random(1), rig.desk.clock())
                seq = rig.desk.create_draft(need, job_id="p1-behind-p3")
                rig.desk.confirm(seq, clerk=CLERK)
                t_confirm, t_confirm_wall = time.monotonic(), time.time()

                def first_rx():
                    r = rig.eoc.conn().execute("SELECT MIN(mono) FROM rx_log WHERE kind='data' AND seq=?",
                                               (seq,)).fetchone()
                    return r[0]
                self.assertTrue(wait_until(lambda: first_rx() is not None, 60), "P1 never arrived (cap 60 s)")
                wait_s = first_rx() - t_confirm
                self.assertTrue(wait_until(lambda: rig.desk.get(seq)["acked_version"] >= 1, 60))
                ack_wall = None
                for e in reversed(rig.desk.events(limit=100000)):
                    if e["type"] == "acked" and e["data"] and f"[{seq}, " in e["data"]:
                        ack_wall = e["wall"]
                        break
                self.assertIsNotNone(ack_wall)
                # no P3 bytes sent while the P1 was unacknowledged (0.15 s for the sender to notice it)
                p3_during = [d for d in list(rig.sender.sent_log)
                             if d["kind"] == "data" and t_confirm_wall + 0.15 < d["wall"] < ack_wall
                             and any(u[2] == 3 for u in d["units"])]
                self.assertEqual(p3_during, [])
                t = rig.desk.get(seq)
                n = max(0, int(t["send_attempts"]) - 1)
                R = rate_bps / 8.0
                p1_bytes = codec.OVERHEAD + codec.summary_record_size(wire_view(t, time.time(), "B"))
                max_base = rig.sender.rto.max_rto_base
                bound = (2 * mtu + p1_bytes) / R + (delay_ms + jitter_ms) / 1000.0 + n * 2.0 * max_base + 1.0
                print(f"\n  P1 behind 200 P3: wait={wait_s:.2f}s bound={bound:.2f}s retransmissions={n} "
                      f"max_rto_base={max_base:.2f}s p1_bytes={p1_bytes}", file=sys.stderr)
                self.assertLessEqual(wait_s, min(bound, 60.0))
            finally:
                rig.stop()


class ClockAndSockets(unittest.TestCase):
    def test_clock_jump_no_loss_no_dups(self):
        with tmpdir() as tmp:
            rig = InProcRig(tmp, seed=9, profile="GOOD")
            try:
                seqs, prios = seed_tickets(rig.desk, n=20, seed=9, p1=3, p2=5)
                rig.start()
                self.assertTrue(wait_until(lambda: not rig.desk.pending_units(), 20))
                rig.desk.clock = lambda: time.time() + 3600.0      # wall clock jumps +1 h
                rig.sender.on_clock_jump()
                more, _ = seed_tickets(rig.desk, n=10, seed=10, p1=2, p2=3)
                for s in seqs[:3]:
                    t = rig.desk.get(s)
                    rig.desk.update_fields(s, {"quantity": int(t["quantity"] or 0) + 1}, CLERK,
                                           expected_version=t["version"])
                self.assertTrue(wait_until(lambda: not rig.desk.pending_units(), 20))
                c = rig.compare()
                self.assertTrue(c["equal"], c)
                self.assertEqual(c["desk"], 30)
                self.assertTrue(unique_rows(rig.eoc))
                self.assertTrue(rig.sender.is_alive())
                self.assertEqual(rig.sender.counters["clock_jumps"], 1)
            finally:
                rig.stop()

    def test_windows_udp_reset_fake_socket(self):
        class ResetSock:
            def __init__(self):
                self.sends = 0

            def recvfrom(self, n):
                raise ConnectionResetError(10054, "An existing connection was forcibly closed")

            def sendto(self, data, addr):
                self.sends += 1
                if self.sends % 2:
                    raise ConnectionResetError(10054, "reset")
                return len(data)

            def setblocking(self, flag):
                pass

            def getsockname(self):
                return ("127.0.0.1", 0)

            def close(self):
                pass

        with tmpdir() as tmp:
            cfg = net_config(tmp)
            store = Store(cfg["paths"]["desk_db"])
            eoc = EocStore(cfg["paths"]["eoc_db"])
            seed_tickets(store, n=3, seed=1, p1=1, p2=1)
            sender = Sender(store, cfg, sock=ResetSock())
            receiver = Receiver(eoc, eoc_keys(cfg), port=0, sock=ResetSock())
            try:
                sender.start()
                receiver.start()
                time.sleep(1.2)
                self.assertTrue(sender.is_alive())
                self.assertTrue(receiver.is_alive())
                self.assertGreater(sender.counters["socket_errors"], 0)
                self.assertGreater(receiver.stats()["socket_errors"], 0)
                self.assertGreaterEqual(sender.counters["probes_sent"], 1)
            finally:
                sender.stop()
                receiver.stop()
                store.close()
                eoc.close()

    def test_sender_to_dead_port_survives(self):
        with tmpdir() as tmp:
            cfg = net_config(tmp)
            store = Store(cfg["paths"]["desk_db"])
            sender = Sender(store, cfg, dest=("127.0.0.1", free_port("udp")))
            try:
                sender.start()
                time.sleep(1.5)
                self.assertTrue(sender.is_alive())
                self.assertGreaterEqual(sender.counters["probes_sent"], 2)
                self.assertEqual(sender.stats()["link_state"], "UNKNOWN")
                if os.name == "nt":   # WSAIoctl(SIO_UDP_CONNRESET) applied by open_udp
                    self.assertEqual(sender.counters["socket_errors"], 0)
            finally:
                sender.stop()
                store.close()


class StatusAndCensus(unittest.TestCase):
    def test_census_first_and_dispatched_reaches_desk(self):
        with tmpdir() as tmp:
            rig = InProcRig(tmp, seed=4, profile="DOWN", net={"heartbeat_s": 0.5})
            try:
                rig.desk.checkin({"family_name": "王", "size": 4, "zone": "B12", "elderly": 1, "infants": 1,
                                  "children": 0, "pregnant": 0, "disabled": 1}, CLERK)
                seqs, prios = seed_tickets(rig.desk, n=6, seed=4, p1=2, p2=2)
                rig.start()
                time.sleep(1.0)
                rig.emu.set_profile("GOOD")
                rig.sender.reset_probe()
                self.assertTrue(wait_until(lambda: not rig.desk.pending_units(), 15))
                rows = rig.eoc.rx_log()
                self.assertEqual(rows[0]["kind"], "hb")                     # census first
                desk = rig.eoc.desks()[0]
                self.assertEqual((desk["census"]["people"], desk["census"]["elderly"],
                                  desk["census"]["disabled"]), (4, 1, 1))
                target = seqs[0]
                self.assertEqual(rig.eoc.set_status(rig.desk_id, target, 1), 1)
                self.assertEqual(rig.eoc.set_status(rig.desk_id, target, 2), 2)   # 已派遣
                self.assertTrue(wait_until(lambda: rig.desk.get(target)["eoc_status"] == 2, 10))
                self.assertEqual(rig.desk.meta("status_seq_applied"), "2")
                self.assertEqual(rig.desk.get(target)["version"], 1)               # status never bumps
            finally:
                rig.stop()

    def test_unit_that_cannot_pack_is_skipped(self):
        with tmpdir() as tmp:
            rig = InProcRig(tmp, seed=2, profile="GOOD", net={"mtu": 70})
            try:
                rng = random.Random(2)
                now = rig.desk.clock()
                big = make_need(1, 2, 201, "elderly", 0, None, rng, now)
                big.update({"zone_code": codebook.ZONE_EXTERNAL, "location": "花蓮縣光復鄉大同村中山路三段一百號",
                            "deadline_text": "10/28 08:00", "deadline_at": now + 7200})
                b = rig.desk.create_draft(big)
                rig.desk.confirm(b, clerk=CLERK)
                small = make_need(3, 8, 801, "unknown", 0, None, rng, now)
                small.update({"zone_code": codebook.zone_code("A", 1), "deadline_at": None, "deadline_text": ""})
                s = rig.desk.create_draft(small)
                rig.desk.confirm(s, clerk=CLERK)
                rig.start()
                self.assertTrue(wait_until(lambda: rig.desk.get(s)["acked_version"] >= 1, 10),
                                "a P3 behind an unpackable P1 must still be sent")
                self.assertEqual(rig.desk.get(b)["acked_version"], 0)
                self.assertTrue(any(e["type"] == "pack_failed" and e["seq"] == b for e in rig.desk.events(500)))
                t = rig.desk.get(b)
                rig.desk.update_fields(b, {"zone_code": codebook.zone_code("B", 1), "location": "",
                                           "deadline_text": ""}, CLERK, expected_version=t["version"])
                self.assertTrue(wait_until(lambda: rig.desk.get(b)["acked_version"] >= 1, 10),
                                "the edited (shorter) P1 is sent once its version changes")
                self.assertTrue(rig.compare()["equal"])
            finally:
                rig.stop()


# ---------------------------------------------------------------- red-team fixes (REDTEAM.md H1, M4, L2, L3)

KEY = bytes(range(32))


def rt_need(item_code, priority, zone=("B", 12), quantity=None):
    it = codebook.item(item_code)
    return {"category": item_code // 100, "item": it["zh"] if it else "", "item_code": item_code,
            "priority": priority, "rule_priority": priority, "subject_group": "elderly", "quantity": quantity,
            "emergency": None, "summary": "（測試）", "flags": [], "zone_code": codebook.zone_code(*zone)}


def rt_ticket(store, item_code, priority, **kw):
    seq = store.create_draft(rt_need(item_code, priority, **kw), job_id="rt", transcript="（測試資料）")
    store.confirm(seq, clerk=CLERK)
    return seq


def rt_deliver(desk, eoc, boot=1, msg_seq=1):
    """Send every pending unit through the real codec into the EOC; feed the acks back. -> conflicts."""
    views = desk.pending_units()
    conflicts = 0
    while views:
        data, n = codec.pack_summary(views, 200, KEY, 7, boot, msg_seq)
        msg = codec.unpack(data, {7: KEY})
        acks = eoc.apply_records(7, msg.boot_id, msg.msg_seq, msg.records, nbytes=len(data))
        conflicts += desk.mark_acked_many(acks)
        views = views[n:]
        msg_seq += 1
    return conflicts


class RedTeamLinkFixes(unittest.TestCase):
    def test_h1_desk_db_reset_new_p1_not_swallowed_as_resolved(self):
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            old = Store(Path(tmp) / "old_desk.db")
            s1 = rt_ticket(old, 801, 3, quantity=1)                  # blankets
            old.update_fields(s1, {"quantity": 2}, CLERK)
            old.update_fields(s1, {"quantity": 3}, CLERK)            # v3 at the EOC
            self.assertEqual(rt_deliver(old, eoc), 0)
            eoc.set_status(7, s1, 3)                                  # resolved at the EOC
            new = Store(Path(tmp) / "new_desk.db")                    # desk database reset, EOC's kept
            p1 = rt_ticket(new, 201, 1)                               # a new dialysis P1, same number
            self.assertEqual(p1, s1)
            new.apply_status_many(eoc.status_changes(7, 0))           # first HB_REPLY: old statuses
            self.assertEqual(rt_deliver(new, eoc), 1)                 # EOC acks v3: not a version we made
            t = new.get(p1)
            self.assertIsNone(t["delivered_wall"])                    # NOT delivered ...
            self.assertEqual(t["eoc_status"], 0)                      # ... and NOT resolved
            self.assertEqual(new.pending_units(), [])                 # not resent forever / no P1 lock-up
            new.apply_status_many([(p1, 3, 99)])                      # later statuses for it are not ours
            self.assertEqual(new.get(p1)["eoc_status"], 0)
            row = eoc.tickets()[(7, s1)]
            self.assertEqual((row["version"], row["status"]), (3, 3))  # the EOC row was not overwritten
            p1b = rt_ticket(new, 201, 1)                              # numbers the EOC never saw work
            self.assertEqual(rt_deliver(new, eoc), 0)
            self.assertIsNotNone(new.get(p1b)["delivered_wall"])

    def test_h1_clash_shows_a_backup_channel_banner_at_once(self):
        from darkshelter.config import make_config
        from darkshelter.timers import Timers
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            old = Store(Path(tmp) / "old_desk.db")
            s1 = rt_ticket(old, 801, 3, quantity=1)
            old.update_fields(s1, {"quantity": 2}, CLERK)
            old.update_fields(s1, {"quantity": 3}, CLERK)
            self.assertEqual(rt_deliver(old, eoc), 0)
            new = Store(Path(tmp) / "new_desk.db")                    # desk database reset, EOC's kept
            p1 = rt_ticket(new, 201, 1)
            self.assertEqual(rt_deliver(new, eoc), 1)                 # the clash is detected ...
            self.assertEqual(new.seq_conflicts(), {p1})
            banners = Timers(new, make_config()).check()              # ... and shown at once, not after the SLA
            clash = [b for b in banners if b["kind"] == "seq_conflict"]
            self.assertEqual([b["seq"] for b in clash], [p1])
            self.assertTrue(clash[0]["backup"] and clash[0]["print"])
            self.assertIn("備援", clash[0]["zh"])

    def test_h1_equal_version_different_content_is_not_yours(self):
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            old = Store(Path(tmp) / "old_desk.db")
            s1 = rt_ticket(old, 801, 3, quantity=2)
            rt_deliver(old, eoc)
            eoc.set_status(7, s1, 2)
            new = Store(Path(tmp) / "new_desk.db")
            p1 = rt_ticket(new, 201, 1)                               # also version 1, different content
            self.assertEqual(rt_deliver(new, eoc), 1)
            t = new.get(p1)
            self.assertEqual((t["acked_version"], t["eoc_status"]), (0, 0))
            self.assertIsNone(t["delivered_wall"])
            self.assertEqual(eoc.stats()["seq_conflicts"], 1)
            # a real retransmission from the same desk database is still a plain duplicate
            v = wire_view(old.get(s1), time.time(), "B")
            data, _ = codec.pack_summary([v], 200, KEY, 7, 1, 77)
            acks = eoc.apply_records(7, 1, 77, codec.unpack(data, {7: KEY}).records)
            self.assertEqual(acks, [(s1, 1, 2, 2)])
            self.assertEqual(eoc.stats()["seq_conflicts"], 1)

    def test_h1_newer_version_created_after_first_rx_is_a_conflict_but_edits_are_not(self):
        with tmpdir() as tmp:
            clock = [1_800_000_000.0]
            eoc = EocStore(Path(tmp) / "eoc.db", clock=lambda: clock[0])
            old = Store(Path(tmp) / "old_desk.db", clock=lambda: clock[0])
            s1 = rt_ticket(old, 801, 3, quantity=1)
            rt_deliver(old, eoc)
            clock[0] += 7200                                          # two hours later
            old.update_fields(s1, {"quantity": 5}, CLERK)              # a real edit of the same ticket
            self.assertEqual(rt_deliver(old, eoc), 0)
            self.assertEqual(eoc.tickets()[(7, s1)]["quantity"], 5)
            new = Store(Path(tmp) / "new_desk.db", clock=lambda: clock[0])
            p1 = rt_ticket(new, 201, 1)
            for q in (1, 2, 3):                                       # new ticket edited up to v4 > v2
                new.update_fields(p1, {"quantity": q}, CLERK)
            self.assertEqual(rt_deliver(new, eoc), 1)
            self.assertEqual(eoc.tickets()[(7, s1)]["item_code"], 801)  # not overwritten
            self.assertIsNone(new.get(p1)["delivered_wall"])

    def test_dup_ack_carries_the_version_the_eoc_holds(self):
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            desk = Store(Path(tmp) / "desk.db")
            s = rt_ticket(desk, 801, 3, quantity=1)
            v1 = desk.pending_units()
            desk.update_fields(s, {"quantity": 2}, CLERK)
            rt_deliver(desk, eoc)                                     # EOC holds v2
            data, _ = codec.pack_summary(v1, 200, KEY, 7, 1, 50)      # a late copy of v1
            msg = codec.unpack(data, {7: KEY})
            acks = eoc.apply_records(7, 1, 50, msg.records)
            self.assertEqual(acks, [(s, 2, 2, 0)])
            self.assertEqual(desk.mark_acked_many(acks), 0)           # a version this desk made: fine
            self.assertEqual(desk.get(s)["acked_version"], 2)

    def test_m4_old_heartbeat_cannot_roll_census_back(self):
        with tmpdir() as tmp:
            clock = [1_800_000_000.0]
            eoc = EocStore(Path(tmp) / "eoc.db", clock=lambda: clock[0])
            self.assertTrue(eoc.apply_heartbeat(7, 1, 100, {"people": 100, "q_p1": 5}))
            clock[0] += 5
            self.assertTrue(eoc.apply_heartbeat(7, 2, 7, {"people": 250, "q_p1": 0}))      # desk restarted
            clock[0] += 1
            self.assertFalse(eoc.apply_heartbeat(7, 1, 100, {"people": 100, "q_p1": 5}))   # replayed
            self.assertFalse(eoc.apply_heartbeat(7, 2, 6, {"people": 90}))                 # late copy
            d = eoc.desks()[0]
            self.assertEqual((d["census"]["people"], d["boot_id"], d["last_seen"]), (250, 2, clock[0] - 1))
            self.assertTrue(eoc.apply_heartbeat(7, 2, 8, {"people": 251}))                 # newer
            self.assertTrue(eoc.apply_heartbeat(7, 0, 65535, {"people": 252}) is False)
            self.assertTrue(eoc.apply_heartbeat(7, 3, 0, {"people": 253}))                 # boot wraps fine
            clock[0] += 31                                                                 # a reset desk
            self.assertTrue(eoc.apply_heartbeat(7, 1, 5, {"people": 7}))                   # boots at 1 again
            self.assertEqual(eoc.desks()[0]["census"]["people"], 7)
            self.assertEqual(eoc.stats()["stale_heartbeats"], 3)

    def test_l2_junk_is_counted_in_memory_and_flushed(self):
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            rx = Receiver(eoc, {7: KEY}, port=0)
            rng = random.Random(4)
            t0 = time.perf_counter()
            for _ in range(300):
                rx.handle(bytes(rng.randrange(256) for _ in range(40)), ("127.0.0.1", 9))
            per = (time.perf_counter() - t0) / 300
            self.assertEqual(eoc.stats()["bad_datagrams"], 0)         # nothing written per datagram
            rx.flush_counters()
            st = eoc.stats()
            self.assertEqual(st["bad_datagrams"], 300)
            self.assertEqual(st["bytes_rx"], 300 * 40)
            self.assertLess(per, 0.0005)                              # was ~0.6 ms with one fsync each

    def test_l3_priority_outside_1_to_3_is_clamped(self):
        with tmpdir() as tmp:
            eoc = EocStore(Path(tmp) / "eoc.db")
            base = {"version": 1, "level": 2, "category": 2, "item_code": 201, "quantity": 0, "subject_group": 1,
                    "flags": 0, "age_min": 0, "deadline_h": 255, "zone": 524, "summary": "x"}
            eoc.apply_records(7, 1, 1, [dict(base, seq=1, priority=1)])
            eoc.apply_records(7, 1, 2, [dict(base, seq=2, priority=0)])
            board = eoc.board()
            self.assertEqual([r["seq"] for r in board], [1, 2])       # 0 no longer sorts above the P1s
            self.assertEqual(board[1]["priority"], 1)
            self.assertEqual(eoc.stats()["bad_priority"], 1)


if __name__ == "__main__":
    unittest.main()

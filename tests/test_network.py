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


if __name__ == "__main__":
    unittest.main()

"""Codec: round trips, MTU, saturation, fuzz (SPEC 8.1 / 11)."""
import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from darkshelter.net import codec  # noqa: E402
from darkshelter.wireview import wire_view  # noqa: E402

KEY = bytes(range(32))
KEYS = {7: KEY}


def view(seq=1, **kw):
    v = {"seq": seq, "version": 3, "level": 2, "priority": 1, "category": 2, "item_code": 201, "quantity": 2,
         "subject_group": 4, "flags": 0b00100001, "age_min": 12, "deadline_h": 19, "zone": (2 << 8) | 12,
         "summary": "B區12床 長者 洗腎 10/28 08:00"}
    v.update(kw)
    return v


class CodecRoundTrip(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(codec.HEADER.size, 7)
        self.assertEqual(codec.OVERHEAD, 16)
        self.assertEqual(codec.DIGEST.size, 18)
        self.assertEqual(codec.ACK_ENTRY.size, 8)
        self.assertEqual(codec.HB_ENTRY.size, 9)
        self.assertEqual(codec.HEARTBEAT_SIZE, 33)

    def test_summary_roundtrip(self):
        views = [view(i, priority=1 + i % 3, category=i % 14, quantity=i * 7) for i in range(1, 8)]
        data, n = codec.pack_summary(views, 200, KEY, 7, 5, 4242)
        self.assertGreater(n, 0)
        self.assertLessEqual(len(data), 200)
        msg = codec.unpack(data, KEYS)
        self.assertEqual((msg.type, msg.desk_id, msg.boot_id, msg.msg_seq, msg.count), (codec.DATA_SUMMARY, 7, 5, 4242, n))
        for v, r in zip(views[:n], msg.records):
            for k in ("seq", "version", "priority", "category", "item_code", "quantity", "subject_group", "flags",
                      "age_min", "deadline_h", "zone", "summary"):
                self.assertEqual(r[k], v[k], k)
            self.assertEqual(r["level"], 2)

    def test_summary_record_at_most_80_bytes(self):
        long = view(summary="中" * 50)  # 150 UTF-8 bytes -> truncated to 60 on a char boundary
        rec = codec.encode_summary_record(long)
        self.assertLessEqual(len(rec), 79)
        data, n = codec.pack_summary([long], 200, KEY, 7, 1, 1)
        msg = codec.unpack(data, KEYS)
        self.assertEqual(msg.records[0]["summary"], "中" * 20)

    def test_digest_roundtrip(self):
        views = [view(i) for i in range(1, 30)]
        data, n = codec.pack_digest(views, 200, KEY, 7, 1, 2)
        self.assertEqual(n, (200 - 16) // 18)
        self.assertLessEqual(len(data), 200)
        msg = codec.unpack(data, KEYS)
        self.assertEqual(msg.type, codec.DATA_DIGEST)
        self.assertEqual([r["seq"] for r in msg.records], list(range(1, n + 1)))
        self.assertTrue(all(r["level"] == 1 and r["summary"] is None for r in msg.records))

    def test_ack_roundtrip(self):
        entries = [(10, 2, 2, 1), (11, 65535, 2, 3), (4294967295, 1, 1, 0)]
        data = codec.pack_ack(entries, KEY, 7, 9, 777)
        self.assertEqual(len(data), 16 + 8 * 3)
        msg = codec.unpack(data, KEYS)
        self.assertEqual((msg.type, msg.boot_id, msg.msg_seq), (codec.ACK, 9, 777))
        self.assertEqual([(r["seq"], r["version"], r["level"], r["eoc_status"]) for r in msg.records], entries)

    def test_heartbeat_roundtrip(self):
        census = {"people": 123, "elderly": 9, "infant_child": 4, "disabled": 2, "pregnant": 1, "q_p1": 3,
                  "q_p2": 5, "q_p3": 8, "battery": 77, "status_seq_applied": 99}
        data = codec.pack_heartbeat(census, KEY, 7, 3, 12)
        self.assertEqual(len(data), 33)
        msg = codec.unpack(data, KEYS)
        self.assertEqual(msg.type, codec.HEARTBEAT)
        self.assertEqual(msg.census, census)
        unknown = codec.unpack(codec.pack_heartbeat({}, KEY, 7, 3, 13), KEYS)
        self.assertEqual(unknown.census["battery"], 255)

    def test_hb_reply_roundtrip_and_mtu(self):
        entries = [(i, 1 + i % 3, 100 + i) for i in range(100)]
        data, n = codec.pack_hb_reply(entries, KEY, 7, 2, 3, mtu=200)
        self.assertEqual(n, (200 - 16) // 9)
        self.assertLessEqual(len(data), 200)
        msg = codec.unpack(data, KEYS)
        self.assertEqual([(r["seq"], r["status"], r["status_seq"]) for r in msg.records], entries[:n])

    def test_every_datagram_within_mtu(self):
        rng = random.Random(5)
        for mtu in (64, 95, 120, 200, 340, 1200):
            views = [view(i, summary="區床長者洗腎" * rng.randint(0, 6), quantity=rng.randint(0, 99999))
                     for i in range(1, 60)]
            data, n = codec.pack_summary(views, mtu, KEY, 7, 1, 1)
            if n:
                self.assertLessEqual(len(data), mtu)
            d2, n2 = codec.pack_digest(views, mtu, KEY, 7, 1, 1)
            if n2:
                self.assertLessEqual(len(d2), mtu)
            d3, _ = codec.pack_hb_reply([(i, 1, i) for i in range(300)], KEY, 7, 1, 1, mtu=mtu)
            self.assertLessEqual(len(d3), mtu)
            self.assertLessEqual(len(codec.pack_ack([(1, 1, 2, 0)] * ((mtu - 16) // 8), KEY, 7, 1, 1, mtu=mtu)), mtu)

    def test_record_too_big_for_mtu_packs_nothing(self):
        data, n = codec.pack_summary([view(summary="中" * 20)], 40, KEY, 7, 1, 1)
        self.assertEqual((data, n), (b"", 0))


class Saturation(unittest.TestCase):
    def test_saturated_fields_roundtrip(self):
        now = time.time()
        t = {"seq": 5, "version": 70000, "status": "confirmed", "confirmed_wall": now, "priority": 1, "category": 3,
             "item_code": 301, "quantity": 99999, "subject_group": "elderly", "zone_code": 300,
             "created_wall": now + 300,           # age -5 min (clock skew)
             "deadline_at": now - 3 * 3600,       # overdue -3 h
             "deadline_text": "10/28 08:00", "flags": [], "report_count": 1}
        v = wire_view(t, now, "B")
        data, n = codec.pack_summary([v], 200, KEY, 7, 1, 1)
        self.assertEqual(n, 1)
        r = codec.unpack(data, KEYS).records[0]
        self.assertEqual(r["deadline_h"], 0)
        self.assertEqual(r["age_min"], 0)
        self.assertEqual(r["quantity"], 65535)
        self.assertEqual(r["version"], 65535)
        raw = codec.unpack(codec.pack_summary([dict(v, deadline_h=-3, age_min=-5, quantity=99999)], 200, KEY, 7, 1, 1)[0],
                           KEYS).records[0]
        self.assertEqual((raw["deadline_h"], raw["age_min"], raw["quantity"]), (0, 0, 65535))
        self.assertEqual(codec.clamp_deadline_h(300), 254)
        self.assertEqual(codec.clamp_deadline_h(None), 255)

    def test_census_saturation(self):
        census = {"people": 70000, "elderly": 300, "infant_child": -4, "disabled": 1e9, "pregnant": float("nan"),
                  "q_p1": 99999, "q_p2": None, "q_p3": "x", "battery": 400, "status_seq_applied": 2 ** 40}
        msg = codec.unpack(codec.pack_heartbeat(census, KEY, 7, 1, 1), KEYS)
        c = msg.census
        self.assertEqual((c["people"], c["elderly"], c["infant_child"], c["disabled"], c["pregnant"]),
                         (65535, 255, 0, 255, 0))
        self.assertEqual((c["q_p1"], c["q_p2"], c["q_p3"], c["battery"], c["status_seq_applied"]),
                         (65535, 0, 0, 255, 2 ** 32 - 1))


class Fuzz(unittest.TestCase):
    ALLOWED = (codec.BadMac, codec.BadVersion, codec.Truncated)

    def _check(self, data, keys=KEYS):
        try:
            codec.unpack(data, keys)
        except self.ALLOWED:
            pass

    def test_random_bytes(self):
        rng = random.Random(1234)
        for _ in range(10000):
            n = rng.randint(0, 260)
            data = bytes(rng.getrandbits(8) for _ in range(n))
            if n and rng.random() < 0.5:
                data = bytes([1]) + data[1:]          # valid version byte half the time
            if n > 3 and rng.random() < 0.5:
                data = data[:2] + bytes([0, 7]) + data[4:]   # known desk id
            self._check(data)

    def test_bit_flips(self):
        rng = random.Random(99)
        good = [codec.pack_summary([view(i) for i in range(1, 5)], 200, KEY, 7, 1, 1)[0],
                codec.pack_ack([(1, 1, 2, 0), (2, 3, 2, 1)], KEY, 7, 1, 2),
                codec.pack_heartbeat({"people": 5}, KEY, 7, 1, 3),
                codec.pack_hb_reply([(1, 2, 3)], KEY, 7, 1, 4)[0],
                codec.pack_digest([view(1)], 200, KEY, 7, 1, 5)[0]]
        for g in good:
            codec.unpack(g, KEYS)  # sanity: valid
            for _ in range(600):
                b = bytearray(g)
                for _ in range(rng.randint(1, 3)):
                    i = rng.randrange(len(b))
                    b[i] ^= 1 << rng.randrange(8)
                if bytes(b) == g:   # two flips cancelled out
                    continue
                with self.assertRaises(self.ALLOWED):
                    codec.unpack(bytes(b), KEYS)
            for cut in range(len(g)):
                with self.assertRaises(self.ALLOWED):
                    codec.unpack(g[:cut], KEYS)

    def test_signed_garbage_bodies(self):
        """Valid MAC over a malformed body still raises only the defined errors."""
        import hashlib
        import hmac
        rng = random.Random(7)
        for _ in range(3000):
            mtype = rng.randint(0, 9)
            count = rng.randint(0, 20)
            body = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 120)))
            head = codec.HEADER.pack(1, mtype, 7, 1, 1) + bytes([count])
            data = head + body
            data += hmac.new(KEY, data, hashlib.sha256).digest()[:8]
            self._check(data)

    def test_wrong_key_and_unknown_desk(self):
        data = codec.pack_heartbeat({}, KEY, 7, 1, 1)
        with self.assertRaises(codec.BadMac):
            codec.unpack(data, {7: b"x" * 32})
        with self.assertRaises(codec.BadMac):
            codec.unpack(data, {8: KEY})
        with self.assertRaises(codec.BadVersion):
            codec.unpack(bytes([2]) + data[1:], KEYS)
        with self.assertRaises(codec.Truncated):
            codec.unpack(data[:15], KEYS)
        with self.assertRaises(codec.Truncated):
            codec.unpack(None, KEYS)


if __name__ == "__main__":
    unittest.main()

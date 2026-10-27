"""Link emulator: throughput, loss, oversize/DOWN drops, FIFO tail-drop, HTTP control (SPEC 8.5 / 11).

Each case takes <= 3 s.
"""
import json
import math
import select
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from darkshelter.net.harness import free_port  # noqa: E402
from darkshelter.net.linkemu import LinkEmulator, open_udp  # noqa: E402


class Sink(threading.Thread):
    """UDP sink recording (arrival time, size) of each datagram."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = open_udp("127.0.0.1", 0)
        self.port = self.sock.getsockname()[1]
        self.got = []
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.is_set():
            r, _, _ = select.select([self.sock], [], [], 0.02)
            if not r:
                continue
            while True:
                try:
                    data, _ = self.sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                self.got.append((time.monotonic(), len(data)))

    def close(self):
        self.stop_evt.set()
        self.join(1)
        self.sock.close()


class EmuCase(unittest.TestCase):
    def setUp(self):
        self.sink = Sink()
        self.sink.start()
        self.emu = LinkEmulator(0, ("127.0.0.1", self.sink.port), control_port=None, seed=42, profile="DOWN")
        self.emu.start()
        self.desk = open_udp("127.0.0.1", 0, blocking=True)
        self.addr = ("127.0.0.1", self.emu.port)

    def tearDown(self):
        self.emu.stop()
        self.sink.close()
        self.desk.close()

    def send(self, n, size, gap=0.0):
        payload = b"x" * size
        for _ in range(n):
            self.desk.sendto(payload, self.addr)
            if gap:
                time.sleep(gap)

    def test_throughput_within_10pct(self):
        rate_bps = 64000                   # 8000 B/s
        self.emu.set_custom(rate_bps=rate_bps, loss=0, delay_ms=0, jitter_ms=0, mtu=1000)
        t_end = time.monotonic() + 1.6
        while time.monotonic() < t_end:    # offer ~20 kB/s, more than the link carries
            self.desk.sendto(b"y" * 1000, self.addr)
            time.sleep(0.05)
        time.sleep(0.4)
        got = self.sink.got
        self.assertGreaterEqual(len(got), 8)
        span = got[-1][0] - got[0][0]
        rate = sum(sz for _, sz in got[1:]) / span
        self.assertLess(abs(rate - rate_bps / 8) / (rate_bps / 8), 0.10, f"measured {rate:.0f} B/s")

    def test_loss_within_3_sigma(self):
        p = 0.3
        self.emu.set_custom(rate_bps=100_000_000, loss=p, delay_ms=0, jitter_ms=0, mtu=1400)
        n = 400
        self.send(n, 50, gap=0.0005)
        time.sleep(0.5)
        delivered = len(self.sink.got)
        expected = n * (1 - p)
        sigma = math.sqrt(n * p * (1 - p))
        self.assertLessEqual(abs(delivered - expected), 3 * sigma, f"delivered {delivered}, expected {expected}")
        self.assertEqual(self.emu.stats()["up"]["drops"]["loss"], n - delivered)

    def test_oversize_and_down_dropped(self):
        self.send(10, 100)
        time.sleep(0.2)
        self.assertEqual(len(self.sink.got), 0)
        self.assertEqual(self.emu.stats()["up"]["drops"]["outage"], 10)
        self.emu.set_custom(rate_bps=1_000_000, loss=0, delay_ms=0, jitter_ms=0, mtu=200)
        self.send(1, 201)
        self.send(1, 200)
        time.sleep(0.3)
        self.assertEqual([sz for _, sz in self.sink.got], [200])
        self.assertEqual(self.emu.stats()["up"]["drops"]["mtu"], 1)

    def test_mtu_burst_exactly_five(self):
        # bucket = 1 MTU, FIFO of 4: a 20-datagram MTU burst delivers exactly 5
        self.emu.set_custom(rate_bps=8000, loss=0, delay_ms=0, jitter_ms=0, mtu=200)
        self.send(20, 200)
        time.sleep(1.2)                    # 5th departs at 0.8 s
        self.assertEqual(len(self.sink.got), 5)
        self.assertEqual(self.emu.stats()["up"]["drops"]["queue"], 15)

    def test_delay_and_profile_switch_clears_queue(self):
        self.emu.set_custom(rate_bps=8000, loss=0, delay_ms=300, jitter_ms=0, mtu=200)
        t0 = time.monotonic()
        self.send(1, 100)
        time.sleep(0.6)
        self.assertEqual(len(self.sink.got), 1)
        self.assertGreaterEqual(self.sink.got[0][0] - t0, 0.28)
        self.send(5, 200)                  # 1 in the air + 4 queued
        time.sleep(0.08)                   # let the emulator take them in
        self.emu.set_profile("DOWN")      # entering DOWN drops the queue and the air
        time.sleep(0.6)
        self.assertEqual(len(self.sink.got), 1)
        self.assertGreaterEqual(self.emu.stats()["up"]["drops"]["switch"], 5)


class ReplyPath(unittest.TestCase):
    def test_reply_returns_to_desk(self):
        eoc = open_udp("127.0.0.1", 0, blocking=False)
        emu = LinkEmulator(0, ("127.0.0.1", eoc.getsockname()[1]), seed=1, profile="GOOD").start()
        desk = open_udp("127.0.0.1", 0, blocking=False)
        try:
            desk.sendto(b"ping", ("127.0.0.1", emu.port))
            deadline = time.monotonic() + 2
            data = addr = None
            while time.monotonic() < deadline and data is None:
                select.select([eoc], [], [], 0.05)
                try:
                    data, addr = eoc.recvfrom(100)
                except (BlockingIOError, OSError):
                    pass
            self.assertEqual(data, b"ping")
            eoc.sendto(b"pong", addr)
            data = None
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and data is None:
                select.select([desk], [], [], 0.05)
                try:
                    data, _ = desk.recvfrom(100)
                except (BlockingIOError, OSError):
                    pass
            self.assertEqual(data, b"pong")
        finally:
            emu.stop()
            eoc.close()
            desk.close()


class ControlHttp(unittest.TestCase):
    def test_control_endpoints(self):
        ctl = free_port("tcp")
        emu = LinkEmulator(0, ("127.0.0.1", free_port("udp")), control_port=ctl, seed=3).start()
        base = f"http://127.0.0.1:{ctl}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=3) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        try:
            self.assertEqual(get("/healthz"), (200, {"ok": True, "profile": "DOWN"}))
            st, prof = get("/profile")
            self.assertEqual((st, prof["name"]), (200, "DOWN"))
            st, prof = get("/profile?name=LORA_RELAY")
            self.assertEqual((prof["name"], prof["params"]["rate_bps"], prof["params"]["mtu"]), ("LORA_RELAY", 1200, 200))
            st, prof = get("/profile?name=FLAPPING")
            self.assertEqual((prof["name"], prof["phase"]), ("FLAPPING", "up"))
            req = urllib.request.Request(base + "/custom", method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"rate_bps": 1200, "loss": 0.2, "delay_ms": 400,
                                                          "jitter_ms": 200, "mtu": 200, "seed": 9}).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=3) as r:
                prof = json.loads(r.read().decode("utf-8"))
            self.assertEqual((prof["name"], prof["params"]["loss"]), ("CUSTOM", 0.2))
            st, stats = get("/stats")
            self.assertIn("drops", stats["up"])
            with self.assertRaises(urllib.error.HTTPError) as cm:
                get("/profile?name=NOPE")
            self.assertEqual(cm.exception.code, 400)
        finally:
            emu.stop()

    def test_flapping_cycle(self):
        emu = LinkEmulator(0, ("127.0.0.1", free_port("udp")), seed=3).start()
        try:
            emu.set_custom(rate_bps=16000, loss=0, delay_ms=0, jitter_ms=0, mtu=1200, flap_up_s=0.3, flap_down_s=0.3)
            self.assertEqual(emu.profile_info()["phase"], "up")
            time.sleep(0.45)
            self.assertEqual(emu.profile_info()["phase"], "down")
            self.assertIsNone(emu.profile_info()["params"])
            time.sleep(0.3)
            self.assertEqual(emu.profile_info()["phase"], "up")
        finally:
            emu.stop()


if __name__ == "__main__":
    unittest.main()

"""RTO: Jacobson/Karels known sequences, Karn's rule, backoff cap, reset (SPEC 8.2)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from darkshelter.net.rto import RTO  # noqa: E402


class RtoTests(unittest.TestCase):
    def test_initial(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        self.assertIsNone(r.srtt)
        self.assertEqual((r.rto_base, r.rto), (3.0, 3.0))

    def test_known_sequence(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        r.sample(1.0)                       # SRTT=1, RTTVAR=0.5 -> 1 + 2 = 3
        self.assertAlmostEqual(r.srtt, 1.0)
        self.assertAlmostEqual(r.rttvar, 0.5)
        self.assertAlmostEqual(r.rto_base, 3.0)
        r.sample(2.0)                       # RTTVAR = .375 + .25 = .625; SRTT = .875 + .25 = 1.125
        self.assertAlmostEqual(r.rttvar, 0.625)
        self.assertAlmostEqual(r.srtt, 1.125)
        self.assertAlmostEqual(r.rto_base, 1.125 + 2.5)
        self.assertAlmostEqual(r.rto, r.rto_base)
        r.sample(1.125)                     # RTTVAR = .46875 + 0 ; SRTT unchanged
        self.assertAlmostEqual(r.rttvar, 0.46875)
        self.assertAlmostEqual(r.srtt, 1.125)
        self.assertAlmostEqual(r.rto_base, 1.125 + 1.875)

    def test_clamps(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        for _ in range(50):
            r.sample(0.001)
        self.assertAlmostEqual(r.rto_base, 0.5)       # rto_min
        r2 = RTO(3.0, 0.5, 30.0, 2.0)
        r2.sample(100.0)
        self.assertAlmostEqual(r2.rto_base, 30.0)     # rto_max
        r3 = RTO(3.0, 0.5, 30.0, 2.0)
        r3.sample(0.0)                                # 0 + max(0.01, 0) -> clamped to rto_min
        self.assertAlmostEqual(r3.rto_base, 0.5)

    def test_backoff_cap(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        self.assertAlmostEqual(r.on_timeout(), 6.0)
        self.assertAlmostEqual(r.on_timeout(), 6.0)   # capped at 2 x rto_base
        r.sample(1.0)
        r.sample(2.0)
        base = r.rto_base
        self.assertAlmostEqual(r.rto, base)           # a valid sample resets RTO to rto_base
        self.assertAlmostEqual(r.on_timeout(), 2 * base)
        self.assertAlmostEqual(r.on_timeout(), 2 * base)
        big = RTO(20.0, 0.5, 30.0, 2.0)
        self.assertAlmostEqual(big.on_timeout(), 30.0)  # capped at rto_max

    def test_karn(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        self.assertFalse(r.sample_if(1.0, first_try=False, echoed_boot=4, current_boot=4))
        self.assertFalse(r.sample_if(1.0, first_try=True, echoed_boot=3, current_boot=4))
        self.assertIsNone(r.srtt)
        self.assertTrue(r.sample_if(1.0, first_try=True, echoed_boot=4, current_boot=4))
        self.assertEqual(r.srtt, 1.0)
        self.assertFalse(r.sample(-1.0))
        self.assertFalse(r.sample(float("nan")))
        self.assertEqual(r.samples, 1)

    def test_reset(self):
        r = RTO(3.0, 0.5, 30.0, 2.0)
        r.sample(10.0)
        r.on_timeout()
        r.reset()
        self.assertIsNone(r.srtt)
        self.assertEqual((r.rto_base, r.rto, r.samples), (3.0, 3.0, 0))


if __name__ == "__main__":
    unittest.main()

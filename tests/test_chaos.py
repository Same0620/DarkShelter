"""Chaos (proof of G1/G2): 30% loss + flapping; kill and restart the sender subprocess AND the EOC
subprocess at least twice each. Pass: the EOC set equals the desk's confirmed wire views with
0 duplicates shown. Fail: not equal after 180 s. Skipped with DS_FAST=1.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from darkshelter.net.harness import ProcRig, run_chaos, seed_tickets  # noqa: E402

FAST = os.environ.get("DS_FAST") == "1"


@unittest.skipIf(FAST, "slow chaos test (DS_FAST=1)")
class ChaosUnitTest(unittest.TestCase):
    def test_kill_sender_and_eoc_twice_each(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rig = ProcRig(tmp, seed=13)
            try:
                seqs, prios = seed_tickets(rig.desk, n=30, seed=13, p1=5, p2=10)
                rig.start()
                res = run_chaos(rig, seqs, prios, kills_each=2, seed=13, timeout=180.0,
                                log=lambda m: print(m, file=sys.stderr))
                print(f"\n  chaos: equal={res['equal']} kills={res['kills']} elapsed={res['elapsed_s']}s "
                      f"desk={res['tickets_desk']} eoc={res['tickets_eoc']} shown_duplicates={res['shown_duplicates']} "
                      f"dup_receptions_suppressed={res['dup_receptions_suppressed']}", file=sys.stderr)
                self.assertGreaterEqual(res["kills"]["sender"], 2)
                self.assertGreaterEqual(res["kills"]["eoc"], 2)
                self.assertTrue(res["equal"], res["compare"])
                self.assertEqual(res["shown_duplicates"], 0)
            finally:
                rig.stop()


if __name__ == "__main__":
    unittest.main()

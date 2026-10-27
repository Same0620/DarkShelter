"""Chaos experiment (G1/G2): 30% loss + flapping; kill and restart the sender subprocess and the
EOC subprocess at least 5 times each, then require the EOC set to equal the desk's set of
confirmed wire views with 0 duplicates shown (fail after 180 s).

    python eval/chaos.py [--kills 5] [--tickets 40] [--seed 13] [--timeout 180]

Writes eval/results/chaos.md and eval/results/chaos.json.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darkshelter.net.harness import CHAOS_PROFILE, ProcRig, run_chaos, seed_tickets  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kills", type=int, default=5, help="kills per process (>= 5 for the deck)")
    ap.add_argument("--tickets", type=int, default=40)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"chaos: {args.tickets} tickets, {args.kills} kills each of sender and EOC, profile {CHAOS_PROFILE}")
    t0 = time.time()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        rig = ProcRig(tmp, seed=args.seed)
        try:
            seqs, prios = seed_tickets(rig.desk, n=args.tickets, seed=args.seed, p1=max(1, args.tickets // 6),
                                       p2=args.tickets // 3)
            rig.start()
            res = run_chaos(rig, seqs, prios, kills_each=args.kills, seed=args.seed, timeout=args.timeout, log=print)
        finally:
            rig.stop()
    passed = res["equal"] and res["shown_duplicates"] == 0 and min(res["kills"].values()) >= args.kills
    res.update({"passed": passed, "profile": CHAOS_PROFILE, "seed": args.seed, "kills_requested": args.kills,
                "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0))})
    (out / "chaos.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = res["compare"] or {}
    md = [
        "# Chaos experiment",
        "",
        f"Run {res['started']} · seed {args.seed} · link: {CHAOS_PROFILE['loss']:.0%} loss each way, "
        f"{CHAOS_PROFILE['rate_bps']} bps, {CHAOS_PROFILE['delay_ms']:.0f} ± {CHAOS_PROFILE['jitter_ms']:.0f} ms, "
        f"flapping {CHAOS_PROFILE['flap_up_s']:.0f} s up / {CHAOS_PROFILE['flap_down_s']:.0f} s down.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Result | **{'PASS' if passed else 'FAIL'}** |",
        f"| Sender kills (subprocess, hard kill) | {res['kills']['sender']} |",
        f"| EOC kills (subprocess, hard kill) | {res['kills']['eoc']} |",
        f"| Tickets on the desk (wire views) | {res['tickets_desk']} |",
        f"| Tickets on the EOC board | {res['tickets_eoc']} |",
        f"| Missing at the EOC | {len(c.get('missing', []))} |",
        f"| Wrong version/content at the EOC | {len(c.get('mismatched', []))} |",
        f"| Extra at the EOC | {len(c.get('extra', []))} |",
        f"| Duplicates shown on the board | {res['shown_duplicates']} |",
        f"| Duplicate receptions suppressed (re-acked) | {res['dup_receptions_suppressed']} |",
        f"| Time until equal | {res['elapsed_s']} s ({res['converge_after_kills_s']} s after the last restart) |",
        f"| Mid-run changes | {json.dumps(res['changes'], ensure_ascii=False)} + 5 new tickets |",
        "",
        "Pass = the EOC set equals the desk's set of confirmed wire views (version, flags, zone, quantity,",
        "summary, priority, category, item) with 0 duplicates shown, within 180 s.",
    ]
    (out / "chaos.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

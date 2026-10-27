"""Network experiment: 60 seeded tickets, the link DOWN for 5 s, then each link profile.

For each profile (fresh databases each run, capped at 180 s):
  - time until the census arrives, until all P1s arrive, until everything is delivered and acked
  - bytes up/down, datagrams, retransmissions
  - duplicates (shown / suppressed), losses, ordering violations
  - the voice-note baseline: 10 s x 2,000 B/s per request; time = bytes / rate, ignoring loss

    python eval/net_experiment.py [--profiles LORA_RELAY,GOOD] [--seed 7] [--tickets 60]

Writes eval/results/net_experiment.csv and eval/results/net_experiment.md.
The sender is NOT told when the link returns (no reset_probe): it finds out from its own
heartbeat probes, as it would in the field.
"""
import argparse
import csv
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darkshelter.net.harness import InProcRig, ordering_violations, seed_tickets  # noqa: E402
from darkshelter.net.linkemu import FLAPPING, PROFILES  # noqa: E402

ALL_PROFILES = ["LORA_RELAY", "LTE_ONE_BAR", "SATELLITE", "GOOD", "FLAPPING"]
VOICE_S = 10.0
VOICE_BPS = 2000.0


def effective_rate_Bps(name):
    if name == "FLAPPING":
        up = PROFILES[FLAPPING["up"]]
        return up["rate_bps"] / 8.0 * FLAPPING["up_s"] / (FLAPPING["up_s"] + FLAPPING["down_s"])
    return PROFILES[name]["rate_bps"] / 8.0


def run_profile(name, n_tickets, seed, cap_s=180.0, down_s=5.0):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        rig = InProcRig(tmp, seed=seed, profile="DOWN")
        try:
            seqs, prios = seed_tickets(rig.desk, n=n_tickets, seed=seed, p1=max(1, n_tickets // 6),
                                       p2=n_tickets // 3)
            p1 = {s for s in seqs if prios[s] == 1}
            rig.start()
            time.sleep(down_s)                                  # link DOWN: the queue waits, probes go nowhere
            pre = rig.sender.stats()
            t0 = time.monotonic()
            rig.emu.set_profile(name)
            t_census = t_p1 = t_all = None
            while time.monotonic() - t0 < cap_s:
                rows = rig.eoc.rx_log()
                if t_census is None:
                    hb = [r["mono"] for r in rows if r["kind"] == "hb"]
                    if hb:
                        t_census = min(hb) - t0
                if t_p1 is None:
                    got = {}
                    for r in rows:
                        if r["kind"] == "data" and r["seq"] in p1 and r["seq"] not in got:
                            got[r["seq"]] = r["mono"]
                    if len(got) == len(p1):
                        t_p1 = max(got.values()) - t0
                if t_all is None and not rig.desk.pending_units():
                    c = rig.compare()
                    if c["equal"]:
                        t_all = time.monotonic() - t0
                        break
                time.sleep(0.05)
            time.sleep(0.3)
            s = rig.sender.stats()
            c = rig.compare()
            delivered_by_cap = c["desk"] - len(c["missing"]) - len(c["mismatched"])
            drained_s = None
            if t_all is None:
                # Not delivered by the cap is not lost: the rest is still in the crash-proof outbox.
                # Prove it: switch to GOOD, drain, and count only what never arrives as lost.
                t_drain = time.monotonic()
                rig.emu.set_profile("GOOD")
                rig.sender.reset_probe()
                end = time.monotonic() + 60.0
                while time.monotonic() < end:
                    if not rig.desk.pending_units() and rig.compare()["equal"]:
                        drained_s = round(time.monotonic() - t_drain, 2)
                        break
                    time.sleep(0.1)
                c = rig.compare()
            rows = rig.eoc.rx_log()
            keys = [(r["desk_id"], r["seq"]) for r in rig.eoc.board()]
            emu = rig.emu.stats()
            eoc_stats = rig.eoc.stats()
            n = len(seqs)
            voice_bytes = n * VOICE_S * VOICE_BPS
            rate = effective_rate_Bps(name)
            return {
                "profile": name, "tickets": n, "p1": len(p1),
                "census_s": round(t_census, 2) if t_census is not None else None,
                "all_p1_s": round(t_p1, 2) if t_p1 is not None else None,
                "all_s": round(t_all, 2) if t_all is not None else None,
                "bytes_up": s["bytes_up"] - pre.get("bytes_up", 0), "bytes_down": s["bytes_down"],
                "data_bytes_up": s["data_bytes_up"],
                "bytes_per_request": round(s["data_bytes_up"] / max(1, n), 1),
                "datagrams_up": s["datagrams_up"] - pre.get("datagrams_up", 0),
                "datagrams_down": s["datagrams_down"],
                "retransmissions": s["retransmissions"], "timeouts": s["timeouts"],
                "dups_shown": len(keys) - len(set(keys)), "dups_suppressed": eoc_stats.get("duplicates", 0),
                "losses": len(c["missing"]) + len(c["mismatched"]),
                "ordering_violations": len(ordering_violations(rows, p1)),
                "emu_drops_up": sum(emu["up"]["drops"].values()), "emu_drops_down": sum(emu["down"]["drops"].values()),
                "voice_bytes": int(voice_bytes), "voice_time_s": round(voice_bytes / rate, 1),
                "ratio_voice_vs_data": round(voice_bytes / max(1, s["data_bytes_up"]), 1),
                "srtt_s": round(s["srtt"], 3) if s["srtt"] else None, "rto_s": round(s["rto"], 2),
                "completed": t_all is not None, "delivered_by_cap": delivered_by_cap,
                "drained_after_cap_s": drained_s,
            }
        finally:
            rig.stop()


def write_results(results, out_dir, seed):
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = list(results[0].keys())
    with open(out_dir / "net_experiment.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(r)
    fmt = (lambda v: "—" if v is None else str(v))

    def all_cell(r):
        if r["all_s"] is not None:
            return str(r["all_s"])
        return f"> cap ({r['delivered_by_cap']}/{r['tickets']} by the cap)"
    md = ["# Network experiment", "",
          f"{results[0]['tickets']} seeded tickets ({results[0]['p1']} P1) per run, seed {seed}. The link is DOWN "
          "for 5 s, then switches to the profile; the sender finds out from its own heartbeat probes. "
          "Times are measured from the switch. Each run is capped at 180 s.", "",
          "| Profile | Census (s) | All P1 (s) | All (s) | Bytes up | Bytes down | Datagrams up | Retrans. | "
          "B/request | Dups shown | Dups suppressed | Losses | Ordering violations | Voice notes: bytes | "
          "Voice notes: time (s) | Voice ÷ data |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['profile']} | {fmt(r['census_s'])} | {fmt(r['all_p1_s'])} | {all_cell(r)} | "
                  f"{r['bytes_up']} | {r['bytes_down']} | {r['datagrams_up']} | {r['retransmissions']} | "
                  f"{r['bytes_per_request']} | {r['dups_shown']} | {r['dups_suppressed']} | {r['losses']} | "
                  f"{r['ordering_violations']} | {r['voice_bytes']:,} | {r['voice_time_s']:,} | "
                  f"{r['ratio_voice_vs_data']}× |")
    ok = all(r["losses"] == 0 and r["dups_shown"] == 0 and r["ordering_violations"] == 0 for r in results)
    late = [r for r in results if not r["completed"]]
    md += ["", f"**Result: {'PASS' if ok else 'FAIL'}** — 0 duplicates shown, 0 losses and 0 ordering violations "
           "required on every profile."]
    if late:
        md += ["", "Not finished within the 180 s cap: " + ", ".join(
            f"{r['profile']} ({r['delivered_by_cap']}/{r['tickets']} delivered by the cap; the rest stayed in the "
            f"outbox and arrived {fmt(r['drained_after_cap_s'])} s after switching to GOOD)" for r in late) +
            ". A ticket still queued at the cap is not lost; 'Losses' counts tickets that never arrived."]
    else:
        md += ["", "Every profile delivered every ticket within the 180 s cap."]
    md += ["",
           "Voice-note baseline: each request as a 10 s voice note at 2,000 B/s (16 kbps); time = bytes ÷ link rate, "
           "ignoring loss (FLAPPING uses its average rate, LTE one bar up 1/3 of the time). "
           "'Dups suppressed' are duplicate receptions the EOC re-acked and dropped; none is ever shown."]
    (out_dir / "net_experiment.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return ok, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser(description="DarkShelter network experiment")
    ap.add_argument("--profiles", default=",".join(ALL_PROFILES))
    ap.add_argument("--tickets", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cap", type=float, default=180.0)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args(argv)
    results = []
    for name in [p.strip().upper() for p in args.profiles.split(",") if p.strip()]:
        t = time.monotonic()
        print(f"running {name} ...", flush=True)
        r = run_profile(name, args.tickets, args.seed, cap_s=args.cap)
        results.append(r)
        print(f"  {name}: census {r['census_s']} s, all P1 {r['all_p1_s']} s, all {r['all_s']} s, "
              f"bytes up {r['bytes_up']}, retrans {r['retransmissions']}, losses {r['losses']}, "
              f"dups shown {r['dups_shown']}, ordering violations {r['ordering_violations']} "
              f"({time.monotonic() - t:.0f} s wall)", flush=True)
    ok, md = write_results(results, Path(args.out), args.seed)
    print(md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Acceptance check 2 driver: run against `python run_demo.py --mock --no-browser --reset`.

Check-in and typed requests -> drafts -> confirm -> queued while DOWN. On LORA_RELAY the census arrives first,
then P1s before any P3, visible on the EOC page; 已派遣 at the EOC shows at the desk.

python eval/demo_check.py [--desk http://127.0.0.1:8000] [--eoc http://127.0.0.1:8100]
Writes eval/results/demo_check.md. Exit code 0 only if every step passes.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import darkshelter  # noqa: E402,F401  (cp950-safe console: ✓/⏳ print as ? instead of crashing)
REQUESTS = [
    "阿嬤明天早上要洗腎，她的胰島素今天也用完了",   # 2 x P1
    "我爸還在中正路三巷的家裡沒出來",               # P1 rescue
    "C區5床的爺爺需要輪椅",                         # P2
    "我們家四個人需要毛毯",                         # P3
    "我想要手機充電",                               # P3
    "A區2床需要飲用水三瓶",                         # P3
]


def call(base, path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Check:
    def __init__(self):
        self.lines = []
        self.ok = True

    def step(self, name, passed, detail=""):
        self.ok = self.ok and bool(passed)
        line = f"{'PASS' if passed else 'FAIL'}  {name}" + (f" — {detail}" if detail else "")
        print(line, flush=True)
        self.lines.append(line)


def wait_job(desk, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = call(desk, f"/api/jobs/{job_id}")
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--desk", default="http://127.0.0.1:8000")
    ap.add_argument("--eoc", default="http://127.0.0.1:8100")
    ap.add_argument("--profile", default="LORA_RELAY")
    args = ap.parse_args(argv)
    desk, eoc = args.desk, args.eoc
    ck = Check()
    t0 = time.time()

    call(desk, "/api/link/profile", {"name": "DOWN"})
    st = call(desk, "/api/state")
    ck.step("desk up, link DOWN", st["sender"].get("link_state") in ("DOWN", "UNKNOWN"),
            f"mode={st['health']['mode']} link={st['sender'].get('link_state')}")

    # 1. check-in with a screening "yes"
    r = call(desk, "/api/checkin", {"family_name": "陳", "size": 4, "zone": "B12", "elderly": 1, "infants": 1,
                                    "children": 1, "screening": {"q1": True}, "clerk": "值班人員A"})
    ck.step("check-in: screening yes -> draft", len(r["drafts"]) == 1, f"household {r['household_id']} drafts {r['drafts']}")

    # 2. typed requests
    seqs = list(r["drafts"])
    for text in REQUESTS:
        j = wait_job(desk, call(desk, "/api/intake/text", {"text": text, "clerk": "值班人員A"})["job_id"])
        seqs += j["seqs"]
    st = call(desk, "/api/state")
    drafts = {d["seq"]: d for d in st["drafts"]}
    ck.step("typed requests -> drafts", all(s in drafts for s in seqs),
            ", ".join(f"{drafts[s]['code']} P{drafts[s]['priority']} {drafts[s]['category_zh'].split('（')[0]}"
                      for s in seqs if s in drafts))

    # 3. confirm everything (as the clerk would: one tap each)
    for s in seqs:
        call(desk, f"/api/tickets/{s}/confirm", {"clerk": "值班人員A"})
    st = call(desk, "/api/state")
    board = {t["seq"]: t for t in st["board"]["tickets"]}
    ck.step("all confirmed onto the board", all(s in board for s in seqs), f"{len(seqs)} tickets")
    prio = {s: board[s]["priority"] for s in seqs if s in board}

    # 4. queued while DOWN
    time.sleep(4)
    st = call(desk, "/api/state")
    est = call(eoc, "/api/state")
    queued = [t for t in st["board"]["tickets"] if t["wire_state"]["icon"] == "⏳"]
    ck.step("queued while DOWN (desk ⏳, EOC empty)", len(queued) == len(seqs) and not est["tickets"],
            f"desk queued={len(queued)} eoc tickets={len(est['tickets'])} data_bytes_up={st['sender'].get('data_bytes_up')}")

    # 5. link comes back on the LoRa-class relay
    call(desk, "/api/link/profile", {"name": args.profile})
    t_switch = time.time()
    seen = {}
    census_seen = None
    deadline = time.time() + 150
    while time.time() < deadline:
        est = call(eoc, "/api/state")
        now = time.time()
        if census_seen is None and any((d.get("census") or {}).get("people") for d in est["desks"]):
            census_seen = now
        for t in est["tickets"]:
            seen.setdefault(t["seq"], now)
        if all(s in seen for s in seqs):
            break
        time.sleep(0.1)
    rows = {t["seq"]: t for t in est["tickets"]}
    ck.step(f"all {len(seqs)} tickets visible on the EOC page", all(s in rows for s in seqs),
            f"{len(rows)} rows after {time.time() - t_switch:.1f} s")
    first_ticket = min(seen.values()) if seen else None
    ck.step("census arrives first", census_seen is not None and first_ticket is not None and census_seen <= first_ticket,
            f"census at +{(census_seen or 0) - t_switch:.2f} s, first ticket at +{(first_ticket or 0) - t_switch:.2f} s, "
            f"people={est['desks'][0]['census'].get('people') if est['desks'] else None}")
    rx = {s: rows[s]["first_rx_wall"] for s in seqs if s in rows}
    p1 = [rx[s] for s in rx if prio.get(s) == 1]
    p3 = [rx[s] for s in rx if prio.get(s) == 3]
    ck.step("every P1 before any P3 (EOC receive times)", p1 and p3 and max(p1) <= min(p3),
            f"P1 rx +{max(p1) - t_switch:.2f} s (last), P3 rx +{min(p3) - t_switch:.2f} s (first)" if p1 and p3 else "")
    dup = sum(int(t.get("dup_count") or 0) for t in est["tickets"])
    # the sender refreshes its voice-note total every few seconds: wait until it covers every delivery
    deadline = time.time() + 15
    while True:
        st = call(desk, "/api/state")
        delivered = sum(1 for t in st["board"]["tickets"] if t.get("delivered_wall"))
        if st["bytes"]["voice_equiv_bytes"] >= delivered * 10 * 2000 or time.time() > deadline:
            break
        time.sleep(0.5)
    ck.step("byte meter", st["bytes"]["data_bytes_up"] > 0,
            f"sent {st['bytes']['data_bytes_up']} B vs voice notes {st['bytes']['voice_equiv_bytes']} B "
            f"(ratio {st['bytes']['ratio']}x); duplicates re-acked at EOC: {dup}")

    # 6. EOC clicks 已派遣 on the first P1 -> desk shows it
    target = sorted(s for s in seqs if prio.get(s) == 1)[0]
    desk_id = rows[target]["desk_id"]
    call(eoc, f"/api/tickets/{desk_id}/{target}/status", {"status": 2})
    deadline = time.time() + 60
    shown = None
    while time.time() < deadline:
        st = call(desk, "/api/state")
        row = [t for t in st["board"]["tickets"] if t["seq"] == target]
        if row and row[0]["eoc_status"] == 2:
            shown = row[0]
            break
        time.sleep(0.25)
    ck.step("已派遣 at the EOC shows at the desk", shown is not None,
            f"{shown['code']} {shown['wire_state']['icon']} {shown['wire_state']['zh']}" if shown else "not shown in 60 s")

    out = ROOT / "eval" / "results" / "demo_check.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# Acceptance check 2 — demo flow (mock mode)\n\n" + time.strftime("%Y-%m-%d %H:%M:%S") +
                   f", profile {args.profile}, {time.time() - t0:.1f} s total\n\n```\n" + "\n".join(ck.lines) + "\n```\n",
                   encoding="utf-8")
    print(("ALL PASS" if ck.ok else "SOME CHECKS FAILED") + f" ({time.time() - t0:.1f} s)")
    return 0 if ck.ok else 1


if __name__ == "__main__":
    sys.exit(main())

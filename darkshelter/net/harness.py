"""Harness for network tests and experiments (tests/, eval/net_experiment.py, eval/chaos.py).

- net_config(): a config with free ports and temp databases
- seed_tickets(): seeded mix of confirmed P1/P2/P3 tickets
- InProcRig: desk Store + Sender thread, LinkEmulator, EocStore + Receiver thread (one process)
- ProcRig: sender and EOC as subprocesses (killable), emulator in-process (chaos)
- compare() / ordering_violations(): the pass criteria
"""
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .. import codebook
from ..config import ROOT, eoc_keys, make_config, save_config
from ..store import Store
from ..wireview import wire_view
from .eoc_store import EocStore
from .linkemu import LinkEmulator
from .receiver import Receiver
from .sender import Sender

WIRE_KEYS = ("version", "flags", "zone", "quantity", "summary", "priority", "category", "item_code")

# (priority, category, item_code, subject_group, quantity, emergency)
TEMPLATES = [
    (1, 2, 201, "elderly", 0, None),
    (1, 2, 203, "elderly", 0, None),
    (1, 3, 301, "adult", 0, None),
    (1, 4, 401, "unknown", 2, "rescue"),
    (1, 1, 101, "elderly", 0, "medical"),
    (2, 5, 501, "elderly", 1, None),
    (2, 6, 601, "infant", 2, None),
    (2, 3, 306, "elderly", 0, None),
    (2, 12, 1203, "unknown", 0, None),
    (3, 8, 801, "unknown", 4, None),
    (3, 7, 701, "unknown", 10, None),
    (3, 10, 1001, "adult", 0, None),
    (3, 11, 1101, "unknown", 1, None),
    (3, 8, 802, "child", 3, None),
]


def free_port(kind="udp"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def net_config(tmpdir, net=None, extra=None):
    """Config with free ports and both databases inside `tmpdir`."""
    tmpdir = Path(tmpdir)
    ports = {"desk_http": free_port("tcp"), "eoc_http": free_port("tcp"), "emu_udp": free_port("udp"),
             "eoc_udp": free_port("udp"), "emu_control_http": free_port("tcp")}
    over = {"ports": ports, "paths": {"desk_db": str(tmpdir / "desk.db"), "eoc_db": str(tmpdir / "eoc.db")}}
    if net:
        over["net"] = dict(net)
    if extra:
        over.update(extra)
    return make_config(over)


def make_need(priority, category, item_code, subject_group, quantity, emergency, rng, now, external=False):
    it = codebook.item(item_code)
    need = {"category": category, "item": it["zh"] if it else "", "item_code": item_code,
            "priority": priority, "rule_priority": priority, "subject_group": subject_group,
            "quantity": quantity or None, "emergency": emergency,
            "summary": "（測試）", "flags": []}
    if external:
        need["zone_code"] = codebook.ZONE_EXTERNAL
        need["location"] = rng.choice(["中正路三巷", "中山路28號", "光復鄉大同村", "民生街5巷"])
    else:
        need["zone_code"] = codebook.zone_code(rng.choice("ABCDE"), rng.randint(1, 40))
    if rng.random() < 0.6:
        hours = rng.choice([1, 6, 12, 20, 30, 48, 90])
        need["deadline_at"] = now + hours * 3600
        need["deadline_text"] = time.strftime("%m/%d %H:%M", time.localtime(now + hours * 3600))
    return need


def seed_tickets(store, n=60, seed=1, p1=10, p2=20, clerk="測試員", confirm=True):
    """Create n tickets (p1 x P1, p2 x P2, rest P3) in a shuffled order and confirm them.

    Returns (seqs, priorities {seq: p}).
    """
    rng = random.Random(seed)
    order = [1] * p1 + [2] * p2 + [3] * max(0, n - p1 - p2)
    rng.shuffle(order)
    now = store.clock()
    seqs, prios = [], {}
    for i, p in enumerate(order):
        tpl = rng.choice([t for t in TEMPLATES if t[0] == p])
        external = tpl[1] == 4 and rng.random() < 0.5
        need = make_need(*tpl, rng=rng, now=now, external=external)
        seq = store.create_draft(need, job_id=f"seed-{seed}-{i}", transcript="（測試資料）")
        if confirm:
            store.confirm(seq, clerk=clerk)
        seqs.append(seq)
        prios[seq] = p
    return seqs, prios


def desk_views(store, now=None):
    now = store.clock() if now is None else now
    out = {}
    for t in store.tickets():
        v = wire_view(t, now, store.desk_letter)
        if v is not None:
            out[t["seq"]] = v
    return out


def compare(desk_store, eoc_store, desk_id):
    """EOC set vs the desk's wire views at level 2 -> dict with missing/mismatched/extra."""
    views = desk_views(desk_store)
    eoc = eoc_store.tickets(desk_id)
    missing, mismatched = [], []
    for seq, v in views.items():
        row = eoc.get((desk_id, seq))
        if row is None:
            missing.append(seq)
            continue
        diffs = {k: (v[k], row.get(k)) for k in WIRE_KEYS if v[k] != row.get(k)}
        if row.get("level") != 2:
            diffs["level"] = (2, row.get("level"))
        if diffs:
            mismatched.append((seq, diffs))
    extra = sorted(s for (d, s) in eoc if d == desk_id and s not in views)
    dup_count = sum(int(r.get("dup_count") or 0) for r in eoc.values())
    return {"equal": not missing and not mismatched and not extra, "missing": sorted(missing),
            "mismatched": mismatched, "extra": extra, "dup_count": dup_count, "desk": len(views), "eoc": len(eoc)}


def ordering_violations(rx_rows, p1_seqs):
    """DATA before the first HEARTBEAT, or a P2/P3 record before every seeded P1 arrived."""
    violations = []
    seen_hb = False
    left = set(p1_seqs)
    for r in rx_rows:
        if r["kind"] == "hb":
            seen_hb = True
            continue
        if not seen_hb:
            violations.append(("data_before_heartbeat", r["seq"], r["id"]))
        if r["priority"] == 1:
            left.discard(r["seq"])
        elif left:
            violations.append(("p23_before_all_p1", r["seq"], r["id"]))
    return violations


def wait_until(pred, timeout, poll=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(poll)
    try:
        return bool(pred())
    except Exception:
        return False


def acked_count(store):
    return store.query("SELECT COUNT(*) AS n FROM tickets WHERE acked_version > 0")[0]["n"]


class InProcRig:
    """Everything in this process: desk Store + Sender, LinkEmulator, EocStore + Receiver."""

    def __init__(self, tmpdir, seed=0, profile="DOWN", net=None):
        self.cfg = net_config(tmpdir, net=net)
        self.seed = seed
        self.desk = Store(self.cfg["paths"]["desk_db"], desk_letter=self.cfg["desk_letter"])
        self.eoc = EocStore(self.cfg["paths"]["eoc_db"])
        self.receiver = Receiver(self.eoc, eoc_keys(self.cfg), port=self.cfg["ports"]["eoc_udp"],
                                 mtu=self.cfg["net"]["mtu"])
        self.emu = LinkEmulator(self.cfg["ports"]["emu_udp"], ("127.0.0.1", self.cfg["ports"]["eoc_udp"]),
                                control_port=None, seed=seed, profile=profile)
        self.sender = None
        self.desk_id = int(self.cfg["desk_id"])

    def start(self, sender=True):
        self.receiver.start()
        self.emu.start()
        if sender:
            self.start_sender()
        return self

    def start_sender(self, **kw):
        self.sender = Sender(self.desk, self.cfg, seed=self.seed, **kw)
        self.sender.start()
        return self.sender

    def stop(self):
        for part in (self.sender, self.emu, self.receiver):
            if part is not None:
                try:
                    part.stop()
                except Exception:
                    pass
        self.desk.close()
        self.eoc.close()

    def compare(self):
        return compare(self.desk, self.eoc, self.desk_id)


class ProcRig:
    """Sender and EOC as killable subprocesses; emulator and both stores (for checks) in-process."""

    def __init__(self, tmpdir, seed=0, net=None):
        self.tmp = Path(tmpdir)
        self.cfg = net_config(tmpdir, net=net)
        self.cfg_path = self.tmp / "config.json"
        save_config(self.cfg, self.cfg_path)
        self.desk = Store(self.cfg["paths"]["desk_db"], desk_letter=self.cfg["desk_letter"])
        self.eoc = EocStore(self.cfg["paths"]["eoc_db"])
        self.emu = LinkEmulator(self.cfg["ports"]["emu_udp"], ("127.0.0.1", self.cfg["ports"]["eoc_udp"]),
                                control_port=None, seed=seed, profile="DOWN")
        self.desk_id = int(self.cfg["desk_id"])
        self.procs = {}
        self.logs = {}
        self.starts = {"sender": 0, "eoc": 0}
        self.kills = {"sender": 0, "eoc": 0}

    def _spawn(self, name, args):
        self.starts[name] += 1
        log_path = self.tmp / f"{name}.{self.starts[name]}.log"
        log = open(log_path, "w", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        p = subprocess.Popen([sys.executable, "-m"] + args, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                             env=env)
        self.procs[name] = p
        self.logs[name] = (log, log_path)
        return p

    def start_eoc(self, timeout=20.0):
        self._spawn("eoc", ["darkshelter.eoc_app", "--config", str(self.cfg_path)])
        url = f"http://127.0.0.1:{self.cfg['ports']['eoc_http']}/healthz"
        ok = wait_until(lambda: urllib.request.urlopen(url, timeout=1).status == 200, timeout, poll=0.1)
        if not ok:
            raise RuntimeError("EOC did not become ready: " + self.tail("eoc"))

    def start_sender(self):
        self._spawn("sender", ["darkshelter.net.sender", "--db", self.cfg["paths"]["desk_db"],
                               "--config", str(self.cfg_path)])

    def kill(self, name):
        p = self.procs.get(name)
        if p is not None and p.poll() is None:
            p.kill()
            p.wait(timeout=10)
            self.kills[name] += 1
        log = self.logs.get(name)
        if log:
            try:
                log[0].close()
            except Exception:
                pass

    def alive(self, name):
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def tail(self, name, n=20):
        log = self.logs.get(name)
        if not log:
            return ""
        try:
            log[0].flush()
            lines = Path(log[1]).read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except Exception:
            return ""

    def eoc_state(self):
        url = f"http://127.0.0.1:{self.cfg['ports']['eoc_http']}/api/state"
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))

    def start(self):
        self.emu.start()
        self.start_eoc()
        self.start_sender()
        return self

    def stop(self):
        for name in list(self.procs):
            self.kill(name)
        self.emu.stop()
        self.desk.close()
        self.eoc.close()

    def compare(self):
        return compare(self.desk, self.eoc, self.desk_id)


CHAOS_PROFILE = {"rate_bps": 16000, "loss": 0.30, "delay_ms": 150, "jitter_ms": 100, "mtu": 1200,
                 "flap_up_s": 6.0, "flap_down_s": 3.0}


def make_changes(store, seqs, prios, rng, n_edit=3, n_discard=1, n_resolve=1, clerk="測試員"):
    """Edit / discard / resolve some P2-P3 tickets mid-run (P1s stay untouched)."""
    pool = [s for s in seqs if prios.get(s) != 1 and store.get(s)["status"] == "confirmed"]
    rng.shuffle(pool)
    done = {"edited": [], "discarded": [], "resolved": []}
    for s in pool[:n_edit]:
        t = store.get(s)
        store.update_fields(s, {"quantity": int(t["quantity"] or 0) + 3}, clerk, expected_version=t["version"])
        done["edited"].append(s)
    for s in pool[n_edit:n_edit + n_discard]:
        store.discard(s, clerk, reason="測試：重複登記")
        done["discarded"].append(s)
    for s in pool[n_edit + n_discard:n_edit + n_discard + n_resolve]:
        store.resolve(s, clerk)
        done["resolved"].append(s)
    return done


def run_chaos(rig, seqs, prios, kills_each=2, seed=13, timeout=180.0, new_tickets=5, log=None):
    """Randomly kill and restart the sender and the EOC subprocesses, then poll for equality.

    Pass: the EOC set equals the desk's set of wire views, 0 duplicates shown, within `timeout` s.
    """
    log = log or (lambda *a: None)
    rng = random.Random(seed)
    t_start = time.monotonic()
    rig.emu.set_custom(seed=seed, **CHAOS_PROFILE)
    plan = ["sender"] * kills_each + ["eoc"] * kills_each
    rng.shuffle(plan)
    changes = None
    for i, who in enumerate(plan):
        time.sleep(rng.uniform(2.5, 4.5))
        rig.kill(who)
        log(f"  t={time.monotonic() - t_start:5.1f}s killed {who} (#{rig.kills[who]})")
        if i == 0:
            changes = make_changes(rig.desk, seqs, prios, rng)
            more, more_p = seed_tickets(rig.desk, n=new_tickets, seed=seed + 1000, p1=1, p2=2)
            seqs = seqs + more
            prios.update(more_p)
        time.sleep(rng.uniform(0.3, 1.0))
        if who == "eoc":
            rig.start_eoc()
        else:
            rig.start_sender()
        log(f"  t={time.monotonic() - t_start:5.1f}s restarted {who}")
    t_kills_done = time.monotonic()
    result = None
    while time.monotonic() - t_start < timeout:
        result = rig.compare()
        if result["equal"]:
            break
        if not rig.alive("sender"):
            log("  sender died unexpectedly; restarting\n" + rig.tail("sender"))
            rig.start_sender()
        if not rig.alive("eoc"):
            log("  eoc died unexpectedly; restarting\n" + rig.tail("eoc"))
            rig.start_eoc()
        time.sleep(0.25)
    elapsed = time.monotonic() - t_start
    state = rig.eoc_state()
    keys = [(t["desk_id"], t["seq"]) for t in state["tickets"]]
    shown_dups = len(keys) - len(set(keys))
    return {"equal": bool(result and result["equal"]), "compare": result, "elapsed_s": round(elapsed, 1),
            "converge_after_kills_s": round(time.monotonic() - t_kills_done, 1), "kills": dict(rig.kills),
            "shown_duplicates": shown_dups, "dup_receptions_suppressed": state["stats"].get("duplicates", 0),
            "tickets_desk": result["desk"] if result else None, "tickets_eoc": len(keys), "changes": changes,
            "seqs": len(seqs)}

"""Desk sender (SPEC 8.3): one daemon thread with one non-blocking UDP socket.

Loop, every 20-50 ms (wakes at once when a reply arrives):
1. Receive everything: ACK -> store.mark_acked per entry (safe across boots), RTT sample if
   Karn allows, drop the matching in-flight datagram, link UP, reset the probe backoff.
   HB_REPLY -> store.apply_status, RTT sample, link UP. Bad MAC -> count and drop.
2. Timeouts: an in-flight datagram older than RTO leaves the in-flight table (its units become
   eligible again at their current version); rto.on_timeout(); after `down_after_timeouts`
   consecutive timeouts the link is DOWN.
3. Link DOWN/UNKNOWN: no DATA; HEARTBEAT probes every min(2^k, probe_max_s) x U(0.5, 1) s.
   Probes ignore the P1 reservation; any reply -> UP. So the census arrives first.
4. Link UP: fill the window. Candidates = pending units not in flight, ordered by
   (priority, deadline, created). P1 reservation: while any P1 unit is unacknowledged, only P1
   datagrams may be sent (and only P1 datagrams count against the window), no heartbeats.
   store.mark_sent() is committed BEFORE sendto.
5. Heartbeat every heartbeat_s while UP and no P1 is pending (uses a window slot).
6. Clock jump (from timers): requeue in-flight datagrams and rto.reset().
"""
import argparse
import collections
import random
import select
import sys
import threading
import time

from ..config import hmac_key
from . import codec
from .linkemu import open_udp
from .rto import RTO

LINK_UNKNOWN = "UNKNOWN"
LINK_UP = "UP"
LINK_DOWN = "DOWN"
TYPED_NOMINAL_S = 10.0
LOOP_S = 0.025          # base loop period (20-50 ms)
PENDING_POLL_S = 0.05   # re-read pending units at least this often while UP


class Sender(threading.Thread):
    def __init__(self, store, cfg, beat=None, battery_fn=None, sock=None, dest=None, boot_id=None, seed=None,
                 log_size=50000):
        super().__init__(daemon=True, name="sender")
        net = cfg["net"]
        self.store = store
        self.cfg = cfg
        self.beat = beat
        self.battery_fn = battery_fn
        self.mtu = int(net["mtu"])
        self.window = max(1, int(net["window"]))
        self.heartbeat_s = float(net["heartbeat_s"])
        self.probe_max_s = float(net["probe_max_s"])
        self.down_after = max(1, int(net["down_after_timeouts"]))
        self.rto = RTO.from_config(net)
        self.desk_id = int(cfg["desk_id"])
        self.key = hmac_key(cfg)
        self.keys = {self.desk_id: self.key}
        self.dest = tuple(dest) if dest else ("127.0.0.1", int(cfg["ports"]["emu_udp"]))
        self.voice_bps = float(cfg.get("voice_baseline_bytes_per_s", 2000))
        self.sock = sock
        self._own_sock = sock is None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._rng = random.Random(seed if seed is not None else time.time_ns())
        self.boot_id = store.new_boot() if boot_id is None else int(boot_id) % 256
        self._msg_seq = self._rng.randrange(0, 65536)
        self.link_state = LINK_UNKNOWN
        self.inflight = {}          # msg_seq -> record
        self.probes = {}            # msg_seq -> sent_at (probe heartbeats while DOWN/UNKNOWN)
        self.tries = {}             # (seq, version) -> times packed
        self.consecutive_timeouts = 0
        self.probe_k = 0
        self.next_probe_at = 0.0    # probe at once
        self.last_hb_at = -1e9
        self._reset_probe_flag = False
        self._clock_jump_flag = False
        self._dirty = True
        self._pend = []
        self._pend_at = -1e9
        self.sent_log = collections.deque(maxlen=log_size)   # for tests and experiments
        self.last_ack_wall = None
        self.counters = {"datagrams_up": 0, "bytes_up": 0, "data_bytes_up": 0, "data_datagrams": 0,
                         "bytes_down": 0, "datagrams_down": 0, "retransmissions": 0, "retransmitted_units": 0,
                         "bad_mac": 0, "bad_datagrams": 0, "socket_errors": 0, "acked_units": 0,
                         "timeouts": 0, "probes_sent": 0, "heartbeats_sent": 0, "pack_failed": 0,
                         "clock_jumps": 0, "errors": 0}
        self._tp_bytes = 0
        self._tp_t = time.monotonic()
        self._tp_ewma = 0.0
        self._voice = {"bytes": 0, "tickets": 0, "typed": 0}
        self._voice_t = -1e9
        self._last_err = {}
        self._snap_t = -1e9
        self.last_loop_mono = time.monotonic()
        r = self.rto.stats()
        self._snapshot = {   # full key set from the start, so callers never see a partial dict
            "link_state": self.link_state, "srtt": None, "rto": r["rto"], "rto_base": r["rto_base"],
            "max_rto_base": r["max_rto_base"], "throughput_Bps": 0.0, "data_bytes_up": 0, "bytes_up": 0,
            "bytes_down": 0, "datagrams_up": 0, "datagrams_down": 0, "retransmissions": 0,
            "retransmitted_units": 0, "bad_mac": 0, "socket_errors": 0, "acked_units": 0, "last_ack_wall": None,
            "backlog_bytes": 0, "voice_equiv_bytes": 0, "voice_equiv_tickets": 0, "voice_equiv_typed": 0,
            "typed_nominal_s": TYPED_NOMINAL_S, "voice_baseline_bytes_per_s": self.voice_bps,
            "boot_id": self.boot_id, "inflight": 0, "pending": 0, "pending_p1": 0, "probes_sent": 0,
            "heartbeats_sent": 0, "timeouts": 0, "consecutive_timeouts": 0, "probe_k": 0, "data_datagrams": 0,
            "pack_failed": 0, "clock_jumps": 0, "errors": 0, "window": self.window, "mtu": self.mtu,
        }

    # ------------------------------------------------------------ control (any thread)
    def start(self):
        if self.sock is None:
            self.sock = open_udp("127.0.0.1", 0)
        super().start()
        return self

    def stop(self, join=True):
        self._stop_evt.set()
        if join and self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=2.0)
        if self._own_sock and self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass

    def reset_probe(self):
        """Called by /api/link/profile: k = 0 and probe at once."""
        self._reset_probe_flag = True

    def on_clock_jump(self):
        """Called by timers on a wall-clock jump: requeue in-flight datagrams, rto.reset()."""
        self._clock_jump_flag = True

    def stats(self):
        with self._lock:
            return dict(self._snapshot)

    # ------------------------------------------------------------ helpers
    def _next_msg_seq(self):
        self._msg_seq = (self._msg_seq + 1) & 0xFFFF
        return self._msg_seq

    def _error(self, kind, exc):
        self.counters["errors"] += 1
        now = time.monotonic()
        if now - self._last_err.get(kind, -1e9) >= 60.0:
            self._last_err[kind] = now
            try:
                self.store.log_event("sender_error", {"kind": kind, "error": repr(exc)})
            except Exception:
                pass
            print(f"sender_error [{kind}]: {exc!r}", file=sys.stderr, flush=True)

    def _event(self, etype, data=None):
        try:
            self.store.log_event(etype, data)
        except Exception as e:
            self._error("event", e)

    def _sendto(self, data):
        try:
            self.sock.sendto(data, self.dest)
            return True
        except OSError as e:
            self.counters["socket_errors"] += 1
            self._error("send", e)
            return False

    def _set_link(self, state, now):
        if state != self.link_state:
            old = self.link_state
            self.link_state = state
            self._event("link_" + state.lower(), {"from": old, "boot_id": self.boot_id})
            if state == LINK_DOWN:
                self.next_probe_at = now
            self._dirty = True

    def _on_reply(self, now):
        self.consecutive_timeouts = 0
        self.probe_k = 0
        self._set_link(LINK_UP, now)

    # ------------------------------------------------------------ loop
    def run(self):
        while not self._stop_evt.is_set():
            try:
                self.last_loop_mono = time.monotonic()
                if self.beat:
                    self.beat()
                self.iteration()
            except Exception as e:  # never die from one bad input
                self._error(type(e).__name__, e)
            self._wait(LOOP_S)

    def _wait(self, timeout):
        try:
            select.select([self.sock], [], [], timeout)
        except (OSError, ValueError, TypeError, AttributeError):
            self._stop_evt.wait(timeout)

    def iteration(self):
        now = time.monotonic()
        if self._clock_jump_flag:
            self._clock_jump_flag = False
            self.inflight.clear()
            self.rto.reset()
            self.counters["clock_jumps"] += 1
            self._dirty = True
            self._event("clock_jump_requeue", {"boot_id": self.boot_id})
        if self._reset_probe_flag:
            self._reset_probe_flag = False
            self.probe_k = 0
            self.next_probe_at = now
        self._receive()
        now = time.monotonic()
        self._check_timeouts(now)
        if self.link_state != LINK_UP:
            if now >= self.next_probe_at:
                self._send_heartbeat(now, probe=True)
                interval = min(2.0 ** self.probe_k, self.probe_max_s) * self._rng.uniform(0.5, 1.0)
                self.probe_k += 1
                self.next_probe_at = now + interval
        else:
            self._fill_window(now)
        self._update_stats(now)

    def _receive(self):
        resets = 0
        for _ in range(512):
            try:
                data, _addr = self.sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionResetError as e:  # Windows ICMP echo: the socket still works
                self.counters["socket_errors"] += 1
                self._error("recv", e)
                resets += 1
                if resets > 16:
                    return
                continue
            except OSError as e:
                self.counters["socket_errors"] += 1
                self._error("recv", e)
                return
            now = time.monotonic()
            self.counters["bytes_down"] += len(data)
            self.counters["datagrams_down"] += 1
            try:
                msg = codec.unpack(data, self.keys)
            except codec.BadMac:
                self.counters["bad_mac"] += 1
                continue
            except codec.CodecError:
                self.counters["bad_datagrams"] += 1
                continue
            if msg.desk_id != self.desk_id:
                continue
            if msg.type == codec.ACK:
                entries = [(r["seq"], r["version"], r["level"], r["eoc_status"]) for r in msg.records]
                if entries:
                    self.store.mark_acked_many(entries)
                    self.counters["acked_units"] += len(entries)
                    self.last_ack_wall = time.time()
                rec = self.inflight.get(msg.msg_seq) if msg.boot_id == self.boot_id else None
                if rec is not None and rec["kind"] == "data":
                    del self.inflight[msg.msg_seq]
                    self.rto.sample_if(now - rec["sent_at"], rec["first_try"], msg.boot_id, self.boot_id)
                    self._tp_bytes += rec["bytes"]
                    rec["acked_at"] = now
                self._dirty = True
                self._on_reply(now)
            elif msg.type == codec.HB_REPLY:
                entries = [(r["seq"], r["status"], r["status_seq"]) for r in msg.records]
                if entries:
                    self.store.apply_status_many(entries)
                if msg.boot_id == self.boot_id:
                    rec = self.inflight.get(msg.msg_seq)
                    if rec is not None and rec["kind"] == "hb":
                        del self.inflight[msg.msg_seq]
                        self.rto.sample_if(now - rec["sent_at"], True, msg.boot_id, self.boot_id)
                    elif msg.msg_seq in self.probes:
                        sent = self.probes.pop(msg.msg_seq)
                        self.rto.sample_if(now - sent, True, msg.boot_id, self.boot_id)
                self._dirty = True
                self._on_reply(now)

    def _check_timeouts(self, now):
        # Each datagram's timer is armed with the RTO current when it was sent, so one
        # timeout's backoff never stretches the timers of datagrams already in flight.
        for ms, rec in list(self.inflight.items()):
            if now - rec["sent_at"] > rec["rto"]:
                del self.inflight[ms]
                self.rto.on_timeout()
                self.consecutive_timeouts += 1
                self.counters["timeouts"] += 1
                self._dirty = True
                if self.consecutive_timeouts >= self.down_after and self.link_state != LINK_DOWN:
                    self._set_link(LINK_DOWN, now)
        if self.probes:
            for ms, sent in list(self.probes.items()):
                if now - sent > 120.0:
                    del self.probes[ms]

    def _pending(self, now):
        if self._dirty or now - self._pend_at >= PENDING_POLL_S:
            self._pend = self.store.pending_units()
            self._pend_at = now
            self._dirty = False
        return self._pend

    def _fill_window(self, now):
        pend = self._pending(now)
        in_flight_seqs = set()
        for rec in self.inflight.values():
            for u in rec["units"]:
                in_flight_seqs.add(u[0])
        p1_pending = any(v["priority"] == 1 for v in pend)
        if p1_pending:
            used = sum(1 for rec in self.inflight.values() if rec["has_p1"])
        else:
            used = len(self.inflight)
        cands = [v for v in pend if v["seq"] not in in_flight_seqs]
        if p1_pending:
            cands = [v for v in cands if v["priority"] == 1]
        while used < self.window and cands:
            head = cands[0]
            try:
                fits = codec.summary_record_size(head) + codec.OVERHEAD <= self.mtu
                codec.encode_summary_record(head)
            except Exception:
                fits = False
            if not fits:
                self.store.mark_pack_failed(head["seq"], head["version"])
                self.counters["pack_failed"] += 1
                cands.pop(0)
                self._dirty = True
                continue
            msg_seq = self._next_msg_seq()
            data, n = codec.pack_summary(cands, self.mtu, self.key, self.desk_id, self.boot_id, msg_seq)
            if n == 0:
                self.store.mark_pack_failed(head["seq"], head["version"])
                self.counters["pack_failed"] += 1
                cands.pop(0)
                continue
            units, cands = cands[:n], cands[n:]
            first_try = True
            retried = 0
            for u in units:
                k = (u["seq"], u["version"])
                c = self.tries.get(k, 0) + 1
                self.tries[k] = c
                if c > 1:
                    first_try = False
                    retried += 1
            self.store.mark_sent([u["seq"] for u in units])   # committed BEFORE sendto
            sent_at = time.monotonic()
            self._sendto(data)
            has_p1 = any(u["priority"] == 1 for u in units)
            self.inflight[msg_seq] = {"kind": "data", "units": [(u["seq"], u["version"], u["priority"]) for u in units],
                                      "sent_at": sent_at, "rto": self.rto.rto, "bytes": len(data),
                                      "first_try": first_try, "has_p1": has_p1}
            self.counters["datagrams_up"] += 1
            self.counters["data_datagrams"] += 1
            self.counters["bytes_up"] += len(data)
            self.counters["data_bytes_up"] += len(data)
            if not first_try:
                self.counters["retransmissions"] += 1
                self.counters["retransmitted_units"] += retried
            self.sent_log.append({"mono": sent_at, "wall": time.time(), "kind": "data", "msg_seq": msg_seq,
                                  "units": [(u["seq"], u["version"], u["priority"]) for u in units],
                                  "bytes": len(data), "first_try": first_try, "p1_reserved": p1_pending})
            used += 1
        if (not p1_pending and used < self.window and now - self.last_hb_at >= self.heartbeat_s):
            self._send_heartbeat(now, probe=False)

    def _census(self):
        census = dict(self.store.census())
        bat = None
        if self.battery_fn is not None:
            try:
                bat = self.battery_fn()
            except Exception:
                bat = None
        census["battery"] = 255 if bat is None else max(0, min(100, int(round(float(bat)))))
        return census

    def _send_heartbeat(self, now, probe):
        msg_seq = self._next_msg_seq()
        data = codec.pack_heartbeat(self._census(), self.key, self.desk_id, self.boot_id, msg_seq)
        sent_at = time.monotonic()
        self._sendto(data)
        if probe:
            self.probes[msg_seq] = sent_at
            self.counters["probes_sent"] += 1
        else:
            self.inflight[msg_seq] = {"kind": "hb", "units": [], "sent_at": sent_at, "rto": self.rto.rto,
                                      "bytes": len(data), "first_try": True, "has_p1": False}
            self.counters["heartbeats_sent"] += 1
        self.last_hb_at = now
        self.counters["datagrams_up"] += 1
        self.counters["bytes_up"] += len(data)
        self.sent_log.append({"mono": sent_at, "wall": time.time(), "kind": "probe" if probe else "hb",
                              "msg_seq": msg_seq, "units": [], "bytes": len(data), "first_try": True,
                              "p1_reserved": False})

    # ------------------------------------------------------------ stats
    def _update_stats(self, now):
        if now - self._snap_t < 0.2:
            return
        self._snap_t = now
        dt = now - self._tp_t
        if dt >= 1.0:
            inst = self._tp_bytes / dt
            self._tp_ewma = inst if self._tp_ewma == 0.0 else 0.7 * self._tp_ewma + 0.3 * inst
            self._tp_bytes = 0
            self._tp_t = now
        if now - self._voice_t >= 1.0:
            self._voice_t = now
            try:
                rows = self.store.query("SELECT audio_seconds FROM tickets WHERE delivered_wall IS NOT NULL")
                total, typed = 0.0, 0
                for r in rows:
                    a = float(r.get("audio_seconds") or 0.0)
                    if a <= 0:
                        a = TYPED_NOMINAL_S
                        typed += 1
                    total += a * self.voice_bps
                self._voice = {"bytes": int(total), "tickets": len(rows), "typed": typed}
            except Exception as e:
                self._error("voice_stats", e)
        pend = self._pend if self.link_state == LINK_UP else None
        if pend is None:
            try:
                pend = self.store.pending_units()
            except Exception as e:
                self._error("pending_stats", e)
                pend = []
        in_flight_seqs = {u[0] for rec in self.inflight.values() for u in rec["units"]}
        backlog = sum(codec.summary_record_size(v) for v in pend if v["seq"] not in in_flight_seqs)
        r = self.rto.stats()
        snap = {
            "link_state": self.link_state, "srtt": r["srtt"], "rto": r["rto"], "rto_base": r["rto_base"],
            "max_rto_base": r["max_rto_base"], "throughput_Bps": round(self._tp_ewma, 1),
            "data_bytes_up": self.counters["data_bytes_up"], "bytes_up": self.counters["bytes_up"],
            "bytes_down": self.counters["bytes_down"], "datagrams_up": self.counters["datagrams_up"],
            "datagrams_down": self.counters["datagrams_down"],
            "retransmissions": self.counters["retransmissions"],
            "retransmitted_units": self.counters["retransmitted_units"],
            "bad_mac": self.counters["bad_mac"], "socket_errors": self.counters["socket_errors"],
            "acked_units": self.counters["acked_units"], "last_ack_wall": self.last_ack_wall,
            "backlog_bytes": backlog, "voice_equiv_bytes": self._voice["bytes"],
            "voice_equiv_tickets": self._voice["tickets"], "voice_equiv_typed": self._voice["typed"],
            "typed_nominal_s": TYPED_NOMINAL_S, "voice_baseline_bytes_per_s": self.voice_bps,
            "boot_id": self.boot_id, "inflight": len(self.inflight), "pending": len(pend),
            "pending_p1": sum(1 for v in pend if v["priority"] == 1),
            "probes_sent": self.counters["probes_sent"], "heartbeats_sent": self.counters["heartbeats_sent"],
            "timeouts": self.counters["timeouts"], "consecutive_timeouts": self.consecutive_timeouts,
            "probe_k": self.probe_k, "data_datagrams": self.counters["data_datagrams"],
            "pack_failed": self.counters["pack_failed"], "clock_jumps": self.counters["clock_jumps"],
            "errors": self.counters["errors"], "window": self.window, "mtu": self.mtu,
        }
        with self._lock:
            self._snapshot = snap


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m darkshelter.net.sender", description="DarkShelter desk sender")
    ap.add_argument("--db", default=None, help="desk SQLite path (default: paths.desk_db from the config)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--stats-every", type=float, default=0.0, help="print stats every N seconds (0 = never)")
    args = ap.parse_args(argv)
    from ..config import db_path, load_config
    from ..store import Store
    cfg = load_config(args.config)
    db = args.db or str(db_path(cfg, "desk_db"))
    store = Store(db, desk_letter=cfg.get("desk_letter", "B"))
    sender = Sender(store, cfg)
    sender.start()
    print(f"sender ready: desk {cfg['desk_id']} boot {sender.boot_id} -> udp {sender.dest[0]}:{sender.dest[1]}",
          flush=True)
    last = time.monotonic()
    try:
        while sender.is_alive():
            sender.join(0.5)
            if args.stats_every and time.monotonic() - last >= args.stats_every:
                last = time.monotonic()
                s = sender.stats()
                print(f"link={s.get('link_state')} pending={s.get('pending')} inflight={s.get('inflight')} "
                      f"acked={s.get('acked_units')} rto={s.get('rto')}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sender.stop()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

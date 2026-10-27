"""Link emulator (SPEC 8.5): a UDP relay between the desk and the EOC.

Per direction, in this order:
  outage -> drop (never buffer)
  random loss p from a seeded random.Random
  MTU check (drop oversize datagrams)
  token bucket at rate_bps (bucket = 1 x MTU) with a FIFO of at most 4 datagrams, tail-drop
  delay +- jitter
The bucket is evaluated in virtual time, so late wake-ups never cost throughput. One heap
holds every scheduled delivery; the loop waits in select() until the next event and never
sleeps in the forwarding path. Switching profile clears both FIFOs (entering DOWN also drops
datagrams in the air).

HTTP control (stdlib, threaded): GET /healthz, GET /profile, GET /profile?name=X,
POST /custom {rate_bps, loss, delay_ms, jitter_ms, mtu, seed}, GET /stats. Starts DOWN.
In-process: LinkEmulator(listen_port, eoc_addr, control_port, seed).start() / .stop().
"""
import argparse
import collections
import heapq
import itertools
import json
import os
import random
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROFILES = {
    "DOWN": None,
    "LORA_RELAY": {"rate_bps": 1200, "loss": 0.10, "delay_ms": 400, "jitter_ms": 200, "mtu": 200},
    "LTE_ONE_BAR": {"rate_bps": 16000, "loss": 0.25, "delay_ms": 300, "jitter_ms": 300, "mtu": 1200},
    "SATELLITE": {"rate_bps": 2400, "loss": 0.05, "delay_ms": 800, "jitter_ms": 100, "mtu": 340},
    "GOOD": {"rate_bps": 1000000, "loss": 0.0, "delay_ms": 20, "jitter_ms": 5, "mtu": 1400},
}
FLAPPING = {"up": "LTE_ONE_BAR", "up_s": 10.0, "down_s": 20.0}
PROFILE_NAMES = ("DOWN", "LORA_RELAY", "LTE_ONE_BAR", "SATELLITE", "GOOD", "FLAPPING")
FIFO_MAX = 4
UP = "up"
DOWN = "down"
DROP_REASONS = ("outage", "loss", "mtu", "queue", "switch", "no_desk")


SIO_UDP_CONNRESET = 0x9800000C   # _WSAIOW(IOC_VENDOR, 12)


def disable_udp_connreset(s):
    """Stop Windows from turning an ICMP 'port unreachable' into WSAECONNRESET on recvfrom.

    CPython has no socket.SIO_UDP_CONNRESET and socket.ioctl() rejects the raw code, so the
    documented `if hasattr(socket, "SIO_UDP_CONNRESET")` guard alone never fires; fall back to
    WSAIoctl through ctypes. Every loop also survives the error, so this is belt and braces.
    """
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            s.ioctl(socket.SIO_UDP_CONNRESET, False)
            return True
        except (OSError, ValueError, AttributeError):
            pass
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        ws2 = ctypes.WinDLL("ws2_32")
        fn = ws2.WSAIoctl
        fn.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
        fn.restype = ctypes.c_int
        flag = wintypes.BOOL(False)
        returned = wintypes.DWORD(0)
        rc = fn(s.fileno(), SIO_UDP_CONNRESET, ctypes.byref(flag), ctypes.sizeof(flag), None, 0,
                ctypes.byref(returned), None, None)
        return rc == 0
    except Exception:
        return False


def open_udp(host="127.0.0.1", port=0, blocking=False):
    """UDP socket bound to (host, port); Windows ICMP 'connection reset' disabled.

    SO_REUSEADDR only on POSIX, never on Windows (it would let two processes share a port).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    disable_udp_connreset(s)
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, int(port)))
    s.setblocking(blocking)
    return s


def _params(rate_bps, loss, delay_ms, jitter_ms, mtu):
    return {"rate_bps": max(1.0, float(rate_bps)), "loss": min(1.0, max(0.0, float(loss))),
            "delay_ms": max(0.0, float(delay_ms)), "jitter_ms": max(0.0, float(jitter_ms)),
            "mtu": max(1, int(mtu))}


class _Direction:
    def __init__(self, name, seed):
        self.name = name
        self.rng = random.Random(seed)
        self.params = None
        self.last_dep = 0.0       # virtual time of the last scheduled departure
        self.tokens = 0.0         # tokens right after that departure
        self.waiting = collections.deque()   # departure times still in the future (FIFO)
        self.rx_datagrams = self.rx_bytes = self.tx_datagrams = self.tx_bytes = 0
        self.drops = {r: 0 for r in DROP_REASONS}

    def configure(self, params, now):
        self.params = params
        self.waiting.clear()
        self.last_dep = now
        self.tokens = float(params["mtu"]) if params else 0.0

    def admit(self, size, now):
        """Decide one datagram -> (deliver_at, depart_at) or (None, reason)."""
        p = self.params
        if p is None:
            return None, "outage"
        if p["loss"] > 0.0 and self.rng.random() < p["loss"]:
            return None, "loss"
        if size > p["mtu"]:
            return None, "mtu"
        while self.waiting and self.waiting[0] <= now:
            self.waiting.popleft()
        rate = p["rate_bps"] / 8.0
        cap = float(p["mtu"])
        start = max(now, self.last_dep)
        tokens = min(cap, self.tokens + rate * (start - self.last_dep))
        if tokens >= size:
            depart, after = start, tokens - size
        else:
            depart, after = start + (size - tokens) / rate, 0.0
        if depart > now and len(self.waiting) >= FIFO_MAX:
            return None, "queue"
        self.last_dep, self.tokens = depart, after
        if depart > now:
            self.waiting.append(depart)
        delay = p["delay_ms"] / 1000.0
        jit = p["jitter_ms"] / 1000.0
        if jit > 0.0:
            delay += self.rng.uniform(-jit, jit)
        return depart + max(0.0, delay), depart

    def stats(self):
        return {"rx_datagrams": self.rx_datagrams, "rx_bytes": self.rx_bytes,
                "tx_datagrams": self.tx_datagrams, "tx_bytes": self.tx_bytes,
                "drops": dict(self.drops), "fifo": len(self.waiting)}


class LinkEmulator:
    def __init__(self, listen_port, eoc_addr, control_port=None, seed=0, profile="DOWN", host="127.0.0.1"):
        self.host = host
        self.listen_port = int(listen_port)
        if isinstance(eoc_addr, (tuple, list)):
            self.eoc_addr = (str(eoc_addr[0]), int(eoc_addr[1]))
        else:
            self.eoc_addr = ("127.0.0.1", int(eoc_addr))
        self.control_port = control_port
        self.seed = int(seed)
        self._initial_profile = profile
        self._lock = threading.RLock()
        self._heap = []
        self._counter = itertools.count()
        self.dirs = {UP: _Direction(UP, self.seed * 2 + 1), DOWN: _Direction(DOWN, self.seed * 2 + 2)}
        self.desk_addr = None
        self.profile_name = "DOWN"
        self.params = None
        self.flap = None
        self.phase = None
        self.switches = 0
        self.errors = 0
        self._last_err = {}
        self._stop = threading.Event()
        self._thread = None
        self._http = None
        self._http_thread = None
        self.sock_desk = None
        self.sock_eoc = None
        self.port = None
        self.control_bound = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.sock_desk = open_udp(self.host, self.listen_port)
        self.sock_eoc = open_udp(self.host, 0)
        self.port = self.sock_desk.getsockname()[1]
        self.set_profile(self._initial_profile or "DOWN")
        self._thread = threading.Thread(target=self._run, name="linkemu", daemon=True)
        self._thread.start()
        if self.control_port is not None:
            handler = type("EmuHandler", (_Handler,), {"emu": self})
            self._http = _QuietServer((self.host, int(self.control_port)), handler)
            self.control_bound = self._http.server_address[1]
            self._http_thread = threading.Thread(target=self._http.serve_forever, kwargs={"poll_interval": 0.2},
                                                 name="linkemu-http", daemon=True)
            self._http_thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for s in (self.sock_desk, self.sock_eoc):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ profiles
    def _switch(self, params, now):
        """Apply params to both directions; clear FIFOs (and the air when going DOWN)."""
        keep = []
        for entry in self._heap:
            deliver_at, _, direction, _data, _dest, depart = entry
            if params is None or depart > now:
                self.dirs[direction].drops["switch"] += 1
            else:
                keep.append(entry)
        heapq.heapify(keep)
        self._heap = keep
        for d in self.dirs.values():
            d.configure(params, now)
        self.params = params
        self.switches += 1

    def set_profile(self, name):
        name = str(name or "").strip().upper()
        if name not in PROFILE_NAMES:
            raise ValueError(f"unknown profile {name!r}; use one of {', '.join(PROFILE_NAMES)}")
        now = time.monotonic()
        with self._lock:
            self.profile_name = name
            if name == "FLAPPING":
                self.flap = {"up_params": dict(PROFILES[FLAPPING["up"]]), "up_name": FLAPPING["up"],
                             "up_s": FLAPPING["up_s"], "down_s": FLAPPING["down_s"], "t0": now}
                self.phase = "up"
                self._switch(dict(self.flap["up_params"]), now)
            else:
                self.flap = None
                self.phase = None
                p = PROFILES[name]
                self._switch(dict(p) if p else None, now)
        return self.profile_info()

    def set_custom(self, rate_bps, loss, delay_ms, jitter_ms, mtu, seed=None, flap_up_s=None, flap_down_s=None):
        params = _params(rate_bps, loss, delay_ms, jitter_ms, mtu)
        now = time.monotonic()
        with self._lock:
            if seed is not None:
                self.seed = int(seed)
                self.dirs[UP].rng.seed(self.seed * 2 + 1)
                self.dirs[DOWN].rng.seed(self.seed * 2 + 2)
            self.profile_name = "CUSTOM"
            if flap_up_s and flap_down_s:
                self.flap = {"up_params": dict(params), "up_name": "CUSTOM", "up_s": float(flap_up_s),
                             "down_s": float(flap_down_s), "t0": now}
                self.phase = "up"
            else:
                self.flap = None
                self.phase = None
            self._switch(dict(params), now)
        return self.profile_info()

    def _flap_tick(self, now):
        f = self.flap
        if not f:
            return
        period = f["up_s"] + f["down_s"]
        pos = (now - f["t0"]) % period
        want = "up" if pos < f["up_s"] else "down"
        if want != self.phase:
            self.phase = want
            self._switch(dict(f["up_params"]) if want == "up" else None, now)

    def _next_flap_boundary(self, now):
        f = self.flap
        if not f:
            return None
        period = f["up_s"] + f["down_s"]
        k = (now - f["t0"]) // period
        base = f["t0"] + k * period
        b1 = base + f["up_s"]
        return b1 if b1 > now else base + period

    def profile_info(self):
        with self._lock:
            return {"name": self.profile_name, "phase": self.phase,
                    "params": dict(self.params) if self.params else None,
                    "flap": ({k: v for k, v in self.flap.items() if k != "t0"} if self.flap else None),
                    "profiles": list(PROFILE_NAMES)}

    # ------------------------------------------------------------ forwarding
    def _err(self, kind, exc):
        self.errors += 1
        now = time.monotonic()
        if now - self._last_err.get(kind, -1e9) >= 60.0:
            self._last_err[kind] = now
            print(f"linkemu_error [{kind}]: {exc!r}", file=sys.stderr, flush=True)

    def _run(self):
        socks = [self.sock_desk, self.sock_eoc]
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                with self._lock:
                    self._flap_tick(now)
                    timeout = 0.05
                    if self._heap:
                        timeout = min(timeout, max(0.0, self._heap[0][0] - now))
                    nb = self._next_flap_boundary(now)
                    if nb is not None:
                        timeout = min(timeout, max(0.0, nb - now))
                try:
                    ready, _, _ = select.select(socks, [], [], timeout)
                except (OSError, ValueError):
                    if self._stop.is_set():
                        break
                    ready = []
                for s in ready:
                    self._drain(s)
                self._deliver_due(time.monotonic())
            except Exception as e:  # never die from one bad input
                self._err(type(e).__name__, e)

    def _drain(self, s):
        resets = 0
        for _ in range(512):
            try:
                data, addr = s.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionResetError as e:   # Windows ICMP echo of an earlier send: keep reading
                self._err("recv_reset", e)
                resets += 1
                if resets > 16:
                    return
                continue
            except OSError as e:
                self._err("recv", e)
                return
            now = time.monotonic()
            with self._lock:
                if s is self.sock_desk:
                    self.desk_addr = addr
                    self._admit(UP, data, self.eoc_addr, now)
                else:
                    self._admit(DOWN, data, self.desk_addr, now)

    def _admit(self, direction, data, dest, now):
        d = self.dirs[direction]
        d.rx_datagrams += 1
        d.rx_bytes += len(data)
        if dest is None:
            d.drops["no_desk"] += 1
            return
        deliver_at, depart = d.admit(len(data), now)
        if deliver_at is None:
            d.drops[depart] += 1
            return
        heapq.heappush(self._heap, (deliver_at, next(self._counter), direction, data, dest, depart))

    def _deliver_due(self, now):
        while True:
            with self._lock:
                if not self._heap or self._heap[0][0] > now:
                    return
                _, _, direction, data, dest, _ = heapq.heappop(self._heap)
                d = self.dirs[direction]
            sock = self.sock_eoc if direction == UP else self.sock_desk
            try:
                sock.sendto(data, dest)
                with self._lock:
                    d.tx_datagrams += 1
                    d.tx_bytes += len(data)
            except OSError as e:
                self._err("send", e)

    def stats(self):
        with self._lock:
            return {"profile": self.profile_name, "phase": self.phase,
                    "params": dict(self.params) if self.params else None,
                    "desk_addr": list(self.desk_addr) if self.desk_addr else None,
                    "up": self.dirs[UP].stats(), "down": self.dirs[DOWN].stats(),
                    "scheduled": len(self._heap), "switches": self.switches, "errors": self.errors}


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"   # never SO_REUSEADDR on Windows


class _Handler(BaseHTTPRequestHandler):
    emu = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(min(n, 65536))
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/healthz":
                return self._json(200, {"ok": True, "profile": self.emu.profile_name})
            if u.path == "/profile":
                if "name" in q:
                    try:
                        return self._json(200, self.emu.set_profile(q["name"][0]))
                    except ValueError as e:
                        return self._json(400, {"error": "bad_profile", "zh": "不明的連線設定", "en": str(e)})
                return self._json(200, self.emu.profile_info())
            if u.path == "/stats":
                return self._json(200, self.emu.stats())
            return self._json(404, {"error": "not_found", "zh": "找不到", "en": "not found"})
        except Exception as e:
            return self._json(500, {"error": "server_error", "zh": "伺服器錯誤", "en": repr(e)})

    def do_POST(self):
        try:
            u = urlparse(self.path)
            body = self._body()
            if u.path == "/custom":
                allowed = ("rate_bps", "loss", "delay_ms", "jitter_ms", "mtu", "seed", "flap_up_s", "flap_down_s")
                args = {k: body[k] for k in allowed if k in body}
                for k in ("rate_bps", "loss", "delay_ms", "jitter_ms", "mtu"):
                    if k not in args:
                        return self._json(400, {"error": "missing", "zh": f"缺少 {k}", "en": f"missing {k}"})
                return self._json(200, self.emu.set_custom(**args))
            if u.path == "/profile":
                try:
                    return self._json(200, self.emu.set_profile(body.get("name")))
                except ValueError as e:
                    return self._json(400, {"error": "bad_profile", "zh": "不明的連線設定", "en": str(e)})
            return self._json(404, {"error": "not_found", "zh": "找不到", "en": "not found"})
        except (ValueError, TypeError) as e:
            return self._json(400, {"error": "bad_request", "zh": "請求格式錯誤", "en": repr(e)})
        except Exception as e:
            return self._json(500, {"error": "server_error", "zh": "伺服器錯誤", "en": repr(e)})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m darkshelter.net.linkemu", description="DarkShelter link emulator")
    ap.add_argument("--config", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--profile", default="DOWN")
    args = ap.parse_args(argv)
    from ..config import load_config
    cfg = load_config(args.config)
    ports = cfg["ports"]
    emu = LinkEmulator(ports["emu_udp"], ("127.0.0.1", ports["eoc_udp"]), control_port=ports["emu_control_http"],
                       seed=args.seed, profile=args.profile)
    try:
        emu.start()
    except OSError as e:
        print(f"linkemu: cannot bind UDP {ports['emu_udp']} / HTTP {ports['emu_control_http']}: {e}",
              file=sys.stderr, flush=True)
        return 2
    print(f"linkemu ready: udp 127.0.0.1:{emu.port} -> eoc 127.0.0.1:{ports['eoc_udp']}, "
          f"control http://127.0.0.1:{emu.control_bound} (profile {emu.profile_name})", flush=True)
    try:
        while emu.is_alive():
            emu._thread.join(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        emu.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

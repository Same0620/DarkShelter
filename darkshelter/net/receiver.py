"""EOC receiver (SPEC 8.4): verify, store (COMMIT), then ack every record.

DATA: apply every record in one transaction, COMMIT, THEN ack every record (duplicates and
older versions included) to the source address, echoing boot_id and msg_seq.
HEARTBEAT: update the census and last_seen (commit), then reply HB_REPLY with that desk's
status changes where status_seq > status_seq_applied, in order, as many as fit.
The loop body runs under try/except Exception; the Windows UDP reset error is disabled.
"""
import select
import sys
import threading
import time

from . import codec
from .linkemu import open_udp


class Receiver(threading.Thread):
    def __init__(self, store, keys, port, host="127.0.0.1", mtu=200, sock=None, beat=None):
        super().__init__(daemon=True, name="receiver")
        self.store = store
        self.keys = dict(keys)
        self.host = host
        self.port = int(port)
        self.mtu = int(mtu or 200)
        self.sock = sock
        self.beat = beat
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._last_err = {}
        self.counters = {"datagrams_rx": 0, "bytes_rx": 0, "bad_mac": 0, "bad_version": 0, "truncated": 0,
                         "socket_errors": 0, "acks_tx": 0, "hb_replies_tx": 0, "bytes_tx": 0, "errors": 0}
        self.last_beat = time.monotonic()

    def start(self):
        if self.sock is None:
            self.sock = open_udp(self.host, self.port)
        try:
            self.port = self.sock.getsockname()[1]
        except Exception:
            pass
        super().start()
        return self

    def stop(self, join=True):
        self._stop_evt.set()
        if join and self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=2.0)
        try:
            self.sock.close()
        except Exception:
            pass

    def stats(self):
        with self._lock:
            return dict(self.counters)

    def _count(self, key, n=1):
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def _error(self, kind, exc):
        self._count("errors")
        now = time.monotonic()
        if now - self._last_err.get(kind, -1e9) >= 60.0:
            self._last_err[kind] = now
            try:
                self.store.log_event("receiver_error", {"kind": kind, "error": repr(exc)})
            except Exception:
                pass
            print(f"receiver_error [{kind}]: {exc!r}", file=sys.stderr, flush=True)

    # ------------------------------------------------------------ loop
    def run(self):
        while not self._stop_evt.is_set():
            try:
                self.last_beat = time.monotonic()
                if self.beat:
                    self.beat()
                self._wait(0.2)
                self._drain()
            except Exception as e:  # never die from one bad input
                self._error(type(e).__name__, e)
                self._stop_evt.wait(0.05)

    def _wait(self, timeout):
        try:
            select.select([self.sock], [], [], timeout)
        except (OSError, ValueError, TypeError):
            self._stop_evt.wait(timeout)

    def _drain(self):
        resets = 0
        for _ in range(256):
            try:
                data, addr = self.sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionResetError as e:  # Windows ICMP echo: the socket still works
                self._count("socket_errors")
                self._error("recv", e)
                resets += 1
                if resets > 16:
                    return
                continue
            except OSError as e:
                self._count("socket_errors")
                self._error("recv", e)
                return
            try:
                self.handle(data, addr)
            except Exception as e:
                self._error(type(e).__name__, e)

    def _send(self, data, addr, counter):
        try:
            self.sock.sendto(data, addr)
            self._count(counter)
            self._count("bytes_tx", len(data))
        except OSError as e:
            self._count("socket_errors")
            self._error("send", e)

    def handle(self, data, addr):
        self._count("datagrams_rx")
        self._count("bytes_rx", len(data))
        try:
            msg = codec.unpack(data, self.keys)
        except codec.BadMac:
            self._count("bad_mac")
            self.store.bump(bad_mac=1, bytes_rx=len(data), bad_datagrams=1)
            return
        except codec.BadVersion:
            self._count("bad_version")
            self.store.bump(bad_datagrams=1, bytes_rx=len(data))
            return
        except codec.Truncated:
            self._count("truncated")
            self.store.bump(bad_datagrams=1, bytes_rx=len(data))
            return
        key = self.keys[msg.desk_id]
        if msg.type in (codec.DATA_SUMMARY, codec.DATA_DIGEST):
            acks = self.store.apply_records(msg.desk_id, msg.boot_id, msg.msg_seq, msg.records, nbytes=len(data))
            reply = codec.pack_ack(acks, key, msg.desk_id, msg.boot_id, msg.msg_seq)
            self._send(reply, addr, "acks_tx")
        elif msg.type == codec.HEARTBEAT:
            self.store.apply_heartbeat(msg.desk_id, msg.boot_id, msg.msg_seq, msg.census, nbytes=len(data))
            limit = max(0, (self.mtu - codec.OVERHEAD) // codec.HB_ENTRY.size)
            changes = self.store.status_changes(msg.desk_id, msg.census.get("status_seq_applied", 0), limit=limit)
            reply, _ = codec.pack_hb_reply(changes, key, msg.desk_id, msg.boot_id, msg.msg_seq, self.mtu)
            self._send(reply, addr, "hb_replies_tx")
        # ACK / HB_REPLY never travel towards the EOC: ignore.

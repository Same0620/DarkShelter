"""EOC store (SQLite, own database): one row per (desk, ticket) at its newest version.

Split ownership: the desk owns content, the EOC owns `status`.
DATA (one transaction per datagram, committed BEFORE the ack is sent):
  - a newer version replaces the content and keeps the EOC-owned status
    (a newer version at level 1 sets summary = NULL and level = 1)
  - an equal version at a higher level adds the summary
  - otherwise dup_count += 1
Status from the EOC UI: 1 ACKNOWLEDGED, 2 DISPATCHED, 3 RESOLVED, with
status_seq = 1 + COALESCE(MAX(status_seq), 0) for that desk, inside the same transaction.
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS eoc_tickets (
  desk_id INTEGER NOT NULL, seq INTEGER NOT NULL, version INTEGER, level INTEGER,
  priority INTEGER, category INTEGER, item_code INTEGER, quantity INTEGER, subject_group INTEGER,
  flags INTEGER, age_min_at_rx INTEGER, rx_wall REAL, created_est_wall REAL, deadline_est_wall REAL,
  zone INTEGER, summary TEXT, status INTEGER DEFAULT 0, status_seq INTEGER DEFAULT 0,
  dup_count INTEGER DEFAULT 0, first_rx_wall REAL, status_wall REAL,
  PRIMARY KEY (desk_id, seq)
);
CREATE TABLE IF NOT EXISTS desks (desk_id INTEGER PRIMARY KEY, last_seen REAL, census TEXT, rtt REAL,
  boot_id INTEGER, heartbeats INTEGER DEFAULT 0, first_seen REAL);
CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS rx_log (id INTEGER PRIMARY KEY AUTOINCREMENT, wall REAL, mono REAL, desk_id INTEGER,
  kind TEXT, seq INTEGER, version INTEGER, priority INTEGER, dup INTEGER, boot_id INTEGER, msg_seq INTEGER);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, wall REAL, type TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS idx_eoc_status_seq ON eoc_tickets(desk_id, status_seq);
"""

STATUS_NAMES = {0: ("未回覆", "no status"), 1: ("已收到", "acknowledged"), 2: ("已派遣", "dispatched"),
                3: ("已解決", "resolved")}


class EocConflict(Exception):
    def __init__(self, error, zh, en, http_status=409):
        super().__init__(en)
        self.error, self.zh, self.en, self.http_status = error, zh, en, http_status

    def to_dict(self):
        return {"error": self.error, "zh": self.zh, "en": self.en}


class EocStore:
    def __init__(self, path, clock=None):
        self.path = str(path)
        self.clock = clock or time.time
        self._local = threading.local()
        self._lock = threading.RLock()
        self._conns = []
        self._conns_lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=5.0)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ connections
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def close(self):
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        self._local = threading.local()

    @contextmanager
    def _txn(self):
        with self._lock:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    @staticmethod
    def _bump(c, key, delta):
        if delta:
            c.execute("INSERT INTO stats (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value", (key, float(delta)))

    def bump(self, **counters):
        with self._txn() as c:
            for k, v in counters.items():
                self._bump(c, k, v)

    def log_event(self, etype, data=None):
        with self._txn() as c:
            c.execute("INSERT INTO events (wall, type, data) VALUES (?, ?, ?)",
                      (self.clock(), etype, json.dumps(data, ensure_ascii=False) if data is not None else None))

    # ------------------------------------------------------------ receive path
    def apply_records(self, desk_id, boot_id, msg_seq, records, nbytes=0):
        """Apply DATA records in ONE transaction; returns ack entries (seq, version, level, status)."""
        now = self.clock()
        mono = time.monotonic()
        acks = []
        dups = 0
        with self._txn() as c:
            for r in records:
                seq, ver, lvl = int(r["seq"]), int(r["version"]), int(r.get("level", 2))
                row = c.execute("SELECT version, level, status FROM eoc_tickets WHERE desk_id=? AND seq=?",
                                (desk_id, seq)).fetchone()
                dup = 0
                age = int(r.get("age_min") or 0)
                dh = int(r.get("deadline_h", 255))
                created_est = now - age * 60.0
                deadline_est = None if dh == 255 else now + dh * 3600.0
                summary = r.get("summary") if lvl >= 2 else None
                content = (ver, lvl, int(r.get("priority") or 0), int(r.get("category") or 0),
                           int(r.get("item_code") or 0), int(r.get("quantity") or 0),
                           int(r.get("subject_group") or 0), int(r.get("flags") or 0), age, now,
                           created_est, deadline_est, int(r.get("zone") or 0), summary)
                if row is None:
                    c.execute("INSERT INTO eoc_tickets (version, level, priority, category, item_code, quantity, "
                              "subject_group, flags, age_min_at_rx, rx_wall, created_est_wall, deadline_est_wall, "
                              "zone, summary, desk_id, seq, status, status_seq, dup_count, first_rx_wall) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,?)", (*content, desk_id, seq, now))
                    status = 0
                elif ver > row["version"]:
                    c.execute("UPDATE eoc_tickets SET version=?, level=?, priority=?, category=?, item_code=?, "
                              "quantity=?, subject_group=?, flags=?, age_min_at_rx=?, rx_wall=?, created_est_wall=?, "
                              "deadline_est_wall=?, zone=?, summary=? WHERE desk_id=? AND seq=?",
                              (*content, desk_id, seq))
                    status = row["status"] or 0
                elif ver == row["version"] and lvl > (row["level"] or 0):
                    c.execute("UPDATE eoc_tickets SET level=?, summary=? WHERE desk_id=? AND seq=?",
                              (lvl, summary, desk_id, seq))
                    status = row["status"] or 0
                else:
                    c.execute("UPDATE eoc_tickets SET dup_count = dup_count + 1 WHERE desk_id=? AND seq=?",
                              (desk_id, seq))
                    status = row["status"] or 0
                    dup = 1
                    dups += 1
                c.execute("INSERT INTO rx_log (wall, mono, desk_id, kind, seq, version, priority, dup, boot_id, "
                          "msg_seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (now, mono, desk_id, "data", seq, ver, int(r.get("priority") or 0), dup, boot_id, msg_seq))
                acks.append((seq, ver, lvl, status))
            self._bump(c, "datagrams_rx", 1)
            self._bump(c, "data_datagrams_rx", 1)
            self._bump(c, "bytes_rx", nbytes)
            self._bump(c, "records_rx", len(acks))
            self._bump(c, "duplicates", dups)
        return acks

    def apply_heartbeat(self, desk_id, boot_id, msg_seq, census, nbytes=0):
        now = self.clock()
        with self._txn() as c:
            c.execute("INSERT INTO desks (desk_id, last_seen, census, boot_id, heartbeats, first_seen) "
                      "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(desk_id) DO UPDATE SET last_seen=excluded.last_seen, "
                      "census=excluded.census, boot_id=excluded.boot_id, heartbeats=desks.heartbeats + 1",
                      (desk_id, now, json.dumps(census or {}, ensure_ascii=False), boot_id, now))
            c.execute("INSERT INTO rx_log (wall, mono, desk_id, kind, seq, version, priority, dup, boot_id, msg_seq) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (now, time.monotonic(), desk_id, "hb", None, None, None, 0,
                                                       boot_id, msg_seq))
            self._bump(c, "datagrams_rx", 1)
            self._bump(c, "heartbeats_rx", 1)
            self._bump(c, "bytes_rx", nbytes)

    def status_changes(self, desk_id, after_seq, limit=None):
        """[(seq, status, status_seq)] for this desk with status_seq > after_seq, in order."""
        q = ("SELECT seq, status, status_seq FROM eoc_tickets WHERE desk_id=? AND status_seq > ? "
             "ORDER BY status_seq")
        params = [int(desk_id), int(after_seq or 0)]
        if limit:
            q += " LIMIT ?"
            params.append(int(limit))
        return [(r["seq"], r["status"], r["status_seq"]) for r in self.conn().execute(q, params).fetchall()]

    # ------------------------------------------------------------ EOC UI actions
    def set_status(self, desk_id, seq, status):
        """1 ACKNOWLEDGED, 2 DISPATCHED, 3 RESOLVED. Returns the new status_seq."""
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = -1
        if status not in (1, 2, 3):
            raise EocConflict("bad_status", "狀態必須是 1、2 或 3", "Status must be 1, 2 or 3", 400)
        now = self.clock()
        with self._txn() as c:
            row = c.execute("SELECT status, status_seq FROM eoc_tickets WHERE desk_id=? AND seq=?",
                            (int(desk_id), int(seq))).fetchone()
            if row is None:
                raise EocConflict("not_found", "找不到這筆需求", "Ticket not found", 404)
            if status < (row["status"] or 0):
                raise EocConflict("status_backwards", "狀態不能倒退", "Status cannot go backwards")
            if status == (row["status"] or 0):
                return row["status_seq"]
            sseq = 1 + c.execute("SELECT COALESCE(MAX(status_seq), 0) FROM eoc_tickets WHERE desk_id=?",
                                 (int(desk_id),)).fetchone()[0]
            c.execute("UPDATE eoc_tickets SET status=?, status_seq=?, status_wall=? WHERE desk_id=? AND seq=?",
                      (status, sseq, now, int(desk_id), int(seq)))
            c.execute("INSERT INTO events (wall, type, data) VALUES (?, ?, ?)",
                      (now, "status", json.dumps({"desk_id": int(desk_id), "seq": int(seq), "status": status,
                                                  "status_seq": sseq})))
        return sseq

    # ------------------------------------------------------------ reads
    def tickets(self, desk_id=None):
        """{(desk_id, seq): row dict}."""
        if desk_id is None:
            rows = self.conn().execute("SELECT * FROM eoc_tickets").fetchall()
        else:
            rows = self.conn().execute("SELECT * FROM eoc_tickets WHERE desk_id=?", (int(desk_id),)).fetchall()
        return {(r["desk_id"], r["seq"]): dict(r) for r in rows}

    def board(self):
        rows = self.conn().execute("SELECT * FROM eoc_tickets ORDER BY priority, rx_wall, desk_id, seq").fetchall()
        return [dict(r) for r in rows]

    def desks(self):
        out = []
        for r in self.conn().execute("SELECT * FROM desks ORDER BY desk_id").fetchall():
            d = dict(r)
            try:
                d["census"] = json.loads(d.get("census") or "{}")
            except ValueError:
                d["census"] = {}
            out.append(d)
        return out

    def stats(self):
        s = {r["key"]: r["value"] for r in self.conn().execute("SELECT key, value FROM stats").fetchall()}
        out = {k: int(v) for k, v in s.items()}
        row = self.conn().execute("SELECT COUNT(*), COALESCE(SUM(dup_count), 0) FROM eoc_tickets").fetchone()
        out["tickets"] = row[0]
        out["duplicates_suppressed"] = int(row[1])
        for k in ("datagrams_rx", "bytes_rx", "records_rx", "duplicates", "heartbeats_rx", "bad_mac",
                  "bad_datagrams", "acks_tx", "hb_replies_tx", "bytes_tx"):
            out.setdefault(k, 0)
        return out

    def rx_log(self, after_id=0):
        return [dict(r) for r in self.conn().execute("SELECT * FROM rx_log WHERE id > ? ORDER BY id",
                                                     (int(after_id),)).fetchall()]

    def max_rx_id(self):
        return self.conn().execute("SELECT COALESCE(MAX(id), 0) FROM rx_log").fetchone()[0]

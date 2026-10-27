"""Desk store (SQLite): tickets, households, events, meta.

Every write is one transaction under a process-wide lock:
BEGIN IMMEDIATE -> SELECT the row -> check the transition table (R-T) -> write ->
append to events -> COMMIT. A violation raises Conflict (HTTP 409 for clerk calls).

R-V: version += 1 iff wire_view(ticket, now) differs before vs after the write
(compared at the same `now`). No code assigns `version` directly.
"""
import copy
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import codebook
from .wireview import CLOSED_STATUSES, view_key, wire_view

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  priority INTEGER, priority_reasons TEXT, rule_priority INTEGER,
  emergency TEXT, emergency_action TEXT, emergency_action_wall REAL,
  category INTEGER, item TEXT, item_code INTEGER, quantity INTEGER, quantity_approx INTEGER DEFAULT 0,
  subject TEXT, subject_group TEXT, location TEXT, zone_code INTEGER,
  household_id INTEGER, time_phrase TEXT, deadline_at REAL, deadline_text TEXT,
  summary TEXT, transcript TEXT, evidence TEXT, field_sources TEXT, flags TEXT, missing_info TEXT,
  dup_candidates TEXT, merged_into INTEGER, report_count INTEGER DEFAULT 1, merged_from TEXT,
  ai_suggestion TEXT,
  job_id TEXT, audio_seconds REAL DEFAULT 0, clerk_edited TEXT,
  created_wall REAL, confirmed_wall REAL, confirmed_by TEXT,
  alert_sent INTEGER DEFAULT 0, sent_once INTEGER DEFAULT 0,
  acked_version INTEGER DEFAULT 0, acked_level INTEGER DEFAULT 0,
  first_sent_wall REAL, delivered_wall REAL, send_attempts INTEGER DEFAULT 0,
  eoc_status INTEGER DEFAULT 0, eoc_status_wall REAL, escalated_wall REAL,
  reminded_wall REAL
);
CREATE TABLE IF NOT EXISTS households (id INTEGER PRIMARY KEY AUTOINCREMENT, family_name TEXT, size INTEGER,
  zone_code INTEGER, location TEXT, elderly INTEGER, infants INTEGER, children INTEGER,
  pregnant INTEGER, disabled INTEGER, screening TEXT, checkin_wall REAL, checkout_wall REAL, clerk TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, wall REAL, seq INTEGER, type TEXT, clerk TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_events_seq ON events(seq);
"""

JSON_DEFAULTS = {
    "priority_reasons": [], "evidence": {}, "field_sources": {}, "flags": [], "missing_info": [],
    "dup_candidates": [], "merged_from": [], "ai_suggestion": None, "clerk_edited": [],
}

# Fields a clerk may edit (update_fields / confirm).
EDITABLE = ("category", "item", "item_code", "quantity", "quantity_approx", "subject", "subject_group",
            "location", "zone_code", "time_phrase", "deadline_at", "deadline_text", "summary",
            "missing_info")
_INT_FIELDS = ("category", "item_code", "quantity", "quantity_approx", "zone_code", "priority",
               "rule_priority")
EMERGENCY_ACTIONS = ("medic_told", "called_119", "119_unreachable", "runner_sent", "protection_private")
OPEN_STATUSES = ("draft", "confirmed")

# Process-wide write lock (all Store instances in this process).
_LOCK = threading.RLock()


class Conflict(Exception):
    """A state conflict: HTTP 409 {error, zh, en} for clerk calls."""
    http_status = 409

    def __init__(self, error, zh, en):
        super().__init__(en)
        self.error, self.zh, self.en = error, zh, en

    def to_dict(self):
        return {"error": self.error, "zh": self.zh, "en": self.en}


class StaleVersion(Conflict):
    def __init__(self, current):
        super().__init__("stale_version", "資料已被更新，請重新載入", "This request changed elsewhere; please reload")
        self.current = current


class NotFound(Conflict):
    http_status = 404

    def __init__(self, seq):
        super().__init__("not_found", f"找不到單號 {seq}", f"Ticket {seq} not found")


def _bad(op, status):
    return Conflict("bad_transition", f"目前狀態（{status}）不能執行「{op}」",
                    f"'{op}' is not allowed while the ticket is {status}")


def init_db(path):
    """Create the schema (idempotent)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=5.0)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def _dumps(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _min_opt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def union_emergency(a, b):
    """Union of comma-joined emergency types, order kept (medical,rescue,...)."""
    out = []
    for part in (a or "").split(",") + (b or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return ",".join(out) or None


class Store:
    def __init__(self, path, desk_letter="B", clock=None):
        self.path = str(path)
        self.desk_letter = desk_letter
        self.clock = clock or time.time
        self._local = threading.local()
        self._all_conns = []
        self._conns_lock = threading.Lock()
        self._pack_failed = {}  # seq -> version that failed to pack
        init_db(self.path)

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
                self._all_conns.append(c)
        return c

    def close(self):
        """Close every connection this Store opened (tests / shutdown)."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        self._local = threading.local()

    @contextmanager
    def _txn(self):
        with _LOCK:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    # ------------------------------------------------------------ helpers
    def code(self, seq):
        return f"{self.desk_letter}-{int(seq):03d}"

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        t = dict(row)
        for k, default in JSON_DEFAULTS.items():
            v = t.get(k)
            if v is None or v == "":
                t[k] = copy.deepcopy(default)
            else:
                try:
                    t[k] = json.loads(v)
                except (TypeError, ValueError):
                    t[k] = copy.deepcopy(default)
        return t

    def _load(self, c, seq):
        t = self._decode(c.execute("SELECT * FROM tickets WHERE seq=?", (int(seq),)).fetchone())
        if t is None:
            raise NotFound(seq)
        return t

    def _event(self, c, now, seq, etype, clerk=None, data=None):
        c.execute("INSERT INTO events (wall, seq, type, clerk, data) VALUES (?,?,?,?,?)",
                  (now, seq, etype, clerk, _dumps(data) if data is not None else None))

    def _save(self, c, old, new, now):
        """Write changed columns of `new`; bump version iff the wire view changed (R-V)."""
        new["version"] = old["version"]
        if view_key(wire_view(old, now, self.desk_letter)) != view_key(wire_view(new, now, self.desk_letter)):
            new["version"] = old["version"] + 1
        changed = {}
        for k, v in new.items():
            if k == "seq":
                continue
            if v != old.get(k):
                changed[k] = _dumps(v) if k in JSON_DEFAULTS else v
        if changed:
            cols = ", ".join(f"{k}=?" for k in changed)
            c.execute(f"UPDATE tickets SET {cols} WHERE seq=?", (*changed.values(), int(old["seq"])))
        return new

    def _op(self, seq, opname, allowed, fn, clerk=None, data=None):
        now = self.clock()
        with self._txn() as c:
            old = self._load(c, seq)
            if allowed is not None and old["status"] not in allowed:
                raise _bad(opname, old["status"])
            new = copy.deepcopy(old)
            result = fn(new, now, old)
            if result is False:  # no-op
                return old
            self._save(c, old, new, now)
            self._event(c, now, int(seq), opname, clerk, data)
            return new

    @staticmethod
    def _coerce(k, v):
        if k in _INT_FIELDS:
            if v is None or v == "":
                return 0 if k in ("category", "item_code", "zone_code", "quantity_approx") else None
            return int(v)
        if k == "deadline_at":
            return None if v in (None, "") else float(v)
        if k == "missing_info":
            return list(v or [])
        return "" if v is None else str(v)

    def _apply_edit(self, t, fields, mark_clerk=True):
        fields = dict(fields or {})
        if "location" in fields and "zone_code" not in fields:
            zone, _, _ = codebook.parse_location(fields.get("location") or "")
            fields["zone_code"] = zone
        if "item_code" in fields and "item" not in fields:
            it = codebook.item(fields["item_code"])
            fields["item"] = it["zh"] if it else ""
        if "category" in fields and "item_code" not in fields:
            it = codebook.item(t.get("item_code"))
            if it and it["category"] != int(fields["category"] or 0):
                fields["item_code"] = 0
        edited = list(t.get("clerk_edited") or [])
        sources = dict(t.get("field_sources") or {})
        for k, v in fields.items():
            if k not in EDITABLE:
                continue
            v = self._coerce(k, v)
            if t.get(k) != v:
                t[k] = v
                if mark_clerk:
                    if k not in edited:
                        edited.append(k)
                    sources[k] = "clerk"
        t["clerk_edited"] = edited
        t["field_sources"] = sources
        return t

    # ------------------------------------------------------------ creation
    def create_draft(self, need, job_id=None, transcript="", audio_seconds=0.0, flags=None, household_id=None):
        """Insert a draft from a Need (dataclass or dict). Returns seq."""
        if hasattr(need, "to_dict"):
            d = need.to_dict()
        elif hasattr(need, "__dataclass_fields__"):
            import dataclasses
            d = dataclasses.asdict(need)
        else:
            d = dict(need)
        now = self.clock()
        fl = list(d.get("flags") or [])
        for f in flags or []:
            if f not in fl:
                fl.append(f)
        evidence = dict(d.get("evidence") or {})
        if d.get("red_flags"):
            evidence["_red_flags"] = list(d["red_flags"])
        if d.get("suppressed"):
            evidence["_suppressed"] = list(d["suppressed"])
        questions = []
        for q in list(d.get("ask_back") or []) + list(d.get("missing_info") or []):
            if q and q not in questions:
                questions.append(q)
        priority = int(d.get("priority") or codebook.cat(d.get("category") or 0)["default_priority"])
        row = {
            "status": "draft", "version": 0,
            "priority": priority,
            "priority_reasons": _dumps(list(d.get("priority_reasons") or [])),
            "rule_priority": int(d.get("rule_priority") or priority),
            "emergency": d.get("emergency") or None,
            "category": int(d.get("category") or 0),
            "item": d.get("item") or "",
            "item_code": int(d.get("item_code") or 0),
            "quantity": d.get("quantity"),
            "quantity_approx": 1 if d.get("quantity_approx") else 0,
            "subject": d.get("subject") or "",
            "subject_group": d.get("subject_group") or "unknown",
            "location": d.get("location") or "",
            "zone_code": int(d.get("zone_code") or 0),
            "household_id": household_id if household_id is not None else d.get("household_id"),
            "time_phrase": d.get("time_phrase") or "",
            "deadline_at": d.get("deadline_at"),
            "deadline_text": d.get("deadline_text") or "",
            "summary": d.get("summary") or "",
            "transcript": transcript or "",
            "evidence": _dumps(evidence),
            "field_sources": _dumps(dict(d.get("field_sources") or {})),
            "flags": _dumps(fl),
            "missing_info": _dumps(questions),
            "dup_candidates": _dumps(list(d.get("dup_candidates") or [])),
            "merged_from": _dumps([]),
            "clerk_edited": _dumps([]),
            "job_id": job_id,
            "audio_seconds": float(audio_seconds or 0.0),
            "created_wall": now,
        }
        with self._txn() as c:
            cols = ", ".join(row)
            qs = ", ".join("?" for _ in row)
            cur = c.execute(f"INSERT INTO tickets ({cols}) VALUES ({qs})", tuple(row.values()))
            seq = cur.lastrowid
            self._event(c, now, seq, "draft_created", None,
                        {"job_id": job_id, "priority": priority, "category": row["category"]})
        return seq

    # ------------------------------------------------------------ R-T operations
    def confirm(self, seq, fields=None, priority=None, clerk="", reason=None):
        def fn(t, now, old):
            if fields:
                self._apply_edit(t, fields)
            p = int(t["priority"] if priority in (None, "") else priority)
            if p not in (1, 2, 3):
                raise Conflict("bad_priority", "優先等級必須是 1、2 或 3", "Priority must be 1, 2 or 3")
            floor = int(t.get("rule_priority") or t["priority"] or 3)
            if p > floor and not (reason and str(reason).strip()):
                raise Conflict("reason_required", "調降優先等級需要填寫原因",
                               "Lowering the priority below the rule floor needs a reason")
            if p != t["priority"]:
                reasons = list(t.get("priority_reasons") or [])
                if p > t["priority"]:
                    reasons.append({"code": "clerk_lowered", "zh": f"值班人員調降：{reason}",
                                    "en": f"Lowered by clerk: {reason}"})
                else:
                    reasons.append({"code": "clerk_raised", "zh": "值班人員調升", "en": "Raised by clerk"})
                t["priority_reasons"] = reasons
                t["priority"] = p
            t["status"] = "confirmed"
            t["confirmed_wall"] = now
            t["confirmed_by"] = clerk
        return self._op(seq, "confirm", ("draft",), fn, clerk,
                        {"priority": priority, "reason": reason, "fields": sorted((fields or {}).keys())})

    def update_fields(self, seq, fields, clerk="", expected_version=None):
        def fn(t, now, old):
            if expected_version is not None and int(expected_version) != int(old["version"]):
                raise StaleVersion(old["version"])
            self._apply_edit(t, fields)
        return self._op(seq, "update", OPEN_STATUSES, fn, clerk, {"fields": sorted((fields or {}).keys())})

    def raise_worried(self, seq, clerk=""):
        def fn(t, now, old):
            reasons = list(t.get("priority_reasons") or [])
            if not any(r.get("code") == "clerk_worried" for r in reasons):
                reasons.append({"code": "clerk_worried", "zh": "值班人員擔心，提高為P1", "en": "Clerk is worried: P1"})
            t["priority_reasons"] = reasons
            t["priority"] = 1
        return self._op(seq, "worried", OPEN_STATUSES, fn, clerk)

    def dismiss_duplicates(self, seq, clerk=""):
        """Clerk says 'not a duplicate': clear the merge suggestions."""
        def fn(t, now, old):
            t["dup_candidates"] = []
        return self._op(seq, "not_duplicate", OPEN_STATUSES, fn, clerk)

    def log_emergency_action(self, seq, action, clerk=""):
        if action not in EMERGENCY_ACTIONS:
            raise Conflict("bad_action", "不明的緊急處置", f"Unknown emergency action {action!r}")

        def fn(t, now, old):
            t["emergency_action"] = action
            t["emergency_action_wall"] = now
        return self._op(seq, "emergency_action", OPEN_STATUSES, fn, clerk, {"action": action})

    def apply_ai_fields(self, seq, fields, sources=None, flags=None):
        """Draft: write fields (skipping clerk_edited). Confirmed: ai_suggestion only. Else no-op."""
        fields = dict(fields or {})

        def fn(t, now, old):
            if t["status"] == "confirmed":
                t["ai_suggestion"] = {"fields": fields, "sources": sources or {}, "flags": list(flags or []),
                                      "wall": now}
                return None
            if t["status"] != "draft":
                return False
            edited = set(t.get("clerk_edited") or [])
            src = dict(t.get("field_sources") or {})
            for k, v in fields.items():
                if k in edited:
                    continue
                if k == "priority":
                    t["priority"] = min(int(t["priority"] or 3), int(v))
                elif k == "rule_priority":
                    t["rule_priority"] = min(int(t.get("rule_priority") or 3), int(v))
                elif k == "deadline_at":
                    nd = _min_opt(t.get("deadline_at"), v)
                    if nd != t.get("deadline_at"):
                        t["deadline_at"] = nd
                        if "deadline_text" in fields and v == nd:
                            t["deadline_text"] = fields["deadline_text"]
                elif k == "deadline_text":
                    continue
                elif k == "emergency":
                    t["emergency"] = union_emergency(t.get("emergency"), v)
                elif k in ("priority_reasons", "missing_info"):
                    merged = list(t.get(k) or [])
                    for x in v or []:
                        if x not in merged:
                            merged.append(x)
                    t[k] = merged
                elif k == "evidence":
                    ev = dict(t.get("evidence") or {})
                    ev.update(v or {})
                    t["evidence"] = ev
                elif k in EDITABLE or k in ("subject", "summary"):
                    t[k] = self._coerce(k, v)
                else:
                    continue
                if sources and k in sources:
                    src[k] = sources[k]
            t["field_sources"] = src
            fl = list(t.get("flags") or [])
            for f in flags or []:
                if f not in fl:
                    fl.append(f)
            t["flags"] = fl
        now_status = None
        try:
            return self._op(seq, "ai_applied", None, fn, None, {"fields": sorted(fields.keys())})
        except NotFound:
            return now_status

    def discard(self, seq, clerk="", reason=None):
        def fn(t, now, old):
            life_safety = int(t.get("priority") or 3) == 1 or int(t.get("rule_priority") or 3) == 1 \
                or bool(t.get("emergency"))
            if life_safety and not (reason and str(reason).strip()):
                raise Conflict("reason_required", "這筆是P1或有危險字詞，捨棄需要填寫原因",
                               "Discarding a P1 or red-flag request needs a reason")
            t["status"] = "discarded"
        return self._op(seq, "discard", OPEN_STATUSES, fn, clerk, {"reason": reason})

    def resolve(self, seq, clerk=""):
        def fn(t, now, old):
            t["status"] = "resolved"
        return self._op(seq, "resolve", ("confirmed",), fn, clerk)

    def merge(self, src, dst, clerk=""):
        src, dst = int(src), int(dst)
        if src == dst:
            raise Conflict("merge_self", "不能合併到自己", "Cannot merge a request into itself")
        now = self.clock()
        with self._txn() as c:
            s_old, d_old = self._load(c, src), self._load(c, dst)
            if s_old["status"] not in OPEN_STATUSES:
                raise _bad("merge", s_old["status"])
            if d_old["status"] not in OPEN_STATUSES:
                raise Conflict("bad_transition", "合併目標已關閉", "The merge target is closed")
            if s_old["version"] > 0 and d_old["status"] != "confirmed":
                raise Conflict("merge_needs_confirmed", "已送出的需求只能合併到已確認的需求",
                               "An announced request can only merge into a confirmed one")
            s_new, d_new = copy.deepcopy(s_old), copy.deepcopy(d_old)
            s_new["status"] = "merged"
            s_new["merged_into"] = dst
            d_new["report_count"] = int(d_old.get("report_count") or 1) + 1
            d_new["priority"] = min(int(d_old["priority"] or 3), int(s_old["priority"] or 3))
            d_new["rule_priority"] = min(int(d_old.get("rule_priority") or 3), int(s_old.get("rule_priority") or 3))
            nd = _min_opt(d_old.get("deadline_at"), s_old.get("deadline_at"))
            if nd != d_old.get("deadline_at"):
                d_new["deadline_at"] = nd
                d_new["deadline_text"] = s_old.get("deadline_text") or d_old.get("deadline_text")
            d_new["emergency"] = union_emergency(d_old.get("emergency"), s_old.get("emergency"))
            if not codebook.is_person_specific(d_old.get("category")):
                d_new["quantity"] = int(d_old.get("quantity") or 0) + int(s_old.get("quantity") or 0) or None
            mf = list(d_old.get("merged_from") or [])
            if src not in mf:
                mf.append(src)
            d_new["merged_from"] = mf
            self._save(c, s_old, s_new, now)
            self._save(c, d_old, d_new, now)
            self._event(c, now, src, "merged", clerk, {"into": dst})
            self._event(c, now, dst, "merged_in", clerk, {"from": src})
        return d_new

    def restore(self, seq, clerk=""):
        seq = int(seq)
        now = self.clock()
        with self._txn() as c:
            old = self._load(c, seq)
            if old["status"] not in ("discarded", "merged"):
                raise _bad("restore", old["status"])
            new = copy.deepcopy(old)
            new["status"] = "confirmed" if old.get("confirmed_wall") is not None else "draft"
            if old["status"] == "merged" and old.get("merged_into"):
                try:
                    d_old = self._load(c, old["merged_into"])
                except NotFound:
                    d_old = None
                if d_old is not None:
                    d_new = copy.deepcopy(d_old)
                    d_new["report_count"] = max(1, int(d_old.get("report_count") or 1) - 1)
                    if not codebook.is_person_specific(d_old.get("category")):
                        q = int(d_old.get("quantity") or 0) - int(old.get("quantity") or 0)
                        d_new["quantity"] = max(0, q) or None
                    d_new["merged_from"] = [x for x in (d_old.get("merged_from") or []) if x != seq]
                    self._save(c, d_old, d_new, now)
                    self._event(c, now, d_old["seq"], "unmerged", clerk, {"from": seq})
                new["merged_into"] = None
            self._save(c, old, new, now)
            self._event(c, now, seq, "restored", clerk, {"to": new["status"]})
        return new

    def mark_alert_sent(self, seq):
        def fn(t, now, old):
            if t["status"] != "draft" or int(t.get("alert_sent") or 0) != 0 or int(t.get("priority") or 3) != 1:
                raise _bad("mark_alert_sent", f"{t['status']}/alert={t.get('alert_sent')}/P{t.get('priority')}")
            t["alert_sent"] = 1
        return self._op(seq, "alert_sent", ("draft",), fn)

    def escalate(self, seq, sla_s):
        """Confirmed + eoc_status 0 + (never escalated or older than sla_s) -> set escalated_wall.

        Returns the ticket if it escalated now, else None. Only the first escalation changes
        the wire view (and so re-sends).
        """
        result = {"done": False}

        def fn(t, now, old):
            if int(t.get("eoc_status") or 0) != 0:
                return False
            last = t.get("escalated_wall")
            if last is not None and now - last <= sla_s:
                return False
            t["escalated_wall"] = now
            result["done"] = True
        t = self._op(seq, "escalated", ("confirmed",), fn)
        return t if result["done"] else None

    def set_reminded(self, seq):
        """Bookkeeping: the unconfirmed-P1 reminder was shown."""
        now = self.clock()
        with self._txn() as c:
            c.execute("UPDATE tickets SET reminded_wall=? WHERE seq=? AND reminded_wall IS NULL", (now, int(seq)))
            self._event(c, now, int(seq), "reminded")

    # ------------------------------------------------------------ send / ack bookkeeping
    def mark_sent(self, seqs):
        """Committed BEFORE sendto: sent_once=1, first_sent_wall if NULL, send_attempts+1."""
        seqs = [int(s) for s in seqs]
        if not seqs:
            return
        now = self.clock()
        with self._txn() as c:
            c.executemany(
                "UPDATE tickets SET sent_once=1, first_sent_wall=COALESCE(first_sent_wall, ?), "
                "send_attempts=send_attempts+1 WHERE seq=?", [(now, s) for s in seqs])
            self._event(c, now, None, "sent", None, {"seqs": seqs})

    def _ack_one(self, c, now, seq, v, level, eoc_status):
        r = c.execute("SELECT version, acked_version, acked_level, eoc_status, delivered_wall FROM tickets "
                      "WHERE seq=?", (int(seq),)).fetchone()
        if r is None:
            return False
        version, av, al, es, dw = (r["version"], r["acked_version"] or 0, r["acked_level"] or 0,
                                   r["eoc_status"] or 0, r["delivered_wall"])
        upd = {}
        v, level = int(v), int(level)
        if v > version or v < av:
            pass
        elif v > av:
            upd["acked_version"], upd["acked_level"] = v, level
        elif level > al:
            upd["acked_level"] = level
        new_es = max(es, int(eoc_status or 0))
        if new_es != es:
            upd["eoc_status"] = new_es
            upd["eoc_status_wall"] = now
        if dw is None:
            upd["delivered_wall"] = now
        if upd:
            cols = ", ".join(f"{k}=?" for k in upd)
            c.execute(f"UPDATE tickets SET {cols} WHERE seq=?", (*upd.values(), int(seq)))
        return True

    def mark_acked(self, seq, v, level, eoc_status=0):
        self.mark_acked_many([(seq, v, level, eoc_status)])

    def mark_acked_many(self, entries):
        """entries: [(seq, version, level, eoc_status)] in one transaction."""
        entries = list(entries)
        if not entries:
            return
        now = self.clock()
        with self._txn() as c:
            for seq, v, level, st in entries:
                self._ack_one(c, now, seq, v, level, st)
            self._event(c, now, None, "acked", None, {"acks": [[int(e[0]), int(e[1]), int(e[2])] for e in entries]})

    def apply_status(self, seq, eoc_status, status_seq):
        self.apply_status_many([(seq, eoc_status, status_seq)])

    def apply_status_many(self, entries):
        """HB_REPLY entries [(seq, eoc_status, status_seq)] in order. Never bumps versions."""
        entries = list(entries)
        if not entries:
            return
        now = self.clock()
        with self._txn() as c:
            applied = int(self._meta_get(c, "status_seq_applied", "0"))
            for seq, st, sseq in entries:
                if int(sseq) <= applied:
                    continue
                r = c.execute("SELECT eoc_status FROM tickets WHERE seq=?", (int(seq),)).fetchone()
                if r is not None and int(st) > int(r["eoc_status"] or 0):
                    c.execute("UPDATE tickets SET eoc_status=?, eoc_status_wall=? WHERE seq=?",
                              (int(st), now, int(seq)))
                applied = max(applied, int(sseq))
            self._meta_set(c, "status_seq_applied", str(applied))
            self._event(c, now, None, "status_applied", None, {"entries": [list(map(int, e)) for e in entries]})

    def mark_pack_failed(self, seq, version):
        """Exclude (seq, version) from pending_units until its version changes."""
        self._pack_failed[int(seq)] = int(version)
        now = self.clock()
        with self._txn() as c:
            self._event(c, now, int(seq), "pack_failed", None, {"version": int(version)})

    def pending_units(self, exclude=(), now=None):
        """Views of tickets with wire_view != None and (acked_version < version or acked_level < 2).

        Ordered by (priority, deadline_at or inf, created_wall). Units that failed to pack are
        excluded until their version changes. Each view carries '_deadline' and '_created'.
        """
        now = self.clock() if now is None else now
        exclude = set(int(s) for s in exclude)
        rows = self.conn().execute(
            "SELECT * FROM tickets WHERE version > 0 AND (acked_version < version OR acked_level < 2)").fetchall()
        out = []
        for r in rows:
            t = self._decode(r)
            if t["seq"] in exclude:
                continue
            if self._pack_failed.get(t["seq"]) == t["version"]:
                continue
            v = wire_view(t, now, self.desk_letter)
            if v is None:
                continue
            v["_deadline"] = t["deadline_at"] if t["deadline_at"] is not None else math.inf
            v["_created"] = t["created_wall"] or 0.0
            out.append(v)
        out.sort(key=lambda v: (v["priority"], v["_deadline"], v["_created"], v["seq"]))
        return out

    # ------------------------------------------------------------ meta / boot / recovery
    def _meta_get(self, c, key, default=None):
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def _meta_set(self, c, key, value):
        c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))

    def meta(self, key, default=None):
        return self._meta_get(self.conn(), key, default)

    def set_meta(self, key, value):
        with self._txn() as c:
            self._meta_set(c, key, value)

    def new_boot(self):
        """Persisted boot id, incremented mod 256 on every start."""
        now = self.clock()
        with self._txn() as c:
            b = (int(self._meta_get(c, "boot_id", "0")) + 1) % 256
            self._meta_set(c, "boot_id", b)
            self._event(c, now, None, "boot", None, {"boot_id": b})
        return b

    def boot_id(self):
        return int(self.meta("boot_id", "0"))

    def recover(self):
        """Counts after a restart: open drafts and unsent (pending) units."""
        c = self.conn()
        drafts = c.execute("SELECT COUNT(*) FROM tickets WHERE status='draft'").fetchone()[0]
        unsent = len(self.pending_units())
        now = self.clock()
        with self._txn() as cc:
            self._event(cc, now, None, "recovered", None, {"drafts": drafts, "unsent": unsent})
        return {"drafts": drafts, "unsent": unsent}

    # ------------------------------------------------------------ households
    def checkin(self, household, clerk=""):
        h = dict(household or {})
        now = self.clock()
        zone = h.get("zone_code")
        if zone is None:
            zone = codebook.parse_zone_input(h.get("zone") or "")
        row = {
            "family_name": (h.get("family_name") or "").strip(),
            "size": max(0, int(h.get("size") or 0)),
            "zone_code": int(zone or 0),
            "location": h.get("location") or "",
            "elderly": max(0, int(h.get("elderly") or 0)),
            "infants": max(0, int(h.get("infants") or 0)),
            "children": max(0, int(h.get("children") or 0)),
            "pregnant": max(0, int(h.get("pregnant") or 0)),
            "disabled": max(0, int(h.get("disabled") or 0)),
            "screening": _dumps(h.get("screening") or {}),
            "checkin_wall": now,
            "clerk": clerk,
        }
        with self._txn() as c:
            cols = ", ".join(row)
            cur = c.execute(f"INSERT INTO households ({cols}) VALUES ({', '.join('?' for _ in row)})",
                            tuple(row.values()))
            hid = cur.lastrowid
            self._event(c, now, None, "checkin", clerk, {"household_id": hid, "size": row["size"]})
        return hid

    def checkout(self, hid, clerk=""):
        now = self.clock()
        with self._txn() as c:
            r = c.execute("SELECT checkout_wall FROM households WHERE id=?", (int(hid),)).fetchone()
            if r is None:
                raise Conflict("not_found", "找不到這一戶", "Household not found")
            if r["checkout_wall"] is not None:
                raise Conflict("already_checked_out", "這一戶已經離開", "Household already checked out")
            c.execute("UPDATE households SET checkout_wall=? WHERE id=?", (now, int(hid)))
            self._event(c, now, None, "checkout", clerk, {"household_id": int(hid)})

    def households(self, include_out=False):
        q = "SELECT * FROM households" + ("" if include_out else " WHERE checkout_wall IS NULL") + " ORDER BY id"
        out = []
        for r in self.conn().execute(q).fetchall():
            h = dict(r)
            try:
                h["screening"] = json.loads(h.get("screening") or "{}")
            except ValueError:
                h["screening"] = {}
            out.append(h)
        return out

    def household(self, hid):
        r = self.conn().execute("SELECT * FROM households WHERE id=?", (int(hid),)).fetchone()
        return dict(r) if r else None

    def census(self):
        c = self.conn()
        h = c.execute("SELECT COALESCE(SUM(size),0), COALESCE(SUM(elderly),0), COALESCE(SUM(infants),0) + "
                      "COALESCE(SUM(children),0), COALESCE(SUM(disabled),0), COALESCE(SUM(pregnant),0), COUNT(*) "
                      "FROM households WHERE checkout_wall IS NULL").fetchone()
        q = {1: 0, 2: 0, 3: 0}
        for r in c.execute("SELECT priority, COUNT(*) FROM tickets WHERE status IN ('draft','confirmed') "
                           "GROUP BY priority").fetchall():
            if r[0] in q:
                q[r[0]] = r[1]
        return {"people": h[0], "elderly": h[1], "infant_child": h[2], "disabled": h[3], "pregnant": h[4],
                "households": h[5], "q_p1": q[1], "q_p2": q[2], "q_p3": q[3],
                "status_seq_applied": int(self._meta_get(c, "status_seq_applied", "0"))}

    # ------------------------------------------------------------ reads
    def get(self, seq):
        return self._decode(self.conn().execute("SELECT * FROM tickets WHERE seq=?", (int(seq),)).fetchone())

    def tickets(self, statuses=None, order="seq"):
        if statuses:
            qs = ", ".join("?" for _ in statuses)
            rows = self.conn().execute(f"SELECT * FROM tickets WHERE status IN ({qs}) ORDER BY {order}",
                                       tuple(statuses)).fetchall()
        else:
            rows = self.conn().execute(f"SELECT * FROM tickets ORDER BY {order}").fetchall()
        return [self._decode(r) for r in rows]

    def drafts(self):
        return self.tickets(("draft",), order="priority, created_wall, seq")

    def board(self):
        """Confirmed and resolved tickets, most urgent first (the app hides hidden categories)."""
        rows = self.tickets(("confirmed", "resolved"))
        rows.sort(key=lambda t: (t["status"] != "confirmed", t["priority"] or 3,
                                 t["deadline_at"] if t["deadline_at"] is not None else math.inf,
                                 t["created_wall"] or 0))
        return rows

    def open_tickets(self):
        return self.tickets(OPEN_STATUSES)

    def query(self, sql, params=()):
        return [dict(r) for r in self.conn().execute(sql, tuple(params)).fetchall()]

    def events(self, limit=100, seq=None):
        if seq is None:
            rows = self.conn().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = self.conn().execute("SELECT * FROM events WHERE seq=? ORDER BY id DESC LIMIT ?",
                                       (int(seq), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def log_event(self, etype, data=None, seq=None, clerk=None):
        """Append a free event (health, timers, sender) in its own transaction."""
        now = self.clock()
        with self._txn() as c:
            self._event(c, now, seq, etype, clerk, data)

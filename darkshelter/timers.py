"""Stateless timers (SPEC 6.11): every 2 s, ages computed from persisted wall columns.

They survive restarts and sleep because nothing is kept in memory except the clock-jump
reference. Conflicts from the store are ignored (another write got there first).
"""
import threading
import time

from . import codebook
from .store import Conflict

P1_UNDELIVERED = {"zh": "網路中斷且有P1待送：請派人通報／使用衛星電話",
                  "en": "Link down with a P1 waiting: send a runner / use the satellite phone"}


class Timers:
    def __init__(self, store, cfg, sender=None, clock=None, mono=None, beat=None):
        self.store = store
        self.cfg = cfg
        self.sender = sender
        self.clock = clock or time.time
        self.mono = mono or time.monotonic
        self.beat = beat or (lambda: None)
        self.banners = []
        self._ref = None  # (wall, mono) at the last check
        self._stop = threading.Event()
        self.thread = None
        self._last_error_log = {}
        self.clock_jumps = 0

    def start(self):
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="timers", daemon=True)
        self.thread.start()
        return self.thread

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.beat()
                self.check()
            except Exception as e:
                now = time.monotonic()
                if now - self._last_error_log.get("timers_error", -1e9) >= 60:
                    self._last_error_log["timers_error"] = now
                    try:
                        self.store.log_event("timers_error", {"error": f"{type(e).__name__}: {e}"})
                    except Exception:
                        pass
            self._stop.wait(2.0)

    # ------------------------------------------------------------ the check
    def _sla_s(self, key):
        return float(self.cfg["triage"]["sla_minutes"].get(str(key), 30)) * 60.0

    def check_clock(self):
        wall, mono = self.clock(), self.mono()
        if self._ref is not None:
            d_wall = wall - self._ref[0]
            d_mono = mono - self._ref[1]
            if abs(d_wall - d_mono) > 5.0:
                self.clock_jumps += 1
                try:
                    self.store.log_event("clock_jump", {"delta_s": round(d_wall - d_mono, 1)})
                except Exception:
                    pass
                if self.sender is not None:
                    try:
                        self.sender.on_clock_jump()
                    except Exception:
                        pass
        self._ref = (wall, mono)

    def check(self, now=None):
        self.check_clock()
        now = self.clock() if now is None else now
        tcfg = self.cfg["triage"]
        reminder_s = float(tcfg.get("unconfirmed_p1_reminder_s", 120))
        alert_s = float(tcfg.get("unconfirmed_p1_alert_s", 300))
        letter = self.cfg.get("desk_letter", "B")
        backup = self.cfg.get("backup_channel", {})
        banners = []
        # Recordings still queued when the desk crashed were never saved (keep_audio is off): ask for them again.
        lost_fn = getattr(self.store, "lost_intakes", None)
        lost = lost_fn(now) if lost_fn is not None else 0
        if lost:
            banners.append({"kind": "lost_audio", "level": "danger", "count": lost,
                            "zh": f"{lost} 筆語音在當機時遺失，請重錄",
                            "en": (f"{lost} voice recordings were lost in a crash — please record again" if lost != 1
                                   else "1 voice recording was lost in a crash — please record again")})
        # The EOC holds a different ticket under these numbers (database reset, or two desks sharing desk_id).
        conflicts_fn = getattr(self.store, "seq_conflicts", None)
        conflicts = conflicts_fn() if conflicts_fn is not None else set()
        for t in self.store.open_tickets():
            seq = t["seq"]
            code = f"{letter}-{seq:03d}"
            if seq in conflicts:
                banners.append({"kind": "seq_conflict", "seq": seq, "code": code, "level": "danger",
                                "print": True, "backup": True,
                                "zh": f"{code} 無法送達應變中心（編號與中心已有的單重複：資料庫被重設或兩台登記台同編號），請改用備援管道",
                                "en": f"{code} can't reach the EOC (its number clashes with a ticket the EOC already "
                                      f"holds: database reset or two desks sharing desk_id) — use the backup channel"})
                continue
            created = t.get("created_wall") or now
            age = max(0.0, now - created)
            hidden = codebook.is_hidden(t.get("category"))
            # --- unconfirmed P1 draft
            if t["status"] == "draft" and int(t.get("priority") or 3) == 1:
                if age >= reminder_s:
                    if t.get("reminded_wall") is None:
                        self.store.set_reminded(seq)
                    banners.append({"kind": "unconfirmed_p1", "seq": seq, "code": code, "level": "warn",
                                    "beep": True, "zh": f"{code} P1 尚未確認已 {int(age // 60)} 分鐘，請確認",
                                    "en": f"{code}: P1 not confirmed for {int(age // 60)} min"})
                if alert_s > 0 and age >= alert_s and not t.get("alert_sent"):
                    try:
                        self.store.mark_alert_sent(seq)
                    except Conflict:
                        pass
            # --- emergency banner until an action is logged
            if t.get("emergency"):
                if not t.get("emergency_action"):
                    banners.append({"kind": "emergency", "seq": seq, "code": code, "level": "danger",
                                    "emergency": t["emergency"], "hidden": hidden,
                                    "zh": "保護個案：請移到隱密處，聯絡社工／113" if "protection" in t["emergency"]
                                    else "緊急狀況：請通知現場醫護或撥打119",
                                    "en": "Protection case: private spot, social worker / 113"
                                    if "protection" in t["emergency"] else "Emergency: get the medic / call 119"})
                elif t.get("emergency_action") == "119_unreachable":
                    banners.append({"kind": "backup_channel", "seq": seq, "code": code, "level": "danger",
                                    "zh": f"{code} 119打不通：{backup.get('zh', '')}",
                                    "en": f"{code} 119 unreachable: {backup.get('en', '')}", "backup": True})
            if t["status"] != "confirmed" or t.get("confirmed_wall") is None:
                continue
            since = max(0.0, now - t["confirmed_wall"])
            prio = int(t.get("priority") or 3)
            eoc = int(t.get("eoc_status") or 0)
            delivered = t.get("delivered_wall") is not None
            is_emergency = bool(t.get("emergency")) and "protection" not in (t.get("emergency") or "")
            if is_emergency:
                sla = self._sla_s("emergency")
                if since >= sla and eoc == 0:
                    if not delivered:
                        banners.append({"kind": "emergency_undelivered", "seq": seq, "code": code, "level": "danger",
                                        "print": True, "backup": True,
                                        "zh": f"{code} 緊急需求 {int(sla // 60)} 分鐘未送達應變中心：{backup.get('zh', '')}",
                                        "en": f"{code} emergency not delivered: {backup.get('en', '')}"})
                    else:
                        banners.append({"kind": "emergency_unacked", "seq": seq, "code": code, "level": "danger",
                                        "backup": True,
                                        "zh": f"{code} 應變中心 {int(sla // 60)} 分鐘未回覆：{backup.get('zh', '')}",
                                        "en": f"{code} EOC has not acknowledged: {backup.get('en', '')}"})
                    try:
                        self.store.escalate(seq, sla)
                    except Conflict:
                        pass
                continue
            sla = self._sla_s(prio)
            if since < sla or eoc != 0:
                continue
            if not delivered:
                if prio == 1:
                    banners.append({"kind": "p1_undelivered", "seq": seq, "code": code, "level": "danger",
                                    "print": True, "backup": True, "zh": f"{code} {P1_UNDELIVERED['zh']}",
                                    "en": f"{code} {P1_UNDELIVERED['en']}"})
            else:
                try:
                    if self.store.escalate(seq, sla) is None and prio == 1:
                        banners.append({"kind": "p1_unacked", "seq": seq, "code": code, "level": "warn",
                                        "zh": f"{code} 已送達但應變中心尚未回覆（已標記升級）",
                                        "en": f"{code} delivered but not acknowledged (escalated)"})
                except Conflict:
                    pass
        self.banners = banners
        return banners

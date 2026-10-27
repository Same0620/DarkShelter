"""Desk process: Flask UI/API + worker, sender, timers and health threads (SPEC 2, 9, 10).

python -m darkshelter.desk_app --config config.json [--mock]
"""
import argparse
import csv
import io
import json
import logging
import mimetypes
import threading
import time
import urllib.request
from pathlib import Path

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from flask import Flask, Response, jsonify, request, send_from_directory  # noqa: E402

from . import codebook  # noqa: E402
from .config import load_config, resolve_path  # noqa: E402
from .health import Health  # noqa: E402
from .pipeline import Pipeline  # noqa: E402
from .store import Conflict, Store  # noqa: E402
from .timers import Timers  # noqa: E402
from .wireview import build_summary, export_row, flag_names, wire_view  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 5 * 1024 * 1024
PROFILES = ("DOWN", "LORA_RELAY", "LTE_ONE_BAR", "SATELLITE", "GOOD", "FLAPPING")


def err(error, zh, en, status=400):
    return jsonify({"error": error, "zh": zh, "en": en}), status


class EmuClient:
    """Talks to the link emulator's HTTP control port; stats cached by a background poller."""

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self._stats = None
        self._stats_wall = 0.0
        self._lock = threading.Lock()
        self._polling = False

    def _get(self, path, timeout=1.0):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def set_profile(self, name):
        return self._get(f"/profile?name={name}", timeout=2.0)

    def stats(self):
        now = time.time()
        with self._lock:
            stale = now - self._stats_wall > 1.5
            if stale and not self._polling:
                self._polling = True
                threading.Thread(target=self._poll, daemon=True).start()
            return self._stats

    def _poll(self):
        try:
            s = self._get("/stats", timeout=0.8)
        except Exception:
            s = None
        with self._lock:
            self._stats = s
            self._stats_wall = time.time()
            self._polling = False


class Desk:
    """Holds the desk's parts so routes and the watchdog can reach them."""

    def __init__(self, cfg, mock=False, store=None, asr=None, llm=None, start_threads=True, start_sender=True):
        self.cfg = cfg
        self.mock = mock
        self.letter = cfg.get("desk_letter", "B")
        self.store = store or Store(resolve_path(cfg["paths"]["desk_db"]), self.letter)
        self.boot_id = self.store.new_boot()
        self.recovered = self.store.recover()
        self.recovered_wall = time.time()
        if asr is None:
            asr = self._make_asr()
        if llm is None:
            llm = self._make_llm()
        self.asr, self.llm = asr, llm
        self.health = Health(cfg, self.store, asr=asr, llm=llm, mock=mock)
        self.pipeline = Pipeline(self.store, cfg, asr=asr, llm=llm, health=self.health,
                                 beat=self.health.beater("worker"))
        self.health.pipeline = self.pipeline
        self.sender = None
        self.timers = None
        self.emu = EmuClient(cfg["ports"]["emu_control_http"])
        self._start_sender_flag = start_sender
        if start_threads:
            self.start()

    def _make_asr(self):
        try:
            if self.mock:
                from .asr import MockASR
                return MockASR(self.cfg)
            from .asr import FasterWhisperASR
            return FasterWhisperASR(self.cfg)
        except Exception as e:
            self.store.log_event("asr_unavailable", {"error": f"{type(e).__name__}: {e}"})
            return None

    def _make_llm(self):
        try:
            if self.mock:
                from .llm import MockLLM
                return MockLLM()
            if not self.cfg["llm"].get("enabled", True):
                return None
            from .llm import OllamaLLM
            return OllamaLLM.from_config(self.cfg)
        except Exception as e:
            self.store.log_event("llm_unavailable", {"error": f"{type(e).__name__}: {e}"})
            return None

    def _new_sender(self, first=False):
        from .net.sender import Sender
        from .health import battery_status

        def battery():
            b = battery_status()
            return None if b is None else b["percent"]
        # The first sender uses this boot's id; a restarted sender gets a fresh one (new msg_seq space).
        return Sender(self.store, self.cfg, beat=self.health.beater("sender"), battery_fn=battery,
                      boot_id=self.boot_id if first else None)

    def restart_sender(self):
        try:
            if self.sender is not None:
                self.sender.stop()
        except Exception:
            pass
        self.sender = self._new_sender()
        self.sender.start()
        if self.timers is not None:
            self.timers.sender = self.sender

    def start(self):
        self.pipeline.start()
        self.health.register("worker", lambda: self.pipeline.thread, self.pipeline.start, stale_s=60)
        if self._start_sender_flag:
            try:
                self.sender = self._new_sender(first=True)
                self.sender.start()
                self.health.register("sender", lambda: getattr(self.sender, "thread", None) or self.sender,
                                     self.restart_sender, stale_s=10)
            except Exception as e:
                self.store.log_event("sender_unavailable", {"error": f"{type(e).__name__}: {e}"})
        self.timers = Timers(self.store, self.cfg, sender=self.sender, beat=self.health.beater("timers"))
        self.timers.start()
        self.health.register("timers", lambda: self.timers.thread, self.timers.start, stale_s=10)
        self.health.start()

    def stop(self):
        for part in (self.health, self.timers, self.pipeline, self.sender):
            try:
                if part is not None:
                    part.stop()
            except Exception:
                pass

    # ------------------------------------------------------------ views
    def wire_state(self, t):
        """Board status icon: ⏳ ↑ ✓ ✓✓ 🚑 ✔ ⚠ (+ zh/en)."""
        es = int(t.get("eoc_status") or 0)
        if t["status"] == "resolved" or es == 3:
            icon, zh, en = "✔", "已解決", "resolved"
        elif es == 2:
            icon, zh, en = "🚑", "已派遣", "dispatched"
        elif es == 1:
            icon, zh, en = "✓✓", "應變中心已收到", "acknowledged"
        elif t.get("version") and int(t.get("acked_version") or 0) >= int(t["version"]):
            icon, zh, en = "✓", "已送達", "delivered"
        elif t.get("sent_once"):
            icon, zh, en = "↑", "傳送中", "sending"
        elif t.get("version"):
            icon, zh, en = "⏳", "排隊待送", "queued"
        else:
            icon, zh, en = "", "", ""
        if t.get("escalated_wall") and es == 0 and t["status"] != "resolved":
            icon, zh, en = "⚠", "已升級，未回覆", "escalated"
        return {"icon": icon, "zh": zh, "en": en}

    def ticket_view(self, t, now):
        c = codebook.cat(t.get("category"))
        dups = []
        for d in t.get("dup_candidates") or []:
            if isinstance(d, dict) and d.get("seq"):
                dd = dict(d)
                dd["code"] = self.store.code(d["seq"])
                dups.append(dd)
        v = wire_view(t, now, self.letter)
        return {
            "seq": t["seq"], "code": self.store.code(t["seq"]), "status": t["status"], "version": t["version"],
            "priority": t.get("priority"), "rule_priority": t.get("rule_priority"),
            "priority_reasons": t.get("priority_reasons") or [], "emergency": t.get("emergency"),
            "emergency_action": t.get("emergency_action"),
            "category": t.get("category"), "category_key": c["key"], "category_zh": c["zh"],
            "category_icon": c.get("icon", ""), "hidden": bool(c.get("hidden")),
            "item": t.get("item") or "", "item_code": t.get("item_code") or 0,
            "quantity": t.get("quantity"), "quantity_approx": bool(t.get("quantity_approx")),
            "subject": t.get("subject") or "", "subject_group": t.get("subject_group") or "unknown",
            "subject_group_zh": codebook.subject_group_zh(t.get("subject_group")),
            "location": t.get("location") or "", "zone_code": t.get("zone_code") or 0,
            "zone_label": codebook.zone_label(t.get("zone_code"), t.get("location") or ""),
            "time_phrase": t.get("time_phrase") or "", "deadline_at": t.get("deadline_at"),
            "deadline_text": t.get("deadline_text") or "", "summary": t.get("summary") or "",
            "wire_summary": v["summary"] if v else build_summary(t, self.letter),
            "wire_flags": flag_names(v["flags"]) if v else [],
            "transcript": t.get("transcript") or "", "evidence": t.get("evidence") or {},
            "field_sources": t.get("field_sources") or {}, "flags": t.get("flags") or [],
            "missing_info": t.get("missing_info") or [], "dup_candidates": dups,
            "report_count": t.get("report_count") or 1, "merged_into": t.get("merged_into"),
            "ai_suggestion": t.get("ai_suggestion"), "clerk_edited": t.get("clerk_edited") or [],
            "created_wall": t.get("created_wall"), "confirmed_wall": t.get("confirmed_wall"),
            "confirmed_by": t.get("confirmed_by"), "eoc_status": t.get("eoc_status") or 0,
            "eoc_status_zh": codebook.eoc_status_zh(t.get("eoc_status") or 0),
            "delivered_wall": t.get("delivered_wall"), "sent_once": bool(t.get("sent_once")),
            "alert_sent": bool(t.get("alert_sent")), "household_id": t.get("household_id"),
            "escalated": bool(t.get("escalated_wall")), "audio_seconds": t.get("audio_seconds") or 0,
            "wire_state": self.wire_state(t),
        }

    def state(self):
        now = time.time()
        drafts = [self.ticket_view(t, now) for t in self.store.drafts()]
        board, hidden = [], 0
        for t in self.store.board():
            if codebook.is_hidden(t.get("category")):
                hidden += 1
                continue
            board.append(self.ticket_view(t, now))
        counts = {"p1": sum(1 for t in board if t["priority"] == 1 and t["status"] == "confirmed"),
                  "p2": sum(1 for t in board if t["priority"] == 2 and t["status"] == "confirmed"),
                  "p3": sum(1 for t in board if t["priority"] == 3 and t["status"] == "confirmed"),
                  "drafts": len(drafts), "hidden": hidden}
        recent_closed = []
        for t in self.store.query("SELECT seq FROM tickets WHERE status IN ('discarded','merged') "
                                  "ORDER BY seq DESC LIMIT 5"):
            tt = self.store.get(t["seq"])
            if tt:
                recent_closed.append(self.ticket_view(tt, now))
        sender = {}
        if self.sender is not None:
            try:
                sender = self.sender.stats()
            except Exception:
                sender = {}
        data_up = int(sender.get("data_bytes_up") or 0)
        voice = int(sender.get("voice_equiv_bytes") or 0)
        census = self.store.census()
        b = self.health.battery
        census["battery"] = None if b is None else round(b["percent"])
        rec = self.recovered if now - self.recovered_wall < 120 else None
        return {
            "now": now,
            "desk": {"letter": self.letter, "desk_id": self.cfg["desk_id"], "shelter_name": self.cfg.get("shelter_name"),
                     "clerks": self.cfg.get("clerks") or [], "backup_channel": self.cfg.get("backup_channel") or {},
                     "privacy_view_default": bool(self.cfg["privacy"].get("privacy_view_default", True)),
                     "mock": self.mock, "boot_id": self.boot_id,
                     "profiles": list(PROFILES)},
            "health": self.health.status(),
            "drafts": drafts,
            "board": {"tickets": board, "counts": counts, "hidden_count": hidden},
            "recent_closed": recent_closed,
            "jobs": self.pipeline.recent_jobs(),
            "backlog": self.pipeline.backlog(),
            "sender": sender,
            "emulator": self.emu.stats(),
            "bytes": {"data_bytes_up": data_up, "voice_equiv_bytes": voice,
                      "ratio": round(voice / data_up, 1) if data_up else None,
                      "bytes_up": int(sender.get("bytes_up") or 0), "bytes_down": int(sender.get("bytes_down") or 0),
                      "typed_nominal_s": sender.get("typed_nominal_s", 10)},
            "census": census,
            "banners": list(self.timers.banners) if self.timers else [],
            "recovered": rec,
        }


def create_app(cfg, mock=False, desk=None, **kw):
    if desk is None:
        desk = Desk(cfg, mock=mock, **kw)
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    app.config["JSON_AS_ASCII"] = False
    try:
        app.json.ensure_ascii = False
    except Exception:
        pass
    app.desk = desk

    @app.errorhandler(Conflict)
    def _conflict(e):
        return jsonify(e.to_dict()), getattr(e, "http_status", 409)

    @app.errorhandler(413)
    def _too_big(e):
        return err("too_large", "上傳內容超過 5 MB", "Body larger than 5 MB", 413)

    @app.errorhandler(404)
    def _nf(e):
        return err("not_found", "找不到", "Not found", 404)

    def body():
        return request.get_json(silent=True) or {}

    def clerk_of(b=None):
        b = b if b is not None else body()
        return str(b.get("clerk") or request.args.get("clerk") or "")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "boot_id": desk.boot_id})

    @app.get("/")
    def index():
        return send_from_directory(str(STATIC / "desk"), "index.html")

    @app.get("/api/state")
    def state():
        return jsonify(desk.state())

    @app.get("/api/codebook")
    def codebook_route():
        cb = codebook.load()
        return jsonify({"categories": cb["categories"], "items": cb["items"],
                        "subject_groups": cb["subject_groups"], "eoc_status": cb["eoc_status"]})

    @app.post("/api/checkin")
    def checkin():
        b = body()
        try:
            size = int(b.get("size") or 0)
        except (TypeError, ValueError):
            return err("bad_size", "人數格式錯誤", "Bad household size")
        if size <= 0:
            return err("bad_size", "請填人數", "Household size is required")
        h = dict(b)
        h["size"] = size
        hid, seqs = desk.pipeline.checkin(h, clerk_of(b))
        return jsonify({"household_id": hid, "drafts": seqs})

    @app.post("/api/checkout/<int:hid>")
    def checkout(hid):
        desk.store.checkout(hid, clerk_of())
        return jsonify({"ok": True})

    @app.get("/api/households")
    def households():
        hs = desk.store.households()
        for h in hs:
            h["zone_label"] = codebook.zone_label(h.get("zone_code"), h.get("location") or "")
        return jsonify({"households": hs})

    @app.post("/api/intake/audio")
    def intake_audio():
        data = request.get_data(cache=False)
        if not data:
            return err("empty", "沒有錄音資料", "No audio body")
        hh = request.args.get("household")
        mock_text = request.args.get("mock_text") or request.headers.get("X-Mock-Text")
        if mock_text and not desk.mock:
            mock_text = None
        job_id = desk.pipeline.submit_audio(data, clerk_of({}), mock_text=mock_text,
                                            household_id=int(hh) if hh and hh.isdigit() else None)
        return jsonify({"job_id": job_id})

    @app.post("/api/intake/text")
    def intake_text():
        b = body()
        text = str(b.get("text") or "").strip()
        if not text:
            return err("empty", "請輸入內容", "Text is empty")
        if len(text) > 2000:
            return err("too_long", "文字太長（上限2000字）", "Text longer than 2000 characters")
        hid = b.get("household_id")
        job_id = desk.pipeline.submit_text(text, clerk_of(b), household_id=int(hid) if hid else None)
        return jsonify({"job_id": job_id})

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        j = desk.pipeline.get_job(job_id)
        if j is None:
            return err("not_found", "找不到工作", "Job not found", 404)
        j.pop("_stage_t", None)
        return jsonify(j)

    @app.post("/api/tickets/<int:seq>/<action>")
    def ticket_action(seq, action):
        b = body()
        clerk = clerk_of(b)
        s = desk.store
        if action == "update":
            fields = dict(b.get("fields") or {})
            if "time_phrase" in fields and "deadline_at" not in fields:
                from .triage import parse_deadline
                d_at, d_text = parse_deadline(fields["time_phrase"] or "", time.time())
                fields["deadline_at"], fields["deadline_text"] = d_at, d_text
            t = s.update_fields(seq, fields, clerk, b.get("expected_version"))
        elif action == "confirm":
            fields = dict(b.get("fields") or {})
            if "time_phrase" in fields and "deadline_at" not in fields:
                from .triage import parse_deadline
                d_at, d_text = parse_deadline(fields["time_phrase"] or "", time.time())
                fields["deadline_at"], fields["deadline_text"] = d_at, d_text
            t = s.confirm(seq, fields, b.get("priority"), clerk, b.get("reason"))
        elif action == "worried":
            t = s.raise_worried(seq, clerk)
        elif action == "emergency_action":
            t = s.log_emergency_action(seq, str(b.get("action") or ""), clerk)
        elif action == "discard":
            t = s.discard(seq, clerk, b.get("reason"))
        elif action == "restore":
            t = s.restore(seq, clerk)
        elif action == "merge":
            target = b.get("target")
            if target is None:
                return err("no_target", "請選擇要合併的單號", "Merge target missing")
            s.merge(seq, int(target), clerk)
            t = s.get(seq)
        elif action == "resolve":
            t = s.resolve(seq, clerk)
        elif action == "not_duplicate":
            t = s.dismiss_duplicates(seq, clerk)
        elif action == "refine":
            if s.get(seq) is None:
                return err("not_found", "找不到單號", "Ticket not found", 404)
            return jsonify({"job_id": desk.pipeline.submit_refine(seq, clerk)})
        else:
            return err("bad_action", "不明的動作", f"Unknown action {action}", 404)
        return jsonify({"ok": True, "ticket": desk.ticket_view(t, time.time())})

    @app.post("/api/link/profile")
    def link_profile():
        name = str(body().get("name") or "").upper()
        if name not in PROFILES:
            return err("bad_profile", "不明的網路設定", f"Unknown profile {name}")
        try:
            res = desk.emu.set_profile(name)
        except Exception as e:
            return err("emulator_unreachable", "連不到網路模擬器", f"Link emulator unreachable: {e}", 502)
        if desk.sender is not None:
            try:
                desk.sender.reset_probe()
            except Exception:
                pass
        return jsonify({"ok": True, "emulator": res})

    @app.post("/api/mode")
    def set_mode():
        m = body().get("mode")
        m = None if m in (None, "", "AUTO", "auto") else str(m).upper()
        try:
            desk.health.set_forced(m)
        except ValueError:
            return err("bad_mode", "不明的模式", f"Unknown mode {m}")
        return jsonify({"ok": True, "health": desk.health.status()})

    @app.post("/api/mic")
    def set_mic():
        desk.health.set_mic(bool(body().get("ok")))
        return jsonify({"ok": True, "mode": desk.health.mode()})

    @app.get("/export.csv")
    def export_csv():
        now = time.time()
        rows = [r for r in (export_row(t, now, desk.letter) for t in desk.store.board()) if r]
        buf = io.StringIO()
        fields = ["code", "priority", "status", "category", "item", "quantity", "location", "summary",
                  "deadline", "eoc_status", "reports"]
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        data = "﻿" + buf.getvalue()  # BOM so Excel on zh-TW Windows reads UTF-8
        fname = time.strftime("darkshelter-%Y%m%d-%H%M.csv")
        return Response(data.encode("utf-8"), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={fname}"})

    return app


def main(argv=None):
    ap = argparse.ArgumentParser(description="DarkShelter desk")
    ap.add_argument("--config", default=None)
    ap.add_argument("--mock", action="store_true", help="mock ASR and LLM (no models)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app = create_app(cfg, mock=args.mock)
    port = int(cfg["ports"]["desk_http"])
    print(f"[desk] http://127.0.0.1:{port} mock={args.mock} boot={app.desk.boot_id} "
          f"recovered={app.desk.recovered}", flush=True)
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()

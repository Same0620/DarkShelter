"""EOC process: receiver thread (UDP eoc_udp) + board UI (HTTP eoc_http), own SQLite.

python -m darkshelter.eoc_app --config PATH
Routes: GET /healthz, GET /, GET /api/state, POST /api/tickets/<desk_id>/<seq>/status {status}
"""
import argparse
import logging
import mimetypes
import sys
import threading
import time
from pathlib import Path

# The Windows registry can map .js/.css to text/plain: fix before Flask() exists.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from . import codebook  # noqa: E402
from .config import db_path, eoc_keys, load_config  # noqa: E402
from .net.eoc_store import STATUS_NAMES, EocConflict, EocStore  # noqa: E402
from .net.receiver import Receiver  # noqa: E402
from .wireview import (F_APPROX, F_CLOSED, F_EMERGENCY, F_ESCALATED, F_EXTERNAL, F_MULTI,  # noqa: E402
                       F_SENSITIVE, F_UNVERIFIED)

STATIC = Path(__file__).resolve().parent / "static" / "eoc"


def _letters(cfg):
    letters = {int(cfg.get("desk_id", 7)): cfg.get("desk_letter", "B")}
    for k, v in ((cfg.get("eoc") or {}).get("desk_letters") or {}).items():
        try:
            letters[int(k)] = str(v)
        except (TypeError, ValueError):
            pass
    return letters


def ticket_row(t, now, letters):
    flags = int(t.get("flags") or 0)
    cat = codebook.cat(t.get("category"))
    it = codebook.item(t.get("item_code"))
    letter = letters.get(int(t["desk_id"]), f"D{t['desk_id']}")
    zone = int(t.get("zone") or 0)
    created = t.get("created_est_wall")
    deadline = t.get("deadline_est_wall")
    status = int(t.get("status") or 0)
    return {
        "desk_id": t["desk_id"], "seq": t["seq"], "code": f"{letter}-{int(t['seq']):03d}", "desk_letter": letter,
        "version": t.get("version"), "level": t.get("level"), "priority": t.get("priority"),
        "category": t.get("category"), "category_zh": cat["zh"].split("（")[0], "category_en": cat["en"],
        "icon": cat.get("icon", ""), "item_code": t.get("item_code"), "item_zh": it["zh"] if it else "",
        "quantity": t.get("quantity") or 0, "approx": bool(flags & F_APPROX),
        "zone": zone, "external": zone == codebook.ZONE_EXTERNAL,
        "zone_label": "外部地點" if zone == codebook.ZONE_EXTERNAL else codebook.zone_label(zone),
        "summary": t.get("summary") or "",
        "unverified": bool(flags & F_UNVERIFIED), "escalated": bool(flags & F_ESCALATED),
        "sensitive": bool(flags & F_SENSITIVE), "protection": int(t.get("category") or 0) == codebook.PROTECTION,
        "closed": bool(flags & F_CLOSED), "emergency": bool(flags & F_EMERGENCY), "multi": bool(flags & F_MULTI),
        "flags": flags,
        "age_min": int(max(0, (now - created) // 60)) if created else None,
        "latency_min": t.get("age_min_at_rx"),
        "deadline_h_left": round((deadline - now) / 3600.0, 1) if deadline else None,
        "rx_wall": t.get("rx_wall"), "first_rx_wall": t.get("first_rx_wall"),
        "status": status, "status_zh": STATUS_NAMES.get(status, ("", ""))[0],
        "status_en": STATUS_NAMES.get(status, ("", ""))[1], "dup_count": t.get("dup_count") or 0,
    }


def build_state(cfg, store, receiver=None):
    now = store.clock()
    letters = _letters(cfg)
    tickets = [ticket_row(t, now, letters) for t in store.board()]
    tickets.sort(key=lambda r: (r["closed"] or r["status"] == 3, r["priority"] or 9, r["first_rx_wall"] or 0))
    desks = []
    for d in store.desks():
        desks.append({"desk_id": d["desk_id"], "letter": letters.get(int(d["desk_id"]), f"D{d['desk_id']}"),
                      "last_seen": d.get("last_seen"),
                      "last_seen_s": round(now - d["last_seen"], 1) if d.get("last_seen") else None,
                      "census": d.get("census") or {}, "heartbeats": d.get("heartbeats") or 0,
                      "boot_id": d.get("boot_id")})
    stats = store.stats()
    rx = None
    if receiver is not None:
        try:
            rx = receiver.stats()
            rx["alive"] = receiver.is_alive()
        except Exception:
            rx = None
    open_rows = [t for t in tickets if not t["closed"] and t["status"] != 3]
    return {"now": now, "shelter": cfg.get("shelter_name", ""), "tickets": tickets, "desks": desks,
            "stats": stats, "receiver": rx,
            "counts": {"p1": sum(1 for t in open_rows if t["priority"] == 1),
                       "p2": sum(1 for t in open_rows if t["priority"] == 2),
                       "p3": sum(1 for t in open_rows if t["priority"] == 3),
                       "open": len(open_rows), "total": len(tickets)}}


def create_app(cfg, eoc_store, receiver=None):
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static/eoc")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    app.config["DS_RECEIVER"] = receiver
    try:
        app.json.ensure_ascii = False
    except Exception:
        pass

    def _err(error, zh, en, code):
        return jsonify({"error": error, "zh": zh, "en": en}), code

    @app.get("/healthz")
    def healthz():
        r = app.config.get("DS_RECEIVER")
        return jsonify({"ok": True, "receiver_alive": bool(r is not None and r.is_alive())})

    @app.get("/")
    def index():
        resp = send_from_directory(str(STATIC), "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/state")
    def state():
        return jsonify(build_state(cfg, eoc_store, app.config.get("DS_RECEIVER")))

    @app.post("/api/tickets/<int:desk_id>/<int:seq>/status")
    def set_status(desk_id, seq):
        body = request.get_json(silent=True) or {}
        try:
            sseq = eoc_store.set_status(desk_id, seq, body.get("status"))
        except EocConflict as e:
            return jsonify(e.to_dict()), e.http_status
        return jsonify({"ok": True, "status": int(body.get("status")), "status_seq": sseq})

    @app.errorhandler(404)
    def not_found(_e):
        return _err("not_found", "找不到", "Not found", 404)

    @app.errorhandler(413)
    def too_big(_e):
        return _err("too_large", "資料太大", "Request body too large", 413)

    @app.errorhandler(500)
    def server_error(_e):
        return _err("server_error", "伺服器錯誤", "Server error", 500)

    return app


def _watchdog(app, store, keys, port, mtu, stop_evt):
    """Restart the receiver only if its thread has died (never a second live receiver)."""
    while not stop_evt.wait(2.0):
        try:
            r = app.config.get("DS_RECEIVER")
            if r is not None and not r.is_alive():
                try:
                    r.stop(join=False)
                except Exception:
                    pass
                time.sleep(0.2)
                nr = Receiver(store, keys, port=port, mtu=mtu)
                nr.start()
                app.config["DS_RECEIVER"] = nr
                store.log_event("receiver_restarted", {"port": port})
                print("eoc: receiver thread restarted", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"eoc watchdog_error: {e!r}", file=sys.stderr, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m darkshelter.eoc_app", description="DarkShelter EOC board")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    store = EocStore(db_path(cfg, "eoc_db"))
    keys = eoc_keys(cfg)
    port = int(cfg["ports"]["eoc_udp"])
    mtu = int(cfg["net"]["mtu"])
    try:
        receiver = Receiver(store, keys, port=port, mtu=mtu)
        receiver.start()
    except OSError as e:
        print(f"eoc: cannot bind UDP {port}: {e}", file=sys.stderr, flush=True)
        return 2
    app = create_app(cfg, store, receiver)
    stop_evt = threading.Event()
    threading.Thread(target=_watchdog, args=(app, store, keys, port, mtu, stop_evt), name="eoc-watchdog",
                     daemon=True).start()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    http_port = int(cfg["ports"]["eoc_http"])
    print(f"eoc ready: http://127.0.0.1:{http_port}  udp 127.0.0.1:{receiver.port}", flush=True)
    try:
        app.run(host="127.0.0.1", port=http_port, threaded=True, use_reloader=False)
    finally:
        stop_evt.set()
        r = app.config.get("DS_RECEIVER")
        if r is not None:
            r.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

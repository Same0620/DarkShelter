"""Start the DarkShelter demo: link emulator, EOC and desk (SPEC §2).

python run_demo.py [--mock] [--config path] [--no-browser] [--reset]
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from darkshelter.config import load_config, resolve_path  # noqa: E402

CHILDREN = [
    # name, module, extra args, health port key
    ("linkemu", "darkshelter.net.linkemu", [], "emu_control_http"),
    ("eoc", "darkshelter.eoc_app", [], "eoc_http"),
    ("desk", "darkshelter.desk_app", [], "desk_http"),
]


def say(zh, en=""):
    print(f"[run_demo] {zh}" + (f" / {en}" if en else ""), flush=True)


def port_free(port, kind):
    """Bind-test a port on 127.0.0.1 (SO_REUSEADDR on POSIX only, never on Windows)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
    try:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        s.close()
    if kind == "tcp":
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(0.3)
        try:
            c.connect(("127.0.0.1", port))
            return False  # something is listening
        except OSError:
            return True
        finally:
            c.close()
    return True


def check_ports(cfg):
    p = cfg["ports"]
    wanted = [("desk_http", "tcp"), ("eoc_http", "tcp"), ("emu_control_http", "tcp"), ("emu_udp", "udp"), ("eoc_udp", "udp")]
    taken = [(k, p[k], kind) for k, kind in wanted if not port_free(int(p[k]), kind)]
    return taken


def db_files(path):
    return [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]


def wait_healthz(port, timeout):
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def tail(path, n=20):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=n))
    except OSError:
        return ""


class Child:
    def __init__(self, name, module, args, log_path, cfg_path, mock):
        self.name, self.module, self.args = name, module, list(args)
        self.log_path = log_path
        self.cfg_path = cfg_path
        self.mock = mock
        self.proc = None
        self.restarts = deque()

    def cmd(self):
        c = [sys.executable, "-m", self.module, "--config", str(self.cfg_path)] + self.args
        if self.name == "desk" and self.mock:
            c.append("--mock")
        return c

    def start(self, first=False):
        if first:
            log = open(self.log_path, "wb")
        else:
            log = open(self.log_path, "ab")
        if not first:
            log.write(f"\n===== restart {time.strftime('%H:%M:%S')} =====\n".encode("utf-8"))
            log.flush()
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl+C goes to run_demo only
        self.proc = subprocess.Popen(self.cmd(), cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env, **kw)
        log.close()

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="DarkShelter demo launcher")
    ap.add_argument("--mock", action="store_true", help="no models: mock speech and AI")
    ap.add_argument("--config", default=None, help="config JSON (default: config.json, created on first run)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reset", action="store_true", help="delete BOTH databases before starting")
    ap.add_argument("--ready-timeout", type=float, default=60.0)
    args = ap.parse_args(argv)

    def _stop_signal(signum, frame):
        raise KeyboardInterrupt
    # Ctrl+Break (Windows) and SIGTERM stop cleanly like Ctrl+C, so children never become orphans.
    for sig_name in ("SIGBREAK", "SIGTERM"):
        if hasattr(signal, sig_name):
            try:
                signal.signal(getattr(signal, sig_name), _stop_signal)
            except (ValueError, OSError):
                pass

    cfg_path = Path(args.config).resolve() if args.config else ROOT / "config.json"
    cfg = load_config(cfg_path)  # creates config.json + HMAC key on first run
    desk_db = resolve_path(cfg["paths"]["desk_db"])
    eoc_db = resolve_path(cfg["paths"]["eoc_db"])
    run_dir = desk_db.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    taken = check_ports(cfg)
    if taken:
        for k, port, kind in taken:
            say(f"連接埠 {port}/{kind.upper()}（{k}）已被占用，可能是上次沒關掉的程式",
                f"port {port}/{kind} ({k}) is in use — usually an orphan from an earlier run")
        say("請關閉舊的 python 程式或改 config 的 ports 後再試", "close the old python processes or change ports in the config")
        return 2

    if args.reset:
        for p in db_files(desk_db) + db_files(eoc_db):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        say("已刪除兩個資料庫", "both databases reset")
    elif desk_db.exists() != eoc_db.exists():
        say("請用 --reset 同時重設兩個資料庫", "use --reset to reset both databases")
        say(f"desk_db={desk_db} exists={desk_db.exists()}  |  eoc_db={eoc_db} exists={eoc_db.exists()}")
        return 3

    children = [Child(n, m, a, run_dir / f"{n}.log", cfg_path, args.mock) for n, m, a, _ in CHILDREN]
    ports = {n: int(cfg["ports"][k]) for n, _, _, k in CHILDREN}
    stopping = False
    try:
        for ch in children:
            ch.start(first=True)
        for ch in children:
            if not wait_healthz(ports[ch.name], args.ready_timeout):
                say(f"{ch.name} 沒有在 {args.ready_timeout:.0f} 秒內就緒", f"{ch.name} not ready")
                print(tail(ch.log_path), flush=True)
                raise SystemExit(4)
        desk_url = f"http://127.0.0.1:{ports['desk']}"
        eoc_url = f"http://127.0.0.1:{ports['eoc']}"
        say(f"已啟動 ready{' (MOCK)' if args.mock else ''}：登記台 desk {desk_url}  |  應變中心 EOC {eoc_url}  |  "
            f"網路模擬 emulator http://127.0.0.1:{ports['linkemu']}/stats")
        say(f"記錄檔 logs: {run_dir}", "Ctrl+C 結束 / Ctrl+C to stop")
        if not args.no_browser:
            webbrowser.open(desk_url)
            webbrowser.open(eoc_url)
        while True:
            time.sleep(1.0)
            for ch in children:
                if stopping or ch.proc.poll() is None:
                    continue
                code = ch.proc.returncode
                say(f"{ch.name} 結束了（exit {code}），最後20行記錄：", f"{ch.name} exited")
                print(tail(ch.log_path), flush=True)
                now = time.monotonic()
                ch.restarts.append(now)
                while ch.restarts and now - ch.restarts[0] > 60:
                    ch.restarts.popleft()
                if len(ch.restarts) > 3:
                    say(f"{ch.name} 一分鐘內重啟超過3次，全部停止。記錄檔：{ch.log_path}",
                        f"{ch.name} restarted >3 times in a minute; stopping. Log: {ch.log_path}")
                    raise SystemExit(5)
                say(f"重新啟動 {ch.name}", f"restarting {ch.name}")
                ch.start()
    except KeyboardInterrupt:
        say("收到 Ctrl+C，停止中", "stopping")
    finally:
        stopping = True
        for ch in reversed(children):
            ch.stop()
        say("已全部停止", "all stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

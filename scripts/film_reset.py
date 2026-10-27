"""One command per take for the one-take demo video: a clean start every time.

    python scripts/film_reset.py            real models (Whisper + Qwen): use this for filming
    python scripts/film_reset.py --mock     no models, for practising the clicks

What it does, in order:
1. Closes the previous take: the demo it started last time (and any other copy of run_demo.py from this folder
   that holds the demo ports), and the two demo Chrome windows.
2. Makes sure Ollama is running, CPU-only (starts the portable ollama.exe if it isn't).
3. Starts `run_demo.py --reset --no-browser` in the background: BOTH databases are deleted.
4. Waits until the desk is in FULL mode (Whisper + Qwen ready; MOCK with --mock) and sets the link to DOWN.
5. Checks in a few households so the headcount at the EOC isn't zero (--households 0 to skip).
6. Opens the desk on the left half and the EOC on the right half of the screen as Chrome app windows. Each has
   its own profile in data/run/, so zoom and microphone permission are remembered between takes.
Prints READY when the take can start. Logs: data/run/film_run_demo.log and data/run/*.log.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from darkshelter.config import load_config, resolve_path  # noqa: E402

try:
    import psutil
except ImportError:  # psutil comes with the requirements; without it we can't find old processes
    psutil = None

RUN = ROOT / "data" / "run"
PID_FILE = RUN / "film_demo.pid"
OLLAMA_URL = "http://127.0.0.1:11434"
CPU_ONLY = {"CUDA_VISIBLE_DEVICES": "-1", "OLLAMA_VULKAN": "0", "GGML_VK_VISIBLE_DEVICES": "-1",
            "OLLAMA_HOST": "127.0.0.1:11434"}
# Seeded check-ins: (family name, people, bed zone, elderly, infants, children) -> 37 people.
HOUSEHOLDS = [("王", 4, "A3", 1, 0, 1), ("林", 3, "A5", 0, 1, 0), ("陳", 2, "B4", 2, 0, 0), ("黃", 5, "B7", 1, 0, 2),
              ("張", 1, "B9", 1, 0, 0), ("李", 3, "C2", 0, 0, 1), ("吳", 4, "C6", 1, 1, 1), ("劉", 2, "C8", 0, 0, 0),
              ("蔡", 6, "D1", 2, 1, 2), ("楊", 3, "D3", 0, 0, 1), ("許", 2, "D5", 1, 0, 0), ("鄭", 2, "D7", 0, 0, 0)]
WIN = os.name == "nt"


def say(msg):
    print(msg, flush=True)


def http(url, body=None, timeout=3):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def alive(url):
    try:
        http(url, timeout=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 1. close the previous take
def kill_tree(pid, graceful_s=0.0):
    if psutil is None:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return
    try:
        p = psutil.Process(pid)
    except psutil.Error:
        return
    procs = [p] + p.children(recursive=True)
    if graceful_s and WIN:  # WM_CLOSE first, so Chrome shuts down cleanly (no "restore pages?" next time)
        subprocess.run(["taskkill", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        psutil.wait_procs([p], timeout=graceful_s)
    for q in procs:
        try:
            q.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)


def ours(proc):
    """A python process running something from this folder (run_demo.py, desk_app, eoc_app, linkemu)."""
    try:
        cmd = " ".join(proc.cmdline())
    except psutil.Error:
        return False
    return str(ROOT).lower() in cmd.lower() or any(k in cmd for k in ("run_demo.py", "darkshelter.desk_app",
                                                                       "darkshelter.eoc_app", "darkshelter.net.linkemu"))


def close_previous_take(cfg):
    if PID_FILE.exists():
        try:
            kill_tree(int(PID_FILE.read_text(encoding="utf-8").strip()))
        except ValueError:
            pass
        PID_FILE.unlink(missing_ok=True)
    if psutil is None:
        return
    ports = {int(p) for p in cfg["ports"].values()}
    killed = set()
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port in ports and c.pid and c.pid not in killed:
            try:
                proc = psutil.Process(c.pid)
                if proc.name().lower().startswith("python") and ours(proc):
                    say(f"  closing an old demo process (pid {c.pid}, port {c.laddr.port})")
                    parent = proc.parent()
                    target = parent.pid if parent and parent.name().lower().startswith("python") and ours(parent) else c.pid
                    kill_tree(target)
                    killed.add(c.pid)
            except psutil.Error:
                pass
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
            if (proc.info["name"] or "").lower() == "chrome.exe" and "film-chrome-" in cmd and "--type=" not in cmd:
                kill_tree(proc.pid, graceful_s=4)
        except psutil.Error:
            pass


# ---------------------------------------------------------------- 2. Ollama
def find_ollama(arg):
    for cand in (arg, os.environ.get("OLLAMA_EXE"), shutil.which("ollama"),
                 str(Path.home() / "ollama-portable" / "ollama.exe")):
        if cand and Path(cand).exists():
            return cand
    return None


def ensure_ollama(arg):
    if alive(OLLAMA_URL + "/api/tags"):
        say("  Ollama: already running")
        return True
    exe = find_ollama(arg)
    if not exe:
        say("  Ollama: NOT running and ollama.exe not found (use --ollama <path>)")
        return False
    log = open(Path(exe).parent / "serve.log", "ab")
    background([exe, "serve"], env={**os.environ, **CPU_ONLY}, stdout=log, stderr=log, cwd=str(Path(exe).parent))
    deadline = time.time() + 40
    while time.time() < deadline:
        if alive(OLLAMA_URL + "/api/tags"):
            say(f"  Ollama: started CPU-only ({exe})")
            return True
        time.sleep(0.5)
    say("  Ollama: started but not answering after 40 s")
    return False


def background(cmd, **kw):
    """Start a long-running process that outlives this script, with no window popping up.

    Windows: a hidden console of its own (CREATE_NO_WINDOW), shared by its children, so no console windows
    appear and closing this terminal doesn't send it a close event. Breakaway from a job (when the terminal
    runs inside one) is tried first.
    """
    kw.setdefault("stdin", subprocess.DEVNULL)
    if not WIN:
        return subprocess.Popen(cmd, start_new_session=True, **kw)
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        return subprocess.Popen(cmd, creationflags=base, **kw)


# ---------------------------------------------------------------- 3-5. demo
def start_demo(args):
    RUN.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "run_demo.py"), "--reset", "--no-browser"]
    if args.mock:
        cmd.append("--mock")
    if args.config:
        cmd += ["--config", args.config]
    log = open(RUN / "film_run_demo.log", "wb")
    # FOR_DISABLE_CONSOLE_CTRL_HANDLER: Whisper's maths library (Intel runtime) otherwise aborts the desk on a
    # console close event ("forrtl: error (200): program aborting due to window-CLOSE event").
    env = {**os.environ, "PYTHONUTF8": "1", "FOR_DISABLE_CONSOLE_CTRL_HANDLER": "1"}
    p = background(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
    PID_FILE.write_text(str(p.pid), encoding="utf-8")
    return p


def wait_ready(p, desk, eoc, want, timeout):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        if p.poll() is not None:
            tail = (RUN / "film_run_demo.log").read_bytes()[-1500:].decode("utf-8", "replace")
            say(f"  run_demo stopped (exit {p.returncode}). Log tail:\n{tail}")
            return False
        try:
            st = http(desk + "/api/state")
            h = st["health"]
            now = (h.get("mode"), h.get("reason"))
            if now != last:
                say(f"  desk: {now[0]} ({now[1]}) after {time.time() - t0:.0f} s")
                last = now
            if h.get("mode") in want and alive(eoc + "/api/state"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    say(f"  desk not ready after {timeout:.0f} s: last state {last}")
    return False


def seed_households(desk, n):
    people = 0
    for fam, size, zone, eld, inf, kids in HOUSEHOLDS[:n]:
        http(desk + "/api/checkin", {"family_name": fam, "size": size, "zone": zone, "elderly": eld, "infants": inf,
                                      "children": kids, "screening": {}, "clerk": "值班人員A"}, timeout=10)
        people += size
    return people


# ---------------------------------------------------------------- 6. windows
def work_area():
    """(left, top, width, height) of the primary screen's work area in Windows' scaled pixels (= Chrome's units)."""
    if not WIN:
        return 0, 0, 1920, 1040

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def find_chrome():
    for cand in (os.environ.get("CHROME_EXE"),
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                 shutil.which("chrome"), shutil.which("google-chrome")):
        if cand and Path(cand).exists():
            return cand
    return None


def open_windows(desk_url, eoc_url):
    chrome = find_chrome()
    if not chrome:
        say("  Chrome not found: open the two URLs yourself (see FILMING.md)")
        return
    left, top, w, h = work_area()
    half = w // 2
    for name, url, x, width in (("desk", desk_url, left, half), ("eoc", eoc_url, left + half, w - half)):
        subprocess.Popen([chrome, f"--user-data-dir={RUN / ('film-chrome-' + name)}", f"--app={url}",
                          f"--window-position={x},{top}", f"--window-size={width},{h}", "--no-first-run",
                          "--no-default-browser-check", "--hide-crash-restore-bubble"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    say(f"  windows: desk {desk_url} left half, EOC {eoc_url} right half ({w}x{h} work area)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="clean start for one take of the demo video")
    ap.add_argument("--mock", action="store_true", help="no models (practice)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--ollama", default=None, help="path to ollama.exe if it is not found by itself")
    ap.add_argument("--households", type=int, default=len(HOUSEHOLDS), help="check-ins to seed (0..12)")
    ap.add_argument("--no-windows", action="store_true", help="don't open the two Chrome windows")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--wait", action="store_true", help="stay running after READY; Ctrl+C stops the demo")
    ap.add_argument("--stop", action="store_true", help="only close the demo and its two windows (Ollama keeps running)")
    args = ap.parse_args(argv)
    t0 = time.time()
    cfg = load_config(Path(args.config).resolve() if args.config else None)
    ports = cfg["ports"]
    desk, eoc = f"http://127.0.0.1:{ports['desk_http']}", f"http://127.0.0.1:{ports['eoc_http']}"
    say("1/5 closing the previous take")
    close_previous_take(cfg)
    if args.stop:
        say("stopped: the demo and its windows are closed (Ollama keeps running)")
        return 0
    say("2/5 Ollama")
    if not args.mock and not ensure_ollama(args.ollama):
        say("NOT READY: Ollama isn't running (see FILMING.md)")
        return 1
    say("3/5 starting the demo with empty databases")
    p = start_demo(args)
    say("4/5 waiting for the desk")
    if not wait_ready(p, desk, eoc, ("MOCK", "FULL") if args.mock else ("FULL",), args.timeout):
        say("NOT READY (see data/run/film_run_demo.log and data/run/desk.log)")
        return 1
    http(desk + "/api/link/profile", {"name": "DOWN"})
    st = http(desk + "/api/state")
    people = seed_households(desk, max(0, min(args.households, len(HOUSEHOLDS)))) if args.households else 0
    say(f"5/5 link {st['sender'].get('link_state')} (profile DOWN), {people} people checked in, "
        f"desk {st['health']['mode']}, Ollama {'not used (mock)' if args.mock else 'running'}")
    if not args.no_windows:
        open_windows(f"http://127.0.0.1:{ports['desk_http']}/", f"http://localhost:{ports['eoc_http']}/")
    say(f"READY in {time.time() - t0:.0f} s - desk {desk}  EOC http://localhost:{ports['eoc_http']}")
    if args.wait:
        say("(--wait: the demo runs until you press Ctrl+C here)")
        try:
            while p.poll() is None:
                time.sleep(1)
            say(f"run_demo stopped by itself (exit {p.returncode})")
        except KeyboardInterrupt:
            kill_tree(p.pid)
            say("demo stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

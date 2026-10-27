"""UI smoke test (SPEC §11): run_demo.py --mock on test ports + headless Chromium with a fake mic.

1. Start the three processes in mock mode on free ports with fresh temp DBs (via run_demo.py).
2. Type 「阿嬤明天早上要洗腎」, confirm, switch to GOOD, see the row on the EOC page.
3. Click 錄音, then 停止 at 2 s; the job reaches done with duration_s >= 1.5.
4. Save desk and EOC screenshots to eval/results/screens/.
Skipped with DS_FAST=1 or when playwright is not installed.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "eval" / "results" / "screens"

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:
    HAVE_PLAYWRIGHT = False


def free_port(kind="tcp"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_healthz(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


@unittest.skipIf(os.environ.get("DS_FAST") == "1", "slow UI test (DS_FAST=1)")
@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
class UiSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tcp = set()
        while len(tcp) < 3:
            tcp.add(free_port("tcp"))
        udp = set()
        while len(udp) < 2:
            udp.add(free_port("udp"))
        desk, eoc, ctl = sorted(tcp)
        emu_udp, eoc_udp = sorted(udp)
        self.ports = {"desk_http": desk, "eoc_http": eoc, "emu_control_http": ctl, "emu_udp": emu_udp,
                      "eoc_udp": eoc_udp}
        cfg = {"ports": self.ports,
               "paths": {"desk_db": str(Path(self.tmp.name) / "desk.db"), "eoc_db": str(Path(self.tmp.name) / "eoc.db")},
               "net": {"hmac_key_hex": os.urandom(32).hex(), "heartbeat_s": 1},
               "triage": {"unconfirmed_p1_alert_s": 0}}
        self.cfg_path = Path(self.tmp.name) / "config.json"
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.log = open(Path(self.tmp.name) / "run_demo.out", "wb")
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "run_demo.py"), "--mock", "--no-browser", "--reset",
                                      "--config", str(self.cfg_path)], cwd=str(ROOT), stdout=self.log,
                                     stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"}, **kw)
        ok = wait_healthz(desk, 90) and wait_healthz(eoc, 30) and wait_healthz(ctl, 30)
        if not ok:
            self.tearDown()
            self.fail("run_demo did not become ready: " + self._log_text())

    def _log_text(self):
        try:
            return (Path(self.tmp.name) / "run_demo.out").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def tearDown(self):
        if getattr(self, "proc", None) and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.send_signal(signal.SIGINT)
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if getattr(self, "log", None):
            self.log.close()
        self.tmp.cleanup()

    def test_desk_to_eoc_and_record(self):
        SCREENS.mkdir(parents=True, exist_ok=True)
        desk_url = f"http://127.0.0.1:{self.ports['desk_http']}"
        eoc_url = f"http://127.0.0.1:{self.ports['eoc_http']}"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--use-fake-ui-for-media-stream",
                                                              "--use-fake-device-for-media-stream",
                                                              "--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, locale="zh-TW")
            page = ctx.new_page()
            console = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(desk_url)
            page.wait_for_selector("#mode-badge:not(:empty)", timeout=15000)
            # 2. typed request -> draft -> confirm -> GOOD -> EOC row
            page.fill("#text-input", "阿嬤明天早上要洗腎")
            page.press("#text-input", "Enter")
            card = page.locator(".draft-card", has_text="洗腎").first
            card.wait_for(timeout=20000)
            card.locator(".btn-confirm").click()
            page.wait_for_function("() => [...document.querySelectorAll('#board .brow')].some(r => r.textContent.includes('B-'))",
                                   timeout=15000)
            page.click("#profile-GOOD")
            eoc = ctx.new_page()
            eoc.goto(eoc_url)
            eoc.wait_for_function("() => document.body.innerText.includes('洗腎')", timeout=45000)
            # desk sees the delivery (✓) once the ack is back
            page.wait_for_function("() => document.querySelector('#board').innerText.match(/✓|✓✓|已送達/)", timeout=30000)
            # 3. record 2 s with the fake microphone
            page.click("#record-btn")
            page.wait_for_timeout(2000)
            page.click("#record-btn")
            page.wait_for_function("() => { const el = document.querySelector('#job-line');"
                                   " return el && el.dataset.stage === 'done' && el.innerText.includes('錄音'); }",
                                   timeout=30000)
            job_id = page.get_attribute("#job-line", "data-job")
            job = http_json(f"{desk_url}/api/jobs/{job_id}")
            self.assertEqual(job["status"], "done", job)
            self.assertGreaterEqual(job["duration_s"], 1.5, job)
            page.wait_for_timeout(500)
            page.screenshot(path=str(SCREENS / "desk.png"), full_page=True)
            eoc.reload()
            eoc.wait_for_function("() => document.body.innerText.includes('洗腎')", timeout=15000)
            eoc.screenshot(path=str(SCREENS / "eoc.png"), full_page=True)
            errors = [c for c in console if c.startswith("pageerror")]
            self.assertEqual(errors, [], "\n".join(console))
            browser.close()
        self.assertTrue((SCREENS / "desk.png").exists())
        self.assertTrue((SCREENS / "eoc.png").exists())


if __name__ == "__main__":
    unittest.main()

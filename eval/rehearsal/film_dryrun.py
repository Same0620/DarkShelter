"""Dry run of the one-take filming sequence in FILMING.md, in real Google Chrome, typed input, with timings.

Start from a clean reset first:  python scripts/film_reset.py --no-windows
then:                             python eval/rehearsal/film_dryrun.py [--run N]
Each step is timed from the presenter's action to the result being visible on screen (or, for the EOC headcount,
in the EOC's state, which its page shows on the next 1 s poll). Writes eval/results/rehearsal/film_dryrun_<N>.md
plus a screenshot per step. Step 4 stops Ollama with the same command FILMING.md gives.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # optional, only this rehearsal script needs it
    sys.exit("playwright is not installed: pip install playwright (Google Chrome must be installed too)")

for _s in (sys.stdout, sys.stderr):  # cp950 consoles can't print the board's emoji
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results" / "rehearsal"
DESK, EOC = "http://127.0.0.1:8000", "http://localhost:8100"
S1 = "阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了"
S2 = "我爸還在中正路三巷的家裡沒出來"
S4 = "我沒有胸痛，只是有點累"
STOP_OLLAMA = ["powershell", "-NoProfile", "-Command", "Stop-Process -Name ollama,llama-server -Force"]
rows, notes = [], []


def api(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def timed(label, fn):
    t0 = time.time()
    detail = fn()
    dt = time.time() - t0
    rows.append((label, dt, detail or ""))
    print(f"  {dt:6.1f} s  {label}  {detail or ''}", flush=True)
    return detail


def wait_until(pred, timeout=90, every=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError("condition not met")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=1)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    shot = lambda page, name: page.screenshot(path=str(OUT / f"film_run{args.run}_{name}.png"))  # noqa: E731
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        # 1920x1080 at 100 %: each half is 960 px wide. Desk at 80 % zoom (3 columns need >= 1100 CSS px),
        # EOC at 100 %. Emulated as CSS viewport = window / zoom, rendered at the zoom factor.
        dctx = browser.new_context(viewport={"width": 1200, "height": 1250}, device_scale_factor=0.8)
        ectx = browser.new_context(viewport={"width": 960, "height": 1000}, device_scale_factor=1.0)
        desk, eoc = dctx.new_page(), ectx.new_page()
        desk.goto(DESK + "/")
        eoc.goto(EOC + "/")
        desk.wait_for_selector("#mode-badge:not(:empty)", timeout=20000)
        mode0 = desk.inner_text("#mode-badge")
        link0 = desk.inner_text("#link-badge")
        notes.append(f"start: mode badge 「{mode0}」, link badge 「{link0}」, queue line 「{desk.inner_text('#queue-line')}」")

        def cards(word=None):
            return [c for c in desk.locator(".draft-card").all() if word is None or word in c.text_content()]

        def board_text():
            return " | ".join(r.text_content() for r in desk.locator("#board .brow").all())

        # ---- step 1: link DOWN, sentence 1 -> two P1 drafts -> confirm both
        print(f"run {args.run}", flush=True)
        desk.fill("#text-input", S1)
        timed("1a type sentence 1 -> two P1 drafts on screen", lambda: (desk.press("#text-input", "Enter"), wait_until(
            lambda: len([c for c in cards() if "p1" in (c.get_attribute("class") or "")]) >= 2 and
            "洗腎" in " ".join(c.text_content() for c in cards()) and "胰島素" in " ".join(c.text_content() for c in cards())),
            "drafts: " + ", ".join(c.locator(".code").first.inner_text() for c in cards()))[2])

        def confirm_all_p1():
            for c in [c for c in cards() if "p1" in (c.get_attribute("class") or "")]:
                c.locator(".btn-confirm").click()
            wait_until(lambda: board_text().count("B-00") >= 2)
            return "board: " + board_text()[:120]
        timed("1b confirm both P1s -> both on the board, queued", confirm_all_p1)
        shot(desk, "1_desk")

        # ---- step 2: rescue -> banner -> 119 unreachable -> confirm
        desk.fill("#text-input", S2)
        timed("2a type sentence 2 -> rescue draft + red banner", lambda: (desk.press("#text-input", "Enter"), wait_until(
            lambda: cards("中正路") and "緊急" in desk.inner_text("#banners")), "banner: " + desk.inner_text("#banners")[:60])[2])

        def press_119():
            cards("中正路")[0].locator("button[data-em='119_unreachable']").click()
            wait_until(lambda: "備援通報" in (desk.locator(".toast").last.text_content() if desk.locator(".toast").count() else ""))
            return "toast: " + desk.locator(".toast").last.text_content()[:70]
        timed("2b tap 119打不通 -> backup-channel toast", press_119)
        shot(desk, "2_desk")

        def confirm_rescue():
            cards("中正路")[0].locator(".btn-confirm").click()
            wait_until(lambda: "中正路" in board_text())
            return "wire summary on the board: 中正路三巷 受困人員" if "中正路三巷 受困人員" in board_text() else board_text()
        timed("2c confirm the rescue -> on the board", confirm_rescue)

        # ---- step 3: LoRa-class link -> headcount, then P1s at the EOC -> 已派遣 -> 🚑 at the desk
        def lora():
            t_click = time.time()
            desk.click("#profile-LORA_RELAY")
            census = wait_until(lambda: (api(EOC + "/api/state")["desks"] or [{}])[0].get("census", {}).get("people"), 90, 0.05)
            t_census = time.time() - t_click
            wait_until(lambda: sum(1 for r in eoc.locator("div.row").all() if "p1" in (r.get_attribute("class") or "")) >= 3, 90, 0.05)
            t_p1 = time.time() - t_click
            return f"headcount {census} at +{t_census:.1f} s, all 3 P1 rows on the EOC page at +{t_p1:.1f} s"
        timed("3a tap LoRa中繼 -> headcount, then the P1s, at the EOC", lora)
        shot(eoc, "3_eoc")

        def dispatch():
            eoc.locator("div.row", has_text="洗腎").first.locator("button[data-status='2']").click()
            wait_until(lambda: any("洗腎" in r.text_content() and "🚑" in r.text_content()
                                   for r in desk.locator("#board .brow").all()), 90)
            return "desk board: " + [r.text_content() for r in desk.locator("#board .brow").all() if "洗腎" in r.text_content()][0][:70]
        timed("3b tap 已派遣 on the dialysis row -> 🚑 on the desk board", dispatch)
        notes.append(f"after step 3: queue line 「{desk.inner_text('#queue-line')}」, byte meter sent 「{desk.inner_text('#bm-data')}」 "
                     f"vs voice notes 「{desk.inner_text('#bm-voice')}」, ratio 「{desk.inner_text('#bm-ratio')}」")
        shot(desk, "3_desk")

        # ---- step 4: stop Ollama -> FAST -> sentence 4: no alarm, ask-back
        def stop_ollama():
            subprocess.run(STOP_OLLAMA, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_until(lambda: "FAST" in desk.inner_text("#mode-badge"), 120, 0.2)
            return "badge: " + desk.inner_text("#mode-badge")
        timed("4a run the stop-Ollama command -> FAST badge", stop_ollama)
        banners_before = desk.inner_text("#banners").count("緊急")
        desk.fill("#text-input", S4)

        def no_alarm():
            desk.press("#text-input", "Enter")
            card = wait_until(lambda: cards("胸痛") or cards("累"), 60)[0]
            txt = card.text_content()
            ok = "現在有胸痛嗎" in txt and "p1" not in (card.get_attribute("class") or "")
            banners_after = desk.inner_text("#banners").count("緊急")
            return (f"{'OK' if ok else 'CHECK'}: card {card.locator('.code').first.inner_text()} "
                    f"class={card.get_attribute('class')}, ask-back shown={'現在有胸痛嗎' in txt}, "
                    f"new emergency banners={banners_after - banners_before}")
        timed("4b type sentence 4 -> draft, no alarm, ask-back shown", no_alarm)
        shot(desk, "4_desk")
        browser.close()
    md = [f"# Filming dry run {args.run} — {time.strftime('%Y-%m-%d %H:%M:%S')} (typed input, real Chrome)", "",
          "| Step | Seconds | What was on screen |", "|---|---|---|"]
    md += [f"| {label} | {dt:.1f} | {detail} |" for label, dt, detail in rows]
    md += ["", *[f"- {n}" for n in notes]]
    (OUT / f"film_dryrun_{args.run}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

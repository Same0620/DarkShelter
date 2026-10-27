"""Run against `python run_demo.py --mock --no-browser --reset`: one full flow in real Google Chrome (not Playwright's Chromium) through 127.0.0.1 and localhost:
type a P1 -> confirm -> link GOOD -> EOC shows it -> click 已派遣 -> the desk shows 已派遣.
Logs every POST with the Origin / Sec-Fetch-Site headers Chrome actually sent, and any refusal.
"""
import json
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # optional, only this rehearsal script needs it
    sys.exit("playwright is not installed: pip install playwright (Google Chrome must be installed too)")

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results" / "rehearsal"
OUT.mkdir(parents=True, exist_ok=True)
FLOWS = [("127.0.0.1", "阿公在C區3床，他的氧氣快用完了", "氧氣"),
         ("localhost", "我媽在A區7床，明天下午要洗腎", "洗腎")]
lines = []


def say(s):
    print(s, flush=True)
    lines.append(s)


def board_has(page, word, extra=""):
    js = ("([w, x]) => [...document.querySelectorAll('#board .brow')]"
          ".some(r => r.textContent.includes(w) && (!x || r.textContent.includes(x)))")
    return page.wait_for_function(js, arg=[word, extra], timeout=60000)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=False)
    say(f"browser: Google Chrome {browser.version} (channel=chrome, its own temporary profile)")
    ok_all = True
    for host, text, word in FLOWS:
        say(f"\n## http://{host}:8000 and http://{host}:8100 — 「{text}」")
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        posts = []

        def on_response(r, posts=posts):
            if r.request.method == "POST":
                h = r.request.all_headers()
                posts.append((r.status, r.url.split("?")[0], h.get("origin"), h.get("sec-fetch-site"),
                              h.get("content-type")))
        ctx.on("response", on_response)
        page = ctx.new_page()
        t0 = time.time()
        page.goto(f"http://{host}:8000")
        page.wait_for_selector("#mode-badge:not(:empty)", timeout=20000)
        page.fill("#text-input", text)
        page.press("#text-input", "Enter")
        card = page.locator(".draft-card", has_text=word).first
        card.wait_for(timeout=30000)
        code = card.locator(".code").first.inner_text()
        prio = "P1" if "p1" in (card.get_attribute("class") or "") else card.get_attribute("class")
        say(f"  draft {code} ({prio}) after {time.time() - t0:.1f} s")
        card.locator(".btn-confirm").click()
        board_has(page, code)
        say(f"  confirmed {code}: on the desk board")
        page.click("#profile-GOOD")
        eoc = ctx.new_page()
        eoc.goto(f"http://{host}:8100")
        eoc.wait_for_function("(c) => [...document.querySelectorAll('div.row')].some(r => r.textContent.includes(c))",
                              arg=code, timeout=60000)
        say(f"  EOC page shows {code} after {time.time() - t0:.1f} s")
        eoc.locator("div.row", has_text=code).first.locator("button[data-status='2']").click()
        board_has(page, code, "已派遣")
        say(f"  desk board shows {code} 已派遣 after {time.time() - t0:.1f} s")
        page.screenshot(path=str(OUT / f"chrome_{host}_desk.png"))
        eoc.screenshot(path=str(OUT / f"chrome_{host}_eoc.png"))
        refused = [p for p in posts if p[0] >= 400]
        ok_all = ok_all and not refused
        for p in posts:
            say(f"  POST {p[0]} {p[1]}  Origin={p[2]}  Sec-Fetch-Site={p[3]}  Content-Type={p[4]}")
        say(f"  refused by the guard: {refused if refused else 'none'}")
        ctx.close()
    browser.close()
    say("\nRESULT: " + ("both addresses work end to end" if ok_all else "SOME REQUESTS WERE REFUSED"))
(OUT / "chrome_flow.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
sys.exit(0 if ok_all else 1)

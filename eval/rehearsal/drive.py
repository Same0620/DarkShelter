"""Dress rehearsal: run the demo sentences through a running desk (real Whisper + Qwen) as text or audio.

1. python run_demo.py --no-browser --reset          (real models; wait for the FULL badge)
2. python eval/rehearsal/drive.py text               (typed)
3. For audio, first make 16 kHz WAVs of sentences.txt with the zh-TW Windows voice (PowerShell):
     Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
     $s.SelectVoice("Microsoft Hanhan Desktop"); $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
       [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
     New-Item -ItemType Directory -Force eval/rehearsal/wav
     foreach ($l in [IO.File]::ReadAllLines("eval/rehearsal/sentences.txt", [Text.Encoding]::UTF8)) {
       $k, $t = $l.Split("|", 2); $s.SetOutputToWaveFile("eval/rehearsal/wav/$k.wav", $f); $s.Speak($t); $s.SetOutputToNull() }
   then restart run_demo with --reset and run: python eval/rehearsal/drive.py audio
Order follows the demo: each request is confirmed as the clerk would, then the daughter repeats request 1.
Writes eval/results/rehearsal/rehearsal_<mode>.md and prints the same.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results" / "rehearsal"
MODE = sys.argv[1]
DESK = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
SENT = [ln.split("|", 1) for ln in (HERE / "sentences.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
CLERK = "值班人員A"
CONFIRM = {"1_dialysis_insulin", "2_rescue", "3_bp_meds"}   # the clerk confirms these, like the demo
out = []


def call(path, body=None, raw=None, headers=None, timeout=90):
    h = {}
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    elif raw is not None:
        data = raw
    h.update(headers or {})
    req = urllib.request.Request(DESK + path, data=data, method="POST" if data is not None else "GET", headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def say(s=""):
    print(s, flush=True)
    out.append(s)


def wait_ready(timeout=240):
    t0 = time.time()
    st = None
    while time.time() - t0 < timeout:
        try:
            st = call("/api/state")
            if st["health"]["mode"] == "FULL":
                break
        except Exception:
            pass
        time.sleep(1)
    h = st["health"] if st else {}
    say(f"desk ready after {time.time() - t0:.0f} s: mode={h.get('mode')} reason={h.get('reason')} "
        f"asr={h.get('asr_model') or h.get('asr')} llm_ready={h.get('llm_ready')}")


def wait_job(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = call(f"/api/jobs/{job_id}")
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def short_reason(r):
    if isinstance(r, dict):
        txt = r.get("zh") or r.get("text") or r.get("en") or ""
        q = r.get("quote") or r.get("evidence") or ""
        return f"{txt}" + (f" 「{q}」" if q else "")
    return str(r)


def describe(seq, st):
    d = {t["seq"]: t for t in st["drafts"] + st["board"]["tickets"]}.get(seq)
    if not d:
        return f"    {seq}: (not on the desk any more)"
    lines = [f"    {d['code']} P{d['priority']} {d['category_zh'].split('（')[0]} | item {d.get('item') or '-'}"
             f" | status {d['status']} | emergency {d.get('emergency') or '-'}",
             f"      desk summary: {d.get('summary')!r}   wire summary: {d.get('wire_summary')!r}"]
    if d.get("priority_reasons"):
        lines.append("      reasons: " + "; ".join(short_reason(r) for r in d["priority_reasons"]))
    if d.get("evidence"):
        lines.append("      evidence quotes: " + json.dumps(d["evidence"], ensure_ascii=False))
    if d.get("missing_info"):
        lines.append("      ask-backs / missing: " + " | ".join(map(str, d["missing_info"])))
    if d.get("dup_candidates"):
        lines.append("      merge suggestion: " + ", ".join(f"{c.get('code')} ({c.get('reason') or c.get('why') or ''})"
                                                      for c in d["dup_candidates"]))
    if d.get("field_sources"):
        ai = [k for k, v in d["field_sources"].items() if v not in ("rules", "rule", "clerk")]
        if ai:
            lines.append("      fields from the AI: " + ", ".join(ai))
    return "\n".join(lines)


def run_one(key, text):
    say(f"\n### {key}: 「{text}」 ({MODE})")
    t0 = time.time()
    if MODE == "text":
        r = call("/api/intake/text", {"text": text, "clerk": CLERK})
    else:
        wav = (HERE / "wav" / f"{key}.wav").read_bytes()
        r = call("/api/intake/audio?clerk=" + urllib.parse.quote(CLERK), raw=wav,
                 headers={"Content-Type": "audio/wav", "X-DarkShelter": "1"})
    j = wait_job(r["job_id"])
    took = time.time() - t0
    say(f"  job {j['status']} in {took:.1f} s | mode {j.get('mode')} | transcript {j.get('transcript')!r}")
    say(f"  timings: {json.dumps(j.get('timings'), ensure_ascii=False)}")
    if j.get("asr"):
        say(f"  asr: {json.dumps(j['asr'], ensure_ascii=False)[:200]}")
    if j.get("llm"):
        say(f"  llm: {json.dumps(j['llm'], ensure_ascii=False)[:200]}")
    if j.get("warnings"):
        say(f"  warnings: {j['warnings']}")
    st = call("/api/state")
    for s in j["seqs"]:
        say(describe(s, st))
    if not j["seqs"]:
        say("    (no drafts)")
    banners = [b["zh"] for b in st.get("banners") or []]
    say(f"  banners now: {banners if banners else 'none'}")
    if key in CONFIRM:
        for s in j["seqs"]:
            res = call(f"/api/tickets/{s}/confirm", {"clerk": CLERK})
        st = call("/api/state")
        for s in j["seqs"]:
            row = [t for t in st["board"]["tickets"] if t["seq"] == s]
            if row:
                say(f"  confirmed {row[0]['code']}: wire summary {row[0]['wire_summary']!r}, wire state {row[0]['wire_state']['zh']}")
        banners = [b["zh"] for b in st.get("banners") or []]
        say(f"  banners after confirm: {banners if banners else 'none'}")
    return j


def main():
    say(f"# Dress rehearsal — {MODE} input, real models — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    wait_ready()
    for key, text in SENT:
        run_one(key, text)
    say("\n### check-in with screening question 1 = yes")
    r = call("/api/checkin", {"family_name": "陳", "size": 3, "zone": "B12", "elderly": 1, "infants": 0,
                              "children": 1, "screening": {"q1": True}, "clerk": CLERK})
    st = call("/api/state")
    say(f"  household {r.get('household_id')} -> drafts {r.get('drafts')}")
    for s in r.get("drafts") or []:
        say(describe(s, st))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"rehearsal_{MODE}.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

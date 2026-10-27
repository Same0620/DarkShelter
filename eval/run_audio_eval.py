"""Minimal audio evaluation (SPEC 11): CER, P1 recall and ASR/LLM/end-to-end latency by condition.

Input: eval/audio/*.wav + eval/audio/labels.csv with columns
  file, case_id, condition (clerk_repeat | resident_direct | noisy), gold_transcript[, gold_p1]
gold_p1 (1/0) is optional; otherwise the case's gold needs in eval/text_cases.jsonl decide
whether the recording contains a P1.

--tts-demo builds a small labelled set with the OS Mandarin voice (Windows SAPI zh-TW / macOS
`say`) in a temp folder, so the script can be exercised before the team records real voices.
TTS speech is far cleaner than a shelter desk: its numbers are a smoke test, not results.

Writes eval/results/audio_eval.md and .json.
Usage: python eval/run_audio_eval.py [--asr real|mock] [--model small|base] [--llm none|mock|ollama]
                                     [--labels eval/audio/labels.csv] [--tts-demo]
"""
import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkshelter.config import load_config  # noqa: E402

CONDITIONS = ("clerk_repeat", "resident_direct", "noisy")
TTS_DEMO = [
    ("tts01", "阿嬤明天早上要洗腎，她的胰島素今天也用完了", True),
    ("tts02", "我爸還在中正路三巷的家裡沒出來", True),
    ("tts03", "寶寶沒奶粉了", True),
    ("tts04", "阿公已經兩天沒洗腎了", True),
    ("tts05", "我沒有胸痛，只是有點累", False),
    ("tts06", "我們家四個人需要毛毯", False),
    ("tts07", "阿公的降血壓藥只剩兩天份，他在B區5床", False),
    ("tts08", "我的手機沒電了，想聯絡家人", False),
]


def cer(ref, hyp):
    from darkshelter.textnorm import normalize
    strip = re.compile(r"[\s，。、；：！？,.;:!?「」『』（）()…\-]")
    r, h = strip.sub("", normalize(ref or "")), strip.sub("", normalize(hyp or ""))
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def pct(values, q):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(round(q / 100.0 * len(v) + 0.5)) - 1))
    return round(v[k], 3)


def load_gold_p1():
    """case_id -> True if the case's gold has a P1 need (eval/text_cases.jsonl)."""
    path = ROOT / "eval" / "text_cases.jsonl"
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except ValueError:
            continue
        needs = (case.get("gold") or {}).get("needs") or []
        out[str(case.get("id"))] = any(int(n.get("priority") or 3) == 1 for n in needs)
    return out


def _get(n, k, default=None):
    return n.get(k, default) if isinstance(n, dict) else getattr(n, k, default)


def understand(text, llm, now):
    """rules (+ LLM) -> (min priority, n_needs, llm_seconds). Uses extract/triage from the repo."""
    from darkshelter import extract, triage
    needs = extract.rules_extract(text, now)
    llm_s = None
    if llm is not None:
        r = llm.extract(text)
        llm_s = r["seconds"]
        if not r["error"]:
            if hasattr(extract, "refine"):
                needs = extract.refine(needs, r["needs"], text, now)
            else:
                needs = extract.merge_llm(needs, r["needs"], text, now)
    prios = []
    for n in needs:
        p = _get(n, "priority")
        try:
            tp = triage.assign_priority(n, now)[0]
            p = tp if p is None else min(int(p), int(tp))
        except Exception:
            pass
        if p is not None:
            prios.append(int(p))
    return (min(prios) if prios else None), len(needs), llm_s


def build_tts_demo(folder):
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_test_wavs as mtw
    rows = []
    for case_id, text, p1 in TTS_DEMO:
        data = mtw.tts_wav(text)
        if data is None:
            return None
        name = f"{case_id}.wav"
        (folder / name).write_bytes(data)
        rows.append({"file": name, "case_id": case_id, "condition": "clerk_repeat", "gold_transcript": text,
                     "gold_p1": "1" if p1 else "0"})
    with (folder / "labels.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "case_id", "condition", "gold_transcript", "gold_p1"])
        w.writeheader()
        w.writerows(rows)
    return folder / "labels.csv"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audio evaluation: CER, P1 recall, latency")
    ap.add_argument("--labels", default=str(ROOT / "eval" / "audio" / "labels.csv"))
    ap.add_argument("--asr", choices=("real", "mock"), default="real")
    ap.add_argument("--model", default=None, help="override asr.model (small/base)")
    ap.add_argument("--llm", choices=("none", "mock", "ollama"), default="none")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--tts-demo", action="store_true", help="generate a TTS demo set instead of eval/audio")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, ensure_key=False)
    if a.model:
        cfg["asr"]["model"] = a.model
    tmp = None
    labels = Path(a.labels)
    if a.tts_demo:
        tmp = Path(tempfile.mkdtemp(prefix="ds_audio_eval_"))
        labels = build_tts_demo(tmp)
        if labels is None:
            print("No OS Mandarin TTS voice on this machine; record real clips instead (eval/audio/README.md).")
            shutil.rmtree(tmp, ignore_errors=True)
            return 0
    if not labels.exists():
        print(f"No labels at {labels}. Record clips as described in eval/audio/README.md, or use --tts-demo.")
        return 0
    from darkshelter.asr import FasterWhisperASR, MockASR
    from darkshelter.audio import check_audio, decode_wav, public
    asr = MockASR(cfg) if a.asr == "mock" else FasterWhisperASR(cfg)
    llm = None
    if a.llm == "mock":
        from darkshelter.llm import MockLLM
        llm = MockLLM()
    elif a.llm == "ollama":
        from darkshelter.llm import OllamaLLM
        llm = OllamaLLM.from_config(cfg)
        if not llm.available(force=True):
            print(f"Ollama/{llm.model} not available at {llm.url}")
            return 1
        llm.warmup()
    gold_p1 = load_gold_p1()
    rows = list(csv.DictReader(labels.open(encoding="utf-8")))
    results = []
    if a.asr == "real":
        asr.load()  # load outside the timings
    for row in rows:
        wav = labels.parent / row["file"]
        rec = {"file": row["file"], "case_id": row.get("case_id", ""), "condition": row.get("condition", ""),
               "gold": row.get("gold_transcript", "")}
        if "gold_p1" in row and row["gold_p1"] not in (None, ""):
            rec["gold_p1"] = row["gold_p1"].strip() in ("1", "true", "True", "yes")
        else:
            rec["gold_p1"] = gold_p1.get(rec["case_id"])
        t0 = time.monotonic()
        try:
            x, sr = decode_wav(wav.read_bytes())
        except Exception as e:
            rec["error"] = f"decode: {e}"
            results.append(rec)
            continue
        chk = check_audio(x, cfg["asr"].get("max_seconds", 30))
        rec["check"] = public(chk)
        if not chk["ok"]:
            rec["rejected"] = chk["reason"]
            results.append(rec)
            continue
        r = asr.transcribe(chk["x"], mock_text=rec["gold"] if a.asr == "mock" else None)
        rec.update({"text": r.text, "asr_s": r.seconds, "low_conf": r.low_conf, "cer": round(cer(rec["gold"], r.text), 4),
                    "audio_s": round(len(chk["x"]) / 16000.0, 2)})
        try:
            p, n_needs, llm_s = understand(r.text, llm, time.time())
            rec.update({"priority": p, "needs": n_needs, "llm_s": llm_s})
        except Exception as e:
            rec["understand_error"] = repr(e)[:200]
        rec["e2e_s"] = round(time.monotonic() - t0, 3)
        results.append(rec)
        print(f"{rec['file']}: CER {rec['cer']:.0%} P{rec.get('priority')} gold_p1={rec['gold_p1']} "
              f"asr {rec['asr_s']:.2f}s e2e {rec['e2e_s']:.2f}s 「{r.text}」", flush=True)
    summary = {}
    for cond in sorted({r["condition"] for r in results}):
        rs = [r for r in results if r["condition"] == cond]
        done = [r for r in rs if "cer" in r]
        labelled = [r for r in done if r.get("gold_p1") is not None and "priority" in r]
        p1_gold = [r for r in labelled if r["gold_p1"]]
        p1_hit = [r for r in p1_gold if r["priority"] == 1]
        false_p1 = [r for r in labelled if not r["gold_p1"] and r["priority"] == 1]
        summary[cond] = {
            "n": len(rs), "rejected": sum(1 for r in rs if "rejected" in r),
            "cer_mean": round(sum(r["cer"] for r in done) / len(done), 4) if done else None,
            "p1_recall": f"{len(p1_hit)}/{len(p1_gold)}" if p1_gold else "n/a",
            "false_p1": len(false_p1),
            "asr_p50": pct([r.get("asr_s") for r in done], 50), "asr_p95": pct([r.get("asr_s") for r in done], 95),
            "llm_p50": pct([r.get("llm_s") for r in done], 50), "llm_p95": pct([r.get("llm_s") for r in done], 95),
            "e2e_p50": pct([r.get("e2e_s") for r in done], 50), "e2e_p95": pct([r.get("e2e_s") for r in done], 95),
        }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"asr": a.asr, "model": getattr(asr, "model_name", "?"), "llm": a.llm, "labels": str(labels),
            "tts_demo": a.tts_demo, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    (out / "audio_eval.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Audio evaluation", "",
             f"ASR: {meta['asr']} ({meta['model']}), LLM: {meta['llm']}, {len(results)} recordings, {meta['when']}.", ""]
    if a.tts_demo:
        lines += ["**TTS demo set** (OS text-to-speech, clean audio): a smoke test of the pipeline, not a "
                  "measurement of real shelter speech.", ""]
    lines += ["| condition | n | rejected | CER | P1 recall | false P1 | ASR p50/p95 s | LLM p50/p95 s | end-to-end p50/p95 s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for cond, s in summary.items():
        cer_txt = "" if s["cer_mean"] is None else "{:.1%}".format(s["cer_mean"])
        lines.append(f"| {cond} | {s['n']} | {s['rejected']} | {cer_txt} | "
                     f"{s['p1_recall']} | {s['false_p1']} | {s['asr_p50']}/{s['asr_p95']} | {s['llm_p50']}/{s['llm_p95']} | "
                     f"{s['e2e_p50']}/{s['e2e_p95']} |")
    lines += ["", "| file | condition | CER | P | gold P1 | heard |", "|---|---|---|---|---|---|"]
    for r in results:
        heard = r.get("text", r.get("rejected", r.get("error", "")))
        lines.append(f"| {r['file']} | {r['condition']} | {r.get('cer', '')} | {r.get('priority', '')} | "
                     f"{r.get('gold_p1', '')} | {heard} |")
    (out / "audio_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'audio_eval.md'} and .json")
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

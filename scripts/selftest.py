"""DarkShelter laptop self-test (SPEC 11): prints a checklist, exits 1 on critical failures.

Checks: Python and packages; data files read as UTF-8; Whisper load (local_files_only=True)
and real-time factor for asr.model and asr.fast_model, with a recommendation for asr.model;
Ollama reachable, model pulled, warm-up (prefix cache) and a real extraction of
「阿嬤明天早上要洗腎」 with tokens/s; ports free; DB writable; disk; battery.

Usage:  python scripts/selftest.py [--config config.json] [--skip-whisper] [--skip-llm] [--json]
"""
import argparse
import gc
import importlib
import json
import locale
import os
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkshelter.config import load_config, resolve_path  # noqa: E402

RTF_SENTENCE = "阿嬤明天早上要洗腎，然後她的胰島素今天就用完了。"
LLM_SENTENCE = "阿嬤明天早上要洗腎"
# Recommend `small` when it transcribes 10 s of speech in <= 4 s, leaving room for the LLM
# inside the 8 s end-to-end budget (PLAN G4).
RTF_SMALL_MAX = 0.4


class Report:
    def __init__(self):
        self.items = []
        self.facts = {}

    def add(self, section, name, status, detail="", critical=False, **data):
        item = {"section": section, "name": name, "status": status, "detail": detail, "critical": critical}
        item.update(data)
        self.items.append(item)
        return item

    def critical_failures(self):
        return [i for i in self.items if i["status"] == "fail" and i["critical"]]


def _marks():
    marks = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "–", "info": "·"}
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(marks.values()).encode(enc)
        return marks
    except (UnicodeEncodeError, LookupError):
        return {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]", "info": "[i]"}


def say(line):
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


def _version(mod):
    try:
        from importlib import metadata
        return metadata.version(mod)
    except Exception:
        return "?"


# ------------------------------------------------------------------ checks

def check_python(rep):
    v = sys.version_info
    vs = f"{v.major}.{v.minor}.{v.micro} ({platform.system()} {platform.release()}, {platform.machine()})"
    if (v.major, v.minor) < (3, 11):
        rep.add("python", "Python version", "fail", f"{vs} - need 3.11 or 3.12", critical=True)
    elif (v.major, v.minor) in ((3, 11), (3, 12)):
        rep.add("python", "Python version", "ok", vs)
    else:
        rep.add("python", "Python version", "warn", f"{vs} - target is 3.11/3.12 from python.org")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    rep.add("python", "Virtual environment", "ok" if in_venv else "warn",
            sys.prefix if in_venv else "not in a venv (recommended: python -m venv .venv)")


def check_packages(rep, skip_whisper):
    pkgs = [("flask", "flask", True), ("numpy", "numpy", True),
            ("faster_whisper", "faster-whisper", not skip_whisper),
            ("psutil", "psutil", False), ("opencc", "opencc-python-reimplemented", False)]
    for mod, dist, critical in pkgs:
        try:
            importlib.import_module(mod)
            extra = ""
            if mod == "faster_whisper":
                extra = f", ctranslate2 {_version('ctranslate2')}"
            rep.add("packages", f"import {mod}", "ok", f"{dist} {_version(dist)}{extra}")
        except Exception as e:  # ctranslate2 without the VC++ runtime raises OSError
            hint = ""
            if mod == "faster_whisper" and isinstance(e, OSError):
                hint = " - install the Microsoft Visual C++ Redistributable (x64)"
            status = "fail" if critical else "warn"
            rep.add("packages", f"import {mod}", status, f"{type(e).__name__}: {e}{hint}"[:300], critical=critical)


def check_encoding_and_data(rep):
    pref = locale.getpreferredencoding(False)
    rep.add("system", "Text encoding", "ok",
            f"locale {pref}, utf8_mode={sys.flags.utf8_mode}; all files are read/written as UTF-8")
    required = ["data/codebook.json", "data/lexicon.json", "data/prompts/extract_system.txt",
                "data/prompts/fewshot.json"]
    missing, bad = [], []
    for rel in required:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        try:
            text = p.read_text(encoding="utf-8")
            if p.suffix == ".json":
                json.loads(text)
        except Exception as e:
            bad.append(f"{rel}: {e}")
    if missing or bad:
        rep.add("system", "Data files (UTF-8)", "fail", "; ".join([f"missing {m}" for m in missing] + bad),
                critical=True)
    else:
        rep.add("system", "Data files (UTF-8)", "ok", f"{len(required)} files read as UTF-8")


def _speech_clip():
    """(float32 16 kHz array, label, reference text or None)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_test_wavs as mtw
    from darkshelter.audio import decode_wav
    data = mtw.tts_wav(RTF_SENTENCE)
    if data:
        x, _ = decode_wav(data)
        return x, "OS text-to-speech Mandarin sentence", RTF_SENTENCE
    x, _ = decode_wav(mtw.wav_bytes(mtw.speech_like_signal(10.0, seed=7)))
    return x, "10 s synthetic voice-like signal (no TTS voice; VAD off)", None


def _cer(ref, hyp):
    import re
    from darkshelter.textnorm import normalize
    strip = re.compile(r"[\s，。、；：！？,.;:!?「」]")
    r, h = strip.sub("", normalize(ref)), strip.sub("", normalize(hyp))
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def check_whisper(rep, cfg):
    from darkshelter.asr import DOMAIN_PROMPT, FasterWhisperASR, summarize_segments
    a = cfg["asr"]
    names = []
    for n in (a.get("model", "small"), a.get("fast_model", "base"), "small", "base"):
        if n and n not in names:
            names.append(n)
    try:
        x, label, ref = _speech_clip()
    except Exception as e:
        rep.add("whisper", "Test clip", "fail", f"cannot build a test clip: {e!r}", critical=True)
        return
    dur = len(x) / 16000.0
    rep.add("whisper", "Test clip", "ok", f"{label}, {dur:.1f} s")
    rtf = {}
    for name in names:
        asr = FasterWhisperASR({**cfg, "asr": {**a, "model": name}})
        t0 = time.monotonic()
        try:
            model = asr.load()
        except Exception as e:
            critical = name in (a.get("model"), a.get("fast_model"))
            rep.add("whisper", f"Load '{name}' (local_files_only)", "fail" if critical else "warn",
                    f"{e}"[:300], critical=critical)
            continue
        load_s = time.monotonic() - t0
        try:
            if ref is not None:
                asr.transcribe(x[:16000])  # warm-up
                r = asr.transcribe(x)
                secs, text = r.seconds, r.text
            else:  # no speech available: decode with VAD off so the decoder actually runs
                model.transcribe(x[:16000], language="zh", beam_size=1, temperature=0.0, vad_filter=False)
                t1 = time.monotonic()
                segs, info = model.transcribe(x, language="zh", beam_size=1, temperature=0.0, vad_filter=False,
                                              initial_prompt=DOMAIN_PROMPT, condition_on_previous_text=False)
                r = summarize_segments(list(segs), time.monotonic() - t1, name)
                secs, text = r.seconds, r.text
        except Exception as e:
            rep.add("whisper", f"Transcribe with '{name}'", "fail", repr(e)[:300], critical=True)
            continue
        rtf[name] = secs / dur if dur else 0.0
        detail = f"load {load_s:.1f} s, {secs:.2f} s for {dur:.1f} s audio -> RTF {rtf[name]:.2f}"
        extra = {"rtf": round(rtf[name], 3), "load_s": round(load_s, 2), "transcript": text}
        if ref is not None:
            cer = _cer(ref, text)
            detail += f"; heard 「{text}」 (CER {cer:.0%} vs the TTS text)"
            extra["cer"] = round(cer, 3)
        rep.add("whisper", f"Whisper '{name}'", "ok", detail, **extra)
        del asr, model  # free each model before loading the next (and before the LLM check)
        gc.collect()
    if not rtf:
        return
    if "small" in rtf and rtf["small"] <= RTF_SMALL_MAX:
        rec, why = "small", f"small RTF {rtf['small']:.2f} <= {RTF_SMALL_MAX}"
    elif "base" in rtf:
        rec = "base"
        why = (f"small RTF {rtf['small']:.2f} > {RTF_SMALL_MAX}" if "small" in rtf else "small unavailable")
        if rtf["base"] > RTF_SMALL_MAX:
            why += f"; even base is slow (RTF {rtf['base']:.2f}) - prefer typing for long requests"
    else:
        rec, why = next(iter(rtf)), "only model that loaded"
    current = a.get("model")
    note = "" if current == rec else f" (config now says '{current}': set \"asr\": {{\"model\": \"{rec}\"}})"
    rep.add("whisper", "Recommendation for asr.model", "ok", f"\"{rec}\" - {why}{note}", recommendation=rec)
    rep.facts["asr_recommendation"] = rec


def check_ollama(rep, cfg):
    from darkshelter.llm import OllamaLLM
    llm = OllamaLLM.from_config(cfg)
    try:
        tags = llm.tags(timeout=3.0)
        names = sorted({m.get("name") for m in tags.get("models", []) if m.get("name")})
        rep.add("ollama", "Ollama reachable", "ok", f"{llm.url} ({len(names)} models: {', '.join(names)[:120]})")
    except Exception as e:
        rep.add("ollama", "Ollama reachable", "fail", f"{llm.url}: {e!r} - start Ollama (ollama serve)"[:300],
                critical=True)
        return
    if not llm.available(force=True):
        rep.add("ollama", f"Model {llm.model} pulled", "fail", f"run: ollama pull {llm.model}", critical=True)
        return
    rep.add("ollama", f"Model {llm.model} pulled", "ok", "listed in /api/tags")
    w = llm.warmup()
    if not w["ok"]:
        rep.add("ollama", "Warm-up (2 × 「測試」)", "fail", f"error {w['error']} after {w['seconds']} s", critical=True)
        return
    rep.add("ollama", "Warm-up (2 × 「測試」)", "ok" if w["prefix_cached"] else "warn",
            f"{w['seconds']:.1f} s; prompt tokens {w['prompt_eval_count']}, prompt eval s "
            f"{w.get('prompt_eval_s')}; prefix_cached={w['prefix_cached']}",
            prefix_cached=w["prefix_cached"], prompt_eval_count=w["prompt_eval_count"])
    r = llm.extract(LLM_SENTENCE)
    tps = r["tokens_per_s"]
    min_tps = float(cfg["llm"].get("min_tokens_per_s", 4))
    if r["error"]:
        rep.add("ollama", f"Real extraction 「{LLM_SENTENCE}」", "fail", f"error {r['error']} after {r['seconds']} s",
                critical=True)
        return
    cats = [n["category"] for n in r["needs"]]
    shown = "; ".join(f"{n['category']} item={n['item']} subject={n['subject']} time={n['time_phrase']}"
                      for n in r["needs"])
    status = "ok" if "LIFE_SUSTAINING" in cats else "warn"
    rep.add("ollama", f"Real extraction 「{LLM_SENTENCE}」", status,
            f"{r['seconds']:.2f} s, {tps} tokens/s, prefix_cached={w['prefix_cached']} -> {shown}",
            tokens_per_s=tps, seconds=r["seconds"], needs=r["needs"], prefix_cached=w["prefix_cached"])
    if tps is not None and tps < min_tps:
        rep.add("ollama", "LLM speed", "warn", f"{tps} tokens/s < {min_tps}: the LLM will run on demand only (FAST)")
    try:
        with llm._opener.open(llm.url + "/api/ps", timeout=2) as resp:
            ps = json.loads(resp.read().decode("utf-8"))
        for m in ps.get("models", []):
            if m.get("name") == llm.model or m.get("model") == llm.model:
                vram = m.get("size_vram", 0) or 0
                where = "GPU" if vram else "CPU"
                rep.add("ollama", "Ollama device", "info",
                        f"{llm.model} loaded on {where} (size_vram {vram // 1_000_000} MB, keep_alive "
                        f"{m.get('expires_at', '?')})")
    except Exception:
        pass


def check_ports(rep, cfg):
    p = cfg["ports"]
    busy = []
    for name, kind in (("desk_http", "tcp"), ("eoc_http", "tcp"), ("emu_control_http", "tcp"),
                       ("emu_udp", "udp"), ("eoc_udp", "udp")):
        port = int(p[name])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            busy.append(f"{kind.upper()} {port} ({name})")
        finally:
            s.close()
    if busy:
        rep.add("system", "Ports free", "warn", "in use: " + ", ".join(busy) +
                " - an orphaned run_demo.py? close it or change ports in config.json")
    else:
        rep.add("system", "Ports free", "ok", "TCP {desk_http}/{eoc_http}/{emu_control_http}, UDP {emu_udp}/{eoc_udp}"
                .format(**p))


def check_db_and_disk(rep, cfg):
    desk_db = resolve_path(cfg["paths"]["desk_db"])
    d = desk_db.parent
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="selftest_", suffix=".db", dir=str(d))
        os.close(fd)
        try:
            c = sqlite3.connect(tmp)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE t (x TEXT)")
            c.execute("INSERT INTO t VALUES ('洗腎')")
            c.commit()
            ok = c.execute("SELECT x FROM t").fetchone()[0] == "洗腎"
            c.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp + suffix)
                except OSError:
                    pass
        rep.add("system", "DB writable", "ok" if ok else "fail", str(d), critical=not ok)
    except Exception as e:
        rep.add("system", "DB writable", "fail", f"{d}: {e!r}", critical=True)
    try:
        free = shutil.disk_usage(str(d if d.exists() else ROOT)).free / 1e6
        rep.add("system", "Free disk", "ok" if free > 500 else "warn", f"{free:,.0f} MB", disk_free_mb=round(free))
    except Exception as e:
        rep.add("system", "Free disk", "warn", repr(e))


def memory_status():
    """{'avail_phys_gb', 'total_phys_gb', 'avail_commit_gb' (Windows only)} or {} if unknown."""
    out = {}
    if sys.platform.startswith("win"):
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                out = {"avail_phys_gb": st.ullAvailPhys / 2 ** 30, "total_phys_gb": st.ullTotalPhys / 2 ** 30,
                       "avail_commit_gb": st.ullAvailPageFile / 2 ** 30, "commit_limit_gb": st.ullTotalPageFile / 2 ** 30}
        except Exception:
            out = {}
    if not out:
        try:
            import psutil
            vm = psutil.virtual_memory()
            out = {"avail_phys_gb": vm.available / 2 ** 30, "total_phys_gb": vm.total / 2 ** 30}
        except Exception:
            out = {}
    return out


def check_memory(rep):
    m = memory_status()
    if not m:
        rep.add("system", "Free memory", "warn", "unknown (psutil missing)")
        return
    detail = f"{m['avail_phys_gb']:.1f} of {m['total_phys_gb']:.1f} GB RAM free"
    low = m["avail_phys_gb"] < 2.0
    if "avail_commit_gb" in m:
        detail += f"; commit {m['avail_commit_gb']:.1f} of {m['commit_limit_gb']:.1f} GB free"
        low = low or m["avail_commit_gb"] < 2.0
    if low:
        detail += " - Whisper + Qwen need about 2 GB: close browser tabs / other apps before the demo"
    rep.add("system", "Free memory", "warn" if low else "ok", detail, **{k: round(v, 2) for k, v in m.items()})


def check_battery(rep):
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception as e:
        rep.add("system", "Battery", "warn", f"psutil unavailable ({type(e).__name__}); battery unknown")
        return
    if b is None:
        rep.add("system", "Battery", "ok", "no battery sensor (mains power)")
        return
    status = "warn" if (b.percent < 15 and not b.power_plugged) else "ok"
    rep.add("system", "Battery", status, f"{b.percent:.0f}%, {'charging/plugged in' if b.power_plugged else 'on battery'}",
            battery=b.percent, plugged=b.power_plugged)


def main(argv=None):
    ap = argparse.ArgumentParser(description="DarkShelter laptop self-test")
    ap.add_argument("--config", default=None)
    ap.add_argument("--skip-whisper", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--json", action="store_true", help="print a JSON report instead of the checklist")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    t0 = time.monotonic()
    cfg = load_config(a.config, ensure_key=False)
    rep = Report()
    check_python(rep)
    check_packages(rep, a.skip_whisper)
    check_encoding_and_data(rep)
    check_memory(rep)
    if a.skip_whisper:
        rep.add("whisper", "Whisper", "skip", "--skip-whisper")
    elif any(i["name"] == "import faster_whisper" and i["status"] == "fail" for i in rep.items):
        rep.add("whisper", "Whisper", "fail", "faster-whisper not importable", critical=True)
    else:
        check_whisper(rep, cfg)
    if a.skip_llm:
        rep.add("ollama", "Ollama", "skip", "--skip-llm")
    elif not cfg["llm"].get("enabled", True):
        rep.add("ollama", "Ollama", "skip", "llm.enabled is false in the config")
    else:
        check_ollama(rep, cfg)
    check_ports(rep, cfg)
    check_db_and_disk(rep, cfg)
    check_battery(rep)
    crit = rep.critical_failures()
    elapsed = time.monotonic() - t0
    if a.json:
        print(json.dumps({"ok": not crit, "seconds": round(elapsed, 1), "items": rep.items, "facts": rep.facts},
                         ensure_ascii=False, indent=2, default=str))
        return 1 if crit else 0
    m = _marks()
    cfg_path = cfg.get("_path")
    cfg_note = cfg_path if cfg_path and Path(cfg_path).exists() else "defaults + config.example.json (no config.json yet)"
    say(f"DarkShelter self-test - {cfg.get('shelter_name', '')}  (config: {cfg_note})")
    section = None
    for i in rep.items:
        if i["section"] != section:
            section = i["section"]
            say(f"\n[{section}]")
        say(f"  {m[i['status']]} {i['name']}: {i['detail']}")
    warns = sum(1 for i in rep.items if i["status"] == "warn")
    say("")
    if crit:
        say(f"{m['fail']} {len(crit)} critical failure(s), {warns} warning(s) in {elapsed:.0f} s:")
        for i in crit:
            say(f"    - {i['name']}: {i['detail']}")
    else:
        rec = rep.facts.get("asr_recommendation")
        tail = f" Recommended asr.model: \"{rec}\"." if rec else ""
        say(f"{m['ok']} All critical checks passed ({warns} warning(s)) in {elapsed:.0f} s.{tail}")
    return 1 if crit else 0


if __name__ == "__main__":
    sys.exit(main())

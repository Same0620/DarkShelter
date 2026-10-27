"""Fetch the models while online (SPEC 11). Safe to re-run: a no-op when already cached.

- Whisper asr.model and asr.fast_model (default small + base) through the same loader the
  desk uses, with local_files_only=False, into models_dir (null = the Hugging Face cache).
- `ollama pull <llm.model>` when `ollama` is on PATH (or given with --ollama); otherwise
  the command is printed.

Usage: python scripts/download_models.py [--config config.json] [--ollama PATH] [--skip-whisper] [--skip-ollama]
"""
import argparse
import gc
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkshelter.config import load_config  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download Whisper models and pull the Ollama model")
    ap.add_argument("--config", default=None)
    ap.add_argument("--ollama", default=None, help="path to the ollama executable (default: on PATH)")
    ap.add_argument("--skip-whisper", action="store_true")
    ap.add_argument("--skip-ollama", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, ensure_key=False)
    rc = 0
    if not a.skip_whisper:
        from darkshelter.asr import load_whisper
        names = []
        for n in (cfg["asr"].get("model", "small"), cfg["asr"].get("fast_model", "base")):
            if n and n not in names:
                names.append(n)
        for name in names:
            t0 = time.monotonic()
            print(f"Whisper '{name}': checking / downloading ...", flush=True)
            try:
                model = load_whisper(name, cfg, local_files_only=False)
                del model  # one model in memory at a time
                gc.collect()
                print(f"  ok ({time.monotonic() - t0:.1f} s)", flush=True)
            except Exception as e:
                print(f"  FAILED: {e}", flush=True)
                rc = 1
    if not a.skip_ollama:
        model = cfg["llm"].get("model", "qwen2.5:1.5b")
        exe = a.ollama or shutil.which("ollama")
        if exe:
            print(f"{exe} pull {model}", flush=True)
            r = subprocess.run([exe, "pull", model])
            if r.returncode != 0:
                print(f"  ollama pull failed (exit {r.returncode}); is `ollama serve` running?", flush=True)
                rc = 1
        else:
            print("Ollama is not on PATH. Install it from https://ollama.com/download, then run:")
            print(f"    ollama pull {model}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

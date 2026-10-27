# PROGRESS — DarkShelter Stage I build

Build started **2026-10-28 01:35** (Taipei) on the team laptop (Windows 11 Home zh-TW, big5/cp950 code page,
AMD Ryzen AI 7 350, 31 GB RAM, NVIDIA RTX 5070 Laptop GPU). Hard stop: 07:35.

## Checkpoints

| Checkpoint | Time | State |
|---|---|---|
| Setup (venv, models, git) | 01:35–01:50 | done |
| C1 core | 02:28 | verified + committed (`491e2c2`): 181 tests, text eval rules-only P1 recall/precision 100 %, UI flow in Chromium |
| C2 network | 02:29 | acceptance check 2 passes (`eval/demo_check.py`, ALL PASS); **stop-the-line gate (T+2 h = 03:35) cleared at 02:30**, nothing cut |
| C3 AI | 02:30 | adapters + stub tests (59), selftest exit 0 with real Whisper small/base + real qwen2.5:1.5b |
| C4-lite | 02:35 | kill real Ollama → FAST → restart → FULL; hard-kill desk → restarted, 「已復原 2 筆草稿，1 筆待送」, queue delivered; unconfirmed P1 → content-free alert (unit test) |
| C4 rest / C5 | — | in progress |

## Decisions and disagreements (noted per CLAUDE.md)

- **Models were not on the laptop.** CLAUDE.md says Whisper `small`/`base` and Ollama `qwen2.5:1.5b` were already
  installed; they were not (no `~/.cache/huggingface/hub/models--Systran--faster-whisper-*`, no Ollama binary,
  no `~/.ollama`). Because the goal requires the real models, I downloaded exactly those three models and nothing else:
  - faster-whisper `base` + `small` into the default Hugging Face cache (via `WhisperModel(name, local_files_only=False)`,
    the same call `scripts/download_models.py` makes).
  - Ollama **v0.40.11 portable zip** (no installer, no service, no autostart) unpacked to
    `C:\Users\Tony\ollama-portable\`, started with `ollama.exe serve` (127.0.0.1:11434, log in
    `C:\Users\Tony\ollama-portable\serve.log`), then `ollama pull qwen2.5:1.5b` (986 MB, in `~/.ollama/models`).
    To remove: stop the `ollama.exe` process and delete that folder and `~/.ollama`.
    After a reboot it must be restarted: `C:\Users\Tony\ollama-portable\ollama.exe serve` (or install Ollama normally).
- **Ollama runs CPU-only (GPU path crashes here).** Ollama 0.40.11 detected the RTX 5070, but its CUDA path fails on
  this laptop (`ptxas fatal: Memory allocation failure` / `PTX JIT compilation failed`, HTTP 500). It is therefore
  started CPU-only, which also matches the SPEC's CPU-only target:
  ```powershell
  $env:CUDA_VISIBLE_DEVICES="-1"; $env:OLLAMA_VULKAN="0"; $env:GGML_VK_VISIBLE_DEVICES="-1"; $env:OLLAMA_HOST="127.0.0.1:11434"
  Start-Process C:\Users\Tony\ollama-portable\ollama.exe serve -WindowStyle Hidden -RedirectStandardError C:\Users\Tony\ollama-portable\serve.log
  ```
  All tokens/s numbers below are CPU numbers. Whisper runs on the CPU (`device="cpu"`, int8) exactly as the SPEC says.
- **Memory pressure on this laptop.** At 02:15 the system commit charge was 46.2 of 47.1 GB (the user's open apps:
  Chrome 11.7 GB in 48 processes, VS Code 3.9 GB, Edge WebView, Slack, …). Loading Whisper `small` failed once with
  `mkl_malloc: failed to allocate memory` while the selftest was loading models at the same time. I did not close
  any user application; heavy model loads are run one at a time. The desk degrades to TYPED mode with the reason on
  screen when this happens, and health now warns when < 1.5 GB RAM is free. **Before the demo: close browser tabs.**
- **Python:** `.venv` uses CPython 3.12.15 (uv-managed). Python 3.11 syntax is checked with the conda env
  `C:\Users\Tony\anaconda3\envs\coco311\python.exe` (3.11.14) — `tests/test_portability.py` finds it automatically
  (or set `DS_PY311`).
- **Git:** SPEC §0 says "do not run git"; CLAUDE.md (which wins here) says commit locally after each checkpoint.
  Commits are local only; nothing is pushed.
- **Parallel build.** To fit the 6-hour window, three sub-agents built separate file sets at the same time
  (network C2; lexicon/extract/triage; audio/ASR/LLM adapters C3) while I built store, wire view, pipeline, health,
  timers, desk app/UI and run_demo. Checkpoints are still verified and committed in order C1 → C5.

## Real-model runs through the desk pipeline (02:20–02:27)

Speech clips: 8 Mandarin sentences synthesised with the Windows zh-TW voice "Microsoft Hanhan" (TTS, *not* human
voices; real recordings are still the team's job, see eval/audio/README.md).

- **Real Whisper `small` → rules (qwen unloaded to free memory):** 0.44–1.25 s per 3–5.5 s clip on CPU (int8, 8 threads).
  P1 needs found 5/6. Misses/oddities are ASR errors on TTS audio: 「寶寶沒奶粉了」 heard as 「爆包、美奶粉了」 (→ P2),
  毛毯 heard as 矛盾, B區 heard as VQ. The lexicon already maps the mishearing 遺導素 → 胰島素.
- **Real Ollama qwen2.5:1.5b → desk (typed intake, 10 sentences):** LLM warm-up 37 tok/s on CPU, latency p50 2.9 s /
  max 7.3 s per request including the LLM. All rule P1s survived the merge (the 1.5B model merges two needs into one;
  the rules keep both). Downplaying 「沒什麼啦，胰島素昨天就用完了」 → P1; 「我沒有胸痛」 → P3 + ask-back, no alarm.
- **Both models at once did not fit in memory on this laptop at build time** (see Memory pressure above): with
  Whisper loaded, Ollama failed to load qwen (`ggml_backend_cpu_buffer_type_alloc_buffer` failed), and vice versa.
  The desk handled it as designed: FAST mode ("AI warm-up failed, rules only, retrying every minute") or TYPED mode
  with the reason on screen, never a crash or a bad draft. Fixes made from this: failed warm-ups are retried every
  60 s; if `asr.model` cannot load, the desk falls back to `asr.fast_model` (base) and says so.
  **With Chrome/VS Code closed the laptop has ~20 GB free and both models fit easily.**

## What works / verified (filled in as checks pass)

_in progress_

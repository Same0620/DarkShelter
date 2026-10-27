# PROGRESS — DarkShelter Stage I build

Build started **2026-10-28 01:35** (Taipei) on the team laptop: Windows 11 Home zh-TW (console code page cp950),
AMD Ryzen AI 7 350 (16 threads), 31 GB RAM, NVIDIA RTX 5070 Laptop GPU (not used, see below).
Hard stop was 07:35. All acceptance checks passed on the first finished build by 03:09 (pass 02:57–03:09).
A red-team pass (04:40–05:00) and fixes (05:23–06:00) followed, and every acceptance check was rerun on the fixed
code from 05:45 to 06:05. That rerun is the table below.

## Checkpoints (all verified, committed locally in order; nothing pushed)

| Checkpoint | Time | Commit | How it was verified |
|---|---|---|---|
| Setup | 01:35–01:50 | — | venv (CPython 3.12.15), Whisper small/base + Ollama + qwen2.5:1.5b downloaded (see decisions) |
| C1 core | 02:28 | `491e2c2` | 181 unit tests; `run_text_eval.py --llm none` P1 recall/precision 100 %; typed request → P1 draft with reasons → confirm → board, and check-in "yes" → draft, in headless Chromium |
| C2 network | 02:29 → commit 02:39 | `249075a` | acceptance check 2 (`eval/demo_check.py` ALL PASS) + 41 codec/RTO/emulator/network/chaos/wire-audit tests. **Stop-the-line gate (T+2 h = 03:35) cleared at 02:30 — nothing was cut.** |
| C3 AI | 02:30 → commit 02:40 | `6576ad5` | 190 tests incl. a real Whisper `base` and a real Ollama test; Ollama adapter against the stub (slow → timeout, bad JSON → retry → invalid_json, stopped → FAST); real-model runs below |
| C4-lite + C4 rest | 02:35 → commit 02:41 | `cfbbc77` | selftest exit 0 with the real models; kill real Ollama → FAST → restart → FULL; hard-kill the desk under run_demo → restarted with 「已復原 2 筆草稿，1 筆待送」, queue delivered; unconfirmed P1 → content-free alert, watchdog, clock jump (unit tests) |
| C5 | 03:09 | `7b432e7` | every SPEC §13 acceptance check, rerun on the final code |
| Red-team | 04:40–05:00 | `64825d5`–`1d1603d` | 8 agents in two rounds, read-only: 29 findings with proofs of concept (`REDTEAM.md`) |
| Red-team fixes | 05:23–06:05 | (this commit) | 4 fixer agents on separate files plus me; every fix has a regression test; all §13 checks below rerun on the fixed code |

The build used three sub-agents in parallel (network; lexicon/extract/triage; audio/ASR/LLM adapters) while I wrote
the store, wire view, pipeline, health, timers, desk app/UI and run_demo. Checkpoints were still verified and
committed in the C1 → C5 order.

## Acceptance checks (SPEC §13) — rerun on the fixed code on this laptop, cp950 console, no `PYTHONUTF8`

| # | Check | Result |
|---|---|---|
| 1 | `python -m unittest discover -s tests` incl. slow tests | **PASS** — 333 tests OK in 166 s (286 before the fixes + 47 from the red-team fixes), 0 skipped (network, chaos, UI smoke, real Whisper, real Ollama included); run twice on the fixed code, both OK |
| 2 | `python run_demo.py --mock --no-browser --reset` + demo flow | **PASS** — `eval/demo_check.py` ALL PASS (exit 0): check-in + typed → drafts → confirm → queued while DOWN (EOC empty, 0 data bytes); on LORA_RELAY census at +0.69 s, last P1 +1.89 s, first P3 +17.41 s; byte meter 843 B sent vs 160,000 B as voice notes (190×); 已派遣 at the EOC → 🚑 at the desk. `eval/results/demo_check.md` |
| 3 | `eval/net_experiment.py` | **PASS** (exit 0) — 0 duplicates shown, 0 losses, 0 ordering violations on all 5 profiles. LORA 46 s, LTE one bar 45 s, satellite 29 s, GOOD 4.9 s for all 60. **FLAPPING is at the edge of the 180 s cap:** this run had 53/60 by the cap (the other 7 stayed queued and arrived 0.3 s after switching to GOOD, so none lost); before the fixes 56/60, and one earlier run delivered all 60 at 156.9 s. `eval/results/net_experiment.md` |
| 4 | `eval/chaos.py` | **PASS** (exit 0) — 5 sender + 5 EOC hard kills, 30 % loss + flapping, edits/discards/resolves mid-run: EOC = desk (43 = 43), 0 missing/wrong/extra, 0 duplicates shown, equal after 75.5 s. `eval/results/chaos.md` |
| 5 | `eval/run_text_eval.py --llm none` | **PASS** — 127 cases (72 original + 55 red-team), P1 recall 100 %, P1 precision 100 % (91/91), need P/R 100 %, 0 must-case failures. `eval/results/text_eval.md` |
| 6 | wire-audit test | **PASS** — `tests/test_wire_audit.py` (79 texts incl. names, IDs, phones, protection, sensitive; plus red-team regressions: a protection case's other ticket, rebuilt deadline text, a typed address with a name in it) |
| 7 | silent / too-short WAVs rejected, clipped warned | **PASS** — silence, 0.3 s and −60 dBFS → no drafts (「沒有聽到聲音」/「錄音太短」); clipped → warning; 40 s → truncated + warning; 48 kHz stereo resampled |
| 8 | UI smoke test | **PASS** — `tests/test_ui_smoke.py` (run_demo --mock on free ports, Chromium fake mic, 2 s recording `duration_s ≥ 1.5`); screenshots `eval/results/screens/desk.png`, `eoc.png` |
| 9 | `grep -rnE "https?://" darkshelter/static \| grep -vE …` | **PASS** — prints nothing (no URLs at all in static/) |
| 10 | `python3.11 -m compileall -q …` + `test_portability.py` | **PASS** — Python 3.11.14 (conda env `coco311`, no `python3.11` on PATH here) exit 0; portability 6/6 |
| 11 | README covers Windows + macOS setup, running, troubleshooting | **PASS** — all 24 required items present (checked by grep) |
| 12 | this file | see below |

Real models (rerun 06:00 on the fixed code, cp950 console): `python scripts/selftest.py` → **exit 0, 0 warnings**:
Whisper small RTF 0.22 (CER 5 % on a TTS sentence), base RTF 0.08 (CER 14 %), recommendation **small**; Ollama
reachable, qwen2.5:1.5b pulled, warm-up prefix cached, real extraction of 「阿嬤明天早上要洗腎」 in 1.4 s at
44.8 tok/s on CPU.

## Verified on this laptop vs. not verified

This laptop **is** the target, so "here" and "on the laptop" are the same machine.

| Item | Status |
|---|---|
| Real Whisper (faster-whisper small + base, CPU int8) | ✅ selftest; desk pipeline on 8 synthesised Mandarin clips |
| Real Ollama qwen2.5:1.5b | ✅ selftest; text eval with the real model; desk failover kill/restart |
| Both real models loaded together in the desk | ✅ with `llm.use_mmap` (see decisions), 8 synthesised clips: P1s found 6/6, 「我沒有胸痛」 no alarm, speech → rules → LLM 2.4–2.9 s per request |
| Windows UDP (WSAECONNRESET) | ✅ unit test with a fake socket + the real sockets (a `WSAIoctl` fallback was needed, see decisions) |
| zh-TW Windows encoding (cp950) | ✅ every acceptance command run without `PYTHONUTF8`; two crash bugs found and fixed |
| Real microphone with a human speaker | ❌ not verified (nobody awake). Only Chromium's fake mic (UI smoke) and synthesised speech files. **Team: do one live recording before filming.** |
| Real recordings for `eval/run_audio_eval.py` (30 clips, 3–4 voices) | ❌ team task (PLAN §11.3). `--tts-demo` runs on OS-voice clips only |
| Safari / macOS setup | ❌ not verified (no Mac). The README has the macOS steps; the UI uses only ES5-level JS + standard CSS |
| Real LoRa / satellite link | ❌ Stage II; all link numbers come from the emulator |

## What is mocked (and where)

- `--mock` mode and most tests: `MockASR` (cycles demo sentences / `mock_text`) and `MockLLM` (rules → LLM JSON).
- Ollama adapter tests: `scripts/ollama_stub.py` (normal / slow / bad-JSON / stopped).
- The network: `darkshelter/net/linkemu.py` emulates every link profile (seeded).
- Speech test clips: Windows zh-TW TTS voice "Microsoft Hanhan" (clean speech — real accuracy will be lower).

## Results worth knowing (for the deck)

- Text eval (labelled by us against our own rules — regression coverage, not real-world accuracy): rules-only
  P1 recall 100 % / precision 100 % on all 127 cases (72 original + 55 from the red-team pass). The real-qwen run
  was on the original 72 cases: P1 recall/precision 100 %, need P/R 96.2 %, LLM latency
  p50 1.3 s / p95 1.9 s, 0 LLM errors. **Hallucination (LLM fields not found in the transcript): 12.2 %** — above the
  PLAN's 5 % target; those fields are flagged "AI guess" (amber) and can only raise urgency, never lower it.
- Network (`net_experiment.md`): census first on every profile (~4.1–4.8 s after the link returns, 5 s probe pacing);
  all P1s in 4.3–10.8 s; 60–140 B per request vs. 1.2 MB as voice notes (~140–335× fewer bytes; 143–335× in the
  final run).
- P1 behind 200 P3s at 1.2 kbps / 20 % loss: arrived in 1.5 s (bound 4.65 s), no P3 byte sent meanwhile.

## Decisions and disagreements (per CLAUDE.md)

- **Models were not on the laptop.** CLAUDE.md said Whisper small/base and qwen2.5:1.5b were installed; they were
  not. I downloaded exactly those three models and nothing else: faster-whisper base + small into the default HF
  cache (same call as `scripts/download_models.py`), and Ollama **v0.40.11 portable zip** (no installer, no service)
  to `C:\Users\Tony\ollama-portable\` + `ollama pull qwen2.5:1.5b`. To remove: stop `ollama.exe`, delete that
  folder and `~/.ollama`. **After a reboot Ollama must be started again** (command below).
- **Ollama runs CPU-only.** Its CUDA path crashes on this RTX 5070 (`ptxas fatal: Memory allocation failure`,
  HTTP 500). Start it like this (matches the SPEC's CPU-only target):
  ```powershell
  $env:CUDA_VISIBLE_DEVICES="-1"; $env:OLLAMA_VULKAN="0"; $env:GGML_VK_VISIBLE_DEVICES="-1"; $env:OLLAMA_HOST="127.0.0.1:11434"
  Start-Process C:\Users\Tony\ollama-portable\ollama.exe serve -WindowStyle Hidden -RedirectStandardError C:\Users\Tony\ollama-portable\serve.log
  ```
- **Memory pressure.** The laptop's commit charge was ~46 of 47 GB during the build (user apps: Chrome 11.7 GB in
  48 processes, VS Code 3.9 GB, …; page file fixed at 16 GB). I did not close any app or change the page file.
  Consequences and fixes: Whisper and qwen did not fit together at first → added **`llm.use_mmap: true`** (memory-maps
  the model; Ollama's runner drops from ~1,170 to ~830 MB committed) — this is a small deviation from SPEC §6.4's
  exact `options` dict; with it both models run together. Also: failed LLM warm-ups are retried every 60 s; if
  `asr.model` can't load, the desk falls back to `asr.fast_model` (base) and says so; health warns below 1.5 GB free.
  **Before the demo, close browser tabs** — the desk then has plenty of room.
- **cp950 crash bugs found by running the acceptance commands without UTF-8 mode:** `run_demo.py` printed "・"
  (not in cp950) and `eval/demo_check.py` printed "⏳" → `UnicodeEncodeError`. Fixed: importing `darkshelter` switches
  stdout/stderr to `errors="replace"` when the console is not UTF-8 (Chinese still prints; ✓/⏳ become "?").
- **`SIO_UDP_CONNRESET`:** CPython has no such constant on Windows, so SPEC §8.3's `hasattr` guard never fires; the
  network code adds a `WSAIoctl` fallback (5 reset errors without it, 0 with it).
- **Prefix cache detection:** Ollama 0.40 reports the full `prompt_eval_count` even when cached, so `warmup()` also
  counts the prefix as cached when the 2nd prompt-eval *time* is under half the 1st (SPEC's count rule kept too).
- **FLAPPING within 180 s:** with SPEC's probe pacing (2.5–5 s) and 44 % round-trip loss, about half of each 10 s
  up-phase goes to detecting the link, so delivering all 60 takes ~150–190 s: one run finished at 156.9 s, the final
  run had 56/60 at the cap (the rest stayed queued and arrived 0.2 s after switching to GOOD). "Losses" counts
  tickets that never arrive (0 in every run). Faster probing would fix it but changes the PLAN's 2.5–5 s design.
- **EOC status never goes backwards** (409), so desk and EOC can't disagree (the desk keeps the max).
- **Clause splitting:** also on ：/: (real Whisper writes a colon as a pause); not on 、 (「奶粉、尿布都沒了」 must keep
  the ran-out word with 奶粉). Past-tense words suppress a symptom only before it.
- **Git:** SPEC §0 says "do not run git"; CLAUDE.md wins: local commits only.
- **Python:** CPython 3.12.15 venv; Python 3.11 syntax checked with `C:\Users\Tony\anaconda3\envs\coco311\python.exe`
  (`tests/test_portability.py` finds it, or set `DS_PY311`).

### Red-team fixes that depart from SPEC or need a team decision

- **H1 is only partly fixed.** SPEC §8.1 fixes the packet header at 7 bytes and the heartbeat at 33, so the packets
  carry no per-install id. As a result:
  - after a desk database is reset (or two desks share a `desk_id`), a ticket whose number clashes stays undelivered
    instead of showing as resolved;
  - a red banner sends the clerk straight to the backup channel;
  - every desk must have a unique `desk_id` (README).
- **Old heartbeats are ignored** (red-team M4). This is a softer reading of SPEC §8.4's "update the census". An older
  heartbeat is accepted again after 30 s of silence, because a reset desk restarts at boot 1.
- **The audio upload keeps `?clerk=` in the URL,** as SPEC §9 says. That route requires the `X-DarkShelter` header,
  which a page from another site can't send.
- **A P1 rescue at an outside address still sends the address,** even when the same sentence mentions a protection
  case. Life comes first; any other ticket from that sentence goes out without its location.
- **Self-harm (割腕, 自殺, 想不開, 吞藥) is P1 and goes out as 「自殺危機」.** SPEC doesn't list it as sensitive.
  **Team: decide** whether it should go out content-free, like 精神科.
- **Duplicate suggestions are stricter than SPEC's three conditions.** I treat those as necessary, not sufficient: the
  two requests must also be about the same person, at the same bed or address or in the same household.
- **`scrub_national_id` and `keep_audio` stay in the config because SPEC lists them.** The code always scrubs and
  never keeps audio, whatever they are set to.

## Open issues / what's left for the team

1. Record the 30 real test sentences (3–4 voices, some clerk-repeated) into `eval/audio/` and run
   `python eval/run_audio_eval.py` — the deck's CER/latency numbers must come from human voices.
2. One live microphone test on this laptop in Chrome/Edge (permission prompt, level meter, auto-stop).
3. Close browser tabs before the demo (memory); restart Ollama CPU-only after any reboot (command above).
4. The 12 % LLM hallucination rate (flagged fields) is above the 5 % target — consider tightening the prompt.
5. Safari/macOS untested.
6. **Red-team pass (04:40–05:00) and fixes (05:23–06:00): see `REDTEAM.md`.** Eight agents in two rounds found
   2 critical, 6 high, 8 medium and 12 low problems. Since then:
   - 23 are fixed, each with a regression test;
   - H1 is partly fixed (see the decisions above);
   - L7, L9 and L13 are kept on purpose, and L6 was withdrawn.

   What's left:
   - **H1:** a full fix needs a per-install id in the packet header, which means changing SPEC §8.1.
   - **L12:** `/api/state` is sent whole every second (611 kB at 300 tickets). That is fine for the demo, but a
     long real shift needs it to send only changes.
   - **Self-harm on the wire:** the team decision above.
   - **The fixes haven't been red-teamed yet,** above all the new request guard and the bigger lexicon (watch for
     false P1s).

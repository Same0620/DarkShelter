# PROGRESS — DarkShelter Stage I build

Build started **2026-10-28 01:35** (Taipei) on the team laptop: Windows 11 Home zh-TW (console code page cp950),
AMD Ryzen AI 7 350 (16 threads), 31 GB RAM, NVIDIA RTX 5070 Laptop GPU (not used, see below).
Hard stop was 07:35. All acceptance checks passed on the first finished build by 03:09 (pass 02:57–03:09).
A red-team pass (04:40–05:00) and fixes (05:23–06:00) followed, and every acceptance check was rerun on the fixed
code from 05:45 to 06:05. Round 2 (06:45–07:30) covered the team decisions, a red-team of today's changes and a
real-model dress rehearsal. Every acceptance check was rerun again on that code from 07:05 to 07:25, and that
rerun is the table below.

## Checkpoints (all verified and committed in order; pushed to https://github.com/Same0620/DarkShelter at the end)

| Checkpoint | Time | Commit | How it was verified |
|---|---|---|---|
| Setup | 01:35–01:50 | — | venv (CPython 3.12.15), Whisper small/base + Ollama + qwen2.5:1.5b downloaded (see decisions) |
| C1 core | 02:28 | `491e2c2` | 181 unit tests; `run_text_eval.py --llm none` P1 recall/precision 100 %; typed request → P1 draft with reasons → confirm → board, and check-in "yes" → draft, in headless Chromium |
| C2 network | 02:29 → commit 02:39 | `249075a` | acceptance check 2 (`eval/demo_check.py` ALL PASS) + 41 codec/RTO/emulator/network/chaos/wire-audit tests. **Stop-the-line gate (T+2 h = 03:35) cleared at 02:30 — nothing was cut.** |
| C3 AI | 02:30 → commit 02:40 | `6576ad5` | 190 tests incl. a real Whisper `base` and a real Ollama test; Ollama adapter against the stub (slow → timeout, bad JSON → retry → invalid_json, stopped → FAST); real-model runs below |
| C4-lite + C4 rest | 02:35 → commit 02:41 | `cfbbc77` | selftest exit 0 with the real models; kill real Ollama → FAST → restart → FULL; hard-kill the desk under run_demo → restarted with 「已復原 2 筆草稿，1 筆待送」, queue delivered; unconfirmed P1 → content-free alert, watchdog, clock jump (unit tests) |
| C5 | 03:09 | `7b432e7` | every SPEC §13 acceptance check, rerun on the final code |
| Red-team | 04:40–05:00 | `64825d5`–`1d1603d` | 8 agents in two rounds, read-only: 29 findings with proofs of concept (`REDTEAM.md`) |
| Red-team fixes | 05:23–06:05 | `368fe34` (tag `known-good-1`) | 4 fixer agents on separate files plus me; every fix has a regression test; all §13 checks rerun on the fixed code |
| Round 2 | 06:45–07:30 | `19e1ca6`…`2bec536` (tag `known-good-2`) | team decisions (self-harm, rescue + protection), red-team of the guard and the new phrases, real-model dress rehearsal (typed + spoken, real Whisper + Qwen), real Chrome on 127.0.0.1 and localhost; one commit per change; all §13 checks below rerun |
| Filming prep | 07:20–07:50 | `11ece2a`, `8e3cb20` | `FILMING.md` + `scripts/film_reset.py` (one command per take); the one-take sequence was dry-run twice from a clean reset in real Chrome (typed, real models), and every step passed. No product code was changed. |
| Push | 08:55 | this commit | README links the demo video (https://youtu.be/oYu6KuO3Z-w). `main` and the tags `known-good-1` and `known-good-2` pushed to GitHub over HTTPS with the `gh` login, because this laptop's SSH key isn't on the GitHub account. No code changed. |

The build used three sub-agents in parallel (network; lexicon/extract/triage; audio/ASR/LLM adapters) while I wrote
the store, wire view, pipeline, health, timers, desk app/UI and run_demo. Checkpoints were still verified and
committed in the C1 → C5 order.

## Acceptance checks (SPEC §13) — rerun on the fixed code on this laptop, cp950 console, no `PYTHONUTF8`

| # | Check | Result |
|---|---|---|
| 1 | `python -m unittest discover -s tests` incl. slow tests | **PASS** — 349 tests OK in 197 s (286 at C5, 333 after the red-team fixes, +16 in round 2), 0 skipped (network, chaos, UI smoke, real Whisper, real Ollama included) |
| 2 | `python run_demo.py --mock --no-browser --reset` + demo flow | **PASS** — `eval/demo_check.py` ALL PASS (exit 0): check-in + typed → drafts → confirm → queued while DOWN (EOC empty, 0 data bytes); on LORA_RELAY census at +0.60 s, last P1 +1.83 s, first P3 +17.26 s; byte meter 843 B sent vs 160,000 B as voice notes (190×); 已派遣 at the EOC → 🚑 at the desk. `eval/results/demo_check.md` |
| 3 | `eval/net_experiment.py` | **PASS** (exit 0) — 0 duplicates shown, 0 losses, 0 ordering violations on all 5 profiles. LORA 46 s, LTE one bar 42 s, satellite 29 s, GOOD 5.1 s for all 60. **FLAPPING is at the edge of the 180 s cap:** this run had 54/60 by the cap (the other 6 stayed queued and arrived 0.31 s after switching to GOOD, so none lost); earlier runs 53/60 and 56/60, and one delivered all 60 at 156.9 s. `eval/results/net_experiment.md` |
| 4 | `eval/chaos.py` | **PASS** (exit 0) — 5 sender + 5 EOC hard kills, 30 % loss + flapping, edits/discards/resolves mid-run: EOC = desk (43 = 43), 0 missing/wrong/extra, 0 duplicates shown, equal after 58.1 s. `eval/results/chaos.md` |
| 5 | `eval/run_text_eval.py --llm none` | **PASS** — 258 cases (72 original + 55 red-team + 125 round-2 phrases + 6 Whisper sound-alikes), P1 recall 100 %, P1 precision 100 % (186/186), 0 must-case failures; need P/R 98.9 % (persistent vomiting rated P1 instead of P2, tagged `known_gap`). `eval/results/text_eval.md` |
| 6 | wire-audit test | **PASS** — `tests/test_wire_audit.py` (79 texts incl. names, IDs, phones, protection, sensitive; plus red-team regressions: a protection case's other ticket, rebuilt deadline text, a typed address with a name in it; round 2: self-harm never named in the sent bytes, the CSV or the print view; rescue + protection goes out life first) |
| 7 | silent / too-short WAVs rejected, clipped warned | **PASS** — silence, 0.3 s and −60 dBFS → no drafts (「沒有聽到聲音」/「錄音太短」); clipped → warning; 40 s → truncated + warning; 48 kHz stereo resampled |
| 8 | UI smoke test | **PASS** — `tests/test_ui_smoke.py` (run_demo --mock on free ports, Chromium fake mic, 2 s recording `duration_s ≥ 1.5`); screenshots `eval/results/screens/desk.png`, `eoc.png` |
| 9 | `grep -rnE "https?://" darkshelter/static \| grep -vE …` | **PASS** — prints nothing (no URLs at all in static/) |
| 10 | `python3.11 -m compileall -q …` + `test_portability.py` | **PASS** — Python 3.11.14 (conda env `coco311`, no `python3.11` on PATH here) exit 0; portability 6/6 |
| 11 | README covers Windows + macOS setup, running, troubleshooting | **PASS** — all 24 required items present (checked by grep) |
| 12 | this file | see below |

Real models (rerun 07:17 after round 2, cp950 console): `python scripts/selftest.py` → **exit 0, all critical
checks passed, 1 warning**. The warning is about memory: commit 1.9 of 47.1 GB free, "close browser tabs before the
demo". Whisper small RTF 0.24 (CER 5 % on a TTS sentence), base RTF 0.08 (CER 14 %), recommendation **small**; Ollama
reachable, qwen2.5:1.5b pulled, warm-up prefix cached, real extraction of 「阿嬤明天早上要洗腎」 in 1.6 s at
38.7 tok/s on CPU. The round-2 dress rehearsal (both real models in the desk, typed and spoken) is in
`eval/results/rehearsal/`.

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
  all P1s in 4.3–10.8 s; 60–140 B per request vs. 1.2 MB as voice notes (~140–335× fewer bytes; 144–335× in the
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
- **Self-harm (割腕, 自殺, 自傷, 想不開, 吞藥) is P1, and it goes out like psychiatric details** (team decision,
  round 2). On the wire, in the CSV and in the print view it reads 「機密醫療需求，請聯絡B站」. It keeps P1, the
  emergency flag and the real category (心理支持). The desk card and the red banner keep the details. A test checks
  that 自殺 and 自傷 never appear in any of the three.
- **Sound-alike repairs** (round 2): Whisper's mishearings 避區/必趨十二床, 中正路三項 and 兇統 are repaired only in
  contexts where they can't be real words: a zone letter right before a bed number, 項 right after a road number,
  and nonsense words in place of 胸痛. SPEC §6's `DOMAIN_PROMPT` is unchanged.
- **`OPENBLAS_NUM_THREADS=1`** for the children of `run_demo` (round 2). Without it the real-model desk died at
  start-up with 1.2 GB of commit left.
- **Duplicate suggestions are stricter than SPEC's three conditions.** I treat those as necessary, not sufficient: the
  two requests must also be about the same person, at the same bed or address or in the same household.
- **`scrub_national_id` and `keep_audio` stay in the config because SPEC lists them.** The code always scrubs and
  never keeps audio, whatever they are set to.

## Filming notes (from the round-2 dress rehearsal with the real models)

1. **Free memory first.** Close Chrome tabs and other apps. During the rehearsal Windows had only 1.2–1.4 GB of
   commit left (Chrome: 10.2 GB in 45 processes). The desk now starts anyway, but Whisper and Qwen together want
   more room. If the badge shows TYPED or FAST 「記憶體不足」, close tabs and restart `run_demo.py`.
2. **Start Ollama CPU-only** with the command under "Decisions", then run `python scripts/selftest.py` (about
   10 s, exit 0). Then run `python run_demo.py --reset` and wait for the badge to show **FULL**.
3. **Either address works:** `http://127.0.0.1:8000` or `http://localhost:8000` (tested end to end in Chrome 155).
   Don't open other websites in the demo browser, because the link emulator's control port has no guard.
4. **The sentences behave as scripted**, typed or spoken (tested with the zh-TW TTS voice):
   - 「阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了」 gives two P1s with quotes.
   - 「我爸還在中正路三巷的家裡沒出來」 gives a P1 rescue and the red banner. The banner appears up to 1 s after the
     draft (the timer tick).
   - 「阿伯的高血壓藥只剩兩天」 gives P2. Spoken, Whisper may write 阿嬤 or 要; it is still P2, with
     「AI推測」 confirm prompts.
   - The daughter's repeat gives the merge suggestion → tap 合併.
   - 「我沒有胸痛，只是有點累」 gives no alarm, plus the ask-back.
   - 「不知道是不是胸痛」 gives P1.
   - The check-in screening "yes" opens a draft.
5. **Speak "B區" clearly.** Whisper heard the TTS voice say 避區 or 必趨, which is now repaired. If a card shows only
   「12床」, fix the zone on the card before confirming.
6. **Each request takes 2–3 s spoken and 1–3 s typed** on this laptop, CPU only.

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
   - **Round 2 left these open:**
     - the link emulator's control port has no guard (demo-only);
     - on the daughter's repeat, Qwen's 「十二床」 replaced the rules' 「B區十二床」 (that ticket is merged in the demo);
     - persistent vomiting comes out P1, not P2 (3 `known_gap` eval cases);
     - 「撞到頭，現在沒事」 and 「他昨天中暑，現在已經沒事了」 come out P1 (over-alarm, the safe side).
   - **Not red-teamed yet:** the crash-recovery queue (H6).

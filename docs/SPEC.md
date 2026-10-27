# DarkShelter — implementation spec for the coding agent (v3, final)

Read `docs/PLAN.md` first: it explains *why*. This file says *what to build*. Where they disagree, this file wins; note the disagreement in `PROGRESS.md`.
v3 folds in five bug-hunt debates (state, protocol, triage/privacy, runtime, buildability); see PLAN §12.

## 0. Ground rules

- **Repo root:** `/home/claude/darkshelter` (already has `docs/`). Do not run git commands; the team will publish the repo.
- **Your workspace:** Python 3.13 (also `/usr/bin/python3.11`), 2 CPUs, 7 GB RAM. **No internet for packages or models** (PyPI, Hugging Face and the Ollama registry are blocked). Preinstalled: `flask`, `numpy`, `psutil`, `requests`, `pillow`, `playwright` (Chromium), `cryptography`, `jinja2`, `scipy`. You may use WebFetch to read library docs.
- **Target runtime:** the team's laptop (Windows 10/11 zh-TW or macOS, CPU only, 8 GB RAM), Python 3.11/3.12 from python.org, offline after setup. Browser: Chrome/Edge at `http://127.0.0.1` (mic needs localhost); Safari must not crash.
- So the real speech (faster-whisper) and LLM (Ollama) adapters are written against their documented APIs and tested here with mocks **and a stub HTTP server for Ollama**. Everything else must be tested here.
- **Dependencies:** `requirements.txt` = `flask`, `numpy`, `faster-whisper`; optional `psutil`, `opencc-python-reimplemented`. Call Ollama with `urllib`. No pydantic, no FastAPI, no `requests`/`scipy` at runtime. **Optional imports are lazy and wrapped in `except Exception`** (not just `ImportError`: ctranslate2 without the VC++ runtime raises `OSError`) → feature off + health warning.
- **Portability:**
  - **Every text file I/O passes `encoding="utf-8"`.** zh-TW Windows otherwise defaults to cp950 and crashes on the lexicon.
  - Use `pathlib`. **Relative config paths resolve against the repo root, never the current directory.**
  - Everything binds to `127.0.0.1`.
  - Syntax must be **Python 3.11-compatible** (no PEP 695 generics, no nested same-quote f-strings), checked with `python3.11 -m compileall`.
  - Both Flask apps call `mimetypes.add_type("text/javascript", ".js")` and `mimetypes.add_type("text/css", ".css")` before creating `Flask()` (the Windows registry can serve them as text/plain).
- **Time:**
  - Network timers (RTO, in-flight timeouts, probe intervals) use `time.monotonic()`.
  - **SLA and reminder timers are stateless and computed from persisted wall-clock columns** (`created_wall`, `confirmed_wall`, `delivered_wall`, clamped ≥ 0), so they survive restarts and sleep.
  - Only relative ages go on the wire.
- **Robust loops:** every background thread's loop body runs under `try/except Exception` → event `<thread>_error` (≤ 1 per minute per type) → continue. A thread must never die from one bad input.
- **Frontend:** vanilla HTML/CSS/JS, **no CDN, no build step, no external fonts or images** (emoji are fine). UI text in Traditional Chinese with small English sub-labels. Polls are chained with `setTimeout`, never `setInterval`.
- Tests, evals and the smoke test create **both databases in a fresh temp directory**.
- Keep a short `PROGRESS.md`: checkpoint reached, what works, what's mocked, open issues, and the time at each checkpoint.

## 1. Repo layout

```
darkshelter/
  README.md  requirements.txt  config.example.json  run_demo.py  PROGRESS.md
  docs/PLAN.md  docs/SPEC.md
  darkshelter/
    __init__.py  config.py  codebook.py  lexicon.py  textnorm.py  audio.py
    asr.py  llm.py  extract.py  triage.py  dedupe.py  store.py  wireview.py
    pipeline.py  health.py  timers.py  desk_app.py  eoc_app.py
    net/  __init__.py  codec.py  rto.py  sender.py  receiver.py  linkemu.py  eoc_store.py
    static/desk/  index.html  app.js  recorder.js  style.css
    static/eoc/   index.html  app.js  style.css
  data/  codebook.json  lexicon.json  prompts/extract_system.txt  prompts/fewshot.json
  scripts/  selftest.py  download_models.py  make_test_wavs.py  ollama_stub.py
  eval/  text_cases.jsonl  run_text_eval.py  run_audio_eval.py  net_experiment.py  chaos.py
         audio/README.md  audio/labels_template.csv  results/   (generated)
  tests/  (stdlib unittest)
```

## 2. Processes and ports

`run_demo.py` starts three processes with `subprocess.Popen([sys.executable, "-m", ...], cwd=ROOT, stdout=<log>, stderr=STDOUT, env={**os.environ, "PYTHONUTF8": "1"})`. Logs go to `data/run/<name>.log`, so Windows QuickEdit can't freeze them. `PYTHONUTF8=1` keeps a redirected child from encoding Chinese with the ANSI code page and crashing on `print()`. Flags: `--mock` (no models), `--config path`, `--no-browser`, `--reset`.

**Before starting:**
1. **Bind-test all five ports:** 8000/8100/9100 as TCP, 9001/9002 as UDP (`SO_REUSEADDR` on POSIX only, never on Windows). If one is taken, abort and name it (an orphan from an earlier run is the usual cause).
2. **Database check:** if exactly one of `desk_db` / `eoc_db` exists, refuse with 「請用 --reset 同時重設兩個資料庫 / use --reset to reset both databases」. `--reset` deletes both.

**Startup and running:**
- Readiness = `GET /healthz` returns 200 on each HTTP port (the emulator's control port too).
- Then open `http://127.0.0.1:8000` and `http://127.0.0.1:8100` with `webbrowser`.
- A `poll()` loop each second restarts an exited child, never while stopping. When a child exits, print the last 20 lines of its log. After 3 restarts within a minute, stop everything and print the log path. Ctrl+C stops all three.
- Flask runs with `threaded=True, use_reloader=False`, and the werkzeug logger is set to WARNING. Background threads are `daemon=True`.

| Process | Module | Ports (config) |
|---|---|---|
| Link emulator | `darkshelter.net.linkemu` | UDP 9001 (desk side), HTTP control 9100 |
| EOC | `darkshelter.eoc_app` (+ receiver thread), own SQLite | HTTP 8100, UDP 9002 |
| Desk | `darkshelter.desk_app` (+ worker, sender, timers, health threads), own SQLite | HTTP 8000 |

## 3. Config (`config.example.json`; `config.py` merges the user file over defaults)

```json
{
  "desk_id": 7, "desk_letter": "B", "shelter_name": "示範國小 避難收容處所",
  "clerks": ["值班人員A", "值班人員B"],
  "backup_channel": {"zh": "請派人到村辦公室通報，或使用衛星電話", "en": "Send a runner to the village office or use the satellite phone"},
  "ports": {"desk_http": 8000, "eoc_http": 8100, "emu_udp": 9001, "eoc_udp": 9002, "emu_control_http": 9100},
  "paths": {"desk_db": "data/run/desk.db", "eoc_db": "data/run/eoc.db", "models_dir": null},
  "asr": {"model": "small", "fast_model": "base", "compute_type": "int8", "language": "zh",
          "beam_size": 1, "max_seconds": 30, "cpu_threads": 0},
  "llm": {"enabled": true, "url": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b",
          "timeout_s": 15, "warmup_timeout_s": 60, "num_predict": 320, "keep_alive": -1, "min_tokens_per_s": 4},
  "pipeline": {"backlog_fast_mode": 3},
  "triage": {"sla_minutes": {"emergency": 3, "1": 30, "2": 240, "3": 1440},
             "unconfirmed_p1_reminder_s": 120, "unconfirmed_p1_alert_s": 300},
  "net": {"mtu": 200, "window": 2, "rto_initial_s": 3.0, "rto_min_s": 0.5, "rto_max_s": 30,
          "rto_backoff_cap": 2.0, "heartbeat_s": 5, "probe_max_s": 5, "down_after_timeouts": 4,
          "hmac_key_hex": "<generated at first run if absent>"},
  "privacy": {"keep_audio": false, "scrub_national_id": true, "privacy_view_default": true},
  "voice_baseline_bytes_per_s": 2000
}
```
- `models_dir: null` means faster-whisper's default Hugging Face cache. That's where the team has already downloaded `small` and `base`, so nothing is fetched twice. If set, the path resolves against the repo root.
- `cpu_threads: 0` means `os.cpu_count()`.
- The EOC reads `{desk_id: hmac_key}` from the same file.
- `asr.model` is set by the team from `selftest.py`'s recommendation (no automatic calibration).

## 4. Data files

**`data/codebook.json`** (shared by desk and EOC; `"version": 1`):

`categories` — code, key, zh, en, `default_priority`, `person_specific`, `hidden` (never on the board, export or print), `required_slots`:

| code | key | zh | default P | person-specific | hidden |
|---|---|---|---|---|---|
| 0 | OTHER | 其他 | 3 | no | no |
| 1 | MEDICAL_EMERGENCY | 緊急醫療 | 1 | yes | no |
| 2 | LIFE_SUSTAINING | 維生醫療（洗腎、氧氣、電動醫材） | 1 | yes | no |
| 3 | MEDICATION | 藥品 | 2 | yes | no |
| 4 | PERSON_AT_RISK | 受困／失聯／走失 | 1 | yes | no |
| 5 | MOBILITY | 行動協助 | 2 | yes | no |
| 6 | INFANT_CHILD | 嬰幼兒 | 2 | yes | no |
| 7 | FOOD_WATER | 飲食 | 3 | no | no |
| 8 | SUPPLIES | 物資（毛毯、衣物、衛生用品） | 3 | no | no |
| 9 | MENTAL_HEALTH | 心理支持 | 2 | yes | no |
| 10 | COMMUNICATION | 通訊／聯絡家人／翻譯 | 3 | no | no |
| 11 | PET | 寵物 | 3 | no | no |
| 12 | INFRASTRUCTURE | 災情（道路、坍方、停電、停水） | 2 | no | no |
| 13 | PROTECTION | 保護個案 | 2 | yes | **yes** |

`subject_groups` (wire codes): unknown 0, infant 1, child 2, adult 3, elderly 4, pregnant 5, disabled 6.

`items` — at least 35, each with code, zh, en, category, `critical_med`, `powered_device`. Must include: 洗腎; 氧氣; 製氧機／呼吸器／抽痰機 (powered); 醫療器材用電; 胰島素; 抗凝血劑; 抗癲癇藥; 器官移植抗排斥藥; 心臟病藥（含硝化甘油）; 降血壓藥; 一般慢性處方藥; 藥品冷藏; 嬰兒奶粉; 嬰兒尿布; 成人紙尿褲; 輪椅; 助行器; 飲用水; 一般食物; 特殊飲食; 毛毯; 衣物; 衛生棉; 手機充電; 聯絡家人; 助聽器電池; 手語／翻譯; 寵物食品; 受困人員; 失聯人員; 走失兒童; 失智長輩走失; 尋找家人; 無人陪伴兒童; 胸痛／呼吸困難; 外傷出血; 待產; 道路中斷; 坍方／土石流; 停電; 停水; 保護個案. Critical medicines: insulin, anticoagulant, anti-epileptic, transplant drugs, heart medicine.

**Zone codes** (shared by tickets, households and the wire):
- `zone_code = (letter << 8) | min(bed, 255)`
- letters A–Z → 1–26; 甲乙丙丁東西南北 → 27–34
- 0 = none; 65535 = external location (carried in the summary)

**`data/lexicon.json`** — entries `{id, keywords[], not_followed_by[], category, item_code, red_flag, negatable, emergency (null|"medical"|"rescue"|"suicide"|"protection"), sensitive}`, plus pattern tables.

**Keywords** must include Traditional **and** Simplified forms and common mishearings (洗腎/洗肾/洗身, 胰島素/胰岛素/一島素, 氧氣/氧气/養氣, 輪椅/轮椅, 製氧機/制氧机).

**Red flags** (always P1 unless suppressed):

| Type | Keywords |
|---|---|
| Symptoms (`negatable: true`) | 呼吸困難, 喘不過氣, 喘不過來, 很喘, 無法呼吸, 沒辦法呼吸, 胸痛, 胸口痛, 胸悶, 胸口悶, 胸口很悶, 心臟不舒服, 大量出血, 一直流血, 流血不止, 流很多血, 昏倒, 暈倒, 昏過去, 暈過去, 昏迷, 叫不醒, 抽搐, 癲癇發作, 中風, 要生了, 破水, 陣痛 |
| Unresponsive (`negatable: false`) | 沒有呼吸 / 沒呼吸 / 没有呼吸 (`not_followed_by: [困難, 不順]`), 沒心跳, 沒反應, 沒意識 |
| Rescue (`negatable: false`) | 受困, 困在, 被壓, 被埋, 出不來, 還在家裡沒出來, 沒出來, 失聯, 聯絡不上, 失蹤, 走丟, 小孩走失, 找不到小孩, 找不到阿公, 找不到阿嬤, 失智走失, 沒有大人陪. 不見了 only right after a subject word (小孩/孩子/阿公/阿嬤/爸/媽…) |
| Suicide (`negatable: false`) | 想自殺, 不想活了, 想死, 想不開, 活不下去, 輕生, 自殘 |
| Devices (`negatable: false`) | 製氧機沒電, 呼吸器沒電, 氧氣快沒了 |

**Negation / past-tense rule** (only for `negatable` entries):
- **Suppress** the red flag if the text just before the keyword ends with `(沒有|沒|没有|没|不|不會|不是|未|無|无)(有|是|在|再|會)?`.
- **Do not suppress — keep P1 and add the ask-back — if** the clause contains an A-not-A or doubt form: 會不會, 是不是, 有沒有, 不知道.
- **Past words** (上次, 以前, 之前, 去年, 曾經) in the clause also suppress, unless the clause also contains 現在, 突然, 剛剛, 剛才, 今天 or 又. (過去 is *not* a past word; it breaks 昏過去.)
- A suppressed hit creates no need by itself. It adds the ask-back 「確認：現在有{keyword}嗎？」 to the utterance's needs (or one OTHER P3 need carrying it), with flag `negated`.

**Ran-out rule:** a clause with a non-symptom hit (item, medicine, device, 洗腎) and any of 沒/没/無/完/光/忘/斷/留在/沒帶 → deadline = **now**, unless the clause has 快/剩/還有 (then the time table applies). 「已經兩天沒洗腎了」, 「胰島素沒帶出來」 and 「寶寶沒奶粉了」 are all *now*.

**Protection:** `protection_words` → PROTECTION, emergency `protection`: 家暴, 他打我, 被打 (`not_followed_by: [到, 中, 針, 傷]`), 保護令, 性侵, 跟蹤, 不敢回家, 怕他找到. A false hidden case costs the clerk one recategorize tap; a missed one exposes the victim, so no second cue is required.

**`sensitive_words`** (medical confidentiality): 愛滋, HIV, PrEP, 精神科, 思覺失調, 憂鬱症的藥, 美沙冬.

**`subject_words` → subject group:**

| Words | Group |
|---|---|
| 阿嬤, 阿公, 奶奶, 爺爺, 老人家 | elderly |
| 寶寶, 嬰兒 | infant |
| 小孩, 孩子 | child |
| 孕婦, 懷孕 | pregnant |
| 坐輪椅, 身障, 聽障, 視障 | disabled |
| anything else | unknown |

**`time_phrases`:** see §6.6. Like every cue list, they also carry Simplified forms (e.g. 小时 for 小時, 礼拜 for 禮拜, 周 for 週, 后天 for 後天, 没 for 沒). **`data/prompts/`:** the prompt and few-shot in §7.

## 5. Data model (`store.py`, SQLite)

Each connection runs `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000`. Use one connection per thread.

```sql
CREATE TABLE tickets (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,   -- never reused; human code f"{letter}-{seq:03d}"
  status TEXT NOT NULL,                    -- draft | confirmed | merged | resolved | discarded
  version INTEGER NOT NULL DEFAULT 0,      -- changed only by rule R-V
  priority INTEGER, priority_reasons TEXT, rule_priority INTEGER,
  emergency TEXT, emergency_action TEXT, emergency_action_wall REAL,
  category INTEGER, item TEXT, item_code INTEGER, quantity INTEGER, quantity_approx INTEGER DEFAULT 0,
  subject TEXT, subject_group TEXT, location TEXT, zone_code INTEGER,
  household_id INTEGER, time_phrase TEXT, deadline_at REAL, deadline_text TEXT,
  summary TEXT, transcript TEXT, evidence TEXT, field_sources TEXT, flags TEXT, missing_info TEXT,
  dup_candidates TEXT, merged_into INTEGER, report_count INTEGER DEFAULT 1, merged_from TEXT,
  ai_suggestion TEXT,                      -- AI result that arrived after confirmation (never sent)
  job_id TEXT, audio_seconds REAL DEFAULT 0, clerk_edited TEXT,
  created_wall REAL, confirmed_wall REAL, confirmed_by TEXT,
  alert_sent INTEGER DEFAULT 0, sent_once INTEGER DEFAULT 0,
  acked_version INTEGER DEFAULT 0, acked_level INTEGER DEFAULT 0,
  first_sent_wall REAL, delivered_wall REAL, send_attempts INTEGER DEFAULT 0,
  eoc_status INTEGER DEFAULT 0, eoc_status_wall REAL, escalated_wall REAL,
  reminded_wall REAL
);
CREATE TABLE households (id INTEGER PRIMARY KEY AUTOINCREMENT, family_name TEXT, size INTEGER,
  zone_code INTEGER, location TEXT, elderly INTEGER, infants INTEGER, children INTEGER,
  pregnant INTEGER, disabled INTEGER, screening TEXT, checkin_wall REAL, checkout_wall REAL, clerk TEXT);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, wall REAL, seq INTEGER, type TEXT, clerk TEXT, data TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);  -- boot_id, status_seq_applied
```

Flags (JSON list): `asr_low_conf, ungrounded:<field>, raised_by_ai_guess, ai_only, llm_timeout, llm_error, fast_mode, negated, sensitive, unverified, escalated, approx, unclassified_red_flag`.

### R-T — transition table (every store write)
Each write is one transaction under a process-wide lock: `BEGIN IMMEDIATE` → `SELECT` the row → check the table → write → append to `events` → `COMMIT`. A violation raises `Conflict`: HTTP 409 `{error, zh, en}` for clerk calls, silently ignored by the timers and worker. The UI says "saved" only after the commit returns.

| Operation | Allowed from | Result / notes |
|---|---|---|
| `confirm(seq, fields, priority, clerk, reason=None)` | draft | confirmed; `reason` required if `priority > rule_priority` |
| `update_fields(seq, fields, clerk, expected_version)` | draft, confirmed | `StaleVersion` → 409 if versions differ |
| `raise_worried(seq, clerk)` | draft, confirmed | P1, reason `clerk_worried` |
| `log_emergency_action(seq, action, clerk)` | draft, confirmed | |
| `apply_ai_fields(seq, fields, sources, flags)` | draft → writes fields (skipping `clerk_edited`); confirmed → writes `ai_suggestion` JSON only; otherwise no-op | |
| `discard(seq, clerk, reason)` | draft, confirmed | discarded |
| `resolve(seq, clerk)` | confirmed | resolved (final) |
| `merge(src, dst, clerk)` | src: draft or confirmed; dst: draft or confirmed, ≠ src | src → merged. **A src with `version > 0` (already announced) needs a confirmed dst.** In the same txn: `dst.report_count += 1`, `dst.priority = min`, `dst.deadline_at = min`, emergencies combined; non-person-specific quantities add |
| `restore(seq, clerk)` | discarded, merged | → confirmed if `confirmed_wall` is set, else draft. For merged, the same txn reverses dst (`report_count − 1`, `quantity −= src.quantity`, floor 0) |
| `mark_alert_sent(seq)` | draft with `alert_sent = 0` and P1 | |
| `escalate(seq, sla_s)` | confirmed | No-op unless `eoc_status = 0` and (`escalated_wall` is NULL or older than `sla_s`); sets `escalated_wall` |
| `mark_sent(seqs)`, `mark_acked(...)`, `apply_status(...)` | any | Bookkeeping only |

### R-V — version rule
- `version += 1` **if and only if** `wire_view(ticket, now)`, compared at the same `now`, differs before vs after the write.
- No code assigns `version` directly. `mark_sent`, `mark_acked` and `apply_status` never bump it.
- The closed flag is set exactly when status ∈ {merged, discarded, resolved}.

### Send/ack bookkeeping
- **`mark_sent(seqs)`**, committed *before* `sendto`: `sent_once = 1`, `first_sent_wall` if NULL, `send_attempts + 1`.
- **`mark_acked(seq, v, level, eoc_status)`:**
  - `v > acked_version` → set (`acked_version = v`, `acked_level = level`)
  - `v == acked_version` → `acked_level = max(acked_level, level)`
  - `v < acked_version` or `v > version` → ignore
  - always: `eoc_status = max(old, new)`; set `delivered_wall` if NULL
- **`pending_units()`** = tickets where `wire_view ≠ None` and (`acked_version < version` or `acked_level < 2`), excluding units that failed to pack (until their version changes).

**Other store API:** `init_db(path)`, `new_boot() -> boot_id` (persisted, mod 256), `recover() -> {drafts, unsent}`, `create_draft(need, job_id, transcript, audio_seconds, flags, household_id=None) -> seq`, `checkin(household, clerk) -> id`, `checkout(id, clerk)`, `census()`, `board()`.

## 6. Modules

### 6.1 `textnorm.py`
- `to_traditional(s)`: OpenCC `s2twp` if importable (`HAS_OPENCC` in health), else identity. Lexicon and cue lists carry Simplified forms, so matching works without OpenCC.
- `normalize(s)`: to_traditional, full-width → half-width digits and letters, strip whitespace, unify punctuation.
- `parse_cn_number(s) -> (int|None, approx)`: 兩 → 2, 十二 → 12, 二十五 → 25, 一百零五 → 105, 十幾 → (15, approx), 幾個 → (None, approx).
- `scrub_ids(s)`: replace `[A-Z][1289]\d{8}` with `＊＊＊`. Phone numbers (`09\d{2}-?\d{3}-?\d{3}`) stay at the desk.
- `bigram_jaccard(a, b)`.

### 6.2 `audio.py`
- `decode_wav(data) -> (np.float32 mono @16 kHz, original_sr)`: 16-bit PCM at any rate or channel count, linear-interpolation resample. `AudioError` on non-WAV input.
- `check_audio(x) -> {ok, reason, warnings, duration_s, rms_dbfs, clip_ratio}`:
  - `too_short` under 0.6 s; `silent` if RMS < −50 dBFS
  - warning `clipped` if more than 2% of samples have |x| ≥ 0.99
  - `too_long`: truncate to `max_seconds` and warn

### 6.3 `asr.py`
- `AsrResult`: `text, avg_logprob, no_speech_prob, compression_ratio, language, low_conf, reasons, seconds, model`.
- **One loader:** `load_whisper(name, local_files_only) = WhisperModel(name, device="cpu", compute_type=cfg, cpu_threads=cfg, download_root=models_dir, local_files_only=local_files_only)`. Runtime and selftest pass `True`; `download_models.py` passes `False`.
- `FasterWhisperASR.transcribe(x)` passes the **numpy array** to `model.transcribe(x, language="zh", beam_size=1, temperature=0.0, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500}, initial_prompt=DOMAIN_PROMPT, condition_on_previous_text=False, no_speech_threshold=0.6)`. `temperature=0.0` means one decode, with no fallback ladder on hard clips. Materialise the segments, then compute duration-weighted `avg_logprob`, max `no_speech_prob` and max `compression_ratio`.
- `DOMAIN_PROMPT`: 「以下是台灣避難收容處所登記台的對話，使用繁體中文。常見詞：洗腎、胰島素、氧氣、製氧機、輪椅、尿布、奶粉、慢性處方箋、高血壓、糖尿病、阿嬤、阿公、受困、失聯、毛毯、飲用水、床位。」
- `filter_phantoms(text)`: remove, in both scripts, 請不吝點贊訂閱轉發打賞支持明鏡與點點欄目, 明鏡與點點, 謝謝觀看, 感謝觀看, 字幕由Amara.org社群提供, 字幕志願者, 優優獨播劇場, 請訂閱, 點贊, 中文字幕. Fewer than 2 meaningful characters left → `""`.
- `low_conf` if `avg_logprob < -1.0`, `no_speech_prob > 0.6`, `compression_ratio > 2.4`, or < 0.8 characters per second of speech.
- Two models are loaded lazily: `asr.model` for normal use and `asr.fast_model` for FAST mode.
- `MockASR`: returns the request's `mock_text`; without one it cycles through `DEMO_SENTENCES` (the PLAN §10 residents, a duplicate dialysis report, 「我沒有胸痛，只是有點累」).

### 6.4 `llm.py`
**`OllamaLLM(url, model, timeout_s, num_predict, keep_alive)`**
- `available()`: `GET /api/tags` with a 2 s timeout, cached 10 s; true only if the model is listed.
- **`extract(transcript, timeout_s=None)`** — no executor and no threads.
  - Call: `urllib.request.urlopen(req, timeout=max(1, deadline − monotonic()))`. With `stream: false` the socket timeout bounds the whole call, and closing the socket aborts generation.
  - Errors: `TimeoutError` or `URLError(reason=TimeoutError)` → `error="timeout"`; any other exception → `error="llm_error"`.
  - Request body: `{"model", "messages": [system, *fewshot, user], "format": SCHEMA, "stream": false, "keep_alive": -1, "options": {"temperature": 0, "num_predict": N, "num_ctx": 2048}}`.
  - Parse `message.content` as JSON and validate (§7). On invalid JSON, retry once if time remains, then `error="invalid_json"`.
  - Return `{needs, error, seconds, tokens_per_s = eval_count / (eval_duration / 1e9), prompt_eval_count, raw}`.
- **`warmup()`** = `extract("測試")` twice with `warmup_timeout_s` (same prefix and `num_ctx`). `prefix_cached` = the second `prompt_eval_count` is less than half the first. Warm-up and every re-warm (when `llm_ok` goes from False to True) are **enqueued as jobs on the pipeline worker**, never run from the health thread, so they can't overlap Whisper.
- Read Ollama's metrics with `.get(key, 0)`: it omits zero values. Compute `tokens_per_s` only when `eval_duration > 0`.
- Keep the fixed prefix (system prompt + few-shot) under about 500 tokens.

**`MockLLM`** converts `rules_extract` output into the same JSON after a 0.5 s delay. Options:
- `drop_index`: omit one need (tests the red-flag coverage invariant).
- `inject`: add a given need (tests suppressed-symptom handling).

**`scripts/ollama_stub.py`** is a tiny stdlib HTTP server for tests. It serves `/api/tags` and `/api/chat` in normal, slow (sleeps past the timeout), bad-JSON and stopped modes.

### 6.5 `extract.py`
`Need` fields: `category, item, item_code, quantity, quantity_approx, subject, subject_group, location, zone_code, time_phrase, deadline_at, deadline_text, emergency, evidence, field_sources, flags, red_flags (lexicon ids), suppressed (lexicon ids), ask_back`.

**`rules_extract(text, now) -> list[Need]`** (< 50 ms):
1. `normalize`, then `scrub_ids`.
2. Split into clauses on `，,。；;！!？?` and on 還有 / 另外 / 然後.
3. **Lexicon hits per clause.** Longest match first, non-overlapping, respecting `not_followed_by`. Then apply the negation / past-tense rule (§4). One need per distinct `item_code` in the utterance (per category when `item_code` is 0).
4. **Entity attachment:**
   - **Subject and location:** from the need's own clause, else the nearest clause that has one (earlier first).
   - **Time phrase and quantity:** from the need's own clause, else an *adjacent clause that holds no need* (following first). Never taken from another need's clause.
   - Location regexes, address first: external address `[一-鿿0-9]{1,12}(路|街|巷|弄|號|村|里|鄉|鎮|部落)` → zone 65535 + location text. A zone/bed `([A-Z甲乙丙丁東西南北]區)\s*(\d+|[一二三四五六七八九十]+)\s*(床|號)`, or `N床`. A bare `N號` counts as a bed only after `X區`.
   - Quantity: a number + 個/位/人/包/瓶/罐/條/件/台/支/份, or "N個人".
   - The ran-out rule (§4) and time phrases (§6.6) set `deadline_at` and `deadline_text`.
5. No hit at all → one OTHER need. Record evidence spans; `field_sources = "rules"`.

**`merge_llm(rules_needs, llm_needs, transcript, now) -> list[Need]`:**
- Match LLM needs to rules needs by `item_code` (look the LLM item up in the lexicon), else by category.
- Field precedence: clerk > rules (item/category when a red flag fired) > LLM grounded > rules > LLM ungrounded (kept, flagged `ungrounded:<field>`).
- LLM-only needs are kept with flag `ai_only`. **Rules needs with a red flag are never dropped.**
- **An LLM need that maps to a lexicon entry suppressed in this utterance becomes P2:** no banner, flag `negated`, and the ask-back (「不是胸痛，是胸口很悶」 stays visible; 「我沒有胸痛」 gets no 119).
- **Red-flag coverage invariant:** every non-suppressed red-flag hit must belong to a P1 need; otherwise add an OTHER P1 need with flag `unclassified_red_flag`.

**`grounded(field, value, transcript)`:** normalized substring; items via any keyword of their lexicon entry; quantities via digits or Chinese numerals; zones via parsed equality.

### 6.6 `triage.py`
**`parse_deadline(clause, now) -> (deadline_at, deadline_text)`** — minimum over matches; `deadline_text` shown to the clerk, e.g. 「10/28 08:00」:

| Phrase | Deadline |
|---|---|
| Ran-out rule (§4), 現在, 馬上, 立刻 | now |
| 快沒了, 快用完, 剩一點 | now + 12 h |
| 電池還有 N 小時 / N 小時 | now + N h |
| 今天, 今晚 | today 23:00, at least now + 2 h |
| 明天早上, 明早 | tomorrow's date 08:00. **00:00–05:59 → today 08:00** |
| 明天中午, 明天 | tomorrow's date 12:00, same night rule |
| 後天 | now + 48 h |
| N 天 | now + 24N h |
| 星期X, 禮拜X, 週X | next such weekday 08:00. **Same weekday → today 08:00, or now if already past** |
| 下星期X | the same, + 7 days |
| N天前, N小時前, 昨天, 前天 | *not* deadlines (elapsed time; the ran-out rule covers 「兩天沒洗腎」) |

**`assign_priority(need, now) -> (priority, reasons, emergency)`** — lower number = more urgent; the minimum over every rule that fires:

| Category / condition | Priority |
|---|---|
| Red flag, not suppressed | P1 + the entry's emergency type |
| MEDICAL_EMERGENCY | P1, emergency `medical` |
| PERSON_AT_RISK | P1, emergency `rescue` (尋找家人 about an adult with no danger words → P2) |
| LIFE_SUSTAINING | **P1 if the deadline is unknown or ≤ 36 h**, else P2. Powered device: deadline = battery time |
| MEDICATION, critical medicine | **P1 if ≤ 12 h or unknown**, else P2 |
| MEDICATION, other | P2 if ≤ 48 h or unknown, else P3. 藥品冷藏 → P2 |
| INFANT_CHILD | P1 for formula or water within ≤ 6 h (ran out = now), else P2 |
| MENTAL_HEALTH | P1 + emergency `suicide` if a suicide word fired, else P2 |
| PROTECTION | P2, emergency `protection` |
| MOBILITY, INFRASTRUCTURE | P2 |
| FOOD_WATER, SUPPLIES | P3, or P2 for a special diet or a vulnerable subject (elderly, infant, pregnant, disabled) |
| COMMUNICATION | P3, or P2 for a hearing aid, sign language or translation |
| PET, OTHER | P3 |

Reasons are `{code, zh, en}`, e.g. `{"code": "life_sustaining_36h", "zh": "維生醫療36小時內", "en": "Life-sustaining care within 36 h"}`.

**Pipeline invariant** after merging:
- `priority = min(rules-only, merged)`
- `deadline_at = min(rules-only, merged)`
- `emergency` = union

So the AI can only raise urgency. If it was raised only by an ungrounded field, flag `raised_by_ai_guess` and add an ask-back. The clerk lowers urgency only with a reason, and raises it with one tap.

**`missing_info(need)`** builds questions from `required_slots`:

| Category | Questions |
|---|---|
| MEDICATION | 「請問是哪一種藥？」「藥什麼時候會用完？」「是誰需要？在哪一床？」 |
| LIFE_SUSTAINING | 「下次洗腎是什麼時候？」「氧氣／機器電池還能撐多久？」 |
| PERSON_AT_RISK | 「人在哪裡？（地址或地標）」「有幾個人？」「有人受傷嗎？」「孩子／長輩的外觀特徵？」 |
| INFANT_CHILD | 「寶寶幾個月大？」 |
| Supplies | 「需要幾份？」 |
| Person-specific with no location | 「請問在哪一區、幾號床？」 |

### 6.7 `dedupe.py`
- **`find_candidates(ticket, open_tickets, now)`** — a candidate must meet all three:
  1. Same `item_code` (or category when 0).
  2. At least one of:
     - same zone (**both zone codes ∉ {0, 65535}**; two external locations count only if their location text is equal)
     - same subject (**both non-empty**)
     - `bigram_jaccard(summaries) ≥ 0.5`
  3. Created within 12 h.
- Person-specific: suggest only. Supplies: "add to existing". PROTECTION is never deduped.

### 6.8 `wireview.py` — the only code that decides what leaves the desk
`wire_view(ticket, now, desk_letter) -> dict | None`, checked in this order:
1. `status ∈ {discarded, merged, resolved}` and not `sent_once` → **None** (a never-sent ticket closed while offline is never sent).
2. `confirmed_wall IS NULL` (never confirmed, any status) → if `alert_sent`, the **content-free alert**: P1, category 0, item 0, quantity 0, zone 0, flags `unverified` (+ closed if status is closed), summary 「B站有未確認的緊急需求」. Otherwise **None**. Unreviewed AI text never leaves the desk.
3. **PROTECTION** → category 13, item 0, quantity 0, zone 0, summary 「保護個案，請社工聯絡B站」.
4. **`sensitive`** → the real category, item 0, summary 「機密醫療需求，請聯絡B站」.
5. **Normal** → the summary is built **only from codes and parsed fields**: `{location}{subject_group zh}{codebook item zh | category zh}{quantity}{deadline_text}`, e.g. 「B區12床 長者 洗腎 10/28 08:00」 or 「中正路三巷 受困人員」. Location comes first so truncation never cuts the address. Truncated on a character boundary to ≤ 60 UTF-8 bytes. **Free-text subject, item and transcript never leave the desk.**

CSV export and the board's print view use the same builder (`hidden` categories excluded).

### 6.9 `pipeline.py`
- `submit_audio(wav_bytes, clerk, mock_text=None, household_id=None) -> job_id`; `submit_text(text, clerk, household_id=None) -> job_id`; `get_job(id)`.
- One worker thread and a `queue.PriorityQueue` (intake jobs priority 0, LLM warm-up jobs priority 1, so a clerk never queues behind a warm-up). Never run ASR and the LLM concurrently. The worker updates its watchdog heartbeat at every stage boundary. Stages: `queued → checking → transcribing → understanding → refining → done | error`, with timings.
- **Audio:**
  1. `decode_wav` → `check_audio`. Silent or too short → done with a warning, **no drafts**.
  2. ASR, using `fast_model` if the backlog ≥ `backlog_fast_mode` or the mode is FAST.
  3. `filter_phantoms`. Empty → warning 「沒有聽到清楚的話」, no drafts.
- **Then, audio and text alike:**
  1. `rules_extract` → triage → dedupe → **create drafts immediately**.
  2. **LLM gate:** run the LLM if the mode is FULL or TYPED and no FAST condition holds. Then `llm.extract` → `merge_llm` → re-triage with the invariant → `apply_ai_fields`. On error, drafts keep the rules result plus a flag.
- Audio is never written to disk unless `privacy.keep_audio` is true.
- **Check-in screening** — every "yes" creates a draft with the household's zone, the members' subject group and the ask-back questions:

| Question | Draft category |
|---|---|
| q1 | LIFE_SUSTAINING |
| q2 | MEDICATION |
| q3 | MOBILITY if disabled > 0; INFANT_CHILD if infants + pregnant > 0 (both → two drafts; neither → INFANT_CHILD) |
| q4 | PERSON_AT_RISK |
| q5 | OTHER |

### 6.10 `health.py`
- **Startup, in the background:** check the Python version, imports, DB writability and free disk. Load ASR (`local_files_only=True`; a failure means TYPED with the reason). Run Ollama `available` + `warmup`; below `min_tokens_per_s`, the LLM runs on demand only. Read the battery via `psutil` if available.
- **Every 10 s, recompute the mode. Precedence: forced > PAPER > TYPED > FAST > FULL.**
  - PAPER: forced, or battery < 5%.
  - TYPED: ASR failed or the browser reported no mic.
  - FAST: LLM unavailable or slow, backlog ≥ 3, or battery < 15% and not charging.
  - FULL: otherwise.
- **Watchdog:** threads register a heartbeat. **Restart a thread only if `not is_alive()`** (logged, red light). A stale heartbeat (worker > 60 s, others > 10 s) gets an amber light and an event, **never a restart** (a second worker would run ASR and the LLM together).
- **Exposes:** `{mode, reason_zh, reason_en, asr_model, llm_ok, llm_tps, prefix_cached, opencc, battery, disk_free_mb, threads, warnings[]}`.

### 6.11 `timers.py` (stateless check every 2 s; ages from persisted wall columns)
- **Unconfirmed P1 draft:** age from `created_wall`.
  - At `unconfirmed_p1_reminder_s` (and `reminded_wall` NULL): UI reminder (flashing banner + short WebAudio beep); set `reminded_wall`.
  - At `unconfirmed_p1_alert_s`: `mark_alert_sent` → the content-free alert is queued. 0 disables this.
- **Emergency:**
  - The banner shows until `emergency_action` is logged. Actions: `medic_told`, `called_119`, `119_unreachable`, `runner_sent`, `protection_private`. `119_unreachable` immediately shows the backup channel.
  - `sla_minutes.emergency` after `confirmed_wall` with `eoc_status = 0`: show the backup channel and `escalate`. Not delivered at all by then: backup channel + print button (`window.print()`).
- **Other P1:**
  - Not delivered `sla_minutes["1"]` after confirmation: banner 「網路中斷且有P1待送：請派人通報／使用衛星電話」 + print.
  - Delivered but not acknowledged: `escalate`. **Only the first escalation re-sends** (R-V); later periods show the banner and log an event.
- **Clock-jump detection:** compare Δwall with Δmonotonic. A gap > 5 s logs `clock_jump` and tells the sender to requeue in-flight datagrams and `rto.reset()`.

## 7. LLM prompt and schema

Use simple types only (empty string or 0 = "not said"):

```json
{"type": "object", "required": ["needs"], "properties": {"needs": {"type": "array", "maxItems": 5, "items": {
  "type": "object",
  "required": ["category", "item", "quantity", "subject", "subject_group", "location", "time_phrase", "summary"],
  "properties": {
    "category": {"type": "string", "enum": ["MEDICAL_EMERGENCY", "LIFE_SUSTAINING", "MEDICATION", "PERSON_AT_RISK", "MOBILITY", "INFANT_CHILD", "FOOD_WATER", "SUPPLIES", "MENTAL_HEALTH", "COMMUNICATION", "PET", "INFRASTRUCTURE", "PROTECTION", "OTHER"]},
    "item": {"type": "string"}, "quantity": {"type": "integer"}, "subject": {"type": "string"},
    "subject_group": {"type": "string", "enum": ["infant", "child", "adult", "elderly", "pregnant", "disabled", "unknown"]},
    "location": {"type": "string"}, "time_phrase": {"type": "string"}, "summary": {"type": "string"}}}}}}
```

Validation: ≤ 5 needs; category in the enum; strings truncated to 40 characters; quantity clamped to 0–10000 (0 = unknown); empty strings mean null. The LLM's `summary` is shown at the desk only.

System prompt (`data/prompts/extract_system.txt`):

> 你是避難收容處所登記台的資料整理助手。把話整理成 JSON。
> 1. 每個不同需求一筆，最多 5 筆；忽略寒暄和故事。
> 2. item、subject、location、time_phrase 必須抄錄原文；沒說就填 ""，不要猜。quantity 只填明確說出的數字，沒說填 0。
> 3. category：MEDICAL_EMERGENCY（胸痛、呼吸困難、出血、昏迷、待產）、LIFE_SUSTAINING（洗腎、氧氣、製氧機等）、MEDICATION、PERSON_AT_RISK（受困、失聯、走失）、MOBILITY、INFANT_CHILD、FOOD_WATER、SUPPLIES、MENTAL_HEALTH、COMMUNICATION、PET、INFRASTRUCTURE、PROTECTION（家暴、被威脅）、OTHER。
> 4. 「沒有胸痛」「以前中風」不是現在的急症。summary 20 字內。不要判斷緊急程度。只輸出 JSON。

Few-shot (`data/prompts/fewshot.json`; user format 「逐字稿：…」), exactly two:
1. 「我阿嬤在B區12床，她明天早上要洗腎，然後她的胰島素今天就用完了。」 → LIFE_SUSTAINING 洗腎 (阿嬤, elderly, B區12床, 明天早上) and MEDICATION 胰島素 (今天就用完了).
2. 「我們家四個人需要毛毯，還有我兒子手機沒電了，想聯絡他爸爸。」 → SUPPLIES 毛毯 quantity 4, and COMMUNICATION 手機沒電 (我兒子, unknown).

## 8. Network (`darkshelter/net/`)

### 8.1 Codec (`codec.py`, big-endian `struct`)

**Datagram layout:** header (7 B: `ver u8 = 1`, `type u8`, `desk_id u16`, `boot_id u8`, `msg_seq u16`) + `count u8` + body + `HMAC-SHA256(key, everything before)[:8]`. Fixed overhead: 16 B. **Every datagram ≤ `mtu`** (200).

| type | name | body |
|---|---|---|
| 1 | DATA_DIGEST | `count` × digest (18 B). Kept in the codec; the Stage I sender does not use it |
| 2 | DATA_SUMMARY | `count` × (digest + `len u8` + UTF-8 summary ≤ 60 B). **Every ticket is sent at this level** |
| 3 | ACK | `count` × (`ticket_seq u32, version u16, level u8, eoc_status u8`) = 8 B; the header echoes the acked datagram's `boot_id` and `msg_seq` |
| 4 | HEARTBEAT | census: `people u16, elderly u8, infant_child u8, disabled u8, pregnant u8, q_p1 u16, q_p2 u16, q_p3 u16, battery u8 (255 unknown), status_seq_applied u32` (count = 0) → a 33 B datagram |
| 5 | HB_REPLY | `count` × (`ticket_seq u32, eoc_status u8, status_seq u32`) = 9 B; the header echoes the heartbeat |

**Digest record** (18 B): `ticket_seq u32, version u16, prio_cat u8` (priority in bits 7–6, category in 5–0), `item_code u16, quantity u16, subject_group u8` (codes §4), `flags u8, age_min u16, deadline_h u8, zone u16` (§4).

**Flag bits:** 0 sensitive/protection, 1 unverified, 2 escalated, 3 closed, 4 approx quantity, 5 emergency, 6 external location, 7 report_count > 1.

**Clamping:** every integer is clamped to its field.
- `deadline_h` = 255 if there's no deadline, else `clamp(floor(hours_left), 0, 254)` (overdue → 0).
- `age_min`, `quantity` and u16 census fields → [0, 65535]; u8 census fields → ≤ 255.

**API:**
- `pack_summary(views, mtu, key, desk_id, boot_id, msg_seq) -> (bytes, n_packed)` fills one datagram in order.
- `unpack(datagram, keys) -> Message` raises only `BadMac`, `BadVersion` or `Truncated` on arbitrary bytes. Use `hmac.compare_digest`.
- Ages and deadlines are relative, so the EOC computes times on its own clock.

### 8.2 `rto.py`
Jacobson/Karels.
- First sample: `SRTT = R`, `RTTVAR = R/2`.
- Later samples: `RTTVAR = 3/4·RTTVAR + 1/4·|SRTT − R|`, then `SRTT = 7/8·SRTT + 1/8·R`.
- `rto_base = clamp(SRTT + max(0.01, 4·RTTVAR), rto_min, rto_max)`; initial `rto_initial_s`.
- `on_timeout()` doubles the current RTO, capped at `rto_backoff_cap × rto_base` and `rto_max`. A valid sample resets RTO to `rto_base`; `reset()` returns to the initial state.
- **Karn's rule:** sample only from datagrams whose units were all on their first try, with the echoed `boot_id` equal to the current boot.
- Stats expose `rto_base` and `rto`.

### 8.3 `sender.py` (desk; thread with one non-blocking UDP socket)

**Socket setup.** On Windows: `if hasattr(socket, "SIO_UDP_CONNRESET"): sock.ioctl(socket.SIO_UDP_CONNRESET, False)`. Every `sendto`/`recvfrom` is wrapped in `try/except OSError` (counted). The loop body is also under `except Exception` (§0).

**Loop, every 20–50 ms:**
1. **Receive** everything waiting.
   - ACK: `store.mark_acked` per entry (safe across boots), RTT sample if allowed, drop the matching in-flight datagram, link = UP, reset the probe backoff.
   - HB_REPLY: `store.apply_status`, RTT sample, link = UP.
   - Bad MAC: count and drop.
2. **Timeouts.** An in-flight datagram older than RTO is dropped from the in-flight table; its units become eligible again with their *current* version. Call `rto.on_timeout()`. After `down_after_timeouts` consecutive timeouts → DOWN.
3. **Link DOWN or UNKNOWN.** Send no DATA. Send a HEARTBEAT probe at an interval of `min(2^k, probe_max_s) × uniform(0.5, 1)` s (k = probes since the last reply). **Probes ignore the P1 reservation.** Any reply → UP. `reset_probe()` (called by `/api/link/profile`) sets k = 0 and probes at once. The census therefore arrives first after an outage.
4. **Link UP:** fill the window (`window` slots; heartbeats also use a slot).
   - Candidates: `wire_view` of `store.pending_units(exclude=in-flight)`, ordered by `(priority, deadline_at or ∞, created_wall)`.
   - **P1 reservation:** while any P1 unit is unacknowledged (pending or in flight), only P1 datagrams may be sent, and no heartbeats.
   - Pack one DATA_SUMMARY datagram from the head of the list.
   - Before `sendto`, **commit `store.mark_sent(seqs)`**. Record `{msg_seq: units, sent_at_mono, bytes, first_try}`.
   - A unit that fails to pack is skipped with one event and excluded from `pending_units` and the P1 reservation until its version changes.
5. **Heartbeat** every `heartbeat_s` while UP and no P1 is pending.
6. **Clock jump** (from `timers`): requeue all in-flight datagrams and call `rto.reset()`.

**Stats** (thread-safe): `link_state, srtt, rto, rto_base, throughput_Bps (EWMA), data_bytes_up, bytes_up, bytes_down, datagrams_up, retransmissions, bad_mac, socket_errors, acked_units, last_ack_wall, backlog_bytes, voice_equiv_bytes`. `voice_equiv_bytes` = Σ over delivered tickets of `audio_seconds × voice_baseline_bytes_per_s`; typed tickets count as a nominal 10 s, labelled so in the UI.

Runs standalone too: `python -m darkshelter.net.sender --db … --config …`.

### 8.4 `receiver.py` + `eoc_store.py` (EOC process; own SQLite with the same pragmas, `BEGIN IMMEDIATE`, catch-all loop, Windows `SIO_UDP_CONNRESET` fix)

**Tables:**
- `eoc_tickets(desk_id, seq, version, level, priority, category, item_code, quantity, subject_group, flags, age_min_at_rx, rx_wall, created_est_wall, deadline_est_wall, zone, summary, status, status_seq, dup_count)`, primary key `(desk_id, seq)`
- `desks(desk_id, last_seen, census JSON, rtt)`
- `stats`

**DATA:** verify; then, for each record, in one transaction:
- A newer version replaces the content and keeps the EOC-owned `status`. If the newer version arrives at level 1, set `summary = NULL` and `level = 1`.
- Equal version with a higher level → add the summary.
- Otherwise `dup_count += 1`.

**COMMIT, then** ack every record, including duplicates and older versions, to the source address, echoing `boot_id` and `msg_seq`.

**HEARTBEAT:** update the census and `last_seen` (commit), then reply HB_REPLY with that desk's status changes where `status_seq > status_seq_applied`, in order, as many as fit.

**Status from the EOC UI:** `{1 ACKNOWLEDGED, 2 DISPATCHED, 3 RESOLVED}`, with `status_seq = 1 + COALESCE(MAX(status_seq), 0)` for that desk, computed inside the same transaction.

### 8.5 `linkemu.py`
- **Relay:** UDP on `emu_udp`. Desk → EOC traffic goes out through a dedicated socket; replies return to the desk's last-seen address.
- **Per direction:**
  - outage: **drop, never buffer**
  - random loss `p` from a **seeded** `random.Random(seed)`
  - MTU check (drop oversize datagrams)
  - token-bucket rate (bucket = 1 × MTU) with a **FIFO of at most 4 datagrams, tail-drop**
  - delay ± jitter
- **Scheduling:** one heap and a `select` timeout until the next event; never `sleep` in the forwarding path. Switching profile clears both queues. Catch-all loop.

| Profile | rate (bps) | loss each way | delay ± jitter (ms) | MTU |
|---|---|---|---|---|
| DOWN | — | 100% | — | — |
| LORA_RELAY | 1,200 | 10% | 400 ± 200 | 200 |
| LTE_ONE_BAR | 16,000 | 25% | 300 ± 300 | 1,200 |
| SATELLITE | 2,400 | 5% | 800 ± 100 | 340 |
| GOOD | 1,000,000 | 0% | 20 ± 5 | 1,400 |
| FLAPPING | LTE_ONE_BAR for 10 s, DOWN for 20 s, repeating | | | |

**HTTP control** (stdlib, threaded): `GET /healthz`; `GET /profile`; `GET /profile?name=X`; `POST /custom` `{rate_bps, loss, delay_ms, jitter_ms, mtu, seed}`; `GET /stats` (bytes and datagrams per direction, drops by reason). Starts in DOWN. Also usable in-process: `LinkEmulator(..., seed).start()` / `.stop()`.

## 9. HTTP APIs

**Desk (`desk_app.py`)** — JSON unless noted. Reject bodies > 5 MB. Errors: `{"error", "zh", "en"}`; state conflicts → **409**.

| Route | Purpose |
|---|---|
| `GET /healthz` | Readiness |
| `GET /` | Desk UI |
| `GET /api/state` | Everything the UI needs, polled with a chained `setTimeout` every 1 s: drafts, board (hidden categories as a count only), jobs, health/mode, sender and emulator stats, byte meter, census, banners |
| `POST /api/checkin` | `{family_name?, size, zone, location?, elderly, infants, children, pregnant, disabled, screening: {q1..q5}, clerk}` → `{household_id, drafts}` |
| `POST /api/checkout/<id>` · `GET /api/households` | |
| `POST /api/intake/audio?clerk=…&household=…` | Body `audio/wav`; mock mode accepts `mock_text` → `{job_id}` |
| `POST /api/intake/text` | `{text, clerk, household_id?}` → `{job_id}` |
| `GET /api/jobs/<id>` | |
| `POST /api/tickets/<seq>/…` | `update` `{fields, expected_version}` · `confirm` `{fields, priority, reason?}` · `worried` · `emergency_action` `{action}` · `discard` `{reason}` · `restore` · `merge` `{target}` · `resolve` · `refine`. All take `clerk` |
| `POST /api/link/profile` | `{name}` → emulator; calls `sender.reset_probe()` |
| `POST /api/mode` | `{mode or null}` |
| `POST /api/mic` | `{ok}` |
| `GET /export.csv` | Same builder as the wire; hidden categories and names excluded |

Screening questions q1–q5:
1. 有人需要洗腎、氧氣或用電的醫療器材嗎？
2. 有人每天一定要吃的藥（例如胰島素）嗎？
3. 有嬰幼兒、孕婦或行動不便的人嗎？
4. 有家人失聯，或還有人在家裡沒出來嗎？
5. 還有其他需要幫忙的嗎（寵物、聯絡家人）？

**EOC (`eoc_app.py`):** `GET /healthz`; `GET /`; `GET /api/state`; `POST /api/tickets/<desk_id>/<seq>/status` `{status}`.

## 10. UI

**Desk:** a single page with two intake tabs (登記 Check-in / 需求 Requests) and three columns. Large fonts; colour always paired with an icon or text.

1. **Intake (left).**
   - Consent line 「需求會記錄並傳給應變中心；錄音不保存。也可只用打字。」 with a 只打字 toggle.
   - Record button 「錄音（建議由值班人員複述重點）」 with a live level meter.
   - Typed box (Enter submits) and quick-pick chips (洗腎, 胰島素, 氧氣, 製氧機, 嬰兒奶粉, 尿布, 輪椅, 毛毯, 飲用水, 受困, 失聯).
   - Job progress line. The check-in tab is a short form with the 5 questions.
2. **Draft cards (left),** **keyed by `seq`:**
   - Priority pill with reasons and the resolved deadline (「→ 10/28 08:00」).
   - Emergency banner with action buttons (已通知現場醫護 · 已撥119 · 119打不通 · 已派人通報; protection: 已移到隱密處／聯絡社工 113). It stays until one is clicked.
   - Editable fields with evidence quotes; amber outline for ungrounded or low-confidence fields; ask-back chips; a duplicate suggestion [合併] / [不是重複].
   - **Confirm is one tap; 我擔心→P1 is one tap; lowering below the rule floor needs a reason.** Discard needs a reason for P1 or red flags, with a 10 s undo.
   - **Never re-render a card that has a focused or edited input.** Show a 「AI 已更新」 badge and apply on blur or confirm.
3. **Board (middle).**
   - P1 / P2 / P3 and a draft counter.
   - **Privacy view on by default** (code, category icon, zone, status; click to reveal). Protection cases appear only as a count.
   - Status icons: ⏳ ↑ ✓ ✓✓ 🚑 ✔ ⚠.
   - `@media print` CSS gives a P1-first printable board; the print button calls `window.print()`.
4. **Network and system (right).**
   - Link badge; profile buttons (斷線, LoRa中繼, LTE一格, 衛星, 時斷時續, 正常); queue counts.
   - **Byte meter:** data bytes actually sent vs. as voice notes, with the ratio; total bytes up/down in small text.
   - SRTT / RTO / throughput / retransmissions.
   - Mode badge with a plain-language reason; ASR model; LLM status and tokens/s; battery; thread lights.
   - Census; clerk selector; banners.

**EOC:**
- One board grouped by priority. Each row: desk code, summary, category/item, quantity, zone or external, age, delivery latency, and flags (UNVERIFIED, ESCALATED, 機密/保護, closed).
- Buttons: 已收到 / 已派遣 / 已解決.
- Panels: desk census with last-seen time, received count, duplicates suppressed, bytes received.

**`recorder.js` (ScriptProcessor only):**
- `getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}})`; `AudioContext` created or resumed in the click handler; `ScriptProcessorNode(4096)`.
- **Endpointing:**
  - Noise floor = running minimum of 100 ms-frame RMS.
  - Speech = RMS > max(3 × floor, −45 dBFS).
  - Auto-stop 2.5 s after the last speech frame, but only once speech has been heard.
  - No speech within 8 s → stop (the server answers 「沒有聽到聲音」).
  - A second click stops; hard stop at 30 s.
- On stop, encode 16-bit mono WAV at the native rate (the server resamples) and POST it.

## 11. Tests and evaluation (all runnable here)

`tests/` uses stdlib `unittest`; `python -m unittest discover -s tests` must pass. Slow tests are skippable with `DS_FAST=1`. **All randomness is seeded.**

**Portability and text processing**
- **`test_portability.py`** (AST checks):
  - fails on a text-mode `open`/`read_text`/`write_text` without `encoding=`
  - fails on an import outside stdlib + {flask, werkzeug, jinja2, numpy} unless it's inside `try/except`
- textnorm: numerals, ID scrubbing, Simplified input.
- audio: WAVs from `scripts/make_test_wavs.py` (silence, 0.3 s, clipped sine, 40 s, 48 kHz stereo).
- asr: phantom filter; MockASR cycling.

**Extraction and triage**
- Must be P1:
  - 「阿公已經兩天沒洗腎了」
  - 「胰島素沒了」, 「胰島素沒帶出來」, 「寶寶沒奶粉了」
  - 「他沒有呼吸了」
  - 「會不會是中風」, 「不知道是不是胸痛」, 「之前都好好的現在突然胸痛」
  - 「製氧機電池還有兩小時」 (deadline +2 h)
  - lost child; wandering elder
  - proxy rescue with banner
  - 「降血壓藥星期一才用完，另外阿嬤要洗腎」 → 洗腎 P1
  - 「阿嬤明天早上要洗腎」 at 07:00 → P1 (≤ 36 h)
- Must *not* be P1: 「我沒有胸痛，只是有點累」 (ask-back), 「我没有胸痛」 (Simplified), 「上次中風過，現在要拿降血壓藥」 (P2).
- 「被東西打到頭，流很多血」 → MEDICAL_EMERGENCY, not PROTECTION.
- Night rule: 02:00 + 明天早上 → today 08:00. 星期二 on a Tuesday → today. 下星期二 → +7 d.
- Address vs bed: 「中正路28號」 → external; 「B區12號」 → bed 12.
- Others: multiple needs; downplaying; a charger → P3; vague medicine → ask-back; sensitive and protection → generic wire views; ID scrubbed.
- A 400-character story → ≤ 5 needs, the embedded P1 present, < 50 ms.

**AI merge invariants**
- An ungrounded LLM quantity is flagged; an ungrounded field may raise urgency (flag + ask-back) but never lower it.
- A red-flag need survives `MockLLM(drop_index)`.
- `MockLLM(inject=胸痛)` on 「我沒有胸痛」 → P2, no banner.
- Coverage creates an unclassified P1.
- Deadline = min and emergency = union.

**Dedupe and store**
- dedupe: same bed + same item → candidate; empty zone and empty subject are never "same"; person-specific needs never auto-merge; PROTECTION never dedupes.
- store, R-T: every illegal transition → `Conflict`. Includes a confirm after merge, `mark_alert_sent` after confirm, and merging an announced draft into a draft.
- store, R-V: no bump on no-op writes; one bump per wire change; closed bit ⇔ closed status.
- store, restore after merge reverses dst; AUTOINCREMENT never reuses a seq across deletes or restarts; confirm survives a reopen.

**Wire audit** — for every ticket in the test set, the packed bytes contain no phone/ID digits, no protection details and no free-text subject. Also:
- content-free alert → discard → bytes carry no item or summary
- never-sent + discarded → nothing sent

**Codec and RTO**
- codec: round-trip for all types; every datagram ≤ MTU.
- saturation: overdue −3 h, age −5 min, quantity 99999 and elderly 300 all pack and round-trip saturated.
- fuzz: 10,000 random byte strings plus bit flips raise only the defined exceptions.
- rto: known sequences; Karn's rule; backoff cap; reset.

**Link emulator — `test_linkemu.py`** (each case ≤ 3 s):
- throughput within ±10% of `rate_bps/8`
- loss within 3σ over 400 datagrams
- oversize and DOWN datagrams dropped
- a 20-datagram MTU burst at 0 loss → exactly 5 delivered

**Network**
- **60 tickets over LORA_RELAY + FLAPPING.**
  1. After the first acks, edit 5, discard 2 and resolve 2.
  2. Finish on GOOD, then wait ≤ 120 s until `pending_units()` is empty.
  3. Assert per ticket that the EOC's (version, flags, zone, quantity, summary) equals `wire_view` at level 2. Report `dup_count`.
  4. **Ordering violation** = a P2/P3 record at the EOC before every seeded P1 has arrived, or DATA before the first HEARTBEAT. Must be 0 on the traffic before the first change.
- **P1 behind 200 P3 summaries** (custom 1.2 kbps, 20% loss each way):
  - no P3 bytes sent while the P1 is unacknowledged
  - arrival ≤ `(W·MTU + P1 bytes)/R + delay_max + n·rto_backoff_cap·max(rto_base) + 1 s`, with n = retransmissions while the P1 was pending, and a hard cap of 60 s
- **Chaos:**
  - Setup: 30% loss + flapping. Randomly kill and restart the sender subprocess **and** the EOC process — ≥ 5 times each in `eval/chaos.py`, ≥ 2 times each in the unittest.
  - Pass: poll until the EOC set equals the desk's set of confirmed `wire_view`s, 0 duplicates shown.
  - Fail: not equal after 180 s.
- **Clock jump:** monkeypatch the wall clock +1 h → no loss or duplicates; the sender continues.
- **Windows UDP reset:** a fake socket raising `ConnectionResetError` → the loop survives.

**Ollama adapter** — against `scripts/ollama_stub.py`:
- tags, chat → valid needs
- slow → `timeout` within `timeout_s + 1`
- bad JSON → one retry, then `invalid_json`
- stopped → `available()` false → FAST mode

**API** (Flask test client)
- check-in with two "yes" answers → two drafts
- intake → draft → confirm → board
- stale version → 409
- worried; emergency action
- protection excluded from export and the board

**UI smoke** (playwright, headless Chromium with `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream`):
1. Start the three processes in mock mode on test ports, with fresh temp DBs.
2. Type 「阿嬤明天早上要洗腎」, confirm, switch to GOOD, and see the row on the EOC page.
3. Click 錄音, then 停止 at 2 s. Assert the job reaches `done` with `duration_s ≥ 1.5` (drafts not required).
4. Save desk and EOC screenshots to `eval/results/screens/`.

**`eval/` scripts**
- **`text_cases.jsonl`** — **≥ 50** labelled cases: `{"id", "text", "now"?, "gold": {"needs": [{"category", "item_code", "quantity", "zone", "priority"}], "emergency", "dup_of"}, "tags"}`. Every case listed above, every category, 6+ negation cases, 5 duplicate pairs.
- **`run_text_eval.py --llm none|mock|ollama`** → `results/text_eval.md` + `.json`:
  - need precision/recall, field accuracy, emergency accuracy, dedupe precision/recall, latency
  - P1 recall **and precision**; **rules-only P1 recall must be 100%** on this set
- **`run_audio_eval.py`** (minimal) — `eval/audio/*.wav` + `labels.csv` (`file, case_id, condition (clerk_repeat | resident_direct | noisy), gold_transcript`): CER, P1 recall and ASR/LLM/end-to-end p50/p95, by condition.
- **`net_experiment.py`** — 60 seeded tickets with the link DOWN for 5 s, then each profile. Reports:
  - time until the census arrives, all P1s, everything
  - bytes up/down, datagrams, retransmissions
  - duplicates, losses, ordering violations
  - the voice-note baseline (10 s × 2,000 B/s per request; time = bytes ÷ rate, ignoring loss)

  Writes `results/net_experiment.csv` + `.md`, capped at 180 s per run.

**`scripts/`**
- **`selftest.py`** (for the laptop) prints a ✓/✗ checklist and exits non-zero on critical failures:
  - Python version and packages
  - Whisper load (`local_files_only=True`) and **RTF for `small` and `base`, with a recommendation for `asr.model`**
  - Ollama reachable and model pulled
  - a real extraction of 「阿嬤明天早上要洗腎」 with tokens/s and `prefix_cached`
  - ports free, DB writable, battery
- **`download_models.py`:** the loader with `local_files_only=False` for `small` and `base` (a no-op when already cached), plus `ollama pull qwen2.5:1.5b` if `ollama` is on PATH (else print the command).

## 12. Build order, cuts and the stop-the-line rule

**Cut from Stage I:**
- digest/congestion mode (everything is sent as a summary; the codec keeps type 1)
- AudioWorklet (ScriptProcessor only)
- automatic calibration (selftest recommends)
- `/card` (the picture card is a deck mock-up)
- `/print` (print CSS on the board instead)

**Kept on purpose:**
- the HB_REPLY status loop: without it, every emergency escalates at 3 minutes and the "dispatched" shot fails
- dead-thread restart
- minimal `run_audio_eval`
- ≥ 50 text cases
- real adapters in C3

| Checkpoint | Contents | Demo-able result |
|---|---|---|
| C1 core | config, codebook/lexicon, textnorm, rules extract (negation, ran-out, attachment), triage, wire view, store (R-T, R-V), typed intake, check-in, desk UI (drafts, board, privacy view, emergency actions, worried) | Typed request → P1 draft with reasons → confirm; check-in "yes" → draft |
| C2 network | codec, rto, emulator, sender, receiver, EOC UI, byte meter, profile buttons, census heartbeat, HB_REPLY, `run_demo.py` | Queue during DOWN; census, then P1 first on LoRa; "dispatched" back at the desk |
| C3 AI | audio checks, recorder, MockASR/MockLLM first, then `OllamaLLM` verified against the stub (tags, chat, slow → timeout, stopped → FAST), `FasterWhisperASR`, pipeline, merge invariants, ask-back, dedupe | Record → transcript → drafts; mock mode works |
| C4-lite | recovery, modes, Ollama-down → FAST, P1/emergency timers, selftest, download_models, export.csv | Kill Ollama → FAST; restart → recovered; unconfirmed P1 → content-free alert |
| C4 rest | watchdog, clock-jump, print CSS, remaining banners | |
| C5 | remaining tests, eval scripts, net experiment, chaos, screenshots, README | All acceptance checks pass |

**Stop-the-line:** at **T + 2 h**, C1 and C2 must pass acceptance checks 2 and 5 (rules-only). If not, drop C4-rest and the standalone `net_experiment.py` / `chaos.py` scripts, and go C3 → C4-lite → README. Even then, keep:
- `/export.csv` (PAPER mode)
- catch-all loops in every thread (G5 without the watchdog)
- a chaos **unittest** that kills the sender and EOC subprocesses ≥ 2 times each (the proof of G1)

Write the time of each checkpoint in `PROGRESS.md`.

## 13. Acceptance checks (verify all before reporting done)

1. `python -m unittest discover -s tests` passes, including slow tests.
2. `python run_demo.py --mock --no-browser --reset`: check-in and typed requests → drafts → confirm → queued while DOWN. On LORA_RELAY the census arrives first, then P1s before any P3, visible on the EOC page; 已派遣 at the EOC shows at the desk.
3. `eval/net_experiment.py`: 0 duplicates, 0 losses, 0 ordering violations on every profile; results written.
4. Chaos (script or, under stop-the-line, the unittest): final sets equal, 0 duplicates.
5. `eval/run_text_eval.py --llm none`: P1 recall = 100%; P1 precision reported; all must-be/must-not-be cases pass.
6. The wire-audit test passes.
7. Synthetic silent/too-short WAVs are rejected; clipped ones warned.
8. The UI smoke test passes and writes screenshots.
9. `grep -rnE "https?://" darkshelter/static | grep -vE "127\.0\.0\.1|localhost|w3\.org"` prints nothing.
10. `python3.11 -m compileall -q darkshelter scripts eval tests run_demo.py` succeeds, and `test_portability.py` passes.
11. The README covers Windows and macOS setup and troubleshooting:
    - **Setup:** python.org 3.11/3.12; `pip install -r requirements.txt`; Ollama + `ollama pull qwen2.5:1.5b`; `python scripts/download_models.py` while online; `python scripts/selftest.py`, then set `asr.model` as recommended; `pip freeze > requirements-lock.txt`.
    - **Running:** `python run_demo.py` (and `--reset`), opened at `http://127.0.0.1:8000`; mock mode; the demo script.
    - **Troubleshooting:** mic permission; use 127.0.0.1, not a LAN IP; Ollama not running; port in use or an orphaned process; Windows firewall prompt; Visual C++ runtime for faster-whisper; zh-TW Windows encoding.
12. `PROGRESS.md` lists exactly what is verified here vs. only on the laptop (real Whisper, real Ollama, real mic, Windows UDP), with checkpoint times.
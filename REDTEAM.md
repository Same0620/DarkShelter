# Red-team pass — 2026-10-28, 04:40–05:00 (Taipei)

Six agents attacked the build in parallel for about 12 minutes each. A second round of two agents (G and H) then
checked the findings that hadn't been proven. They all worked read-only, so no code changed during the pass. Each one used its own ports and databases, and none loaded Whisper because free memory was 2.9 GB.
Every finding is **verified** (a proof of concept was run, and its key output is quoted) unless it is marked
**unverified** (found by reading the code only).

**Totals:** 2 critical, 6 high, 8 medium and 12 low (one low was withdrawn in the second round).

## Status after the fixes (05:23–06:00)

Four fixer agents, each owning separate files, plus the orchestrator fixed the findings below. Every fix has a
regression test, and the red-team inputs were added to `eval/text_cases.jsonl`. After that, the full SPEC §13 suite
was rerun on the combined code (see PROGRESS.md).

| ID | Status | What changed |
|---|---|---|
| C1 | **Fixed** | About 90 emergency phrases (Traditional and Simplified) in `data/lexicon.json` make every C1 phrase P1 without the AI. In FAST mode caused by a backlog, the AI still takes a second look at requests the rules can't place. |
| C2 | **Fixed** | Needs are grouped by item, person and place: 兒子/女兒 and 爸爸/媽媽 become separate tickets. |
| H1 | **Partly fixed** | A clashing ticket is no longer shown as resolved. It stays undelivered, and a red banner tells the clerk at once to use the backup channel. The full fix needs a per-install id in every packet, which would change the header that SPEC §8.1 fixes. The README now requires a unique `desk_id` per desk. |
| H2 | **Fixed** | A request guard on both apps refuses writes from other sites, non-JSON writes, and audio uploads without the `X-DarkShelter` header. The clerk name is no longer read from the URL, except on the audio route, where SPEC §9 puts it. |
| H3 | **Fixed** | Only the place part of an address goes out, both when it comes from speech and when the clerk types it. |
| H4 | **Fixed** | Duplicate suggestions never pair two different people. Person-specific needs must share a bed, an address or a household. |
| H5 | **Fixed** | 走路, 迷路, 網路 and 馬路 are not addresses. 「我的孩子迷路了」 is now a lost-person P1. |
| H6 | **Fixed** | Typed requests are saved until processed and re-run after a crash: 0 of 120 were lost in the live test, against 16 before. Lost recordings get a red banner asking for them to be recorded again. |
| M1 | **Fixed** | Any run of 7 or more digits (Arabic, full-width or Chinese) is removed, even with separators between them. This covers +886 numbers, landlines and IDs with spaces. |
| M2 | **Fixed** | The deadline text is always rebuilt from the deadline time, on the server and in the wire summary. |
| M3 | **Fixed** | A ticket from the same sentence as a protection case goes out without its zone or location. The exception is a P1 rescue at an outside address, because the rescuers need it. |
| M4 | **Fixed** | An older heartbeat no longer changes the census. One is accepted again after 30 s of silence, because a reset desk restarts its counters. |
| M5 | **Fixed** | The Host header must be 127.0.0.1, localhost or [::1]. |
| M6 | **Fixed** | Category and item are checked against the codebook, and quantity and zone are range-checked. Priority must be an integer from 1 to 3. |
| M7 | **Fixed** | 依島素, 喜腎, 洗腰子, 喘袂過來, insulin, dialysis and oxygen are now recognised. |
| M8 | **Fixed** | Two AI timeouts in a row switch the desk to FAST (「AI 太慢」), and a probe every 60 s returns it to FULL. |
| L1 | **Fixed** | CSV cells starting with `= + - @`, a tab or a CR get a leading `'`. |
| L2 | **Fixed** | Junk datagrams are counted in memory and written every 2 s. |
| L3 | **Fixed** | The EOC clamps a priority outside 1–3. |
| L4 | **Fixed** | Bad input types get a 400 JSON error, and server errors return JSON with no traceback. |
| L5 | **Fixed** | Invisible format characters are stripped before matching. |
| L6 | Withdrawn | Not exploitable (second round). |
| L7 | Kept | 「不用洗腎了」 still comes out P1. Over-triage errs on the safe side. |
| L8 | **Fixed** | The privacy toggle starts from the config value. |
| L9 | Not changed | SPEC lists both keys. A comment in `config.py` says they are always on. |
| L10 | **Fixed** | 「N號床」 is read as a bed. |
| L11 | **Fixed** | Corrected in PROGRESS.md during the pass. |
| L12 | Not fixed | `/api/state` is still sent whole every second (611 kB at 300 tickets). That is fine for a demo, but a long real shift needs it to send only changes. |
| L13 | Kept | SPEC §0 requires `run_demo` to stop everything after 3 restarts in a minute. |

**Found while checking the fixes** (20 everyday sentences run through the rules):
- 「這裡網路不通」 came out P2 "road blocked", because 路不通 matched inside 網路不通. This was there before the
  fixes. **Fixed:** the internet phrases now map to 聯絡家人 (P3), while 「中正路不通了」 stays P2 road blocked.
  Both are now eval cases.
- 「他昨天中暑，現在已經沒事了」 is now **P1** (it was P3), because 中暑 is a new red flag. **Kept on purpose.**
  Adding 昨天 to the past-tense words would also drop 「他昨天中暑，現在還是很不舒服」 to P3. A false alarm costs the
  clerk one tap (lowering a P1 needs a reason), while a miss could cost a life. A real fix would let an explicit
  「現在沒事了」 in a later clause cancel an earlier past-tense symptom. Both critical findings can lose a life-threatening request.

The proof-of-concept scripts are in the session scratch folder, under `redteam\A` … `redteam\F`. They were not
copied into the repo.

| Agent | Area attacked |
|---|---|
| A | Life-safety triage: missed or downgraded P1s, dedupe merges, "the LLM can only raise urgency" |
| B | Privacy: personal data reaching the wire, the EOC, `/export.csv` or the logs |
| C | Link protocol: fuzzing, replay, forged acks, out-of-range fields, junk floods |
| D | Web/API: CSRF, DNS rebinding, XSS, R-T bypass through the API, double-submit races |
| E | Operations and demo day: Ollama hang/garbage/500, bursts, 100 kB input, CJK paths, restarts |
| F | Spec and claims audit: SPEC requirements not covered by §13, false claims in README/PROGRESS |
| G | Second round: stored XSS on the desk and EOC pages (L6) |
| H | Second round: end-to-end check of M3, and of C2 in the running app |

## What to fix first

Each fix should be followed by the full §13 suite, which takes about 15 minutes.

1. **Missed emergencies on the rules path (C1).** Add red-flag phrases to `data/lexicon.json` in both Traditional
   and Simplified characters. Also run the LLM on category-0 drafts even in FAST mode. This is mostly a data change,
   and it is the largest safety gain.
2. **Second person dropped (C2).** Group needs by (item, subject, location), not by item alone.
3. **CSRF and DNS rebinding (H2, M5).** One `before_request` hook fixes both. It should require a JSON body (or a
   custom header on the audio route), check the `Origin`, `Sec-Fetch-Site` and `Host` headers, and stop reading
   `clerk` from the query string.
4. **Location parsing and privacy (H3, H5, M1, M2).** Names in the location text, everyday words like 走路 taken
   for addresses, phone-number variants, and a client-supplied `deadline_text`.
5. **Demo-day and night-shift resilience (H6, M8).** Tell the clerk about intakes lost in a crash. Switch to FAST
   after two LLM timeouts in a row, so a stalled Ollama doesn't add 15 s to every request.
6. **Before any deployment on two machines (H1).** Add a per-install id to the ticket identity, and require a
   unique `desk_id` for each desk.

## Round 2 (06:45–07:30): only what changed today, plus a real-model dress rehearsal

Starting point: tag `known-good-1` (`368fe34`). Each change is a separate commit.

**Request guard (agent G2).**
- **Bypasses: none.** G2 sent 42 raw requests with Chrome's exact headers: a null Origin, cross-site form and
  text/plain POSTs, multipart, method overrides, HEAD and OPTIONS, Host tricks (a trailing dot, `127.1`,
  `0x7f000001`, `[::ffff:127.0.0.1]`, userinfo, wrong or doubled ports) and DNS rebinding.
- **False blocks: none.**
- **Fixed:** the pages could be framed by another site, which could steer a clerk's click. Both apps now send
  `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
- **Real Chrome 155:** confirm → EOC → 已派遣 → desk works at both `http://127.0.0.1` and `http://localhost`.
  Chrome sent `Origin` set to the page's own origin and `Sec-Fetch-Site: same-origin`, and none of the 8 writes was
  refused.
- **Left open (not changed today):** the link emulator's control port (`:9100/profile?name=…`) has no guard. Any
  web page open in the same browser could switch the demo's link with an `<img>` tag. It is a demo-only component.
  Don't browse other sites in the demo browser.

**The new emergency phrases (agent P).**
- **Everyday exaggeration:** 「X死了」 idioms (熱死了, 累死了, 笑死…) were already P3.
- **救命 next to a complaint** (「救命喔，排隊排好久」) and "almost" before a soft symptom (「我快中暑了」) keep P1 and add
  the ask-back 「確認：是真的緊急狀況嗎？」. Urgency is never dropped.
- **Missed emergencies:** 45 of 75 probes weren't P1; now 3 aren't, and all 3 are over-alarm on the safe side.
  Newly P1: stroke, shock, burns, bites, poisoning, pregnancy bleeding, low blood sugar and more.
- **Domestic violence:** 「我先生打我」-style phrasing now opens a protection ticket.
- **Eval:** 125 new cases.

**Team decisions, implemented.**
- **Self-harm** goes out like psychiatric details: 「機密醫療需求，請聯絡B站」 on the wire, in the CSV and in the print
  view, keeping P1, the emergency flag and the real category. A wire-audit test proves that 自殺 and 自傷 never
  appear in any of the three.
- **Rescue plus protection** in one sentence: the rescue goes out as exactly 「中正路三巷 受困人員」, and the
  protection part as the generic protection message. Both are tested.

**Real-model dress rehearsal** (Whisper small + Qwen on the CPU; zh-TW TTS voice as 16 kHz WAVs; typed and audio).
Typed input gave the right result for every demo sentence. Audio found four problems:

| Whisper heard | Effect | Now |
|---|---|---|
| B區十二床 → 避區/必趨十二床 | zone lost (wire said 「12床」) | **Fixed:** a sound-alike letter right before a bed number becomes B區/C區/A區 |
| 中正路三巷 → 中正路三項 | wire said 「中正路 受困人員」 | **Fixed:** 項 after a road number becomes 巷 |
| 胸痛 → 兇統 | rules missed it; **Qwen guessed 家暴 → P2 protection** | **Fixed:** 兇統 and similar sound-alikes count as 胸痛 (P1), and the ask-back names 胸痛 |
| the desk died at start-up: `OpenBLAS error: Memory allocation still failed` (1.2 GB of commit left) | no real-model desk at all | **Fixed:** `run_demo` limits OpenBLAS to one thread |

**Left alone:**
- For the daughter's repeat, Qwen's location 「十二床」 replaced the rules' 「B區十二床」. That ticket is merged into the
  first one in the demo and is never sent.
- Persistent vomiting comes out P1 where P2 would be right (3 eval cases, tagged `known_gap`).
- 「撞到頭，現在沒事」 and 「我有低血糖」 come out P1 (over-alarm, like the 中暑 case below).

## Findings, most severe first

### Critical

**C1. Common emergencies come out P3 when the LLM is skipped, which happens exactly during a rush (A).**
- **Where:** `pipeline.py:301`. FAST mode skips the LLM when the backlog is 3 or more, when the LLM is down, or
  when ASR has fallen back.
- **PoC (`extract.analyze`, rules only):** all of these come out **P3**: 救命, 心臟病發作, 他不能呼吸, 呼吸好像停了,
  阿公沒在呼吸, 有人割腕, 小孩一直抽筋, 羊水破了, 阿公跌倒頭在流血, 大出血, 頭破血流, 溺水, 觸電, 吐血, 休克,
  不省人事, 意識不清, 嘴唇發紫, 過敏喉嚨腫起來, 一氧化碳, 中暑, 小孩發高燒.
- Agent B also saw 「李大同家中正路三巷有人受傷」 and 「B區3床的阿伯有肺結核在咳血」 come out as category 0 (其他).
- **With the real Ollama:** 9 of 14 were raised to P1. Still wrong: 有人割腕 became PROTECTION P2, and 小孩發高燒,
  有人倒在地上 and 過敏喉嚨腫起來 stayed P2.
- **Fix:** add red flags to the lexicon (Traditional and Simplified), with a generic entry for 救命, 救人 and
  叫救護車. Run the LLM on category-0 drafts even in FAST mode.

**C2. Two people with the same need in one sentence become one need, and the second person is dropped (A).**
- **Where:** `extract.py:345` groups needs by item code only (`key = code if code else ("cat", cat)`).
- **PoC (rules):**
  - 「我兒子被困在中正路的家裡，我女兒被困在和平國小」 gives one need, 「兒子 中正路 受困人員」. The daughter
    at 和平國小 disappears.
  - 「爸爸胸口很痛，媽媽喘不過氣」 gives only 爸爸.
  - 「阿公明天要洗腎，阿嬤後天也要洗腎」 gives only 阿公 明天.
- **With the real Ollama:** the daughter comes back as an AI-only P1, but with no subject and no location. In FAST
  mode she is lost entirely.
- **In the running app (H):** one draft, 「兒子 中正路 受困人員」. The card does show the whole transcript as a quote
  (`app.js:198-199`), so the clerk reading it is the only safeguard.
- **Fix:** group by (item, subject, location), and split the need when they differ.

### High

**H1. After a desk database reset, new tickets are acked as duplicates and marked resolved, but never shown (C).**
- **Where:** `net/eoc_store.py:148` and `store.py:592-611, 635`.
- **What happens:** the EOC identifies a ticket only by (desk_id, seq) and ignores boot_id. A desk with a fresh
  database restarts at seq 1, so its new P1 is taken for an older ticket with the same number. The EOC acks it as
  a duplicate and sends back that old ticket's status. The desk then shows the P1 as delivered and 已解決.
- **When it happens:** a new laptop, a reinstall, or a copied `config.json`, since `desk_id` defaults to 7
  everywhere. Two desks that share an id behave the same way.
- **The demo is safe:** `run_demo --reset` always wipes both databases, and it refuses to start when only one exists.
- **PoC (`poc.py 1`):** a new P1 「B區12床 長者 洗腎」 gets the ack `(1, 1, 2, 3)`. The EOC row stays the old
  「B12 毛毯x2」, status 3. The desk ticket shows acked, eoc_status 3, and nothing pending.
- **Fix:** add a random per-install id to the ticket identity. Accept an ack's status only for a version that was
  actually sent. The README should require a unique `desk_id` for each desk.

**H2. Any web page open in the clerk's browser can confirm, resolve or discard tickets (D).**
- **Where:** `desk_app.py:314-319, 398, 469`.
- **What happens:** `body()` returns `{}` for a request that isn't JSON, and the clerk's name is read from the
  query string. So a cross-site form POST or text/plain POST works with no CORS check. Ticket numbers are small
  sequential integers.
- **PoC (`poc1.py`, checked twice):**
  - a form POST to `/api/tickets/1/confirm?clerk=attacker` returned 200, and the draft was confirmed and sent to
    the EOC;
  - a text/plain POST to `/resolve` silently resolved a P1;
  - a text/plain POST to `/discard` discarded a ticket;
  - a text/plain POST to `/api/mic` switched the desk to TYPED mode.
- **Fix:** see item 3 in "What to fix first".

**H3. A person's name goes out inside the location text (B, confirmed by F).**
- **Where:** `codebook.py`, `parse_location`. `_ADDR_RE` swallows a name in front of an address unless 在, 住 or
  到 separates them.
- **Rule broken:** SPEC says free text never leaves the desk and keeps names out of `/export.csv`. The frames are
  signed with HMAC but not encrypted, so the relay can read them too.
- **PoC (end to end on a mock demo):** after confirming 「王小明中正路三巷五號受困需要救援」, the EOC `/api/state`
  row and the CSV both show 「王小明中正路三巷五號 受困人員」. The same happens with 李大同家… and 陳美麗….
  F found that `parse_location('我叫陳志明中山路五號需要救援')` keeps 「我叫陳志明中山路五號」.
- **Fix:** send only the road, lane and number. At minimum, treat 家, 叫, 的 and 是 as boundaries and cut at the
  first road or village name.

**H4. Dedupe suggests merging two different patients (A).**
- **Where:** `dedupe.py:46-52`.
- **PoC:**
  - 「陳家的阿嬤明天要洗腎」 and 「林家的阿嬤明天也要洗腎」 give "suggest same_subject+similar_summary". The family
    names are dropped from the subject.
  - 我爸 and 我媽, both 被困在中正路三巷, give "suggest same_zone".
- **Why it matters:** the draft is still created, but one mistaken merge tap loses a dialysis patient.
- **Fix:** never suggest a merge when the subjects differ, and require the same household for person-specific P1s.

**H5. Everyday words containing 路 are taken for an outside address, and sent (F).**
- **Where:** `codebook.parse_location`, sent via `wireview.py:71`. This breaks SPEC §6.5 step 4 and §6.8.
- **What happens:** the bed number is lost, the EOC sees a made-up outside address on mobility and lost-child
  requests, and words from the transcript leave the desk.
- **Evidence:**
  - `rules_extract('我在B區12床，阿嬤不能走路')` gives zone 65535 and the wire summary 「阿嬤不能走路 長者 行動協助」
    with the outside-address flag.
  - 「我的孩子迷路了」 becomes the address 「我的孩子迷路」.
  - 「這裡沒有網路」 becomes an address too.
- **Where it bites:** 走路 is common in wheelchair requests. The demo sentence doesn't use it.
- **Fix:** add a stop-list (走路, 迷路, 網路, 一路, 路上), and prefer a zone or bed said in the same clause over an
  outside-address match.

**H6. Intakes still waiting in the queue disappear without warning if the desk crashes (E).**
- **Where:** `pipeline.py:59-61`. The queue and the job list live only in memory, and audio is never written to
  disk.
- **What happens:** a recorded P1 that hasn't been processed yet is gone after a crash. Its job returns 404, no
  banner appears, and its line simply vanishes from the UI.
- **PoC (`crashq.py`):** 120 typed intakes were sent, then the desk child was killed by PID. Output:
  `submitted 120, drafts created from them: 104; lost silently: 16`.
- **Fix:** save text intakes before returning the job id, and requeue them in `recover()`. Audio is never saved,
  by design, so after a restart show 「N 筆在當機時遺失，請重錄」.

### Medium

**M1. Phone numbers and IDs typed in common formats pass the scrubber (B).**
- **Where:** `textnorm.py:169-185`.
- **PoC:** each of these goes out unchanged, for example 「中正路三巷 0912 345 678 緊急醫療」:
  - `0912 345 678`
  - `02-2345-6789`
  - `+886-912-345-678`
  - `A 123 456 789`
  - 零九一二三四五六七八
- **Fix:** remove any run of 7 or more digits (Arabic or Chinese), even with separators between them, and match IDs
  that contain spaces.

**M2. A client-supplied `deadline_text` goes on the wire word for word (B, confirmed by F).**
- **Where:** `desk_app.py:405-416`; `wireview.build_summary` uses `str(t["deadline_text"])`.
- **PoC:** confirming with `deadline_text: "王小明 0912 345 678 A 123 456 789"` made the EOC row
  「飲用水 王小明 0912 345 678 A 123 456 789」.
- **Fix:** ignore the client's value and always derive the text from `deadline_at`.

**M3. A protection case can be located through the other ticket from the same sentence (B; verified end to end by H).**
- **PoC (mock demo):** 「B區12床的王小姐被先生家暴，她也需要毛毯」 was confirmed as two tickets. The EOC holds:
  - `B-001`: 「保護個案，請社工聯絡B站」, zone 0, as intended;
  - `B-002`: 「B區12床 毛毯」, zone 524.

  The codes are adjacent, the ages are the same, and the two arrived 62 ms apart. Anyone reading the EOC or the link
  can place the protection case at bed B12.
- **Fix:** when a sentence produces a protection need, send its other tickets without the zone.

**M4. Replaying an old heartbeat rolls the EOC's census back (C).**
- **Where:** `net/eoc_store.py:175-179`.
- **PoC (`poc.py 2`):** census people went 250 → 100, q_p1 went 0 → 5, and boot went 2 → 1.
- A late copy from a store-and-forward relay does the same thing with no attacker involved.
- **Fix:** ignore a heartbeat unless its (boot_id, msg_seq) is newer than the stored one.

**M5. The Host header is not checked, so DNS rebinding works (D).**
- **Where:** `desk_app.py:289`, `eoc_app.py:102`.
- **PoC:** `GET /api/state` with `Host: evil.example:22000` returned 200 on both apps. A rebinding page could
  read names and notes.
- **Fix:** allow only `127.0.0.1:<port>` and `localhost:<port>`.

**M6. Out-of-range field values are accepted and sent (D).**
- **Where:** `store.py:377`, `update_fields`.
- **PoC:** an update to category 999, item 999999 and quantity −5 returned 200. The EOC showed category 63 and
  「A區2床 其他 -5份」. A quantity of 10¹² was also accepted.
- **Fix:** validate category and item against the codebook, and clamp the quantity to the wire field's range.

**M7. Taiwanese Hokkien, English and homophone gaps on the rules path (A).**
- **PoC:** all of these come out P3: 依島素用完了, 阿嬤要喜腎, 阮阿公欲洗腰子, 喘袂過來, 我需要insulin, dialysis,
  oxygen. The LLM raises some of them.
- **Fix:** add lexicon entries.

**M8. A hung Ollama never moves the desk out of FULL mode (E).**
- **Where:** `health.py:156-160`. `on_llm_result` ignores `error == "timeout"`.
- **What happens:** the worker is single-threaded, so every intake behind a hung LLM call waits a full timeout
  (15 s by default) before its rule draft appears. That includes voice intakes. This is likely on this laptop,
  where memory pressure can stall Ollama while `/api/tags` still answers.
- **PoC (`llmfault.py`):** the real `OllamaLLM` adapter was pointed at the stub in slow mode, with `timeout_s=4`.
  Drafts first appeared at 0.2, 4.1 and 8.1 s. The mode was still `FULL ok` after 4 timeouts in a row.
- **Fix:** after 2 timeouts in a row, switch to FAST (reason `llm_slow`), and let the existing 60 s warm-up retry
  clear it.

### Low

- **L1. CSV formula injection (B).** A location typed as `=HYPERLINK(...)` starts cells in `/export.csv`
  (`desk_app.py:474-489`). Fix: put `'` in front of any cell that starts with `= + - @`.
- **L2. Each junk datagram costs one durable SQLite write (C).** That is 0.62 ms each, so about 1,600 junk datagrams
  per second saturate it (`receiver.py:129-139`). Fix: count bad datagrams in memory and write the counters every
  few seconds.
- **L3. A priority-0 record with a valid MAC sorts above every P1 on the EOC board (C).** This needs the key or a
  buggy desk (`eoc_store.py:237`). Fix: clamp priority to 1–3, or drop the record.
- **L4. Wrong JSON types give unhandled HTML 500s (D).** There are no tracebacks in the responses and no state
  corruption. A priority of `"1"` or `1.5` is silently accepted as P1 (`desk_app.py:342, 387, 405, 429`). Fix:
  validate types and return 400.
- **L5. Zero-width characters defeat matching (A).** 「洗​腎」 and 「救‍命」 come out P3. This affects
  typed or pasted text only. Fix: strip Unicode category Cf in `textnorm`.
- **L6 (withdrawn by G). The two `innerHTML` sinks D flagged are not exploitable.** Every caller of `app.js:79`
  escapes what it passes, and `:502` shows only escaped strings or numbers from the server. See "Attacks that
  failed".
- **L7. 「不用洗腎了」 comes out P1 (A).** This is over-triage, which errs on the safe side. Noted only.
- **L8. The `privacy_view_default` setting is ignored (F).** The toggle is hard-wired on in `index.html:86`. On is
  the safe default. Fix: set the toggle from `/api/state`.
- **L9. The `scrub_national_id` and `keep_audio` settings are never read (F).** The code always does the safe thing,
  so changing them does nothing. Fix: implement them or remove them.
- **L10. 「三號床」 is not recognised as a bed (F).** This matches the SPEC pattern, but people often say it that
  way. Fix: also accept 「N號床」.
- **L11. PROGRESS.md said "~140–335×" (F).** The results file shows 149–335×. Corrected in PROGRESS.md during this
  pass.
- **L12. `/api/state` grows with the board and is polled every second (E).** With 300 tickets it is 611 kB and
  takes 39–116 ms (`app.js:593`). Fix: send only what changed, or leave out resolved tickets.
- **L13. An EOC crash loop also stops the desk (E).** After 3 restarts in a minute, `run_demo` stops everything.
  SPEC §0 asks for this. The team should decide whether the desk, the safety-critical side, should stay up instead.

## Attacks that failed (the build held up)

- **The LLM can only raise urgency (A).** Fake LLM outputs that were empty, blanket-only, said 下個月, relabelled the
  need as water, or had garbage types all kept the dialysis P1. `store.py:425` also uses `min()`.
- **Triage rules (A):**
  - Simplified characters come out P1: 洗肾, 胰岛素没了, 呼吸困难, 氧气.
  - Spaces inside keywords still match.
  - Numerals: 剩0支, 剩零支, 剩１小時 and 半小時 come out P1.
  - Negation traps come out P1: 沒有騙你, 沒事沒事…一直流血, 沒辦法呼吸, 剛剛沒有呼吸.
  - The 5-need cap never drops a P1, including one at the end of a long ramble.
  - Dedupe never suppresses a draft on its own.
- **Privacy (B):**
  - Full-width digits, full-width IDs and lowercase IDs are removed.
  - A phone number stuck to an address is removed.
  - The 60-byte cut lands on a character boundary.
  - The HMAC key appears in no GET response.
  - `desk.log`, `eoc.log` and `linkemu.log` contain no names, phone numbers or key.
  - 愛滋, HIV, 精神科 and 美沙冬 go out as the generic 「機密醫療需求」 with no item.
  - The protection ticket itself is generic, with zone 0.
- **Link (C):**
  - 30,000 fuzzed datagrams raised only `CodecError`, and every resulting row rendered.
  - A record with every field at its maximum renders.
  - A replayed old DATA version doesn't roll back.
  - A forged ACK is rejected (`BadMac`).
  - Old acks and status replies can't roll the desk back.
  - The decoder rejects summaries over 60 B.
- **Web/API (D):**
  - The R-T table holds through the API: resolving a draft, confirming a discarded ticket and merging into itself
    all return 409.
  - Merging a P1 into a P3 raises the target to P1, and restore works.
  - Two simultaneous confirms return `[200, 409]` with one version bump.
  - A stale `expected_version` returns 409.
  - The EOC status route rejects text/plain, so it can't be reached by CSRF.
  - No tracebacks appear in HTTP responses.
  - Garbage audio gives `bad_audio` and no drafts.
- **Spec audit (F):**
  - The AI only raises urgency and only moves deadlines earlier. A confirmed ticket gets only `ai_suggestion`.
  - Lowering a ticket below the rule floor needs a reason.
  - Unconfirmed P1: a reminder and beep at 120 s and an alert at 300 s. 0 turns the alert off.
  - SLA escalation happens once, with the backup channel and a print button.
  - Content-free, protection and sensitive summaries match §6.8.
  - The P1 reservation counts both pending and in-flight tickets.
  - The version number changes only in the save step.
  - Audio is never written to disk.
  - Every §9 route exists, and bodies are capped at 5 MB.
  - Every number in PROGRESS and README, apart from L11, matches `eval/results`, and every README command and flag
    exists.
- **Operations (E):**
  - A burst of 50 intakes finished in 1.7 s with no errors. The mode went to FAST (backlog) and back to FULL.
  - A 100 kB intake is rejected cleanly with `400 too_long`.
  - With the stub returning bad JSON, or stopped, jobs still finish with their rule drafts. A stopped LLM switches
    the desk to FAST (`llm_down`).
  - The repo runs from a path with spaces and CJK characters (`測試 資料夾\ds`).
  - A second instance on the same ports gets a clear zh/en message and exits with code 2.
  - A killed desk child restarts in about 1 s, with every draft kept. No orphan processes are left behind.
- **Stored XSS (G):** `<img src=x onerror=alert(1)>` was sent as the clerk name and as `deadline_text`. It reached
  the EOC `/api/state` and the desk board as raw text. Every renderer escapes it (desk cards, toast, system lines,
  EOC rows and desk list), and numbers are integers from the server or the wire. What remains is M2: the raw text
  goes out on the wire.

## Not done in this pass

- The red-team pass itself changed no code. The fixes came afterwards; see "Status after the fixes".
- Not attacked: the real-microphone path, Whisper on adversarial audio (memory), and Safari/macOS.
- Nobody has red-teamed the fixed code yet. A second pass should start with the new request guard
  (`darkshelter/webguard.py`), the crash-recovery queue and the bigger lexicon, which could now over-trigger.

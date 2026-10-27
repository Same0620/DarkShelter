# Red-team pass — 2026-10-28, 04:40–05:00 (Taipei)

Six agents attacked the build in parallel for about 12 minutes each. They worked read-only, so no code changed
during the pass. Each one used its own ports and databases, and none loaded Whisper because free memory was 2.9 GB.
Every finding is **verified** (a proof of concept was run, and its key output is quoted) unless it is marked
**unverified** (found by reading the code only).

**Totals:** 2 critical, 6 high, 8 medium and 13 low. Both critical findings can lose a life-threatening request.

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
- **L6. Two `innerHTML` sinks skip `esc()` (D, unverified).** They are in `static/desk/app.js:79` (toast) and `:502`
  (system lines). It was not traced whether user text reaches them. Fix: use `textContent`.
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

## Not done in this pass

- No fixes were applied. The pass lasted 20 minutes, and re-running the full §13 acceptance suite after a code
  change takes about 15 minutes. The fixes are listed above, in priority order.
- Not attacked: the real-microphone path, Whisper on adversarial audio (memory), and Safari/macOS.

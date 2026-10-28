# DarkShelter — Stage I plan (high level, v3 — final)

*ASUS UGen AI League · Workplace AI · Battlefield Lightning · v3 2026-10-27 22:30 Taipei, after two red-team reviews and five bug-hunt debates (changes listed in §12)*

## 0. The product in one paragraph

When a typhoon or earthquake knocks out the mobile network, DarkShelter turns a laptop on an evacuation shelter's front desk into a registration, needs-screening and triage station that keeps working.
- **Check-in:** the clerk records each household, which keeps a live headcount, and asks five screening questions, so needs surface even from people too shy to ask.
- **Requests:** the clerk listens to a resident and repeats the request into the mic in Mandarin, or lets a resident who speaks clearly talk directly. On-device speech recognition and a small language model turn the words into a structured request. A fixed rule table sets the urgency, and the clerk confirms.
- **Sending:** confirmed requests and the headcount wait in a crash-proof outbox. They go out most-urgent-first as records of at most 80 bytes over whatever link comes back: one bar of LTE, a satellite terminal or a LoRa relay.

Stage I runs on a laptop processor. Stage II moves the same two models onto the UGen300.

**Who uses it**
- **Intake clerk** (township office staff or a Red Cross volunteer): the only person who touches the desk.
- **Residents:** talk to the clerk, or point at a printed picture card (zh / EN / ID / VI; designed in the deck for Stage I). They never use the device.
- **EOC operator** (災害應變中心): sees requests and headcounts from many shelters on one board and sends status back.

## 1. What the 3-minute video must make judges believe

1. It works with no network and on battery.
2. Speech becomes a correct, structured request within seconds, and every field shows the words it came from.
3. A life-threatening request can't be missed or quietly downgraded, and an emergency at the desk gets handled in minutes, not queued.
4. When a thin link returns, the headcount and the most urgent requests arrive first. They take seconds and hundreds of times fewer bytes than voice notes, and nothing is lost or duplicated through drops and crashes.
5. When a part fails (AI model, microphone, network), the desk keeps working in a simpler mode instead of stopping.

## 2. Design principles

1. **Offline first.** The network is a bonus, never a dependency.
2. **The AI understands, rules decide urgency, the clerk confirms.** The language model fills in what was said. A fixed, explainable rule table sets priority from those fields. Only a human can confirm a request or lower its urgency. (A 1.5B model is good at structure, not at judging danger.)
3. **Raising urgency is easy; lowering it is hard.** Confirming a P1 takes one tap, and so does "I'm worried — make it P1". Lowering a P1 needs a typed reason. An uncertain field may raise urgency but never lower it.
4. **The line of people never waits for the AI.** A rule-based draft appears at once; the AI refines it only if it's fast enough.
5. **Fail soft, and say so.** Every part has a fallback, and the screen always shows which mode the desk is in.
6. **Send meaning, not media.** Requests travel as tiny binary records, most urgent first.
7. **Private by default.** Audio never leaves the laptop and is deleted after transcription. Only the minimum fields go upstream, and protection cases never appear on screen boards, exports or the wire in readable form.
8. **Show the evidence.** Every AI-filled field shows the quote it came from, and anything not actually heard is marked.

## 3. How it works

```
 Clerk's laptop (the desk)                                              EOC
 ──────────────────────────────────────────────────────────────────     ─────────────────────
 Check-in (household, bed, 5 screening Qs) ──► roster + headcount ─┐
 Mic: clerk repeats or resident speaks ─┐                           │
 Typed text / picture-card pick ────────┼─► Audio check ─► Speech-to-text (Whisper, offline,
                                        │   (silent? short? phantom-phrase filter, confidence)
                                        ▼
            Understand: rule draft (instant) + LLM extraction (≤15 s, JSON-locked)
                        + grounding check (was each field actually said?)
                                        ▼
            Triage rules: P1/P2/P3 + reasons; red flags with negation check; emergency banner
                                        ▼
            Ask-back prompts ─► Clerk review: one-tap confirm / edit / discard / merge
                                        ▼
            Durable store (SQLite + append-only event log)
                                        ▼
            Outbox: headcount first, then priority + deadline ─► binary records ─► link ─► Receiver
            (≤80 B per request, ≤200 B datagrams, integrity tag)              (down/LoRa/  (one copy
                        ▲                                                       1-bar/sat)  per version)
                        └──────────── acks + EOC status (acknowledged, dispatched…) ◄──── EOC board
```

## 4. Functions we need

| # | Function | What it does |
|---|---|---|
| 1 | **Check-in & screening** | Records each household (size, zone/bed, elderly / infants / pregnant / disabled counts) with check-in and check-out. Asks 5 yes/no screening questions, and every "yes" opens a draft request. Feeds the live headcount |
| 2 | **Capture** | Browser mic records 16 kHz audio with a live level meter, auto-stops after 2.5 s of silence or at 30 s. Also takes typed text and picture-card picks. A one-line consent notice with a "type only" option |
| 3 | **Audio check** | Rejects silent or too-short recordings; warns on clipped or too-long ones |
| 4 | **Transcribe** | faster-whisper for Mandarin (small, or base if the self-test finds the laptop too slow; base in FAST mode), with a domain-vocabulary prompt and voice-activity filter. Removes known phantom phrases, converts to Traditional Chinese, scores confidence |
| 5 | **Understand** | Instant rule draft (keywords, Chinese numerals, time phrases, zone/bed) plus LLM extraction (Qwen2.5-1.5B via Ollama, JSON-schema output). Then a grounding check and a merge. Several needs become several requests |
| 6 | **Triage** | Priority rule table, red flags with negation and past-tense checks, deadline parsing (night-aware), emergency banner. The AI can never lower urgency |
| 7 | **Emergency handling** | The banner stays until the clerk logs what was done (medic told / 119 called / 119 unreachable / runner sent). If the EOC hasn't acknowledged within 3 minutes, the desk shows the backup channel set at setup |
| 8 | **Ask-back** | Suggests follow-up questions for the required information missing in each category |
| 9 | **Review** | Draft card with evidence quotes and amber uncertain fields. One-tap confirm (including P1), edit, discard with undo, an "I'm worried → P1" button, and a typed reason for lowering urgency |
| 10 | **Dedupe** | Suggests merges for person-specific needs; adds supply requests into running totals |
| 11 | **Protection cases** | Domestic violence and similar cases are hidden from the board, exports and print. The desk prompts "move to a private spot; social worker / 113 hotline". The EOC sees only "protection case, contact desk B" |
| 12 | **Store** | SQLite (crash-safe) plus an append-only event log, with recovery on restart |
| 13 | **Outbox & sync** | Headcount first, then priority and deadline order. Batching, acks, adaptive timeout with capped backoff, and P1s reserve the sending window. Heartbeat probes every 2.5–5 s while the link is down, and overdue escalation |
| 14 | **Codec** | Binary records and ≤200-byte datagrams with an integrity tag and boot ID |
| 15 | **Link emulator** | Outage, LoRa-class, one-bar LTE, satellite and flapping profiles (loss, delay, bandwidth), plus byte counters |
| 16 | **EOC receiver & board** | Keeps one copy per version and acks every record. Status buttons and shelter headcounts |
| 17 | **Health & modes** | Startup self-test that recommends the speech model for this laptop, mode ladder, watchdog for dead threads, battery and status lights |
| 18 | **Paper fallback** | CSV export and a printable board (print view). The picture card (zh / EN / ID / VI) is designed in the deck; a printable version is Stage II |
| 19 | **Evaluation** | Text test set, audio test set, network experiments and chaos tests that produce the deck's numbers |

## 5. Features: tonight vs. later

**Must have (Stage I build):** everything in §4. Check-in is minimal: no ID numbers, family name optional. The EOC status loop stays: without it every emergency escalates at 3 minutes and the "dispatched" shot fails.

**Cut from Stage I (by the buildability debate):** the digest-first congestion mode (every request goes as a ≤80 B summary), automatic model calibration (the self-test recommends a model instead), a printable picture-card page (the card is a deck mock-up), a separate print page (print view on the board instead), and the AudioWorklet recorder.

**Stop-the-line rule:** two hours into the build, the core and the network part must pass their checks. Otherwise the coding agent drops the remaining polish and goes straight to speech/AI, timers, CSV export and the README. It still keeps the crash tests that prove "nothing is lost".

**Stage II (described in the deck, not built):**
- Both models running on the UGen300 (Whisper and Qwen 1.5B on the Hailo-10H), with measured watts and tokens/s
- Speech recognition for Taiwanese Hokkien, Indonesian and Vietnamese
- Encrypting the link (Stage I only protects integrity)
- Resident self-service by QR code
- A "data mule" export over QR or USB when no link exists at all
- A real LoRa (Meshtastic) or satellite modem
- Several desks per shelter, with dedupe across shelters
- Linking check-in to the official 收容登記 / EMIC systems
- A field test with a township office or NTU's disaster-prevention centre

## 6. When people don't behave as expected

### The clerk

| Situation | What DarkShelter does |
|---|---|
| Presses record but nobody speaks | The audio check finds no speech: 「沒有聽到聲音 / No speech heard」. No request is created |
| Forgets to stop recording | Stops 2.5 s after speech ends, with a hard stop at 30 s |
| Mic not working, or permission denied | The live level meter shows it at once; the desk switches to typing |
| Clicks Confirm without reading (automation bias) | Uncertain or unheard fields are outlined in amber and each field shows its quote. Confirming stays one tap (we don't slow down urgent work), but **lowering** urgency needs a typed reason |
| Senses something is wrong that the rules missed | The "我擔心 I'm worried" button makes it P1 in one tap |
| Confirms before the AI finishes | A late AI result never overwrites a confirmed or clerk-edited field; it shows as a suggestion only |
| Discards by mistake | A 10-second undo, and discarded requests stay restorable. Discarding something with a life-safety word needs a reason. If it was already sent, a "withdrawn" update follows it |
| Leaves a P1 draft unconfirmed because they're busy | A reminder at 2 minutes. At 5 minutes a **content-free** alert ("desk B has an unconfirmed urgent request") goes to the EOC, never the unreviewed AI text. The full request follows once confirmed |
| Ignores the emergency banner | It stays until an action is logged (medic told / 119 called / 119 unreachable / runner sent) |
| Enters the same person twice | A duplicate suggestion (same bed + same need) with one-click merge |
| Closes the lid, laptop crashes, battery dies | Every draft is on disk before the screen says "saved". On restart: "Recovered 3 drafts", and sending resumes |
| Has two browser tabs open | Each edit carries a version number, so a stale tab is told to reload |
| Opens the desk from another device on the LAN | The mic only works on `http://127.0.0.1` (browser security). We tell them to use the desk laptop itself |
| Laptop screen faces the queue | Privacy view: the board shows ticket code, category icon and zone only; details open on click |
| Hands over at a shift change | Every action records the clerk's name, and the board persists |

### The resident

| Situation | What DarkShelter does |
|---|---|
| Speaks Taiwanese, Hakka, an indigenous language, Indonesian or heavily accented Mandarin | **Default workflow: the clerk listens, then repeats the key points into the mic in Mandarin.** Whisper turns Taiwanese into confident wrong Mandarin rather than low-confidence output, so we don't rely on it. The picture card lets anyone point. Taiwanese speech recognition is Stage II |
| Too shy to ask (不好意思), or leaves before asking | The five check-in screening questions surface needs proactively: dialysis/oxygen/powered devices, daily medicine/insulin, infants/pregnant/disabled, missing family, other |
| Tells a 3-minute story | Each recording is capped at 30 s, the model keeps only the needs, and the clerk can record again or summarise |
| Lists several needs at once | Split into separate requests, each with its own urgency |
| Is vague ("I need medicine") | Ask-back: 「請問是哪一種藥？什麼時候會用完？」 |
| Negates or talks about the past (「我沒有胸痛」, 「上次中風過」) | A symptom red flag is suppressed only when a negation sits right before it or the clause is about the past. An ask-back confirms (「現在有胸痛嗎？」). Doubt keeps it P1: 「不知道是不是胸痛」, 「會不會是中風」, 「之前都好好的，現在突然胸痛」. If the AI re-adds a denied symptom, it becomes P2 with the ask-back, never a 119 alarm |
| Says something has run out, in any words (「已經兩天沒洗腎了」, 「胰島素沒帶出來」, 「寶寶沒奶粉了」) | Ran-out rule: an item plus 沒 / 完 / 光 / 忘 / 斷 / 留在 means *now*. Elapsed time like 「兩天」 is never read as a future deadline |
| Uses words we didn't list (「胸悶」, 「很喘」, 「他沒有呼吸了」, 「小孩不見了」) | The lexicon covers common spoken forms, Simplified characters and mishearings. The AI is a second net, and the clerk has the "I'm worried" button |
| Has learned the "magic words" | Urgency follows the content, and the clerk sees the quote. We track P1 precision, not just recall |
| Exaggerates ("emergency!" for a phone charger) | Urgency comes from *what* is needed, not the tone |
| Downplays (「沒什麼啦，胰島素昨天就用完了」) | Rule: a critical medicine that has already run out is P1 |
| Depends on a powered device (製氧機, 呼吸器, 抽痰機) | Life-sustaining category. The deadline is the battery time left, and the ask-back asks 「機器電池還能撐多久？」 |
| Reports someone outside (「我爸還在家裡沒出來」), a lost child, or a wandering elder with dementia | Person-at-risk, so P1 with the rescue banner and an action log |
| Has an emergency at the desk (chest pain, labour, can't breathe, suicidal talk) | A red banner: get the on-site medic / call 119 now. The action must be logged, and if the EOC hasn't acknowledged within 3 minutes, the backup channel is shown. DarkShelter is not a dispatcher |
| Discloses domestic violence or another protection issue | A protection case: hidden from the board, exports and print. The prompt says "private spot, social worker / 113". The EOC sees only "protection case, contact desk B". Village officers and social workers have reporting duties (家暴法 §50), and the deck says so |
| Keeps coming back to ask "is my medicine coming?" | Gets a short ticket code (B-017), and the EOC status shows on the board |
| Has four family members report the same need | Merged into one request with a report count |
| Gives a national ID or phone number | ID numbers are scrubbed. Phone numbers stay at the desk and never go upstream. Bed/zone is used instead of names |
| Says numbers in Chinese (「兩包」, 「十幾個人」) | Chinese numeral parser; 十幾 is stored as about 15 and marked approximate |
| Uses relative times at night | Night-aware: at 2 a.m., 「明天早上」 means the *coming* morning. The resolved time is shown for the clerk to check |
| Doesn't want to be recorded | The consent line offers "type only", and audio is never stored anyway |

### The EOC

| Situation | What DarkShelter does |
|---|---|
| Doesn't acknowledge an emergency P1 within 3 minutes, or another P1 within 30 | Desk alert with the backup channel set at setup (runner, satellite phone). The request is resent flagged ESCALATED |
| Receives the same request twice | The receiver keeps one copy per (desk, ticket, version) |

## 7. What can go wrong, and what we do about it

| Layer | Failure | Mitigation |
|---|---|---|
| AI | Speech recognition mishears a medicine name | Domain-vocabulary prompt, clerk repeats clearly, evidence shown, picture card, clerk confirms |
| AI | Whisper invents text from silence or noise (e.g. 「請不吝點贊訂閱轉發打賞支持明鏡與點點欄目」, 「謝謝觀看」) | Voice detection, a phantom-phrase filter and a no-speech threshold |
| AI | Whisper outputs Simplified Chinese | Traditional-Chinese prompt plus converter; keyword lists hold both forms and common mishearings |
| AI | The LLM invents a field | Grounding check: the field is marked "AI guess". It may raise urgency (with ask-back) but never lower it |
| AI | The LLM drops one of several needs | Every red-flag word in the transcript must map to a P1 draft; otherwise an "unclassified P1" is created |
| AI | The LLM returns broken JSON or a wrong category | Schema-locked output, validation and one retry, then the rule draft |
| AI | The LLM is slow, unloaded after idling, or not running | Kept loaded permanently and warmed at startup with a short fixed prompt; 15 s time box; FAST mode |
| AI | Spoken prompt injection ("ignore your rules…") | Output is schema-locked and urgency comes from rules |
| Device | Whisper and the LLM fight for CPU; thermal throttling | One processing queue (never both at once); the self-test measures speed and recommends Whisper small or base; film the speed scenes first |
| Device | Battery low | Economy mode, with battery level sent in the heartbeat |
| Device | Crash or power cut mid-write | SQLite in crash-safe mode; each change plus its event is one transaction; recovery at startup |
| Device | Sleep/resume or a clock jump when network time returns | Timers use the monotonic clock; a detected jump resets the timeout estimator. Only relative ages go on the wire |
| Device | A background thread dies (e.g. the Windows UDP "connection reset" error) | Every thread loop catches errors and carries on. The watchdog restarts only threads that have actually died, so it never starts a second AI worker |
| Device | zh-TW Windows reads files as cp950, or a child process can't print Chinese | UTF-8 everywhere, with `PYTHONUTF8=1` for the child processes, plus an automated check |
| Device | The screen refreshes while the clerk is typing | Cards being edited are never redrawn; an "AI updated" badge waits for blur or confirm |
| Network | No link for days | The queue persists; 33-byte heartbeat probes every 2.5–5 s, so a returning link is noticed within seconds |
| Network | Link flaps every few seconds | Small datagrams, per-request acks, adaptive timeout; nothing is resent once acknowledged |
| Network | Heavy loss | Retries with capped backoff, and P1s reserve the window (§8) |
| Network | Hundreds queued when the link returns | Headcount and P1s first. Every request is ≤ 80 B, so even the LoRa-class link carries about 100 requests a minute |
| Network | The EOC crashes between saving and acknowledging | The EOC saves before it acks, and the desk records "sent" before sending, so a crash at any point loses nothing |
| Data | A request is edited, merged or closed while offline | Its version changes only when what goes on the wire changes. A request closed before it was ever sent is never sent. One transition table guards every change (conflicts → "please reload") |
| Data | One of the two databases is deleted | `run_demo` refuses to start until both are reset together; otherwise reused IDs would be dropped as duplicates |
| Privacy | Names or free text leak into the wire, CSV or printout | The summary that leaves the desk is built only from codes and parsed fields (location first, so truncation never cuts an address) |
| Network | Acks lost | The sender resends; the receiver re-acks duplicates and keeps one copy |
| Network | Restart reuses an ID, so the EOC treats a new request as a duplicate | IDs never reused (SQLite AUTOINCREMENT), persisted versions, boot ID in every datagram |
| Network | Spoofed or corrupted packets | 8-byte HMAC per datagram with a pre-shared desk key. This gives integrity, not secrecy; encryption is Stage II, so only minimal fields are sent |
| Data | A wrong merge hides a second person's need | Person-specific needs are never auto-merged; merges keep both records and can be undone |
| Data | Desk and EOC edit the same request | Split ownership: the desk owns content, the EOC owns status |
| People | Untrained clerk | One-screen workflow, large buttons, zh-TW labels, plain-language messages |
| People | Our fields don't match the official form | The field mapping is a config file |
| People | Privacy law (個資法 §6: health data is sensitive) | Consent line, audio deleted, minimal upstream fields, privacy view, protection cases hidden, purge after the event. Disk encryption is in the deck |
| Demo day | Live mic fails while filming | Typed path and pre-recorded clips; mock mode runs everything without models |
| Demo day | Ollama isn't running | Red status light; the desk carries on in FAST mode (itself a demo moment) |
| Demo day | Port in use, firewall prompt | Ports are configurable; everything binds to 127.0.0.1 |

## 8. Performance guarantees under bad conditions

| # | Guarantee | How it's enforced | How we prove it |
|---|---|---|---|
| G1 | A confirmed request is never lost (crash, power cut, closed lid) | Written to disk with full sync before the screen says "saved"; outbox rebuilt from disk; IDs never reused | Kill the desk *and* the EOC repeatedly mid-sync; the EOC ends with exactly the desk's set |
| G2 | The EOC never shows a duplicate or an older version | Versioned records; one copy per (desk, ticket, version); duplicates re-acked | 30% loss + flapping + restarts: duplicates = 0 |
| G3 | Most urgent first | Strict priority order. While any P1 is unacknowledged, only P1s may use the window. Retries re-enter the priority queue. Backoff capped at 2× the timeout. ≤200-byte datagrams. Window of 2 | One P1 behind 200 queued P3 requests at 20% loss arrives within the bound below, and no P3 byte is sent meanwhile |
| G4 | The desk answers within seconds | Draft ≤ 1 s after the transcript; 30 s recording cap; model chosen to fit this laptop; AI time-boxed at 15 s and kept loaded; backlog → fast mode | p95 latency over 30 recordings on the demo laptop |
| G5 | The desk never stops | Mode ladder (below) switched automatically, plus a thread watchdog | Kill Ollama, deny the mic, cut the link, kill the EOC — on camera |
| G6 | Life-safety words are never missed or downgraded by the AI | Red-flag rules can only raise priority. Every red-flag hit maps to a P1 draft. Only a clerk can lower it, with a reason | P1 recall = 100% **on our labelled test set** (we claim no more), plus P1 precision reported |
| G7 | Only minimal, integrity-protected data leaves the desk | No audio; minimal fields; ID numbers scrubbed; protection and sensitive cases generic; HMAC. *Not encrypted in Stage I* | Inspect the bytes on the wire |
| G8 | Works on any link that can carry 200-byte messages | ≤80 B per request, ≤200 B per datagram, 33 B heartbeat | Run the whole demo on the LoRa-class profile |

**Worst-case wait for a P1, worked example** (on our *emulated* LoRa-class link: 1.2 kbps = 150 B/s, 400 ± 200 ms each way, 10% loss each way). One full 200-byte datagram takes 1.3 s to send, and a round trip with its ack takes about 2.3 s, so the timeout settles around 3–4 s. With a window of 2:
- **Nothing lost:** a new P1 waits behind at most 2 datagrams (2.7 s) plus its own (≈ 0.6 s) and the one-way delay. That's **about 4 s**.
- **With loss:** a try fails if either the data or its ack is lost, 1 − 0.9² = 19% of the time. Each failure adds one timeout (3–7 s, since backoff is capped at 2×). 99.3% of P1s arrive within three tries, about 15 s.
- **Ten P1s:** they fit in 5 datagrams and clear in about 10–12 s.
- **The same ten as voice notes:** 15-second voice notes at 16 kbps are about 30 KB each, so ten would take about **33 minutes**, even with no loss.
- General bound: wait ≤ (W × MTU + bytes of P1s ahead) ÷ R + one-way delay + failed tries × RTO.
- Real LoRa is half-duplex with duty-cycle limits, so real numbers will be slower. Stage II measures them on a real link.

**Mode ladder** (the network part runs the same in every mode):

| Mode | When | What still works |
|---|---|---|
| FULL | Everything healthy | Speech + AI + rules |
| FAST | AI slow or missing, backlog ≥ 3, or battery < 15% | Speech + rules (AI on request) |
| TYPED | No mic, or the speech model failed | Typing + quick-pick + rules (+ AI if available) |
| PAPER | Battery < 5%, or forced | CSV export and the printable board (print view) |

## 9. How we'll measure it (numbers for the deck)

| What | Metric | Target | How |
|---|---|---|---|
| Speech recognition | Character error rate: clerk-repeated vs resident-direct vs noisy | report honestly | 30 recordings by 3–4 voices |
| Understanding | Category / item / quantity / location accuracy | ≥ 85% | Text set (≥ 50 cases) + audio set |
| Hallucination | Share of AI fields not found in the speech | ≤ 5% | Grounding check |
| Triage | P1 recall / precision, including negation and past-tense cases | 100% / ≥ 80% | Labelled set |
| Dedupe | Merge-suggestion precision | report | Labelled pairs |
| Speed | End-to-end p50 / p95 on the laptop CPU | p95 ≤ 8 s | Logged timings |
| Network | Time to deliver all P1s per link profile; bytes per request; duplicates and losses; P1-behind-200-P3s wait | 0 duplicates, 0 losses, within the bound | Network experiment and chaos scripts |

## 10. 3-minute demo script

| Time | Scene |
|---|---|
| 0:00–0:20 | The problem: Typhoon Danas cut power to about 880,000 households and the Hualien quake took 172 base stations down; a shelter desk drowning in paper slips |
| 0:20–1:20 | Wi-Fi off, charger out. Check-in of a family, where a screening "yes" opens a draft. Then three residents: (1) 「阿嬤明天早上要洗腎，她的胰島素今天也用完了」 becomes two P1s with quotes; (2) an elder speaks Taiwanese, the clerk repeats it in Mandarin, and it becomes a P2 medicine request; (3) 「我爸還在中正路三巷的家裡沒出來」 gives a P1 rescue banner, and the clerk logs "119 unreachable". A daughter reports the dialysis again and it's merged |
| 1:20–2:10 | Link down, the queue grows. Switch to the LoRa-class relay: the headcount, then the P1s, land on the EOC board first, in seconds. The byte meter shows under 1 KB sent versus about 90 KB as voice notes. Flap the link, kill the desk app mid-send and restart it: nothing lost, nothing duplicated. The EOC clicks "dispatched" and the status appears at the desk |
| 2:10–2:40 | Failure tour: stop Ollama, and the desk drops to FAST mode and keeps going; 「我沒有胸痛」 does not trigger an alarm; a silent press is rejected |
| 2:40–3:00 | Stage II: the same models on the UGen300; whole kit ≈ 17 W, about a day on a 500 Wh power station. "OWL moves forms; we move meaning." |

## 11. Decisions we need from the team

1. Demo laptop: Windows or Mac? The code is cross-platform, but setup steps differ.
2. Who is the operator in the pitch: a township office clerk or a Red Cross volunteer?
3. Who records the 30 test sentences tomorrow morning (3–4 different voices, some repeated by a "clerk")?
4. The backup channel to show on screen (e.g. "send a runner to the village office").

## 12. What the reviews changed

### v2 → v3: five bug-hunt debates (37 findings, all resolved)
Each debate paired a red team with a defender from the other model. They went back and forth (2–3 rounds) until nothing was disputed.

- **State & storage (9 findings).**
  - The EOC saves before it acks, and the desk records "sent" before sending.
  - One transition table guards every change; the version changes only when what goes on the wire changes.
  - A discarded or never-confirmed request can never leak unreviewed AI text.
  - Only the first escalation re-sends, which removes an escalation loop that would have frozen all non-urgent traffic.
  - The watchdog never starts a second AI worker.
  - Timers survive restarts and sleep.
  - Wiping one database is blocked.
- **Protocol (5).**
  - Probes every 2.5–5 s, so a returning link is noticed in seconds rather than after 30.
  - Every number is clamped before packing, so an overdue deadline can't crash the sender.
  - Exact ack and pending rules; EOC status numbers persist.
  - Seeded, deterministic network tests that also edit and close requests mid-run.
- **Triage & privacy (8).**
  - Fixed under-triage blockers:
    - 「已經兩天沒洗腎了」 was P2 — now caught by a general "ran out" rule.
    - Common spoken red flags were missing: 胸悶, 很喘, 他沒有呼吸了, 小孩不見了.
  - Negation that over-suppressed 「不知道是不是胸痛」.
  - An AI loophole that could re-add a denied symptom as a 119 alarm.
  - Dialysis "tomorrow morning" at 7 a.m. was 25 h → P2; the window is now 36 h.
  - Time words leaking across clauses.
  - Names leaking onto the wire; street numbers mistaken for bed numbers.
- **Runtime (6).**
  - zh-TW Windows encoding (cp950) crashes.
  - The speech model is reused from the team's existing download, never fetched twice.
  - The LLM timeout really times out.
  - Recorder auto-stop works when the clerk starts speaking immediately.
  - Orphaned processes and silent child crashes are caught.
  - The screen no longer wipes what the clerk is typing.
- **Buildability (9).**
  - The spec was 2–3× too big for one night, so we cut the digest-first mode, calibration, the picture-card page, the separate print page and the AudioWorklet recorder. We kept the EOC status loop, the crash tests and the real AI adapters.
  - Added a stop-the-line check at 2 hours.
  - Made two acceptance checks actually runnable.
  - Fixed exact codes for zones and subject groups.

### v1 → v2: first red-team review

- **Emergencies:** the banner now requires a logged action, and the EOC acknowledgement deadline is 3 minutes, not 40.
- **Unconfirmed P1s:** a content-free alert goes out at 5 minutes, never the unreviewed AI text.
- **Friction:** moved from confirming P1s to lowering them, plus an "I'm worried" button.
- **Uncertain AI fields** may raise urgency (with ask-back), never lower it.
- **Negation and past tense** no longer trigger symptom red flags. Night-aware 「明天早上」.
- **Workflow:** the clerk repeats requests into the mic by default, because Whisper mis-hears Taiwanese confidently. A picture card was added.
- **Check-in:** added check-in, 5 screening questions and a headcount that is sent first, because registration and headcount are the desk's real job.
- **Protection cases:** a separate category for domestic violence that is never shown or exported.
- **Consent and privacy:** a consent line and a privacy view for a screen that faces the queue.
- **New needs:** powered medical devices, lost children, wandering elders and family tracing.
- **Network:** P1s reserve the window, backoff is capped, IDs are never reused after a restart, and the emulator no longer buffers through an outage.
- **Reliability:** Windows UDP errors are caught by a watchdog, timers use the monotonic clock, and Ollama is kept loaded.
- **Honest numbers:** the P1 maths now counts loss in both directions, and we say the link is integrity-protected, not encrypted.
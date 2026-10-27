# FILMING — the demo video in one continuous take

Written for the presenter: one take, no editing, English narration live, Mandarin lines spoken into the desk mic.
The times below come from two dry runs of the exact sequence on this laptop. They used typed input in real Chrome,
real Whisper and Qwen, and started from a clean reset (results: `eval/results/rehearsal/film_dryrun_1.md`, `_2.md`).

## Once, before the first take

1. **Free memory.** Close your normal Chrome tabs and other apps. The real models need about 2 GB of free memory,
   and with Chrome open this laptop had only 1.2–1.9 GB.
2. **Screen.** For a 1920×1080 video, set Windows Settings → System → Display to resolution **1920×1080** and scale
   **100 %**. (This laptop is normally 2560×1600 at 150 %. If you keep that, use 75 % wherever this page says 80 %.)
3. **Run the reset command** (next section). Two windows open: the desk on the left half and the EOC on the right.
4. **Zoom:** click into the desk window and press **Ctrl + −** twice (80 %). Do the same in the EOC window. These two
   windows have their own Chrome profiles, so the zoom is remembered for every later take.
   - The desk needs 80 % to keep its three columns: drafts, board, and the link panel with the byte meter. At 100 %
     everything stacks into one column and the link panel is off-screen.
   - The EOC needs 80 % so each request's summary fits on one line next to the headcount.
5. **Microphone permission:** in the desk press 🎙️ 錄音 and click **Allow**. Say anything, then press 錄音 again.
   Then run the reset command again to clear that test draft.
6. **Ollama stop command:** open a PowerShell window and type this without pressing Enter:
   ```powershell
   Stop-Process -Name ollama,llama-server -Force
   ```

## One command per take: a clean start

```powershell
.venv\Scripts\python.exe scripts\film_reset.py
```

It does all of this:
- closes the previous take (the demo and its two windows);
- starts Ollama CPU-only if it isn't running, for example after you stopped it in the last take;
- deletes **both databases**;
- starts the demo and waits for the desk to reach **完整 FULL**, with Whisper and Qwen ready;
- sets the link to **斷線 DOWN**;
- checks in 12 households (**37 people**) so the headcount at the EOC isn't zero;
- reopens the two windows.

It prints `READY in N s`. In the dry runs that took 5–6 s, or 13–18 s when it had to start Ollama.
- `--households 0`: start with nobody checked in.
- `--mock`: practise the clicks without the models.
- If a window doesn't land in its half, click it and press **Win + ←** (desk) or **Win + →** (EOC).

At READY the desk shows:
- 完整 FULL in the top bar;
- 斷線 DOWN highlighted in the 網路 panel;
- 待送 queued 0;
- 人數 37 in the census.

The top link badge reads **📡 未知 (unknown)** while the link is down, because nothing has ever been acknowledged.
It changes to 連線 UP once the link works. To show "the link is down", point at the highlighted 斷線 button and the
待送 count.

## Recording: exactly when it stops

- **Do this:** press **🎙️ 錄音**, say the Mandarin line, then **press 錄音 again straight away**. The second click
  stops the recording. Start the English narration only after that.
- **If you don't click,** the recording stops by itself **2.5 s after the last sound it counts as speech**. English
  narration also counts as speech (anything louder than −45 dBFS and 3× the room noise). If you start talking
  within 2.5 s, the recording keeps going and your narration ends up in the request. So either click to stop, or
  stay silent for 3 s.
- **Other stops:** no speech within 8 s gives 「沒有聽到聲音」 and no draft; there is a hard stop at 30 s.
- Each spoken request takes about 2–3 s from the stop to the draft. Typed requests take about 0.4 s for the draft,
  and the AI refines the card about 2 s later.

## Where things are

**Desk (left window)**
- **Top bar:** the mode badge (完整 FULL / 快速 FAST), the link badge and the clerk.
- **Red banners** appear just under the top bar.
- **Left column:**
  - 🎙️ 錄音 (record);
  - the typing box (Enter sends);
  - the drafts, each with **✔ 確認 Confirm**. A rescue draft has the red 🚨 box with **119打不通**.
- **Middle column:** the 需求看板 board, with P1 first. Each row shows ⏳ 排隊待送 (queued), ✓ 已送達 (delivered)
  or 🚑 已派遣 (dispatched). **隱私檢視 (privacy view)** is ticked by default and hides each row's summary. Untick it,
  or click a row, to show the summaries on camera. It turns back on at every reset.
- **Right column, 網路 Link panel:**
  - the link buttons: 斷線 DOWN, **LoRa中繼 LORA_RELAY** (the LoRa-class link), LTE一格, 衛星, 正常 GOOD and 時斷時續;
  - just below them, the **outbox count**: 「待送 queued N（P1 n）」;
  - under that, the **byte meter**: 實際傳送 sent, 若用語音訊息 as voice notes, and the ratio 「少 N 倍」;
  - below the 網路 panel: 系統 (system) and 收容人數 (census).

**EOC (right window)**
- **Top:** counts of P1, P2, P3 and open requests.
- **Below them:** the requests, P1 first. Each row has three buttons: **已收到 Ack**, **已派遣 Dispatched** and
  **已解決 Resolved**.
- **Right panel:** 收容處所人數, the headcount per desk (👥 37).

## The take (dry-run times; seconds from your action to the result on screen)

| # | You do | On screen | Run 1 | Run 2 |
|---|---|---|---|---|
| 1 | Point at 斷線 DOWN. Say 「阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了」 | two **P1** drafts: 洗腎 and 胰島素, with quotes | 0.4 typed (+2–3 spoken) | 0.4 |
| | Press ✔ 確認 on both | both on the board, ⏳ 排隊待送; 待送 2（P1 2）; sent 0 B | 0.4 | 0.5 |
| 2 | Say 「我爸還在中正路三巷的家裡沒出來」 | P1 rescue draft with the 🚨 box, and the **red banner** at the top | 2.4 | 3.4 |
| | Press **119打不通** in the 🚨 box | red toast 「📻 備援通報：請派人到村辦公室通報，或使用衛星電話」 | 0.1 | 0.1 |
| | Press ✔ 確認 | on the board; its summary is 「中正路三巷 受困人員」 | 0.3 | 0.2 |
| 3 | Press **LoRa中繼** | EOC: **👥 37** and the **3 P1 rows**; the desk outbox goes to 0 | 2.1 | 2.1 |
| | EOC: **已派遣** on the B-001 洗腎 row | desk board: **🚑 已派遣** on B-001 | 4.2 | 4.2 |
| 4 | Press Enter in the PowerShell window (stop Ollama) | desk badge turns **快速 FAST** | 9.0 | 3.9 |
| | Say 「我沒有胸痛，只是有點累」 | P3 draft, **no alarm**, ask-back **「確認：現在有胸痛嗎？」** | 0.2 | 0.2 |

After step 3, the byte meter in run 2 showed **348 B sent against 58.6 KB as voice notes, 172× less**.
- **Step 3:** the headcount and the P1s arrived in the same EOC refresh (the page updates every second). At 1 s per
  refresh you can't show them arriving separately. Point at the headcount first.
- **Step 4:** the desk checks Ollama every 10 s, so FAST appears 0–10 s after you press Enter. Keep talking.
- **Next take:** the reset command starts Ollama again.

## If something goes wrong

- **Anything odd:** run the reset command again. It closes everything and starts clean.
- **Badge TYPED or FAST with 「記憶體不足」 at READY:** close apps or tabs, then reset.
- **A spoken line comes out wrong:** fix the field on the draft card before ✔ 確認. For example, if the zone shows
  only 「12床」, set B區. Or type the line instead; typing always works.
- **A window shows 「連不到登記台程式」:** the demo isn't running. Run the reset command.
- **Stop everything after filming:** close the two windows. The demo keeps running hidden until the next reset. To
  stop it, run `.venv\Scripts\python.exe scripts\film_reset.py --stop`.

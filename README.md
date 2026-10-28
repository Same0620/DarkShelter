# DarkShelter 黑暗收容站 — Stage I

An offline shelter front desk: check-in and screening, speech or typed requests turned into structured,
triaged tickets (Whisper + a small LLM + fixed rules + clerk confirmation), and a crash-proof outbox that sends
≤ 80-byte records most-urgent-first over a thin link (LoRa-class, one-bar LTE, satellite) to an EOC board.

- **Demo video:** https://youtu.be/oYu6KuO3Z-w
- Why: [docs/PLAN.md](docs/PLAN.md) · What: [docs/SPEC.md](docs/SPEC.md) · Status: [PROGRESS.md](PROGRESS.md)
- Three local processes: **desk** (http://127.0.0.1:8000), **EOC** (http://127.0.0.1:8100) and a
  **link emulator** between them (UDP 9001 → 9002, control http://127.0.0.1:9100). Everything binds to 127.0.0.1.

## 1. Setup (once, while online)

### Windows 10/11 (zh-TW is fine)

1. Install **Python 3.11 or 3.12 from python.org** (tick "Add python.exe to PATH").
   Do not use the Microsoft Store Python.
2. Install the **Microsoft Visual C++ Redistributable (x64)** — faster-whisper's ctranslate2 needs it
   (https://aka.ms/vs/17/release/vc_redist.x64.exe).
3. In PowerShell, in this folder:
   ```powershell
   py -3.12 -m venv .venv          # or: py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1     # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Install **Ollama** from https://ollama.com/download (or the portable zip from its GitHub releases and run
   `ollama.exe serve`), then: `ollama pull qwen2.5:1.5b`
5. Download the speech models (Whisper `small` and `base`, into the default Hugging Face cache):
   `python scripts/download_models.py`
6. Check the laptop: `python scripts/selftest.py` — it prints a ✓/✗ list, measures Whisper speed and
   **recommends `small` or `base`**. Put the recommendation into `config.json` → `"asr": {"model": "..."}`.
   (`config.json` is created on the first run from `config.example.json`, with a fresh HMAC key.)
7. Freeze what worked: `pip freeze > requirements-lock.txt`

### macOS

```bash
python3.12 -m venv .venv && source .venv/bin/activate     # python.org installer, 3.11 or 3.12
pip install --upgrade pip && pip install -r requirements.txt
brew install ollama   # or the app from ollama.com; then:
ollama serve &        # the app does this for you
ollama pull qwen2.5:1.5b
python scripts/download_models.py
python scripts/selftest.py        # then set asr.model in config.json as recommended
pip freeze > requirements-lock.txt
```
Use Chrome or Edge for the desk (Safari works but is not the tested browser).

After setup the laptop can go fully offline.

## 2. Running

```bash
python run_demo.py                 # real models; opens the desk and EOC pages
python run_demo.py --reset         # start from empty databases (deletes BOTH desk and EOC DBs)
python run_demo.py --mock          # no models needed: mock speech + mock AI (rules still real)
python run_demo.py --no-browser    # don't open browser tabs
python run_demo.py --config my.json
```
- Open the desk at **http://127.0.0.1:8000** and the EOC board at **http://127.0.0.1:8100**.
- Logs: `data/run/desk.log`, `data/run/eoc.log`, `data/run/linkemu.log` (one per process).
  A crashed process is restarted automatically; after 3 restarts in a minute everything stops and the log path is printed.
- Ctrl+C stops all three processes.
- If only one of the two databases exists, `run_demo` refuses to start: use `--reset` to reset both
  (otherwise reused ticket numbers would be dropped as duplicates at the EOC).
- **Filming a one-take video:** see [FILMING.md](FILMING.md). `python scripts/film_reset.py` gives a clean start
  for each take: both databases, link DOWN, Ollama running, desk FULL, and the two windows side by side.
- **More than one desk:** give every desk its own `desk_id` and `desk_letter` in `config.json`. Never copy a whole
  `config.json` from another desk. At the EOC, add each extra desk's key under `"eoc": {"desk_keys": {"<desk_id>": "<hex key>"}}`.

### Demo script (3 minutes, see PLAN §10)

1. Network panel → **斷線 DOWN**. Unplug the charger; Wi-Fi off.
2. **登記 Check-in** tab: a family of 3 in B12 with an elder; tick question 1 → a LIFE_SUSTAINING draft opens.
3. **需求 Requests**: record or type
   - 「阿嬤明天早上要洗腎，她的胰島素今天也用完了」 → two P1 drafts with quotes → ✔ 確認 each.
   - (elder speaks Taiwanese; clerk repeats in Mandarin) 「阿公的降血壓藥剩兩天」 → P2 medicine.
   - 「我爸還在中正路三巷的家裡沒出來」 → P1 rescue, red banner → 119打不通 (backup channel shows).
   - Daughter reports dialysis again → 🔁 merge suggestion → 合併.
4. Watch the queue grow, then **LoRa中繼 LORA_RELAY**: the census lands at the EOC first, then the P1s,
   in seconds. The byte meter shows bytes sent vs. the same requests as voice notes.
5. **時斷時續 FLAPPING**; kill the desk process mid-send (Task Manager) — run_demo restarts it,
   「已復原 … 筆草稿」 appears, nothing is lost or duplicated at the EOC.
6. At the EOC click **已派遣**: 🚑 appears on the desk board.
7. Failure tour: stop Ollama → the mode badge drops to **FAST** and intake keeps working;
   type 「我沒有胸痛，只是有點累」 → no alarm, just an ask-back; press 錄音 and stay silent → 「沒有聽到聲音」.

## 3. Troubleshooting

| Problem | Fix |
|---|---|
| **Mic doesn't work / permission denied** | Click the 🔒/camera icon in the address bar → allow the microphone, reload. The level meter must move. Without a mic the desk switches to TYPED mode automatically — typing always works. |
| **Mic blocked when opened from another computer** | Browsers only allow the mic on `http://127.0.0.1` / `localhost`. Use the desk laptop itself, never a LAN IP like `http://192.168.x.x:8000`. |
| **Mode badge says FAST, "AI 模型未執行"** | Ollama is not running or the model isn't pulled: start the Ollama app (or `ollama serve`), then `ollama pull qwen2.5:1.5b`. The desk re-warms the model by itself within ~10 s. Rules keep working meanwhile. |
| **Mode badge says FAST, "AI 太慢，只在需要時使用"** | Ollama answered too slowly twice in a row, usually because memory is short. Rules keep working, so drafts appear without waiting for the AI. The desk tests the AI again every minute and goes back to FULL by itself. Close browser tabs to free memory. |
| **Red banner 「N 筆語音在當機時遺失，請重錄」** | The desk crashed while recordings were waiting. For privacy, recordings are never saved to disk, so ask those people to say their request again. Typed requests are saved and finish by themselves after the restart. |
| **"連接埠 8000 已被占用" / port in use** | Usually an orphaned process from an earlier run. Windows: `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F` (or close leftover `python.exe` in Task Manager). macOS: `lsof -i :8000` then `kill <pid>`. Or change `ports` in `config.json`. |
| **Windows Firewall prompt on first run** | Everything binds to 127.0.0.1, so no inbound access is needed: choose **Cancel / don't allow** on public networks; nothing breaks. |
| **`OSError`/`ImportError` loading ctranslate2 or faster-whisper** | Install the Visual C++ Redistributable x64 (link above), then rerun `python scripts/selftest.py`. Until then the desk runs in TYPED mode. |
| **Speech model not found offline** | Run `python scripts/download_models.py` once while online. The desk never downloads at runtime (`local_files_only=True`). |
| **UnicodeDecodeError / 亂碼 on zh-TW Windows (cp950)** | All files are read and written as UTF-8 and child processes get `PYTHONUTF8=1`. If you run a module by hand and see encoding errors, set `$env:PYTHONUTF8=1` (PowerShell) first. Use UTF-8 when editing `config.json`. |
| **Whisper too slow** | Run `python scripts/selftest.py` and set `asr.model` to `base` if recommended. FAST mode also switches to `base` automatically when 3+ recordings queue up. |
| **Ollama returns HTTP 500 / `ptxas fatal` / `PTX JIT compilation failed`** | Its GPU (CUDA) path is broken on some laptops. Run it CPU-only (Windows PowerShell): `$env:CUDA_VISIBLE_DEVICES="-1"; $env:OLLAMA_VULKAN="0"; $env:GGML_VK_VISIBLE_DEVICES="-1"; ollama serve`. qwen2.5:1.5b does ~35–40 tokens/s on a modern laptop CPU. |
| **`OpenBLAS error: Memory allocation still failed`, desk not ready in 60 s** | Windows has run out of memory to hand out (commit charge), usually because of browser tabs. `run_demo.py` already limits OpenBLAS to one thread, which is enough with about 1 GB to spare. If you start `desk_app` by hand, set `$env:OPENBLAS_NUM_THREADS="1"` first. Closing Chrome tabs fixes it for good. |
| **`mkl_malloc: failed to allocate memory`, mode TYPED/FAST "記憶體不足"** | The laptop is out of memory (often dozens of browser tabs). Close Chrome/Edge tabs and other apps, then restart `run_demo.py`. The desk keeps working meanwhile: it falls back to the smaller speech model or to typing, and retries the AI every minute. Whisper small + qwen2.5:1.5b need about 3 GB free together. |
| **The EOC shows nothing** | The link starts **DOWN** on purpose. Pick a profile (正常 GOOD / LoRa中繼) in the desk's network panel. |

## 4. Tests and evaluation

```bash
python -m unittest discover -s tests          # everything, incl. slow network/chaos/UI tests
DS_FAST=1 python -m unittest discover -s tests   # skip the slow ones
python eval/run_text_eval.py --llm none       # rules only; also --llm mock / --llm ollama
python eval/net_experiment.py                 # every link profile → eval/results/net_experiment.md
python eval/chaos.py                          # kill/restart sender and EOC ≥ 5 times each
python eval/run_audio_eval.py                 # needs eval/audio/*.wav + labels.csv (see eval/audio/README.md)
python eval/run_audio_eval.py --tts-demo      # no recordings yet: synthesise clips with the OS voice (not real voices)
python eval/demo_check.py                     # acceptance check 2 against a running `run_demo.py --mock --no-browser --reset`
python scripts/selftest.py --skip-llm         # (or --skip-whisper / --json) partial self-tests
```
The UI smoke test needs `pip install playwright && python -m playwright install chromium` (dev only).

## 5. Layout

`darkshelter/` (desk + EOC + net), `data/` (codebook, lexicon, prompts), `scripts/` (selftest, model download,
test WAVs, Ollama stub), `eval/` (text/audio/network/chaos evaluation), `tests/` (stdlib unittest).

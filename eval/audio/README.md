# Audio test set (recorded by the team)

The audio evaluation measures how well speech becomes the right request on **this laptop**.
Recordings are personal data: `eval/audio/*.wav` is in `.gitignore` and never leaves the laptop.

## What to record (about 30 clips, 3–4 different voices)

| condition | how |
|---|---|
| `clerk_repeat` | The "clerk" listens to a resident and repeats the key points into the mic in Mandarin (the default workflow) |
| `resident_direct` | The "resident" speaks directly into the mic, naturally |
| `noisy` | Either of the above with background noise (TV, crowd, fan, rain recording) |

Use sentences from `eval/text_cases.jsonl` (so each clip has a `case_id` and gold needs), including
P1 cases (洗腎, 胰島素沒了, 受困, 小孩走失, 胸痛) and must-not-be-P1 cases (「我沒有胸痛，只是有點累」).

## Format

- WAV, 16-bit PCM, mono, any sample rate (16 kHz is ideal; the desk recorder's files work as-is).
- One request per file, at most 30 s.
- File names like `v1_clerk_case07.wav` (voice, condition, case).

## labels.csv

Copy `labels_template.csv` to `labels.csv` and fill one row per file:

```
file,case_id,condition,gold_transcript
v1_clerk_case07.wav,case07,clerk_repeat,阿嬤明天早上要洗腎
```

An optional `gold_p1` column (1/0) overrides the P1 label taken from `text_cases.jsonl`.

## Run

```
python eval/run_audio_eval.py                       # real Whisper (asr.model), rules only
python eval/run_audio_eval.py --llm ollama          # + Qwen2.5-1.5B via Ollama
python eval/run_audio_eval.py --model base          # compare the FAST-mode model
python eval/run_audio_eval.py --tts-demo            # smoke test with the OS Mandarin voice (no recordings needed)
```

Results: `eval/results/audio_eval.md` and `.json` — CER, P1 recall and false P1s, and ASR / LLM /
end-to-end p50/p95 latency, per condition. TTS-demo numbers are a smoke test, not real-speech results.

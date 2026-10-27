# Audio evaluation

ASR: real (small), LLM: none, 8 recordings, 2026-10-28 02:15:21.

**TTS demo set** (OS text-to-speech, clean audio): a smoke test of the pipeline, not a measurement of real shelter speech.

| condition | n | rejected | CER | P1 recall | false P1 | ASR p50/p95 s | LLM p50/p95 s | end-to-end p50/p95 s |
|---|---|---|---|---|---|---|---|---|
| clerk_repeat | 8 | 0 | 4.2% | 4/4 | 0 | 1.344/1.625 | None/None | 1.344/1.641 |

| file | condition | CER | P | gold P1 | heard |
|---|---|---|---|---|---|
| tts01.wav | clerk_repeat | 0.1 | 1 | True | 阿嬤明天早上要洗腎,他的遺島素今天也用完了。 |
| tts02.wav | clerk_repeat | 0.0667 | 1 | True | 我爸還在中正路三項的家裡沒出來。 |
| tts03.wav | clerk_repeat | 0.0 | 1 | True | 寶寶沒奶粉了。 |
| tts04.wav | clerk_repeat | 0.0 | 1 | True | 阿公已經兩天沒洗腎了。 |
| tts05.wav | clerk_repeat | 0.0 | 3 | False | 我沒有胸痛﹑只是有點累。 |
| tts06.wav | clerk_repeat | 0.0 | 3 | False | 我們家四個人需要毛毯。 |
| tts07.wav | clerk_repeat | 0.1667 | 3 | False | 阿公的降血壓要只剩兩天份，他在地區舞床。 |
| tts08.wav | clerk_repeat | 0.0 | 3 | False | 我的手機沒電了,想聯絡家人。 |

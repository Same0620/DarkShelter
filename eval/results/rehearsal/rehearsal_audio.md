# Dress rehearsal — audio input, real models — 2026-10-28 07:01:34
desk ready after 0 s: mode=FULL reason=ok asr=small llm_ready=True

### 1_dialysis_insulin: 「阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了」 (audio)
  job done in 4.6 s | mode FULL | transcript '阿嬤在避區十二床,明天早上要洗腎,他的遺島素今天也用完了。'
  timings: {"checking": 0.016, "queued": 0.005, "refining": 2.906, "total": 4.419, "transcribing": 1.484, "understanding": 0.016}
  asr: {"avg_logprob": -0.1867, "compression_ratio": 0.883, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.0005, "raw_text": "阿嬤在避區十二床,明天早上要洗腎,他的遺島素今天也用完了。", "reasons": [], "secon
  llm: {"error": null, "prompt_eval_count": 548, "seconds": 2.891, "tokens_per_s": 45.19}
    B-001 P1 維生醫療 | item 洗腎 | status draft | emergency -
      desk summary: '阿嬤明天早上要洗腎'   wire summary: 'B區12床 長者 洗腎 10/29 08:00'
      reasons: 維生醫療36小時內
      evidence quotes: {"category": "洗腎", "item": "洗腎", "location": "B區十二床", "subject": "阿嬤", "time_phrase": "明天早上"}
      fields from the AI: summary
    B-002 P1 藥品 | item 胰島素 | status draft | emergency -
      desk summary: '阿嬤的遺島素今天用完'   wire summary: 'B區12床 長者 胰島素 10/28 07:01'
      reasons: 關鍵藥品12小時內
      evidence quotes: {"category": "遺島素", "item": "遺島素", "location": "B區十二床", "subject": "阿嬤", "time_phrase": "完了"}
      fields from the AI: summary
  banners now: none
  confirmed B-001: wire summary 'B區12床 長者 洗腎 10/29 08:00', wire state 排隊待送
  confirmed B-002: wire summary 'B區12床 長者 胰島素 10/28 07:01', wire state 排隊待送
  banners after confirm: none

### 2_rescue: 「我爸還在中正路三巷的家裡沒出來」 (audio)
  job done in 2.7 s | mode FULL | transcript '我爸還在中正路三項的家裡沒出來。'
  timings: {"checking": 0.0, "queued": 0.003, "refining": 1.422, "total": 2.673, "transcribing": 1.25, "understanding": 0.0}
  asr: {"avg_logprob": -0.2426, "compression_ratio": 0.8136, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.0013, "raw_text": "我爸還在中正路三項的家裡沒出來。", "reasons": [], "seconds": 1.25, "
  llm: {"error": null, "prompt_eval_count": 537, "seconds": 1.406, "tokens_per_s": 43.8}
    B-003 P1 受困／失聯／走失 | item 受困人員 | status draft | emergency rescue
      desk summary: '我爸還在中正路三項的家裡沒出來'   wire summary: '中正路三巷 受困人員'
      reasons: 危險字詞「沒出來」; 人員受困／失聯／走失
      evidence quotes: {"_red_flags": ["rescue_trapped"], "category": "沒出來", "item": "沒出來", "location": "中正路三項", "red_flag": "沒出來", "subject": "我爸"}
      ask-backs / missing: 有幾個人？ | 有人受傷嗎？
      fields from the AI: location, subject, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']
  confirmed B-003: wire summary '中正路三巷 受困人員', wire state 排隊待送
  banners after confirm: ['緊急狀況：請通知現場醫護或撥打119']

### 3_bp_meds: 「阿伯的高血壓藥只剩兩天」 (audio)
  job done in 2.7 s | mode FULL | transcript '阿嬤的高血壓要只剩兩天。'
  timings: {"checking": 0.0, "queued": 0.003, "refining": 1.391, "total": 2.597, "transcribing": 1.203, "understanding": 0.0}
  asr: {"avg_logprob": -0.1825, "compression_ratio": 0.766, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.0015, "raw_text": "阿嬤的高血壓要只剩兩天。", "reasons": [], "seconds": 1.203, "spee
  llm: {"error": null, "prompt_eval_count": 535, "seconds": 1.391, "tokens_per_s": 43.94}
    B-004 P2 藥品 | item 降血壓藥 | status draft | emergency -
      desk summary: '阿嬤的高血壓藥只剩兩天'   wire summary: '長者 降血壓藥 10/30 07:01'
      reasons: 其他; 藥品48小時內或時間不明
      evidence quotes: {"item": "", "subject": "阿嬤", "time_phrase": "兩天"}
      ask-backs / missing: 確認：物品是否為「高血壓藥」？（AI推測） | 確認：類別是否為「高血壓藥」？（AI推測） | 是誰需要？在哪一床？
      fields from the AI: category, item, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']
  confirmed B-004: wire summary '長者 降血壓藥 10/30 07:01', wire state 排隊待送
  banners after confirm: ['緊急狀況：請通知現場醫護或撥打119']

### 4_daughter_repeat: 「我阿嬤B區12床明天早上要洗腎」 (audio)
  job done in 2.9 s | mode FULL | transcript '我阿嬤必趨十二床明天早上要洗腎。'
  timings: {"checking": 0.0, "queued": 0.003, "refining": 1.5, "total": 2.773, "transcribing": 1.266, "understanding": 0.015}
  asr: {"avg_logprob": -0.2208, "compression_ratio": 0.8136, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.0002, "raw_text": "我阿嬤必趨十二床明天早上要洗腎。", "reasons": [], "seconds": 1.266, 
  llm: {"error": null, "prompt_eval_count": 539, "seconds": 1.485, "tokens_per_s": 43.23}
    B-005 P1 維生醫療 | item 洗腎 | status draft | emergency -
      desk summary: '阿嬤明天早上要洗腎'   wire summary: '12床 長者 洗腎 10/29 08:00'
      reasons: 維生醫療36小時內
      evidence quotes: {"category": "洗腎", "item": "洗腎", "location": "十二床", "subject": "阿嬤", "time_phrase": "明天早上"}
      merge suggestion: B-001 (same_zone+same_subject)
      fields from the AI: location, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']

### 5_no_chest_pain: 「我沒有胸痛，只是有點累」 (audio)
  job done in 2.3 s | mode FULL | transcript '我沒有胸痛﹑只是有點累。'
  timings: {"checking": 0.0, "queued": 0.003, "refining": 0.984, "total": 2.142, "transcribing": 1.14, "understanding": 0.016}
  asr: {"avg_logprob": -0.3302, "compression_ratio": 0.766, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.002, "raw_text": "我沒有胸痛﹑只是有點累。", "reasons": [], "seconds": 1.14, "speech
  llm: {"error": null, "prompt_eval_count": 533, "seconds": 0.969, "tokens_per_s": 44.51}
    B-006 P3 其他 | item - | status draft | emergency -
      desk summary: '沒有胸痛，只是有點累'   wire summary: '其他'
      reasons: 其他
      evidence quotes: {"_suppressed": ["sym_chest_pain"], "subject": "我"}
      ask-backs / missing: 確認：現在有胸痛嗎？
      fields from the AI: subject, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']

### 6_unsure_chest: 「不知道是不是胸痛」 (audio)
  job done in 2.3 s | mode FULL | transcript '不知道是不是兇統。'
  timings: {"checking": 0.0, "queued": 0.003, "refining": 1.047, "total": 2.183, "transcribing": 1.141, "understanding": 0.0}
  asr: {"avg_logprob": -0.6743, "compression_ratio": 0.7714, "language": "zh", "low_conf": false, "model": "small", "no_speech_prob": 0.0013, "raw_text": "不知道是不是兇統。", "reasons": [], "seconds": 1.141, "speech
  llm: {"error": null, "prompt_eval_count": 529, "seconds": 1.047, "tokens_per_s": 44.49}
    B-007 P1 緊急醫療 | item 胸痛／呼吸困難 | status draft | emergency medical
      desk summary: '胸痛／呼吸困難'   wire summary: '胸痛／呼吸困難'
      reasons: 危險字詞「兇統」; 緊急醫療
      evidence quotes: {"_red_flags": ["sym_chest_pain"], "category": "兇統", "item": "兇統", "red_flag": "兇統"}
      ask-backs / missing: 確認：現在有兇統嗎？ | 請問在哪一區、幾號床？
  banners now: ['緊急狀況：請通知現場醫護或撥打119', '緊急狀況：請通知現場醫護或撥打119']

### check-in with screening question 1 = yes
  household 1 -> drafts [8]
    B-008 P1 維生醫療 | item - | status draft | emergency -
      desk summary: '報到篩檢Q1：維生醫療'   wire summary: 'B區12床 長者 維生醫療'
      reasons: 維生醫療，時間不明
      evidence quotes: {"category": "有人需要洗腎、氧氣或用電的醫療器材嗎？"}
      ask-backs / missing: 氧氣／機器電池還能撐多久？
      fields from the AI: category

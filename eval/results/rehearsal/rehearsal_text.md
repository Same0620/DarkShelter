# Dress rehearsal — text input, real models — 2026-10-28 06:57:03
desk ready after 0 s: mode=FULL reason=ok asr=small llm_ready=True

### 1_dialysis_insulin: 「阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了」 (text)
  job done in 2.8 s | mode FULL | transcript '阿嬤在B區12床，明天早上要洗腎，她的胰島素今天也用完了'
  timings: {"queued": 0.002, "refining": 2.765, "total": 2.781, "understanding": 0.016}
  llm: {"error": null, "prompt_eval_count": 548, "seconds": 2.75, "tokens_per_s": 44.62}
    B-001 P1 維生醫療 | item 洗腎 | status draft | emergency -
      desk summary: '阿嬤明天早上要洗腎'   wire summary: 'B區12床 長者 洗腎 10/29 08:00'
      reasons: 維生醫療36小時內
      evidence quotes: {"category": "洗腎", "item": "洗腎", "location": "B區12床", "subject": "阿嬤", "time_phrase": "明天早上"}
      fields from the AI: summary
    B-002 P1 藥品 | item 胰島素 | status draft | emergency -
      desk summary: '阿嬤的胰島素今天用完'   wire summary: 'B區12床 長者 胰島素 10/28 06:57'
      reasons: 關鍵藥品12小時內
      evidence quotes: {"category": "胰島素", "item": "胰島素", "location": "B區12床", "subject": "阿嬤", "time_phrase": "完了"}
      fields from the AI: summary
  banners now: none
  confirmed B-001: wire summary 'B區12床 長者 洗腎 10/29 08:00', wire state 排隊待送
  confirmed B-002: wire summary 'B區12床 長者 胰島素 10/28 06:57', wire state 排隊待送
  banners after confirm: none

### 2_rescue: 「我爸還在中正路三巷的家裡沒出來」 (text)
  job done in 1.5 s | mode FULL | transcript '我爸還在中正路三巷的家裡沒出來'
  timings: {"queued": 0.005, "refining": 1.266, "total": 1.263, "understanding": 0.0}
  llm: {"error": null, "prompt_eval_count": 536, "seconds": 1.25, "tokens_per_s": 44.34}
    B-003 P1 受困／失聯／走失 | item 受困人員 | status draft | emergency rescue
      desk summary: '我爸還在家中，未出門'   wire summary: '中正路三巷 受困人員'
      reasons: 危險字詞「沒出來」; 人員受困／失聯／走失
      evidence quotes: {"_red_flags": ["rescue_trapped"], "category": "沒出來", "item": "沒出來", "location": "中正路三巷", "red_flag": "沒出來", "subject": "我爸"}
      ask-backs / missing: 有幾個人？ | 有人受傷嗎？
      fields from the AI: subject, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']
  confirmed B-003: wire summary '中正路三巷 受困人員', wire state 排隊待送
  banners after confirm: ['緊急狀況：請通知現場醫護或撥打119']

### 3_bp_meds: 「阿伯的高血壓藥只剩兩天」 (text)
  job done in 1.3 s | mode FULL | transcript '阿伯的高血壓藥只剩兩天'
  timings: {"queued": 0.003, "refining": 1.188, "total": 1.185, "understanding": 0.0}
  llm: {"error": null, "prompt_eval_count": 533, "seconds": 1.172, "tokens_per_s": 46.04}
    B-004 P2 藥品 | item 降血壓藥 | status draft | emergency -
      desk summary: '阿伯的高血壓藥只剩兩天'   wire summary: '長者 降血壓藥 10/30 06:57'
      reasons: 藥品48小時內或時間不明
      evidence quotes: {"category": "高血壓藥", "item": "高血壓藥", "subject": "阿伯", "time_phrase": "兩天"}
      ask-backs / missing: 是誰需要？在哪一床？
      fields from the AI: subject, subject_group, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']
  confirmed B-004: wire summary '長者 降血壓藥 10/30 06:57', wire state 排隊待送
  banners after confirm: ['緊急狀況：請通知現場醫護或撥打119']

### 4_daughter_repeat: 「我阿嬤B區12床明天早上要洗腎」 (text)
  job done in 1.7 s | mode FULL | transcript '我阿嬤B區12床明天早上要洗腎'
  timings: {"queued": 0.004, "refining": 1.5, "total": 1.511, "understanding": 0.0}
  llm: {"error": null, "prompt_eval_count": 538, "seconds": 1.5, "tokens_per_s": 44.99}
    B-005 P1 維生醫療 | item 洗腎 | status draft | emergency -
      desk summary: '阿嬤明天早上要洗腎'   wire summary: 'B區12床 長者 洗腎 10/29 08:00'
      reasons: 維生醫療36小時內
      evidence quotes: {"category": "洗腎", "item": "洗腎", "location": "B區12床", "subject": "阿嬤", "time_phrase": "明天早上"}
      merge suggestion: B-001 (same_zone+same_subject)
      fields from the AI: summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']

### 5_no_chest_pain: 「我沒有胸痛，只是有點累」 (text)
  job done in 1.1 s | mode FULL | transcript '我沒有胸痛，只是有點累'
  timings: {"queued": 0.004, "refining": 0.937, "total": 0.948, "understanding": 0.016}
  llm: {"error": null, "prompt_eval_count": 531, "seconds": 0.922, "tokens_per_s": 46.6}
    B-006 P3 其他 | item - | status draft | emergency -
      desk summary: '沒有胸痛，只是有點累'   wire summary: '其他'
      reasons: 其他
      evidence quotes: {"_suppressed": ["sym_chest_pain"], "subject": "我"}
      ask-backs / missing: 確認：現在有胸痛嗎？
      fields from the AI: subject, summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119']

### 6_unsure_chest: 「不知道是不是胸痛」 (text)
  job done in 1.1 s | mode FULL | transcript '不知道是不是胸痛'
  timings: {"queued": 0.004, "refining": 1.0, "total": 1.012, "understanding": 0.015}
  llm: {"error": null, "prompt_eval_count": 527, "seconds": 1.0, "tokens_per_s": 43.85}
    B-007 P1 緊急醫療 | item 胸痛／呼吸困難 | status draft | emergency medical
      desk summary: '不知道是不是胸痛'   wire summary: '胸痛／呼吸困難'
      reasons: 危險字詞「胸痛」; 緊急醫療
      evidence quotes: {"_red_flags": ["sym_chest_pain"], "category": "胸痛", "item": "胸痛", "red_flag": "胸痛"}
      ask-backs / missing: 確認：現在有胸痛嗎？ | 請問在哪一區、幾號床？
      fields from the AI: summary
  banners now: ['緊急狀況：請通知現場醫護或撥打119', '緊急狀況：請通知現場醫護或撥打119']

### check-in with screening question 1 = yes
  household 1 -> drafts [8]
    B-008 P1 維生醫療 | item - | status draft | emergency -
      desk summary: '報到篩檢Q1：維生醫療'   wire summary: 'B區12床 長者 維生醫療'
      reasons: 維生醫療，時間不明
      evidence quotes: {"category": "有人需要洗腎、氧氣或用電的醫療器材嗎？"}
      ask-backs / missing: 氧氣／機器電池還能撐多久？
      fields from the AI: category

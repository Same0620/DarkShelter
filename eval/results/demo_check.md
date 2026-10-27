# Acceptance check 2 — demo flow (mock mode)

2026-10-28 03:00:47, profile LORA_RELAY, 31.8 s total

```
PASS  desk up, link DOWN — mode=FULL link=UNKNOWN
PASS  check-in: screening yes -> draft — household 1 drafts [1]
PASS  typed requests -> drafts — B-001 P1 維生醫療, B-002 P1 維生醫療, B-003 P1 藥品, B-004 P1 受困／失聯／走失, B-005 P2 行動協助, B-006 P3 物資, B-007 P3 通訊／聯絡家人／翻譯, B-008 P3 飲食
PASS  all confirmed onto the board — 8 tickets
PASS  queued while DOWN (desk ⏳, EOC empty) — desk queued=8 eoc tickets=0 data_bytes_up=0
PASS  all 8 tickets visible on the EOC page — 8 rows after 17.5 s
PASS  census arrives first — census at +0.59 s, first ticket at +1.59 s, people=4
PASS  every P1 before any P3 (EOC receive times) — P1 rx +1.87 s (last), P3 rx +17.39 s (first)
PASS  byte meter — sent 843 B vs voice notes 160000 B (ratio 189.8x); duplicates re-acked at EOC: 3
PASS  已派遣 at the EOC shows at the desk — B-001 🚑 已派遣
```

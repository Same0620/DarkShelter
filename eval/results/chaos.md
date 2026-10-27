# Chaos experiment

Run 2026-10-28 05:56:06 · seed 13 · link: 30% loss each way, 16000 bps, 150 ± 100 ms, flapping 6 s up / 3 s down.

| Metric | Value |
|---|---|
| Result | **PASS** |
| Sender kills (subprocess, hard kill) | 5 |
| EOC kills (subprocess, hard kill) | 5 |
| Tickets on the desk (wire views) | 43 |
| Tickets on the EOC board | 43 |
| Missing at the EOC | 0 |
| Wrong version/content at the EOC | 0 |
| Extra at the EOC | 0 |
| Duplicates shown on the board | 0 |
| Duplicate receptions suppressed (re-acked) | 37 |
| Time until equal | 75.5 s (34.5 s after the last restart) |
| Mid-run changes | {"edited": [35, 38, 5], "discarded": [39], "resolved": [28]} + 5 new tickets |

Pass = the EOC set equals the desk's set of confirmed wire views (version, flags, zone, quantity,
summary, priority, category, item) with 0 duplicates shown, within 180 s.

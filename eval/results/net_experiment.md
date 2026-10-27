# Network experiment

60 seeded tickets (10 P1) per run, seed 7. The link is DOWN for 5 s, then switches to the profile; the sender finds out from its own heartbeat probes. Times are measured from the switch. Each run is capped at 180 s.

| Profile | Census (s) | All P1 (s) | All (s) | Bytes up | Bytes down | Datagrams up | Retrans. | B/request | Dups shown | Dups suppressed | Losses | Ordering violations | Voice notes: bytes | Voice notes: time (s) | Voice ÷ data |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LORA_RELAY | 4.31 | 10.78 | 46.0 | 4894 | 904 | 31 | 7 | 79.9 | 0 | 15 | 0 | 0 | 1,200,000 | 8,000.0 | 250.3× |
| LTE_ONE_BAR | 4.11 | 6.7 | 41.89 | 7745 | 880 | 51 | 22 | 124.7 | 0 | 22 | 0 | 0 | 1,200,000 | 600.0 | 160.4× |
| SATELLITE | 4.76 | 9.58 | 29.31 | 4375 | 848 | 27 | 4 | 71.8 | 0 | 5 | 0 | 0 | 1,200,000 | 4,000.0 | 278.5× |
| GOOD | 4.11 | 4.34 | 5.09 | 3618 | 832 | 22 | 0 | 59.8 | 0 | 0 | 0 | 0 | 1,200,000 | 9.6 | 334.7× |
| FLAPPING | 4.11 | 7.03 | > cap (54/60 by the cap) | 9953 | 768 | 99 | 29 | 138.4 | 0 | 19 | 0 | 0 | 1,200,000 | 1,800.0 | 144.5× |

**Result: PASS** — 0 duplicates shown, 0 losses and 0 ordering violations required on every profile.

Not finished within the 180 s cap: FLAPPING (54/60 delivered by the cap; the rest stayed in the outbox and arrived 0.31 s after switching to GOOD). A ticket still queued at the cap is not lost; 'Losses' counts tickets that never arrived.

Voice-note baseline: each request as a 10 s voice note at 2,000 B/s (16 kbps); time = bytes ÷ link rate, ignoring loss (FLAPPING uses its average rate, LTE one bar up 1/3 of the time). 'Dups suppressed' are duplicate receptions the EOC re-acked and dropped; none is ever shown.

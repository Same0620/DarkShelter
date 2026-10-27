# Text evaluation (--llm ollama)

Generated 2026-10-28 02:37 on 72 labelled cases (C:\Users\Tony\Desktop\ASUS_Hackathon\darkshelter\eval\text_cases.jsonl).

| Metric | Value |
|---|---|
| need_precision | 96.2 % |
| need_recall | 96.2 % |
| category_recall | 96.2 % |
| item_accuracy | 100.0 % |
| quantity_accuracy | 93.3 % |
| zone_accuracy | 100.0 % |
| priority_accuracy | 100.0 % |
| emergency_accuracy | 100.0 % |
| p1_recall | 100.0 % |
| p1_precision | 100.0 % |
| dedupe_precision | 100.0 % |
| dedupe_recall | 100.0 % |
| hallucination_rate | 12.2 % |
| latency rules p50 / p95 | 0.33 / 1.12 ms |
| latency LLM p50 / p95 | 1338.48 / 1899.88 ms |
| latency end-to-end p50 / p95 | 1353.69 / 1900.08 ms |
| LLM errors | 0 |

Gold P1 needs: 43, predicted P1 needs: 43. Dedupe pool: {'tp': 5, 'fp': 0, 'fn': 0}.

All must-be-P1 and must-not-be-P1 cases pass.

## Cases

| id | ok | predicted (category/item/P) |
|---|---|---|
| p1_dialysis_two_days | ✓ | LIFE_SUSTAINING/201/P1 |
| p1_insulin_gone | ✓ | MEDICATION/301/P1 |
| p1_insulin_left | ✓ | MEDICATION/301/P1 |
| p1_formula_gone | ✓ | INFANT_CHILD/601/P1 |
| p1_not_breathing | ✓ | MEDICAL_EMERGENCY/107/P1 |
| p1_doubt_stroke | ✓ | MEDICAL_EMERGENCY/106/P1 |
| p1_doubt_chest | ✓ | MEDICAL_EMERGENCY/101/P1 |
| p1_sudden_chest | ✓ | MEDICAL_EMERGENCY/101/P1 |
| p1_oxygen_battery | ✓ | LIFE_SUSTAINING/203/P1 |
| p1_lost_child | ✓ | PERSON_AT_RISK/403/P1 |
| p1_wandering_elder | ✓ | PERSON_AT_RISK/404/P1 |
| p1_proxy_rescue | ✓ | PERSON_AT_RISK/401/P1 |
| p1_dialysis_after_bp | ✓ | MEDICATION/306/P3, LIFE_SUSTAINING/201/P1 |
| p1_dialysis_7am | ✓ | LIFE_SUSTAINING/201/P1 |
| np_denied_chest | ✓ | OTHER/0/P3 |
| np_denied_chest_simplified | ✓ | OTHER/0/P3 |
| np_past_stroke | ✓ | MEDICATION/306/P2 |
| np_denied_dyspnea | ✓ | MEDICAL_EMERGENCY/101/P2 |
| np_denied_seizure | ✓ | MEDICAL_EMERGENCY/105/P2 |
| np_past_stroke_wheelchair | ✓ | MOBILITY/501/P2 |
| np_denied_faint | ✓ | PERSON_AT_RISK/0/P2 |
| head_injury_not_protection | ✓ | MEDICAL_EMERGENCY/102/P1 |
| night_rule | ✓ | LIFE_SUSTAINING/201/P1 |
| address_not_bed | ✓ | PERSON_AT_RISK/401/P1 |
| bed_not_address | ✓ | MOBILITY/501/P2 |
| fewshot_one | ✓ | LIFE_SUSTAINING/201/P1, MEDICATION/301/P1 |
| fewshot_two | ✓ | SUPPLIES/801/P3, COMMUNICATION/1001/P3 |
| downplay_insulin | ✓ | MEDICATION/301/P1 |
| charger_p3 | ✓ | COMMUNICATION/1001/P3 |
| vague_medicine | ✓ | MEDICATION/310/P2 |
| sensitive_medicine | ✓ | MEDICATION/310/P2 |
| protection_case | ✓ | PROTECTION/1301/P2 |
| id_scrubbed | ✓ | SUPPLIES/801/P3 |
| suicide | ✓ | MENTAL_HEALTH/902/P1 |
| mental_support | ✓ | MENTAL_HEALTH/901/P2 |
| pet_food | ✓ | PET/1101/P3 |
| landslide_road | ✓ | INFRASTRUCTURE/1202/P2, INFRASTRUCTURE/1201/P2 |
| interpreter | ✓ | COMMUNICATION/1004/P2 |
| water_boxes | ✓ | FOOD_WATER/701/P3 |
| labour | ✓ | MEDICAL_EMERGENCY/103/P1 |
| concentrator_dead | ✓ | LIFE_SUSTAINING/203/P1 |
| unaccompanied_child | ✓ | PERSON_AT_RISK/406/P1 |
| grandma_missing | ✓ | PERSON_AT_RISK/404/P1 |
| dad_unreachable | ✓ | PERSON_AT_RISK/402/P1 |
| family_tracing | ✓ | PERSON_AT_RISK/405/P2 |
| simplified_dialysis | ✓ | LIFE_SUSTAINING/201/P1 |
| mishearing_insulin | ✓ | MEDICATION/301/P1 |
| mishearing_dialysis | ✓ | LIFE_SUSTAINING/201/P1 |
| tightness_not_pain | ✓ | MEDICAL_EMERGENCY/101/P1 |
| formula_and_diapers | ✓ | INFANT_CHILD/601/P2, INFANT_CHILD/602/P2 |
| approx_people | ✓ | FOOD_WATER/702/P3 |
| insulin_soon | ✓ | MEDICATION/301/P1 |
| insulin_three_days | ✓ | MEDICATION/301/P2 |
| adult_diapers_elder | ✓ | SUPPLIES/804/P2 |
| heart_discomfort | ✓ | MEDICAL_EMERGENCY/101/P1 |
| anticoagulant_forgotten | ✓ | MEDICATION/302/P1 |
| outage_concentrator | ✓ | INFRASTRUCTURE/1203/P2, LIFE_SUSTAINING/203/P1 |
| baby_diapers_bed | ✓ | INFANT_CHILD/602/P2 |
| husband_missing | ✓ | PERSON_AT_RISK/402/P1 |
| dup1a | ✓ | LIFE_SUSTAINING/201/P1 |
| dup1b | ✓ | LIFE_SUSTAINING/201/P1 |
| dup2a | ✓ | MOBILITY/501/P2 |
| dup2b | ✓ | MOBILITY/501/P2 |
| dup3a | ✓ | INFANT_CHILD/601/P1 |
| dup3b | ✓ | INFANT_CHILD/601/P1 |
| dup4a | ✓ | PERSON_AT_RISK/401/P1 |
| dup4b | ✓ | PERSON_AT_RISK/401/P1 |
| dup5a | ✓ | MEDICATION/301/P1 |
| dup5b | ✓ | MEDICATION/301/P1 |
| dupx_wheelchair_other | ✓ | MOBILITY/501/P2 |
| dupx_blanket_e1 | ✓ | SUPPLIES/801/P3 |
| dupx_blanket_f2 | ✓ | SUPPLIES/801/P3 |

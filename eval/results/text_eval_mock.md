# Text evaluation (--llm mock)

Generated 2026-10-28 06:01 on 127 labelled cases (C:\Users\Tony\Desktop\ASUS_Hackathon\darkshelter\eval\text_cases.jsonl).

| Metric | Value |
|---|---|
| need_precision | 100.0 % |
| need_recall | 100.0 % |
| category_recall | 100.0 % |
| item_accuracy | 100.0 % |
| quantity_accuracy | 100.0 % |
| zone_accuracy | 100.0 % |
| priority_accuracy | 100.0 % |
| emergency_accuracy | 100.0 % |
| p1_recall | 100.0 % |
| p1_precision | 100.0 % |
| dedupe_precision | 100.0 % |
| dedupe_recall | 100.0 % |
| hallucination_rate | 0.0 % |
| latency rules p50 / p95 | 0.12 / 0.23 ms |
| latency LLM p50 / p95 | 0.39 / 0.73 ms |
| latency end-to-end p50 / p95 | 0.51 / 0.96 ms |
| LLM errors | 0 |

Gold P1 needs: 91, predicted P1 needs: 91. Dedupe pool: {'tp': 5, 'fp': 0, 'fn': 0}.

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
| np_denied_dyspnea | ✓ | OTHER/0/P3 |
| np_denied_seizure | ✓ | OTHER/0/P3 |
| np_past_stroke_wheelchair | ✓ | MOBILITY/501/P2 |
| np_denied_faint | ✓ | OTHER/0/P3 |
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
| rt_c1_help | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_heart_attack | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_c1_heart_attack_simplified | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_c1_cannot_breathe | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_c1_breath_stopped | ✓ | MEDICAL_EMERGENCY/107/P1 |
| rt_c1_not_breathing_zai | ✓ | MEDICAL_EMERGENCY/107/P1 |
| rt_c1_not_breathing_simplified | ✓ | MEDICAL_EMERGENCY/107/P1 |
| rt_c1_wrist | ✓ | MENTAL_HEALTH/902/P1 |
| rt_c1_seizure | ✓ | MEDICAL_EMERGENCY/105/P1 |
| rt_c1_waters | ✓ | MEDICAL_EMERGENCY/103/P1 |
| rt_c1_head_bleeding | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt_c1_massive_bleeding | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt_c1_drowning | ✓ | PERSON_AT_RISK/0/P1 |
| rt_c1_electric | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_vomit_blood | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_shock | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt_c1_unconscious | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt_c1_confused | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt_c1_cyanosis | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_c1_anaphylaxis | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_c1_co | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_heatstroke | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_fever | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_collapse | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt_c1_injured_address | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt_c1_hemoptysis | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_ambulance | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_119 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c1_help_with_rescue | ✓ | PERSON_AT_RISK/401/P1 |
| rt_c1_rescue_requested | ✓ | PERSON_AT_RISK/401/P1 |
| rt_lost_way_child | ✓ | PERSON_AT_RISK/403/P1 |
| rt_m7_insulin_homophone | ✓ | MEDICATION/301/P1 |
| rt_m7_dialysis_homophone | ✓ | LIFE_SUSTAINING/201/P1 |
| rt_m7_hokkien_kidney | ✓ | LIFE_SUSTAINING/201/P1 |
| rt_m7_hokkien_breath | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt_m7_english_insulin | ✓ | MEDICATION/301/P1 |
| rt_m7_english_dialysis | ✓ | LIFE_SUSTAINING/201/P1 |
| rt_m7_english_oxygen | ✓ | LIFE_SUSTAINING/202/P1 |
| rt_l5_zero_width_space | ✓ | LIFE_SUSTAINING/201/P1 |
| rt_l5_zero_width_joiner | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt_c2_two_children | ✓ | PERSON_AT_RISK/401/P1, PERSON_AT_RISK/401/P1 |
| rt_c2_two_parents | ✓ | MEDICAL_EMERGENCY/101/P1, MEDICAL_EMERGENCY/101/P1 |
| rt_c2_two_dialysis | ✓ | LIFE_SUSTAINING/201/P1, LIFE_SUSTAINING/201/P2 |
| rt_c2_two_beds | ✓ | SUPPLIES/801/P3, SUPPLIES/801/P3 |
| rt_h3_name_before_address | ✓ | PERSON_AT_RISK/401/P1 |
| rt_h5_walk_keeps_bed | ✓ | MOBILITY/503/P2 |
| rt_h5_no_network | ✓ | OTHER/0/P3 |
| rt_l10_number_bed | ✓ | SUPPLIES/801/P2 |
| rt_not_rescuers | ✓ | OTHER/0/P3 |
| rt_not_spill | ✓ | OTHER/0/P3 |
| rt_not_cramp | ✓ | OTHER/0/P3 |
| rt_h4_chen_grandma | ✓ | LIFE_SUSTAINING/201/P1 |
| rt_h4_lin_grandma | ✓ | LIFE_SUSTAINING/201/P1 |
| rt2_internet_not_road | ✓ | COMMUNICATION/1002/P3 |
| rt2_road_still_road | ✓ | INFRASTRUCTURE/1201/P2 |

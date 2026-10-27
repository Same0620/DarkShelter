# Text evaluation (--llm none)

Generated 2026-10-28 07:03 on 258 labelled cases (C:\Users\Tony\Desktop\ASUS_Hackathon\darkshelter\eval\text_cases.jsonl).

| Metric | Value |
|---|---|
| need_precision | 98.9 % |
| need_recall | 98.9 % |
| category_recall | 98.9 % |
| item_accuracy | 100.0 % |
| quantity_accuracy | 100.0 % |
| zone_accuracy | 100.0 % |
| priority_accuracy | 100.0 % |
| emergency_accuracy | 98.8 % |
| p1_recall | 100.0 % |
| p1_precision | 100.0 % |
| dedupe_precision | 100.0 % |
| dedupe_recall | 100.0 % |
| hallucination_rate | n/a |
| latency rules p50 / p95 | 0.12 / 0.21 ms |

Gold P1 needs: 186, predicted P1 needs: 186. Dedupe pool: {'tp': 5, 'fp': 0, 'fn': 0}.

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
| rt2_exag_001 | ✓ | OTHER/0/P3 |
| rt2_exag_002 | ✓ | OTHER/0/P3 |
| rt2_exag_003 | ✓ | OTHER/0/P3 |
| rt2_exag_004 | ✓ | OTHER/0/P3 |
| rt2_exag_005 | ✓ | OTHER/0/P3 |
| rt2_exag_006 | ✓ | OTHER/0/P3 |
| rt2_exag_007 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_008 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_009 | ✓ | OTHER/0/P3 |
| rt2_exag_010 | ✓ | OTHER/0/P3 |
| rt2_exag_011 | ✓ | OTHER/0/P3 |
| rt2_exag_012 | ✓ | OTHER/0/P3 |
| rt2_exag_013 | ✓ | OTHER/0/P3 |
| rt2_exag_014 | ✓ | OTHER/0/P3 |
| rt2_exag_015 | ✓ | OTHER/0/P3 |
| rt2_exag_016 | ✓ | OTHER/0/P3 |
| rt2_exag_017 | ✓ | OTHER/0/P3 |
| rt2_exag_018 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_019 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_020 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_exag_021 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_exag_022 | ✓ | OTHER/0/P3 |
| rt2_exag_023 | ✓ | OTHER/0/P3 |
| rt2_exag_024 | ✓ | OTHER/0/P3 |
| rt2_exag_025 | ✓ | OTHER/0/P3 |
| rt2_exag_026 | ✓ | OTHER/0/P3 |
| rt2_exag_027 | ✓ | OTHER/0/P3 |
| rt2_exag_028 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_029 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_030 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_031 | ✓ | OTHER/0/P3 |
| rt2_exag_032 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_exag_033 | ✓ | OTHER/0/P3 |
| rt2_exag_034 | ✓ | OTHER/0/P3 |
| rt2_exag_035 | ✓ | OTHER/0/P3 |
| rt2_exag_036 | ✓ | OTHER/0/P3 |
| rt2_exag_037 | ✓ | OTHER/0/P3 |
| rt2_exag_038 | ✓ | OTHER/0/P3 |
| rt2_exag_039 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_exag_040 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_exag_041 | ✓ | OTHER/0/P3 |
| rt2_exag_042 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_043 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_044 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_045 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_046 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_047 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_048 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_049 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_050 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_051 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_052 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_053 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_054 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_055 | ✓ | MEDICAL_EMERGENCY/105/P1 |
| rt2_miss_056 | ✓ | MEDICAL_EMERGENCY/105/P1 |
| rt2_miss_057 | ✓ | MEDICAL_EMERGENCY/105/P1 |
| rt2_miss_058 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_059 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_060 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_061 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_062 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_063 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_064 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_065 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_066 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_067 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_068 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_069 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_070 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_071 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_072 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_073 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_074 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_075 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_076 | ✓ | MEDICAL_EMERGENCY/107/P1 |
| rt2_miss_077 | ✓ | MEDICAL_EMERGENCY/107/P1 |
| rt2_miss_078 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_079 | ✓ | MEDICAL_EMERGENCY/103/P1 |
| rt2_miss_080 | ✓ | MEDICAL_EMERGENCY/103/P1 |
| rt2_miss_081 | ✓ | MEDICAL_EMERGENCY/103/P1 |
| rt2_miss_082 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_083 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_084 | ✓ | LIFE_SUSTAINING/203/P1 |
| rt2_miss_085 | ✓ | LIFE_SUSTAINING/203/P1 |
| rt2_miss_086 | ✓ | LIFE_SUSTAINING/203/P1 |
| rt2_miss_087 | ✓ | LIFE_SUSTAINING/203/P1 |
| rt2_miss_088 | ✓ | LIFE_SUSTAINING/203/P1 |
| rt2_miss_089 | ✓ | LIFE_SUSTAINING/201/P1, MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_090 | ✓ | LIFE_SUSTAINING/201/P1, MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_091 | ✓ | OTHER/0/P3 |
| rt2_miss_092 | ✓ | OTHER/0/P3 |
| rt2_miss_093 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_094 | ✓ | OTHER/0/P3 |
| rt2_miss_095 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_096 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_097 | ✓ | MEDICAL_EMERGENCY/106/P1 |
| rt2_miss_098 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_099 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_100 | ✓ | PERSON_AT_RISK/401/P1 |
| rt2_miss_101 | ✓ | PERSON_AT_RISK/401/P1 |
| rt2_miss_102 | ✓ | PERSON_AT_RISK/401/P1 |
| rt2_miss_103 | ✓ | PERSON_AT_RISK/401/P1 |
| rt2_miss_104 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_105 | ✓ | MEDICAL_EMERGENCY/104/P1 |
| rt2_miss_106 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_107 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_108 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_109 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_110 | ✓ | MEDICAL_EMERGENCY/0/P1, MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_111 | ✓ | MEDICAL_EMERGENCY/101/P1 |
| rt2_miss_112 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_113 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_114 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_115 | ✓ | MEDICAL_EMERGENCY/102/P1 |
| rt2_miss_116 | ✓ | MEDICAL_EMERGENCY/0/P1 |
| rt2_miss_117 | ✓ | MEDICAL_EMERGENCY/105/P1 |
| rt2_prot_118 | ✓ | PROTECTION/1301/P2 |
| rt2_prot_119 | ✓ | PROTECTION/1301/P2, PERSON_AT_RISK/401/P1 |
| rt2_prot_120 | ✓ | PROTECTION/1301/P2, PERSON_AT_RISK/401/P1 |
| rt2_prot_121 | ✓ | OTHER/0/P3 |
| rt2_prot_122 | ✓ | OTHER/0/P3 |
| rt2_prot_123 | ✓ | PROTECTION/1301/P2 |
| rt2_prot_124 | ✓ | PROTECTION/1301/P2 |
| rt2_prot_125 | ✓ | PROTECTION/1301/P2 |
| asr_b_zone_dialysis_insulin | ✓ | LIFE_SUSTAINING/201/P1, MEDICATION/301/P1 |
| asr_b_zone_repeat | ✓ | LIFE_SUSTAINING/201/P1 |
| asr_lane_rescue | ✓ | PERSON_AT_RISK/401/P1 |
| asr_chest_pain_soundalike | ✓ | MEDICAL_EMERGENCY/101/P1 |
| asr_not_zone_project | ✓ | OTHER/0/P3 |
| asr_not_zone_shelter_area | ✓ | OTHER/0/P3 |

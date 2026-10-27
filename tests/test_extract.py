"""Rules extraction + triage (SPEC 11 'Extraction and triage')."""
import time
import unittest

from darkshelter import codebook, extract

H = 3600.0


def at(h, m=0, d=28, mo=10, y=2026):
    return time.mktime((y, mo, d, h, m, 0, 0, 0, -1))


NOW = at(10)
CAT = {c["key"]: c["code"] for c in codebook.categories()}


def analyze(text, now=NOW):
    return extract.analyze(text, now)


def p1(needs):
    return [n for n in needs if n.priority == 1]


def describe(needs):
    return [(codebook.cat(n.category)["key"], n.item_code, n.priority, n.emergency, n.flags) for n in needs]


class MustBeP1(unittest.TestCase):
    def assertP1(self, text, cat=None, item=None, now=NOW, emergency=None):
        needs = analyze(text, now)
        hits = [n for n in p1(needs) if (cat is None or n.category == CAT[cat]) and
                (item is None or n.item_code == item)]
        self.assertTrue(hits, f"{text!r}: expected P1 {cat}/{item}, got {describe(needs)}")
        if emergency is not None:
            self.assertIn(emergency, hits[0].emergency or "", f"{text!r}: emergency {hits[0].emergency}")
        return hits[0]

    def test_dialysis_two_days(self):
        n = self.assertP1("阿公已經兩天沒洗腎了", "LIFE_SUSTAINING", 201)
        self.assertEqual(n.deadline_at, NOW)          # ran-out rule: now, not +48 h
        self.assertEqual(n.subject_group, "elderly")

    def test_insulin_gone(self):
        self.assertEqual(self.assertP1("胰島素沒了", "MEDICATION", 301).deadline_at, NOW)

    def test_insulin_left_behind(self):
        self.assertEqual(self.assertP1("胰島素沒帶出來", "MEDICATION", 301).deadline_at, NOW)

    def test_baby_formula_gone(self):
        n = self.assertP1("寶寶沒奶粉了", "INFANT_CHILD", 601)
        self.assertEqual(n.subject_group, "infant")

    def test_not_breathing(self):
        self.assertP1("他沒有呼吸了", "MEDICAL_EMERGENCY", 107, emergency="medical")

    def test_doubt_forms_keep_p1(self):
        n = self.assertP1("會不會是中風", "MEDICAL_EMERGENCY", 106, emergency="medical")
        self.assertIn("確認：現在有中風嗎？", n.ask_back)
        n = self.assertP1("不知道是不是胸痛", "MEDICAL_EMERGENCY", 101, emergency="medical")
        self.assertIn("確認：現在有胸痛嗎？", n.ask_back)
        self.assertP1("是不是中風了", "MEDICAL_EMERGENCY", 106)

    def test_past_then_present(self):
        self.assertP1("之前都好好的現在突然胸痛", "MEDICAL_EMERGENCY", 101)
        self.assertP1("之前都好好的，現在突然胸痛", "MEDICAL_EMERGENCY", 101)

    def test_oxygen_battery(self):
        n = self.assertP1("製氧機電池還有兩小時", "LIFE_SUSTAINING", 203)
        self.assertEqual(n.deadline_at, NOW + 2 * H)

    def test_lost_child(self):
        self.assertP1("我的小孩不見了", "PERSON_AT_RISK", 403, emergency="rescue")
        self.assertP1("小孩走失了", "PERSON_AT_RISK", 403, emergency="rescue")
        self.assertP1("這個小孩沒有大人陪", "PERSON_AT_RISK", 406, emergency="rescue")

    def test_wandering_elder(self):
        self.assertP1("我阿公失智走失了", "PERSON_AT_RISK", 404, emergency="rescue")
        self.assertP1("阿嬤不見了", "PERSON_AT_RISK", 404, emergency="rescue")
        self.assertP1("阿嬤找不到了", "PERSON_AT_RISK", 404, emergency="rescue")
        self.assertP1("阿公走失了", "PERSON_AT_RISK", 404)

    def test_proxy_rescue_with_banner(self):
        n = self.assertP1("我爸還在中正路三巷的家裡沒出來", "PERSON_AT_RISK", 401, emergency="rescue")
        self.assertEqual(n.zone_code, codebook.ZONE_EXTERNAL)
        self.assertEqual(n.location, "中正路三巷")

    def test_dialysis_after_other_medicine(self):
        needs = analyze("降血壓藥星期一才用完，另外阿嬤要洗腎")
        dialysis = [n for n in needs if n.item_code == 201]
        self.assertEqual(len(dialysis), 1)
        self.assertEqual(dialysis[0].priority, 1)
        bp = [n for n in needs if n.item_code == 306]
        self.assertEqual(len(bp), 1)
        self.assertNotEqual(bp[0].deadline_at, NOW)   # 才用完 is not 'ran out'

    def test_dialysis_tomorrow_morning_at_7(self):
        n = self.assertP1("阿嬤明天早上要洗腎", "LIFE_SUSTAINING", 201, now=at(7))
        self.assertEqual(n.deadline_at, at(8, d=29))   # 25 h <= 36 h

    def test_night_rule_through_analyze(self):
        n = self.assertP1("阿嬤明天早上要洗腎", "LIFE_SUSTAINING", 201, now=at(2))
        self.assertEqual(n.deadline_at, at(8))

    def test_other_red_flags(self):
        self.assertP1("她懷孕了，快生了", "MEDICAL_EMERGENCY", 103)
        self.assertP1("我真的不想活了", "MENTAL_HEALTH", 902, emergency="suicide")
        self.assertP1("製氧機沒電了", "LIFE_SUSTAINING", 203)
        self.assertP1("養氣快沒了", "LIFE_SUSTAINING", 202)
        self.assertP1("我爸聯絡不上", "PERSON_AT_RISK", 402, emergency="rescue")
        self.assertP1("他胸悶", "MEDICAL_EMERGENCY", 101)
        self.assertP1("我很喘", "MEDICAL_EMERGENCY", 101)
        self.assertP1("他叫不醒", "MEDICAL_EMERGENCY", 104)

    def test_simplified_and_mishearings(self):
        self.assertP1("我阿嬷明天早上要洗肾", "LIFE_SUSTAINING", 201)
        self.assertP1("胰岛素没了", "MEDICATION", 301)
        self.assertP1("一島素沒了", "MEDICATION", 301)
        self.assertP1("阿公的遺島素沒了", "MEDICATION", 301)
        self.assertP1("阿公要洗身", "LIFE_SUSTAINING", 201)
        self.assertP1("制氧机没电了", "LIFE_SUSTAINING", 203)

    def test_downplaying(self):
        n = self.assertP1("沒什麼啦，胰島素昨天就用完了", "MEDICATION", 301)
        self.assertEqual(n.deadline_at, NOW)

    def test_ran_out_from_adjacent_clause(self):
        self.assertP1("寶寶要喝奶粉，已經沒了", "INFANT_CHILD", 601)

    def test_insulin_soon(self):
        n = self.assertP1("胰島素快沒了", "MEDICATION", 301)
        self.assertEqual(n.deadline_at, NOW + 12 * H)
        self.assertEqual(analyze("胰島素還有三天")[0].priority, 2)


class MustNotBeP1(unittest.TestCase):
    def assertNoP1(self, text, now=NOW):
        needs = analyze(text, now)
        self.assertFalse(p1(needs), f"{text!r}: unexpected P1 {describe(needs)}")
        self.assertFalse(any(n.emergency for n in needs if n.category != CAT["PROTECTION"]), describe(needs))
        return needs

    def test_denied_chest_pain_gets_ask_back(self):
        needs = self.assertNoP1("我沒有胸痛，只是有點累")
        self.assertEqual(len(needs), 1)
        self.assertEqual(needs[0].category, 0)
        self.assertEqual(needs[0].priority, 3)
        self.assertIn("確認：現在有胸痛嗎？", needs[0].ask_back)
        self.assertIn("negated", needs[0].flags)
        self.assertEqual(needs[0].suppressed, ["sym_chest_pain"])

    def test_denied_simplified(self):
        needs = self.assertNoP1("我没有胸痛")
        self.assertIn("確認：現在有胸痛嗎？", needs[0].ask_back)

    def test_past_stroke_is_p2(self):
        needs = self.assertNoP1("上次中風過，現在要拿降血壓藥")
        bp = [n for n in needs if n.item_code == 306]
        self.assertEqual(len(bp), 1)
        self.assertEqual(bp[0].priority, 2)
        self.assertIn("確認：現在有中風嗎？", bp[0].ask_back)

    def test_more_negations(self):
        self.assertNoP1("我沒有呼吸困難，只是咳嗽")
        self.assertNoP1("她沒有抽搐")
        self.assertNoP1("我沒有昏倒，只是頭暈")
        self.assertNoP1("以前中風過")
        self.assertNoP1("他不會喘")   # 「不會」 is not followed by a keyword here: no hit at all

    def test_bujianle_needs_a_subject(self):
        self.assertNoP1("我的錢包不見了")

    def test_injection_is_not_protection(self):
        needs = analyze("小孩被打針會哭")
        self.assertFalse(any(n.category == CAT["PROTECTION"] for n in needs))


class OtherCases(unittest.TestCase):
    def test_head_injury_is_medical_not_protection(self):
        needs = analyze("被東西打到頭，流很多血")
        self.assertTrue(any(n.category == CAT["MEDICAL_EMERGENCY"] and n.priority == 1 for n in needs))
        self.assertFalse(any(n.category == CAT["PROTECTION"] for n in needs))

    def test_address_vs_bed(self):
        n = analyze("我家在中正路28號，還有人受困")[0]
        self.assertEqual(n.zone_code, codebook.ZONE_EXTERNAL)
        self.assertEqual(n.location, "中正路28號")
        n = analyze("B區12號的阿公需要輪椅")[0]
        self.assertEqual(n.zone_code, codebook.zone_code("B", 12))
        self.assertEqual(n.item_code, 501)

    def test_fewshot_one_two_p1s(self):
        needs = analyze("我阿嬤在B區12床，她明天早上要洗腎，然後她的胰島素今天就用完了。", at(7))
        self.assertEqual([(n.item_code, n.priority) for n in needs], [(201, 1), (301, 1)])
        for n in needs:
            self.assertEqual(n.subject, "阿嬤")
            self.assertEqual(n.zone_code, codebook.zone_code("B", 12))
        self.assertEqual(needs[0].deadline_at, at(8, d=29))
        self.assertEqual(needs[1].deadline_at, at(7))

    def test_fewshot_two(self):
        needs = analyze("我們家四個人需要毛毯，還有我兒子手機沒電了，想聯絡他爸爸。")
        self.assertEqual([(codebook.cat(n.category)["key"], n.item_code) for n in needs],
                         [("SUPPLIES", 801), ("COMMUNICATION", 1001)])
        self.assertEqual(needs[0].quantity, 4)
        self.assertEqual(needs[0].subject, "")       # 兒子 belongs to the phone need
        self.assertEqual(needs[1].subject, "兒子")
        self.assertEqual([n.priority for n in needs], [3, 3])

    def test_multiple_quantities(self):
        needs = analyze("兩包奶粉和三包尿布")
        self.assertEqual([(n.item_code, n.quantity) for n in needs], [(601, 2), (602, 3)])

    def test_approx_quantity(self):
        n = analyze("十幾個人需要便當")[0]
        self.assertEqual((n.item_code, n.quantity, n.quantity_approx), (702, 15, True))
        self.assertIn("approx", n.flags)

    def test_charger_is_p3(self):
        needs = analyze("我手機沒電了，需要充電器")
        self.assertEqual([(n.item_code, n.priority) for n in needs], [(1001, 3)])

    def test_vague_medicine_asks_back(self):
        n = analyze("我需要藥")[0]
        self.assertEqual((n.category, n.item_code), (CAT["MEDICATION"], 310))
        self.assertIn("請問是哪一種藥？", n.missing_info)

    def test_sensitive_flag(self):
        n = analyze("我需要愛滋的藥")[0]
        self.assertEqual(n.category, CAT["MEDICATION"])
        self.assertIn("sensitive", n.flags)
        n = analyze("我有在吃PrEP，胰島素也沒了")
        self.assertTrue(all("sensitive" in x.flags for x in n if x.category == CAT["MEDICATION"]))

    def test_protection(self):
        n = analyze("我先生家暴，我不敢回家")[0]
        self.assertEqual((n.category, n.priority, n.emergency), (CAT["PROTECTION"], 2, "protection"))
        self.assertEqual(analyze("他打我")[0].category, CAT["PROTECTION"])

    def test_id_scrubbed(self):
        needs = analyze("我的身分證是A123456789，需要毛毯")
        self.assertEqual(needs[0].item_code, 801)
        blob = repr([n.to_dict() for n in needs])
        self.assertNotIn("123456789", blob)
        _, clauses = extract._clauses("我的身分證是A123456789，需要毛毯", NOW)
        self.assertNotIn("123456789", "".join(c.text for c in clauses))

    def test_whisper_colon_is_a_clause_break(self):
        # real Whisper output: a full-width colon as a pause
        needs = analyze("阿嬤明天早上要洗腎：他的遺導素今天也用完了。")
        dialysis = [n for n in needs if n.item_code == 201][0]
        insulin = [n for n in needs if n.item_code == 301][0]
        self.assertEqual(dialysis.deadline_at, at(8, d=29))   # next 08:00, not 'now'
        self.assertEqual(dialysis.priority, 1)
        self.assertEqual((insulin.priority, insulin.deadline_at), (1, NOW))
        self.assertEqual(insulin.subject, "阿嬤")
        n = analyze("阿嬤明天早上要洗腎:胰島素今天也用完了")
        self.assertEqual([x.deadline_at for x in n], [at(8, d=29), NOW])

    def test_whisper_ascii_comma(self):
        needs = analyze("我沒有胸痛,只是有點累。")
        self.assertEqual([(n.category, n.priority) for n in needs], [(0, 3)])
        self.assertIn("確認：現在有胸痛嗎？", needs[0].ask_back)

    def test_enumeration_comma_keeps_ran_out(self):
        needs = analyze("寶寶的奶粉、尿布都沒了")
        self.assertEqual([(n.item_code, n.priority) for n in needs], [(601, 1), (602, 2)])
        self.assertEqual(needs[0].deadline_at, NOW)

    def test_time_words_do_not_leak_into_another_needs_clause(self):
        needs = analyze("明天早上要毛毯，阿嬤要洗腎")
        dialysis = [n for n in needs if n.item_code == 201][0]
        self.assertIsNone(dialysis.deadline_at)
        self.assertEqual(dialysis.priority, 1)

    def test_time_from_adjacent_clause_without_a_need(self):
        n = analyze("阿嬤要洗腎，明天早上", at(10))[0]
        self.assertEqual(n.deadline_at, at(8, d=29))

    def test_story_400_chars(self):
        story = ("我們家住在山上，颱風來的那天晚上雨下得很大，我們一家人本來想說再等一下看看，"
                 "後來村長來敲門說要撤離，大家就趕快收東西，鄰居的阿伯也一起坐車下來，"
                 "路上很多樹倒下來，車子開得很慢，到了學校已經是半夜了，小朋友都很累，"
                 "我太太一直在擔心家裡的狗，因為來不及帶出來，我自己是覺得房子應該還好，"
                 "只是屋頂可能會漏水，另外我媽媽有高血壓，她的降血壓藥只剩兩天，"
                 "然後我爸爸剛剛說他胸口很悶，喘不過氣，我們有點擔心，他以前沒有這樣過，"
                 "還有我們需要毛毯，晚上這裡很冷，小朋友一直說冷，也需要一些吃的東西，"
                 "如果有熱水更好，因為我小兒子要泡奶粉，奶粉我們有帶一罐，應該還夠，"
                 "我們家總共六個人，住在B區8床，謝謝你們，真的很辛苦，大家都很好心，"
                 "我們也會幫忙打掃，有需要做什麼可以跟我們說，再麻煩你們了，真的很感謝。"
                 "對了，我們的車子停在學校門口，不知道會不會擋到路，如果會的話我再去移，"
                 "還有我太太的手機快沒電了，她想跟娘家報平安，不好意思一次講這麼多。")
        self.assertGreaterEqual(len(story), 380)
        analyze(story)  # warm caches
        t0 = time.perf_counter()
        needs = analyze(story)
        ms = (time.perf_counter() - t0) * 1000
        self.assertLessEqual(len(needs), 5)
        self.assertTrue(any(n.priority == 1 and n.category == CAT["MEDICAL_EMERGENCY"] for n in needs),
                        describe(needs))
        self.assertLess(ms, 50.0, f"{ms:.1f} ms")

    def test_no_hit_gives_one_other_need(self):
        needs = analyze("你好，請問這裡是哪裡")
        self.assertEqual(len(needs), 1)
        self.assertEqual((needs[0].category, needs[0].priority), (0, 3))

    def test_empty_text(self):
        self.assertEqual(analyze(""), [])

    def test_need_contract(self):
        n = analyze("阿嬤要洗腎")[0]
        d = n.to_dict()
        for k in ("category", "item", "item_code", "quantity", "quantity_approx", "subject", "subject_group",
                  "location", "zone_code", "time_phrase", "deadline_at", "deadline_text", "emergency",
                  "evidence", "field_sources", "flags", "red_flags", "suppressed", "ask_back", "priority",
                  "priority_reasons", "rule_priority", "missing_info", "summary", "rules_index",
                  "dup_candidates"):
            self.assertIn(k, d)
        self.assertEqual(n.rules_index, 0)
        self.assertEqual(n.rule_priority, n.priority)
        self.assertEqual(n.field_sources["item"], "rules")
        self.assertEqual(n.evidence["item"], "洗腎")
        self.assertTrue(n.summary)

    def test_deadline_text_is_absolute(self):
        n = analyze("胰島素沒了")[0]
        self.assertEqual(n.deadline_text, time.strftime("%m/%d %H:%M", time.localtime(NOW)))

    def test_family_tracing_adult_is_p2(self):
        n = analyze("有沒有人可以幫忙找家人")[0]
        self.assertEqual((n.item_code, n.priority), (405, 2))

    def test_categories_covered(self):
        samples = {"OTHER": "你好", "FOOD_WATER": "我們需要五箱飲用水", "PET": "我的狗需要飼料",
                   "INFRASTRUCTURE": "中正路那邊坍方了", "MENTAL_HEALTH": "我晚上一直睡不著很害怕",
                   "COMMUNICATION": "他聽不懂國語，需要翻譯", "MOBILITY": "阿公需要輪椅"}
        for key, text in samples.items():
            self.assertEqual(analyze(text)[0].category, CAT[key], text)


class RedTeamRules(unittest.TestCase):
    """REDTEAM.md C1, C2, H3, H5, M7, L5, L10: the rules path on its own (FAST mode skips the LLM)."""

    C1 = ["救命", "心臟病發作", "他不能呼吸", "呼吸好像停了", "阿公沒在呼吸", "有人割腕", "小孩一直抽筋", "羊水破了",
          "阿公跌倒頭在流血", "大出血", "頭破血流", "溺水", "觸電", "吐血", "休克", "不省人事", "意識不清", "嘴唇發紫",
          "過敏喉嚨腫起來", "一氧化碳", "中暑", "小孩發高燒", "有人倒在地上", "他倒在地上", "李大同家中正路三巷有人受傷",
          "B區3床的阿伯有肺結核在咳血", "叫救護車", "快叫119", "需要救援", "我的孩子迷路了",
          # Simplified (Whisper sometimes writes it)
          "心脏病发作", "没在呼吸", "头破血流", "触电", "意识不清", "嘴唇发紫", "发高烧", "叫救护车", "喉咙肿起来",
          # M7: Hokkien, English, homophones
          "依島素用完了", "阿嬤要喜腎", "阮阿公欲洗腰子", "喘袂過來", "我需要insulin", "INSULIN沒了", "Dialysis",
          "阿公需要Oxygen",
          # L5: invisible characters inside the keyword
          "阿嬤要洗​腎", "救‍命", "胰⁠島素沒了"]

    def test_c1_emergencies_are_p1_without_the_llm(self):
        for text in self.C1:
            self.assertTrue(p1(analyze(text)), f"{text}: {describe(analyze(text))}")

    def test_self_harm_is_suicide_p1(self):
        n = analyze("有人割腕")[0]
        self.assertEqual((n.priority, n.category, n.item_code, n.emergency), (1, CAT["MENTAL_HEALTH"], 902, "suicide"))

    def test_everyday_words_stay_quiet(self):
        for text in ["搜救人員已經到了", "水倒在地上了", "我腳抽筋了", "我們一路上都很辛苦", "我迷路了", "東西倒在地上"]:
            self.assertFalse(p1(analyze(text)), f"{text}: {describe(analyze(text))}")

    def test_help_next_to_an_emergency_is_one_need(self):
        needs = analyze("救命，我爸被困在家裡")
        self.assertEqual([(n.item_code, n.priority) for n in needs], [(401, 1)])
        self.assertIn("generic_emergency", needs[0].red_flags)   # the coverage invariant still sees it
        self.assertEqual(needs[0].emergency, "rescue")

    def test_c2_two_people_same_item_are_two_needs(self):
        needs = analyze("我兒子被困在中正路的家裡，我女兒被困在和平國小")
        self.assertEqual([(n.item_code, n.priority, n.subject) for n in needs], [(401, 1, "兒子"), (401, 1, "女兒")])
        self.assertEqual(needs[0].location, "中正路")
        self.assertEqual((needs[1].zone_code, needs[1].location), (0, ""))   # the son's address is not hers
        needs = analyze("爸爸胸口很痛，媽媽喘不過氣")
        self.assertEqual([(n.item_code, n.subject) for n in needs], [(101, "爸爸"), (101, "媽媽")])
        needs = analyze("阿公明天要洗腎，阿嬤後天也要洗腎")
        self.assertEqual([(n.subject, n.priority) for n in needs], [("阿公", 1), ("阿嬤", 2)])
        self.assertEqual(needs[0].deadline_at, at(12, d=29))
        needs = analyze("B區12床要毛毯，C區3床也要毛毯")
        self.assertEqual([n.zone_code for n in needs], [codebook.zone_code("B", 12), codebook.zone_code("C", 3)])

    def test_c2_same_person_still_one_need(self):
        needs = analyze("阿嬤明天要洗腎，她今天沒洗腎")
        self.assertEqual(len(needs), 1)
        needs = analyze("阿公的藥沒了，阿嬤的胰島素也沒了")
        self.assertEqual(sorted(n.item_code for n in needs), [301, 310])   # both people kept

    def test_h3_names_never_in_the_address(self):
        for text, want in [("王小明中正路三巷五號受困需要救援", "中正路三巷五號"), ("我叫陳志明中山路五號需要救援", "中山路五號"),
                           ("李大同家中正路三巷有人受困", "中正路三巷"), ("陳美麗中正路三巷受困", "中正路三巷"),
                           ("中山北路有人受困", "中山北路"), ("民族一路123號有人受困", "民族一路123號"),
                           ("我爸還在中正路三巷的家裡沒出來", "中正路三巷")]:
            n = analyze(text)[0]
            self.assertEqual((n.zone_code, n.location), (codebook.ZONE_EXTERNAL, want), text)

    def test_h5_words_with_road_are_not_addresses(self):
        n = analyze("我在B區12床，阿嬤不能走路")[0]
        self.assertEqual((n.zone_code, n.location), (codebook.zone_code("B", 12), "B區12床"))
        for text in ["我的孩子迷路了", "這裡沒有網路", "外面的馬路淹水", "道路中斷了", "我們一路上都很辛苦", "我想去逛街"]:
            self.assertNotEqual(codebook.parse_location(text)[0], codebook.ZONE_EXTERNAL, text)

    def test_l10_number_hao_bed(self):
        self.assertEqual(codebook.parse_location("三號床的阿嬤")[:2], (codebook.zone_code(0, 3), "三號床"))
        self.assertEqual(codebook.parse_location("3號床")[0], codebook.zone_code(0, 3))
        self.assertEqual(codebook.parse_location("中正路28號")[:2], (codebook.ZONE_EXTERNAL, "中正路28號"))

    def test_llm_pronoun_subject_does_not_replace_the_person(self):
        text = "我兒子被困在中正路的家裡，我女兒被困在和平國小"
        rules = analyze(text)
        llm = [{"category": "PERSON_AT_RISK", "item": "受困", "quantity": None, "subject": "我", "subject_group": "unknown",
                "location": "中正路", "time_phrase": None, "summary": None},
               {"category": "PERSON_AT_RISK", "item": "受困", "quantity": None, "subject": "我", "subject_group": "unknown",
                "location": None, "time_phrase": None, "summary": None}]
        out = extract.refine(rules, llm, text, NOW)
        self.assertEqual([(n.priority, n.subject) for n in out], [(1, "兒子"), (1, "女兒")])

    def test_h4_family_qualifier_kept_in_subject(self):
        self.assertEqual(analyze("陳家的阿嬤明天要洗腎")[0].subject, "陳家的阿嬤")
        self.assertEqual(analyze("我家阿嬤明天要洗腎")[0].subject, "阿嬤")
        self.assertEqual(extract.subject_key("爸爸"), extract.subject_key("我爸"))
        self.assertNotEqual(extract.subject_key("陳家的阿嬤"), extract.subject_key("林家的阿嬤"))


class RedTeamRound2(unittest.TestCase):
    """Round 2: everyday exaggeration asks back but keeps P1; family violence; emergencies that were missed."""
    ASK = "是真的緊急狀況"

    def asks(self, needs):
        return [a for n in needs for a in n.ask_back]

    def test_exaggeration_keeps_p1_and_asks_back(self):
        for text in ("救命喔，排隊排好久", "救命啊這也太難吃了", "救命，好無聊", "我快中暑了", "熱到快中暑",
                     "累到快昏倒了", "我快不能呼吸了，太好笑了", "這裡好擠快窒息了"):
            needs = analyze(text)
            self.assertTrue(p1(needs), f"{text}: urgency must not be dropped, got {describe(needs)}")
            self.assertTrue(any(self.ASK in a for a in self.asks(needs)), f"{text}: no ask-back {self.asks(needs)}")

    def test_idioms_are_not_p1(self):
        for text in ("熱死了", "累死了", "笑死", "笑死我了", "餓死了", "嚇死我了", "氣死我了", "冷死了", "煩死了",
                     "擠死了", "心臟快跳出來了", "這麼多人，快被擠死了"):
            self.assertFalse(p1(analyze(text)), f"{text}: {describe(analyze(text))}")

    def test_real_emergency_with_help_is_not_softened(self):
        # 救命 next to a complaint stays fully active when a real emergency is said too
        needs = analyze("救命，我爸被困在家裡，等好久都沒人來")
        self.assertTrue(p1(needs))
        self.assertFalse(any(self.ASK in a for a in self.asks(needs)), describe(needs))
        # a bare 救命 is taken at its word
        needs = analyze("救命喔")
        self.assertTrue(p1(needs))
        self.assertFalse(any(self.ASK in a for a in self.asks(needs)))

    def test_family_violence_is_protection(self):
        for text in ("我先生打我", "我被老公打", "我男友打我", "我爸打我", "我被我先生打"):
            cats = [n.category for n in analyze(text)]
            self.assertIn(codebook.PROTECTION, cats, f"{text}: {describe(analyze(text))}")
        for text in ("老公打我電話", "我老公打我手機都沒人接"):
            cats = [n.category for n in analyze(text)]
            self.assertNotIn(codebook.PROTECTION, cats, f"{text}: {describe(analyze(text))}")

    def test_violence_plus_rescue_gives_both(self):
        needs = analyze("我先生一直打我，我女兒還被困在中正路三巷")
        cats = {n.category: n for n in needs}
        self.assertIn(codebook.PROTECTION, cats, describe(needs))
        rescue = [n for n in needs if n.category == 4]
        self.assertEqual(len(rescue), 1, describe(needs))
        self.assertEqual(rescue[0].priority, 1)
        self.assertEqual(rescue[0].zone_code, codebook.ZONE_EXTERNAL)
        self.assertIn("中正路三巷", rescue[0].location)

    def test_missed_emergencies_now_p1(self):
        for text in ("阿公半邊身體沒力", "阿嬤嘴歪了", "我爸突然講話不清楚", "他臉色發白一直冒冷汗", "腳的血一直流停不下來",
                     "骨頭露出來了", "手被燙傷了很嚴重", "被熱水燙到起水泡", "被蛇咬了", "小孩吞了電池", "孕婦肚子很痛在流血",
                     "我太太懷孕肚子痛流血", "低血糖昏昏的", "小孩喝到漂白水", "他吸毒過量", "過敏全身起疹子喘",
                     "從樓梯摔下來不能動", "脖子不能動", "頭被撞到一直吐", "阿嬤發燒抽筋", "房子倒了有人在裡面",
                     "一直拉肚子吐到虛脫", "半边身体没力", "小孩吞了电池", "孕妇肚子很痛在流血"):
            self.assertTrue(p1(analyze(text)), f"{text}: {describe(analyze(text))}")

    def test_new_phrases_do_not_misfile(self):
        # 熱水 in a scald is not drinking water; 狗 in a bite is not a pet request
        self.assertNotIn(7, [n.category for n in analyze("被熱水燙到起水泡")])
        self.assertNotIn(11, [n.category for n in analyze("被狗咬了")])
        # unclear speech is a stroke sign only when said of a person (or 突然), not of a loudspeaker
        self.assertFalse(p1(analyze("這個廣播講話不清楚")), describe(analyze("這個廣播講話不清楚")))
        # 肚子痛 alone is not a pregnancy emergency
        self.assertFalse(any(n.item_code == 103 for n in analyze("我肚子痛")))


class SoundalikeAskBack(unittest.TestCase):
    """A Whisper sound-alike (兇統 for 胸痛) still makes the P1, and the clerk is asked about the proper word."""

    def test_askback_uses_the_proper_word(self):
        import time as _t
        needs = analyze("不知道是不是兇統", _t.time())
        self.assertEqual([(n.priority, n.category) for n in needs], [(1, 1)])
        asks = " ".join(needs[0].ask_back)
        self.assertIn("確認：現在有胸痛嗎？", asks)
        self.assertNotIn("兇統", asks)


if __name__ == "__main__":
    unittest.main()

"""AI merge invariants (SPEC 11). The fake LLM output is built here, like MockLLM does."""
import time
import unittest

from darkshelter import codebook, extract
from darkshelter.extract import Need, grounded, merge_llm, refine


def at(h, m=0, d=28, mo=10, y=2026):
    return time.mktime((y, mo, d, h, m, 0, 0, 0, -1))


NOW = at(10)


def llm_need(category="OTHER", item=None, quantity=None, subject=None, subject_group="unknown",
             location=None, time_phrase=None, summary=None):
    """A validated LLM need (llm.validate turns '' and 0 into None)."""
    return {"category": category, "item": item, "quantity": quantity, "subject": subject,
            "subject_group": subject_group, "location": location, "time_phrase": time_phrase,
            "summary": summary}


def as_llm(needs, drop_index=None, inject=None):
    """rules needs -> LLM dicts (what MockLLM does), optionally dropping one / injecting one."""
    out = []
    for n in needs:
        out.append(llm_need(codebook.cat(n.category)["key"], n.item or None, n.quantity or None,
                            n.subject or None, n.subject_group, n.location or None, n.time_phrase or None,
                            n.summary or None))
    if drop_index is not None:
        del out[drop_index]
    if inject is not None:
        out.append(inject)
    return out


def run(text, llm, now=NOW):
    rules = extract.analyze(text, now)
    return rules, refine(rules, llm, text, now)


class GroundingTests(unittest.TestCase):
    def test_item_via_lexicon_keywords(self):
        self.assertTrue(grounded("item", "洗腎", "阿公要洗身"))          # mishearing keyword
        self.assertTrue(grounded("item", "胸痛／呼吸困難", "我胸痛"))    # codebook name
        self.assertFalse(grounded("item", "胰島素", "阿嬤需要藥"))

    def test_quantity_via_numerals(self):
        self.assertTrue(grounded("quantity", 4, "我們家四個人需要毛毯"))
        self.assertTrue(grounded("quantity", 12, "要12瓶水"))
        self.assertFalse(grounded("quantity", 5, "我們家四個人需要毛毯"))

    def test_zone_parsed_equality(self):
        self.assertTrue(grounded("location", "B區12號", "阿嬤在B區12床"))
        self.assertFalse(grounded("location", "C區3床", "阿嬤在B區12床"))
        self.assertTrue(grounded("location", "中正路三巷", "我爸還在中正路三巷的家裡沒出來"))

    def test_substring_fields(self):
        self.assertTrue(grounded("subject", "我阿嬤", "我阿嬤明天要洗腎"))
        self.assertTrue(grounded("time_phrase", "明天早上", "阿嬤明天早上要洗腎"))
        self.assertFalse(grounded("time_phrase", "後天", "阿嬤明天早上要洗腎"))
        self.assertFalse(grounded("subject", "", "阿嬤"))


class MergeInvariants(unittest.TestCase):
    def test_round_trip_keeps_rules_result(self):
        text = "我阿嬤在B區12床，她明天早上要洗腎，然後她的胰島素今天就用完了。"
        rules, out = run(text, None)
        rules, out = run(text, as_llm(rules))
        self.assertEqual([(n.item_code, n.priority) for n in out], [(n.item_code, n.priority) for n in rules])
        for n in out:
            self.assertFalse([f for f in n.flags if f.startswith("ungrounded:")], n.flags)
            self.assertEqual(n.rules_index, out.index(n))

    def test_round_trip_one_rescue_alarm(self):
        # the codebook name 失智長輩走失 contains the keyword 走失 (403): still one need, not two alarms
        for text in ("我阿公失智走失了", "阿嬤找不到了", "我的小孩不見了"):
            rules, out = run(text, None)
            rules, out = run(text, as_llm(rules))
            self.assertEqual([(n.item_code, n.priority) for n in out], [(n.item_code, n.priority) for n in rules],
                             text)
        rules, out = run("阿嬤找不到了", [llm_need("PERSON_AT_RISK", "走失兒童")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].item_code, 404)

    def test_ungrounded_quantity_is_flagged(self):
        rules, out = run("阿嬤要毛毯", [llm_need("SUPPLIES", "毛毯", quantity=5)])
        self.assertEqual(out[0].quantity, 5)
        self.assertIn("ungrounded:quantity", out[0].flags)
        self.assertEqual(out[0].field_sources["quantity"], "llm_ungrounded")

    def test_grounded_llm_field_beats_rules(self):
        rules, out = run("我們家四個人需要毛毯", [llm_need("SUPPLIES", "毛毯", quantity=4, subject="我們家")])
        self.assertEqual(out[0].quantity, 4)
        self.assertEqual(out[0].subject, "我們家")
        self.assertEqual(out[0].field_sources["subject"], "llm")
        self.assertFalse([f for f in out[0].flags if f.startswith("ungrounded")])

    def test_ungrounded_field_may_raise_with_flag_and_ask_back(self):
        rules, out = run("阿嬤需要藥", [llm_need("MEDICATION", "胰島素", subject="阿嬤", subject_group="elderly")])
        self.assertEqual(rules[0].priority, 2)
        self.assertEqual(out[0].priority, 1)
        self.assertIn("raised_by_ai_guess", out[0].flags)
        self.assertIn("ungrounded:item", out[0].flags)
        self.assertTrue(any("AI推測" in q for q in out[0].ask_back))

    def test_ungrounded_field_never_lowers(self):
        # dialysis with unknown deadline is P1; an invented far deadline must not lower it
        rules, out = run("阿嬤要洗腎", [llm_need("LIFE_SUSTAINING", "洗腎", time_phrase="下星期五")])
        self.assertEqual(rules[0].priority, 1)
        self.assertEqual(out[0].priority, 1)
        self.assertIn("ungrounded:time_phrase", out[0].flags)

    def test_llm_cannot_change_red_flag_category(self):
        rules, out = run("他胸口很悶", [llm_need("OTHER", "不舒服")])
        self.assertEqual(out[0].category, codebook.cat_by_key("MEDICAL_EMERGENCY")["code"])
        self.assertEqual(out[0].priority, 1)

    def test_red_flag_need_survives_drop(self):
        text = "我阿嬤胸口很悶，還需要毛毯"
        rules = extract.analyze(text, NOW)
        self.assertEqual(rules[0].priority, 1)
        out = refine(rules, as_llm(rules, drop_index=0), text, NOW)
        chest = [n for n in out if n.item_code == 101]
        self.assertEqual(len(chest), 1)
        self.assertEqual(chest[0].priority, 1)
        self.assertEqual(chest[0].emergency, "medical")

    def test_injected_denied_symptom_is_p2_no_banner(self):
        text = "我沒有胸痛"
        rules = extract.analyze(text, NOW)
        inject = llm_need("MEDICAL_EMERGENCY", "胸痛", summary="胸痛")
        out = refine(rules, as_llm(rules, inject=inject), text, NOW)
        self.assertFalse([n for n in out if n.priority == 1])
        self.assertFalse([n for n in out if n.emergency])
        injected = [n for n in out if n.category == 1]
        self.assertEqual(len(injected), 1)
        self.assertEqual(injected[0].priority, 2)
        self.assertIn("negated", injected[0].flags)
        self.assertIn("確認：現在有胸痛嗎？", injected[0].ask_back)

    def test_denied_symptom_only_llm_need(self):
        text = "我沒有胸痛"
        rules, out = run(text, [llm_need("MEDICAL_EMERGENCY", "胸痛")])
        self.assertFalse([n for n in out if n.priority == 1 or n.emergency])

    def test_not_chest_pain_but_tightness_stays_p1(self):
        rules, out = run("不是胸痛，是胸口很悶", [llm_need("MEDICAL_EMERGENCY", "胸口很悶")])
        self.assertTrue(any(n.priority == 1 and n.emergency == "medical" for n in out))

    def test_coverage_creates_unclassified_p1(self):
        out = refine([], [], "他昏倒了", NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].priority, 1)
        self.assertIn("unclassified_red_flag", out[0].flags)
        self.assertEqual(out[0].red_flags, ["sym_faint"])
        self.assertEqual(out[0].emergency, "medical")
        self.assertIsNone(out[0].rules_index)

    def test_coverage_with_partial_rules(self):
        text = "阿嬤要毛毯，阿公昏倒了"
        rules = extract.analyze(text, NOW)
        blanket_only = [n for n in rules if n.item_code == 801]
        out = refine(blanket_only, [], text, NOW)
        self.assertTrue(any("unclassified_red_flag" in n.flags and n.priority == 1 for n in out))

    def test_deadline_min_and_emergency_union(self):
        text = "胰島素明天就沒了"
        rules, out = run(text, [llm_need("MEDICATION", "胰島素", time_phrase="明天")])
        self.assertEqual(rules[0].deadline_at, NOW)          # ran-out rule
        self.assertEqual(out[0].deadline_at, NOW)            # min(rules now, LLM tomorrow)
        # emergency union with a synthetic rules need
        rn = Need(category=1, item_code=101, item="胸痛／呼吸困難", emergency="rescue", priority=1,
                  rule_priority=1, rules_index=0)
        out = refine([rn], [llm_need("MEDICAL_EMERGENCY", "胸痛")], "我胸痛", NOW)
        self.assertEqual(set(out[0].emergency.split(",")), {"rescue", "medical"})

    def test_ai_only_need_kept_and_flagged(self):
        rules, out = run("需要毛毯，還有老花眼鏡", [llm_need("SUPPLIES", "毛毯"), llm_need("OTHER", "老花眼鏡")])
        extra = [n for n in out if n.rules_index is None]
        self.assertEqual(len(extra), 1)
        self.assertIn("ai_only", extra[0].flags)
        self.assertEqual(extra[0].field_sources["item"], "llm")

    def test_merged_llm_need_cannot_steal_other_needs_fields(self):
        # the real qwen2.5:1.5b merged two needs and put time words in location
        text = "阿嬤明天早上要洗腎，然後她的胰島素今天就用完了"
        rules = extract.analyze(text, at(7))
        llm = [llm_need("LIFE_SUSTAINING", "洗腎", subject="阿嬤", subject_group="elderly",
                        location="明天早上", time_phrase="今天就用完了", summary="阿嬤洗腎")]
        out = refine(rules, llm, text, at(7))
        dialysis = [n for n in out if n.item_code == 201][0]
        insulin = [n for n in out if n.item_code == 301][0]
        self.assertEqual(dialysis.deadline_at, at(8, d=29))
        self.assertEqual(dialysis.time_phrase, "明天早上")
        self.assertEqual((dialysis.location, dialysis.zone_code), ("", 0))
        self.assertFalse([f for f in dialysis.flags if f.startswith("ungrounded")])
        r_ins = [n for n in rules if n.item_code == 301][0]
        self.assertEqual((insulin.priority, insulin.deadline_at), (r_ins.priority, r_ins.deadline_at))

    def test_duration_is_not_a_quantity(self):
        # real qwen: 「剩兩天」 / 「兩天沒洗腎」 -> quantity 2
        self.assertFalse(grounded("quantity", 2, "阿公的降血壓藥剩兩天"))
        self.assertTrue(grounded("quantity", 2, "阿公要兩包尿布"))
        for text, cat, item in (("阿公的降血壓藥剩兩天", "MEDICATION", "降血壓藥"),
                                ("阿公已經兩天沒洗腎了", "LIFE_SUSTAINING", "洗腎"),
                                ("E區1床需要毛毯", "SUPPLIES", "毛毯")):
            rules, out = run(text, [llm_need(cat, item, quantity=2 if "兩" in text else 1)])
            self.assertIsNone(out[0].quantity, text)
            self.assertNotIn("ungrounded:quantity", out[0].flags, text)

    def test_evidence_free_ai_only_needs_are_dropped(self):
        # real qwen added an OTHER need (no item) and a PERSON_AT_RISK need (no item) next to a real P1
        rules, out = run("不是胸痛，是胸口很悶",
                         [llm_need("MEDICAL_EMERGENCY", "胸口很悶"), llm_need("OTHER", None, subject="他")])
        self.assertFalse([n for n in out if n.rules_index is None])
        rules, out = run("我爸心臟不舒服",
                         [llm_need("MEDICAL_EMERGENCY", "心臟不舒服"), llm_need("PERSON_AT_RISK", None, subject="我爸")])
        self.assertEqual([(n.item_code, n.priority) for n in out], [(101, 1)])
        self.assertFalse([n for n in out if n.category == 4])
        # exactly what qwen2.5:1.5b returned: the person in 'item'
        rules, out = run("我爸心臟不舒服", [llm_need("PERSON_AT_RISK", "我爸", subject="我爸", summary="我爸心臟不舒服")])
        self.assertEqual([(n.item_code, n.priority, n.emergency) for n in out], [(101, 1, "medical")])
        rules, out = run("我爸聯絡不上", [llm_need("COMMUNICATION", "我爸", subject="我", summary="我父親聯絡不上")])
        self.assertEqual([(n.item_code, n.priority) for n in out], [(402, 1)])
        self.assertFalse(grounded("item", "我爸", "我爸聯絡不上"))
        self.assertTrue(grounded("item", "老花眼鏡", "需要老花眼鏡"))

    def test_denied_symptom_with_unsupported_urgent_category(self):
        # real qwen: 「我沒有昏倒，只是頭暈」 -> PERSON_AT_RISK, item 頭暈
        rules, out = run("我沒有昏倒，只是頭暈", [llm_need("PERSON_AT_RISK", "頭暈")])
        self.assertFalse([n for n in out if n.priority == 1 or n.emergency])
        self.assertTrue(any("確認：現在有昏倒嗎？" in n.ask_back for n in out))

    def test_grounded_unmapped_item_does_not_ground_category(self):
        rules, out = run("他頭很暈", [llm_need("MEDICAL_EMERGENCY", "頭很暈")])
        self.assertIn("ungrounded:category", out[0].flags)
        self.assertIn("raised_by_ai_guess", out[0].flags)   # may raise, flagged, with an ask-back
        self.assertTrue(any("AI推測" in q for q in out[0].ask_back))

    def test_none_fields_accepted(self):
        rules, out = run("阿嬤要洗腎", [llm_need("LIFE_SUSTAINING", None)])
        self.assertEqual(out[0].item_code, 201)
        out = merge_llm(rules, [{"category": None, "item": None}], "阿嬤要洗腎", NOW)
        self.assertEqual(len(out), 1)

    def test_duplicate_llm_need_dropped(self):
        rules, out = run("胰島素沒了", [llm_need("MEDICATION", "胰島素"), llm_need("MEDICATION", "胰島素")])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

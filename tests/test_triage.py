import time
import unittest

from darkshelter import triage
from darkshelter.extract import Need
from darkshelter.triage import assign_priority, missing_info, parse_deadline

H = 3600.0


def at(h, m=0, d=28, mo=10, y=2026):
    return time.mktime((y, mo, d, h, m, 0, 0, 0, -1))


def weekday_date(target_wd):
    """A date in late Oct 2026 whose weekday (Mon=0) is target_wd."""
    for d in range(20, 32):
        if time.localtime(at(12, d=d)).tm_wday == target_wd:
            return d
    raise AssertionError


class DeadlineTests(unittest.TestCase):
    def test_night_rule_tomorrow_morning(self):
        dl, txt = parse_deadline("明天早上要洗腎", at(2))
        self.assertEqual(dl, at(8))                       # 02:00 -> today 08:00
        self.assertEqual(txt, time.strftime("%m/%d %H:%M", time.localtime(at(8))))
        self.assertEqual(parse_deadline("明早", at(5, 59))[0], at(8))
        self.assertEqual(parse_deadline("明天早上", at(7))[0], at(8, d=29))   # 07:00 -> tomorrow

    def test_tomorrow_noon_and_night_rule(self):
        self.assertEqual(parse_deadline("明天", at(10))[0], at(12, d=29))
        self.assertEqual(parse_deadline("明天中午", at(1))[0], at(12))      # same night rule

    def test_today(self):
        self.assertEqual(parse_deadline("今天", at(10))[0], at(23))
        self.assertEqual(parse_deadline("今晚", at(22, 30))[0], at(22, 30) + 2 * H)  # at least now + 2 h

    def test_now_words(self):
        for w in ("現在", "馬上", "立刻", "现在", "马上"):
            self.assertEqual(parse_deadline(f"{w}就要", at(10))[0], at(10), w)

    def test_soon(self):
        for w in ("快沒了", "快用完", "剩一點", "快没了", "剩一点"):
            self.assertEqual(parse_deadline(f"胰島素{w}", at(10))[0], at(10) + 12 * H, w)

    def test_hours_and_battery(self):
        self.assertEqual(parse_deadline("電池還有兩小時", at(10))[0], at(12))
        self.assertEqual(parse_deadline("還有2個小時", at(10))[0], at(12))
        self.assertEqual(parse_deadline("十二小時", at(10))[0], at(22))
        self.assertEqual(parse_deadline("两小时", at(10))[0], at(12))
        self.assertEqual(parse_deadline("30分鐘", at(10))[0], at(10, 30))

    def test_days(self):
        self.assertEqual(parse_deadline("後天", at(10))[0], at(10) + 48 * H)
        self.assertEqual(parse_deadline("后天", at(10))[0], at(10) + 48 * H)
        self.assertEqual(parse_deadline("藥還可以撐三天", at(10))[0], at(10) + 72 * H)
        self.assertEqual(parse_deadline("兩個禮拜", at(10))[0], at(10) + 14 * 24 * H)

    def test_elapsed_time_is_not_a_deadline(self):
        for s in ("三天前就沒了", "兩小時前", "昨天", "前天", "已經兩天沒洗腎了", "兩天沒洗腎"):
            self.assertIsNone(parse_deadline(s, at(10))[0], s)

    def test_weekday_same_day(self):
        tue = weekday_date(1)
        self.assertEqual(parse_deadline("星期二", at(7, d=tue))[0], at(8, d=tue))    # before 08:00 -> today 08:00
        self.assertEqual(parse_deadline("星期二", at(10, d=tue))[0], at(10, d=tue))  # past -> now
        self.assertEqual(parse_deadline("禮拜二", at(7, d=tue))[0], at(8, d=tue))

    def test_next_week(self):
        tue = weekday_date(1)
        self.assertEqual(parse_deadline("下星期二", at(7, d=tue))[0], at(8, d=tue) + 7 * 24 * H)

    def test_next_such_weekday(self):
        wed = weekday_date(2)
        self.assertEqual(parse_deadline("星期五", at(10, d=wed))[0], at(8, d=wed + 2))
        self.assertEqual(parse_deadline("週一", at(10, d=wed))[0], at(8, d=wed + 5))
        self.assertEqual(parse_deadline("礼拜五", at(10, d=wed))[0], at(8, d=wed + 2))

    def test_once_a_week_is_not_a_weekday(self):
        self.assertIsNone(parse_deadline("一週一次", at(10))[0])

    def test_minimum_over_matches(self):
        self.assertEqual(parse_deadline("明天或今天", at(10))[0], at(23))

    def test_ran_out(self):
        self.assertTrue(triage.ran_out("胰島素沒帶出來"))
        self.assertTrue(triage.ran_out("已經兩天沒洗腎了"))
        self.assertTrue(triage.ran_out("寶寶沒奶粉了"))
        self.assertFalse(triage.ran_out("胰島素快沒了"))
        self.assertFalse(triage.ran_out("製氧機電池還有兩小時"))
        self.assertFalse(triage.ran_out("降血壓藥星期一才用完"))
        self.assertFalse(triage.ran_out("需要毛毯"))


def need(**kw):
    return Need(**kw)


class PriorityTests(unittest.TestCase):
    now = at(10)

    def p(self, **kw):
        return assign_priority(need(**kw), self.now)

    def test_red_flag(self):
        pr, reasons, em = self.p(category=0, red_flags=["sym_chest_pain"])
        self.assertEqual((pr, em), (1, "medical"))
        self.assertEqual(reasons[0]["code"], "red_flag")

    def test_medical_emergency(self):
        self.assertEqual(self.p(category=1)[0::2], (1, "medical"))

    def test_person_at_risk(self):
        self.assertEqual(self.p(category=4, item_code=401)[0::2], (1, "rescue"))
        self.assertEqual(self.p(category=4, item_code=405, subject_group="adult")[0], 2)
        self.assertEqual(self.p(category=4, item_code=405, subject_group="child")[0], 1)
        self.assertEqual(self.p(category=4, item_code=405, red_flags=["rescue_missing"])[0], 1)

    def test_life_sustaining_36h(self):
        pr, reasons, _ = self.p(category=2, item_code=201, deadline_at=self.now + 25 * H)
        self.assertEqual(pr, 1)
        self.assertEqual(reasons[0], {"code": "life_sustaining_36h", "zh": "維生醫療36小時內",
                                      "en": "Life-sustaining care within 36 h"})
        self.assertEqual(self.p(category=2, item_code=201, deadline_at=self.now + 37 * H)[0], 2)
        self.assertEqual(self.p(category=2, item_code=201)[0], 1)    # unknown -> P1

    def test_critical_medicine(self):
        self.assertEqual(self.p(category=3, item_code=301)[0], 1)    # unknown
        self.assertEqual(self.p(category=3, item_code=301, deadline_at=self.now + 12 * H)[0], 1)
        self.assertEqual(self.p(category=3, item_code=302, deadline_at=self.now + 13 * H)[0], 2)

    def test_other_medicine(self):
        self.assertEqual(self.p(category=3, item_code=306)[0], 2)
        self.assertEqual(self.p(category=3, item_code=306, deadline_at=self.now + 47 * H)[0], 2)
        self.assertEqual(self.p(category=3, item_code=306, deadline_at=self.now + 49 * H)[0], 3)
        self.assertEqual(self.p(category=3, item_code=308, deadline_at=self.now + 99 * H)[0], 2)

    def test_infant(self):
        self.assertEqual(self.p(category=6, item_code=601, deadline_at=self.now)[0], 1)
        self.assertEqual(self.p(category=6, item_code=601, deadline_at=self.now + 7 * H)[0], 2)
        self.assertEqual(self.p(category=6, item_code=602, deadline_at=self.now)[0], 2)
        self.assertEqual(self.p(category=7, item_code=701, subject_group="infant", deadline_at=self.now)[0], 1)

    def test_mental_health(self):
        self.assertEqual(self.p(category=9, item_code=902, red_flags=["suicide"])[0::2], (1, "suicide"))
        self.assertEqual(self.p(category=9, item_code=901)[0::2], (2, None))

    def test_protection(self):
        self.assertEqual(self.p(category=13, item_code=1301)[0::2], (2, "protection"))

    def test_p2_categories(self):
        self.assertEqual(self.p(category=5, item_code=501)[0], 2)
        self.assertEqual(self.p(category=12, item_code=1203)[0], 2)

    def test_food_supplies(self):
        self.assertEqual(self.p(category=8, item_code=801)[0], 3)
        self.assertEqual(self.p(category=8, item_code=801, subject_group="elderly")[0], 2)
        self.assertEqual(self.p(category=7, item_code=703)[0], 2)
        self.assertEqual(self.p(category=7, item_code=702, subject_group="pregnant")[0], 2)

    def test_communication_pet_other(self):
        self.assertEqual(self.p(category=10, item_code=1001)[0], 3)
        self.assertEqual(self.p(category=10, item_code=1003)[0], 2)
        self.assertEqual(self.p(category=10, item_code=1004)[0], 2)
        self.assertEqual(self.p(category=11, item_code=1101)[0], 3)
        self.assertEqual(self.p(category=0)[0], 3)

    def test_reason_shape(self):
        for kw in ({"category": 3, "item_code": 301}, {"category": 8}, {"category": 4, "item_code": 403}):
            _, reasons, _ = self.p(**kw)
            for r in reasons:
                self.assertEqual(set(r), {"code", "zh", "en"})

    def test_accepts_dicts(self):
        self.assertEqual(assign_priority({"category": 2, "item_code": 201}, self.now)[0], 1)


class MissingInfoTests(unittest.TestCase):
    def test_medication(self):
        q = missing_info(need(category=3, item_code=310))
        self.assertEqual(q, ["請問是哪一種藥？", "藥什麼時候會用完？", "是誰需要？在哪一床？"])
        self.assertEqual(missing_info(need(category=3, item_code=301, deadline_at=1.0, zone_code=524)), [])

    def test_life_sustaining(self):
        self.assertIn("下次洗腎是什麼時候？", missing_info(need(category=2, item_code=201)))
        self.assertIn("氧氣／機器電池還能撐多久？", missing_info(need(category=2, item_code=203)))

    def test_person_at_risk(self):
        q = missing_info(need(category=4, item_code=401))
        self.assertIn("人在哪裡？（地址或地標）", q)
        self.assertIn("有幾個人？", q)
        self.assertIn("有人受傷嗎？", q)
        self.assertIn("孩子／長輩的外觀特徵？", missing_info(need(category=4, item_code=403)))

    def test_infant_supplies_and_bed(self):
        self.assertIn("寶寶幾個月大？", missing_info(need(category=6, item_code=601)))
        self.assertIn("需要幾份？", missing_info(need(category=8, item_code=801)))
        self.assertIn("請問在哪一區、幾號床？", missing_info(need(category=5, item_code=501)))
        self.assertNotIn("請問在哪一區、幾號床？", missing_info(need(category=5, item_code=501, zone_code=524)))
        self.assertEqual(missing_info(need(category=13, item_code=1301)), [])


if __name__ == "__main__":
    unittest.main()

import time
import unittest

from darkshelter import codebook
from darkshelter.dedupe import find_candidates

NOW = time.mktime((2026, 10, 28, 10, 0, 0, 0, 0, -1))
B12 = codebook.zone_code("B", 12)
C5 = codebook.zone_code("C", 5)
EXT = codebook.ZONE_EXTERNAL


def t(seq, category=2, item_code=201, zone_code=0, location="", subject="", summary="", age_h=0.0,
      status="draft", household_id=None):
    return {"seq": seq, "category": category, "item_code": item_code, "zone_code": zone_code,
            "location": location, "subject": subject, "summary": summary,
            "created_wall": NOW - age_h * 3600, "status": status, "household_id": household_id}


class DedupeTests(unittest.TestCase):
    def test_same_bed_same_item_is_candidate(self):
        out = find_candidates(t(2, zone_code=B12), [t(1, zone_code=B12)], NOW)
        self.assertEqual([(c["seq"], c["kind"]) for c in out], [(1, "suggest")])
        self.assertIn("same_zone", out[0]["reason"])

    def test_different_item_is_not(self):
        self.assertEqual(find_candidates(t(2, zone_code=B12), [t(1, category=3, item_code=301, zone_code=B12)],
                                         NOW), [])

    def test_empty_zone_and_empty_subject_never_same(self):
        a = t(2, zone_code=0, subject="", summary="洗腎")
        b = t(1, zone_code=0, subject="", summary="輪椅 C區")
        self.assertEqual(find_candidates(a, [b], NOW), [])

    def test_external_needs_equal_location_text(self):
        a = t(2, category=4, item_code=401, zone_code=EXT, location="中正路三巷")
        self.assertTrue(find_candidates(a, [t(1, category=4, item_code=401, zone_code=EXT,
                                              location="中正路三巷")], NOW))
        self.assertEqual(find_candidates(a, [t(1, category=4, item_code=401, zone_code=EXT,
                                               location="中山路")], NOW), [])

    def test_same_subject(self):
        # supplies: the same subject is enough
        out = find_candidates(t(2, category=8, item_code=801, subject="阿嬤"),
                              [t(1, category=8, item_code=801, subject="阿嬤", zone_code=C5)], NOW)
        self.assertEqual(out[0]["reason"], "same_subject")
        # person-specific: only within the same household (or at the same place)
        out = find_candidates(t(2, subject="阿嬤", household_id=7), [t(1, subject="阿嬤", zone_code=C5, household_id=7)], NOW)
        self.assertEqual(out[0]["reason"], "same_subject")

    def test_similar_summary(self):
        out = find_candidates(t(2, summary="阿嬤 B區12床 洗腎 明天早上", household_id=3),
                              [t(1, summary="阿嬤 B區12床 洗腎", household_id=3)], NOW)
        self.assertIn("similar_summary", out[0]["reason"])

    # red-team H4: one wrong merge tap loses a dialysis patient
    def test_two_families_grandmas_are_not_candidates(self):
        a = t(2, subject="陳家的阿嬤", summary="陳家的阿嬤 洗腎 明天")
        b = t(1, subject="林家的阿嬤", summary="林家的阿嬤 洗腎 明天")
        self.assertEqual(find_candidates(a, [b], NOW), [])

    def test_different_subjects_same_address_are_not_candidates(self):
        a = t(2, category=4, item_code=401, zone_code=EXT, location="中正路三巷", subject="我爸")
        b = t(1, category=4, item_code=401, zone_code=EXT, location="中正路三巷", subject="我媽")
        self.assertEqual(find_candidates(a, [b], NOW), [])
        # the same kinship word said two ways is the same subject
        c = t(3, category=4, item_code=401, zone_code=EXT, location="中正路三巷", subject="爸爸")
        self.assertTrue(find_candidates(c, [a], NOW))

    def test_person_specific_needs_place_or_household(self):
        a = t(2, subject="阿嬤", summary="阿嬤 洗腎 明天")
        b = t(1, subject="阿嬤", summary="阿嬤 洗腎 明天")
        self.assertEqual(find_candidates(a, [b], NOW), [])          # two shelters' 阿嬤
        a["household_id"] = b["household_id"] = 4
        self.assertTrue(find_candidates(a, [b], NOW))

    def test_person_specific_suggest_supplies_add(self):
        out = find_candidates(t(2, category=8, item_code=801, zone_code=B12),
                              [t(1, category=8, item_code=801, zone_code=B12)], NOW)
        self.assertEqual(out[0]["kind"], "add")
        out = find_candidates(t(2, zone_code=B12), [t(1, zone_code=B12)], NOW)
        self.assertEqual(out[0]["kind"], "suggest")

    def test_protection_never(self):
        a = t(2, category=13, item_code=1301, zone_code=B12, subject="我")
        self.assertEqual(find_candidates(a, [t(1, category=13, item_code=1301, zone_code=B12, subject="我")], NOW), [])

    def test_within_12_hours(self):
        self.assertTrue(find_candidates(t(2, zone_code=B12), [t(1, zone_code=B12, age_h=11.9)], NOW))
        self.assertEqual(find_candidates(t(2, zone_code=B12), [t(1, zone_code=B12, age_h=12.5)], NOW), [])

    def test_closed_and_self_skipped(self):
        self.assertEqual(find_candidates(t(1, zone_code=B12), [t(1, zone_code=B12)], NOW), [])
        self.assertEqual(find_candidates(t(2, zone_code=B12), [t(1, zone_code=B12, status="resolved")], NOW), [])

    def test_new_need_without_seq(self):
        a = t(None, zone_code=B12)
        a["created_wall"] = None
        self.assertEqual([c["seq"] for c in find_candidates(a, [t(1, zone_code=B12)], NOW)], [1])

    def test_category_match_when_item_zero(self):
        a = t(2, category=0, item_code=0, zone_code=B12)
        self.assertTrue(find_candidates(a, [t(1, category=0, item_code=0, zone_code=B12)], NOW))
        self.assertEqual(find_candidates(a, [t(1, category=5, item_code=0, zone_code=B12)], NOW), [])


if __name__ == "__main__":
    unittest.main()

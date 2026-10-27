import unittest

from darkshelter import textnorm
from darkshelter.textnorm import bigram_jaccard, normalize, parse_cn_number, scrub_ids


class NumeralTests(unittest.TestCase):
    def test_spec_examples(self):
        self.assertEqual(parse_cn_number("兩"), (2, False))
        self.assertEqual(parse_cn_number("十二"), (12, False))
        self.assertEqual(parse_cn_number("二十五"), (25, False))
        self.assertEqual(parse_cn_number("一百零五"), (105, False))
        self.assertEqual(parse_cn_number("十幾"), (15, True))
        self.assertEqual(parse_cn_number("幾個"), (None, True))

    def test_more_forms(self):
        self.assertEqual(parse_cn_number("12"), (12, False))
        self.assertEqual(parse_cn_number("12個"), (12, False))
        self.assertEqual(parse_cn_number("四個"), (4, False))
        self.assertEqual(parse_cn_number("两"), (2, False))
        self.assertEqual(parse_cn_number("十"), (10, False))
        self.assertEqual(parse_cn_number("一百五"), (150, False))
        self.assertEqual(parse_cn_number("兩千三"), (2300, False))
        self.assertEqual(parse_cn_number("一萬"), (10000, False))
        self.assertEqual(parse_cn_number("3千"), (3000, False))
        self.assertEqual(parse_cn_number("二十幾"), (25, True))
        self.assertEqual(parse_cn_number("兩三"), (3, True))
        self.assertEqual(parse_cn_number(""), (None, False))
        self.assertEqual(parse_cn_number("毛毯"), (None, False))


class ScrubTests(unittest.TestCase):
    def test_national_id_scrubbed(self):
        self.assertEqual(scrub_ids("我的身分證是A123456789"), "我的身分證是＊＊＊")
        self.assertNotIn("123456789", scrub_ids("證號 F223456789，需要毛毯"))
        self.assertNotIn("812345678", scrub_ids("居留證 a812345678"))

    def test_phone_stays_at_desk(self):
        s = scrub_ids("電話0912-345-678")
        self.assertIn("0912-345-678", s)

    def test_not_part_of_longer_token(self):
        self.assertEqual(scrub_ids("AB1234567890"), "AB1234567890")

    def test_strip_contact_numbers(self):
        s = textnorm.strip_contact_numbers("中正路28號 0912345678 A123456789")
        self.assertNotIn("0912345678", s)
        self.assertNotIn("123456789", s)
        self.assertIn("28號", s)


class NormalizeTests(unittest.TestCase):
    def test_full_width_and_punctuation(self):
        self.assertEqual(normalize("Ｂ區１２床,需要毛毯!"), "B區12床，需要毛毯！")

    def test_whitespace_around_cjk(self):
        self.assertEqual(normalize("我 阿嬤  在 B區 12床"), "我阿嬤在B區12床")

    def test_simplified_input(self):
        out = normalize("我阿嬷明天早上要洗肾")
        if textnorm.has_opencc():
            self.assertIn("洗腎", out)
        else:
            self.assertIn("洗肾", out)

    def test_traditional_village_kept(self):
        # 里 must not become 裡 for Traditional input (no Simplified-only characters)
        self.assertEqual(normalize("光復里"), "光復里")

    def test_decimal_point_kept(self):
        self.assertIn("2.5", normalize("還有2.5小時"))


class JaccardTests(unittest.TestCase):
    def test_identical_and_disjoint(self):
        self.assertEqual(bigram_jaccard("阿嬤要洗腎", "阿嬤要洗腎"), 1.0)
        self.assertEqual(bigram_jaccard("毛毯", "輪椅"), 0.0)
        self.assertEqual(bigram_jaccard("", "輪椅"), 0.0)

    def test_similar(self):
        self.assertGreaterEqual(bigram_jaccard("阿嬤 B區12床 洗腎", "阿嬤 B區12床 洗腎 明天早上"), 0.5)


class RedTeamTextTests(unittest.TestCase):
    """REDTEAM.md L5 (invisible characters) and M1 (contact numbers on the wire)."""

    def test_format_characters_removed(self):
        self.assertEqual(normalize("阿嬤要洗​腎"), "阿嬤要洗腎")
        self.assertEqual(normalize("救‍命﻿"), "救命")
        self.assertEqual(textnorm.strip_format_chars("a‮b­c"), "abc")

    def test_phone_numbers_in_any_format_are_stripped(self):
        strip = textnorm.strip_contact_numbers
        for raw, want in [("中正路三巷 0912 345 678", "中正路三巷"), ("中正路 02-2345-6789", "中正路"),
                          ("+886-912-345-678", ""), ("中正路 0912.345.678", "中正路"),
                          ("(02)2345-6789 中正路", "中正路"), ("中正路零九一二三四五六七八", "中正路"),
                          ("中正路０９１２３４５６７８", "中正路"), ("0912​345678", "")]:
            self.assertEqual(strip(raw).strip(), want, raw)

    def test_ids_with_spaces_are_stripped(self):
        strip = textnorm.strip_contact_numbers
        for raw in ["A 123 456 789", "a123456789", "A-123-456-789", "Ａ１２３４５６７８９"]:
            self.assertEqual(strip(raw).strip(), "", raw)
        self.assertEqual(scrub_ids("身分證A 123 456 789"), "身分證＊＊＊")

    def test_places_dates_and_counts_survive(self):
        strip = textnorm.strip_contact_numbers
        for keep in ["中正路三巷5號", "B區12床 10/28 08:00", "中正路100巷200號", "約20份", "飲用水 10000 10/28 08:00"]:
            self.assertEqual(strip(keep), keep)


if __name__ == "__main__":
    unittest.main()

"""Shared codebook (desk and EOC): categories, items, subject groups, zone codes.

Zone codes (tickets, households and the wire):
  zone_code = (letter << 8) | min(bed, 255)
  letters A-Z -> 1-26; 甲乙丙丁東西南北 -> 27-34
  0 = none; 65535 = external location (the address travels in the summary)
"""
import json
import re
from functools import lru_cache

from .config import ROOT

ZONE_NONE = 0
ZONE_EXTERNAL = 65535
EXTRA_LETTERS = "甲乙丙丁東西南北"
PROTECTION = 13

CN_DIGITS = "零〇一二兩两三四五六七八九十百"


@lru_cache(maxsize=1)
def load():
    data = json.loads((ROOT / "data" / "codebook.json").read_text(encoding="utf-8"))
    data["_cat_by_code"] = {c["code"]: c for c in data["categories"]}
    data["_cat_by_key"] = {c["key"]: c for c in data["categories"]}
    data["_item_by_code"] = {i["code"]: i for i in data["items"]}
    data["_item_by_zh"] = {i["zh"]: i for i in data["items"]}
    data["_sg_by_key"] = {g["key"]: g for g in data["subject_groups"]}
    data["_sg_by_code"] = {g["code"]: g for g in data["subject_groups"]}
    return data


def categories():
    return load()["categories"]


def cat(code):
    """Category dict by integer code (falls back to OTHER)."""
    cb = load()
    try:
        return cb["_cat_by_code"].get(int(code), cb["_cat_by_code"][0])
    except (TypeError, ValueError):
        return cb["_cat_by_code"][0]


def cat_by_key(key):
    return load()["_cat_by_key"].get(str(key or "").upper())


def cat_code(key):
    c = cat_by_key(key)
    return c["code"] if c else 0


def is_hidden(code):
    return bool(cat(code).get("hidden"))


def is_person_specific(code):
    return bool(cat(code).get("person_specific"))


def items():
    return load()["items"]


def item(code):
    try:
        return load()["_item_by_code"].get(int(code or 0))
    except (TypeError, ValueError):
        return None


def item_by_zh(zh):
    return load()["_item_by_zh"].get(zh)


def subject_group_code(key):
    g = load()["_sg_by_key"].get(key or "unknown")
    return g["code"] if g else 0


def subject_group_key(code):
    g = load()["_sg_by_code"].get(int(code or 0))
    return g["key"] if g else "unknown"


def subject_group_zh(key):
    g = load()["_sg_by_key"].get(key or "unknown")
    return g["zh"] if g else ""


def eoc_status_zh(code):
    for s in load()["eoc_status"]:
        if s["code"] == int(code or 0):
            return s["zh"]
    return ""


# ---------------------------------------------------------------- zone codes

def letter_code(ch):
    """'A'..'Z' -> 1..26, 甲乙丙丁東西南北 -> 27..34, anything else -> 0."""
    if not ch:
        return 0
    ch = ch[0]
    if "A" <= ch.upper() <= "Z":
        return ord(ch.upper()) - ord("A") + 1
    i = EXTRA_LETTERS.find(ch)
    return 27 + i if i >= 0 else 0


def code_letter(n):
    if 1 <= n <= 26:
        return chr(ord("A") + n - 1)
    if 27 <= n <= 34:
        return EXTRA_LETTERS[n - 27]
    return ""


def zone_code(letter, bed):
    lc = letter_code(letter) if isinstance(letter, str) else int(letter or 0)
    bed = max(0, min(int(bed or 0), 255))
    code = (lc << 8) | bed
    return code if code != ZONE_EXTERNAL else ZONE_EXTERNAL - 1


def zone_label(code, location=""):
    """Human label: 'B區12床', 'B區', '12床', the external address, or ''."""
    code = int(code or 0)
    if code == ZONE_NONE:
        return ""
    if code == ZONE_EXTERNAL:
        return location or "外部地點"
    letter, bed = code_letter(code >> 8), code & 0xFF
    if letter and bed:
        return f"{letter}區{bed}床"
    if letter:
        return f"{letter}區"
    return f"{bed}床"


_CN_SMALL = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _small_number(s):
    """Digits or a Chinese numeral below 1000 -> int (None if unparseable)."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_SMALL:
            cur = _CN_SMALL[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur


_ZONE_BED_RE = re.compile(
    r"([A-Za-z" + EXTRA_LETTERS + r"])\s*區\s*(\d{1,3}|[" + CN_DIGITS + r"]{1,4})\s*(床|號|号)")
_LETTER_BED_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*-?\s*(\d{1,3})\s*(?:號|号)?\s*床")
_BED_RE = re.compile(r"(\d{1,3}|[" + CN_DIGITS + r"]{1,4})\s*(?:號|号)?\s*床")   # 12床, 三號床
_ZONE_ONLY_RE = re.compile(r"([A-Za-z" + EXTRA_LETTERS + r"])\s*區")
_ADDR_SUFFIX = "(?:路|街|巷|弄|號|号|村|里|鄉|乡|鎮|镇|部落)"
_ADDR_RE = re.compile(r"[一-鿿0-9]{1,12}" + _ADDR_SUFFIX + r"(?![長长民])")  # 村長/里長/鄉民 are people, not places
# The address starts after the last of these (在/住/到…, and 家/叫/的: 「李大同家中正路」「我叫陳志明中山路」).
_ADDR_BOUNDARY = "在住到於于從从去回是說说家叫的"
# Words that look like address suffixes but are not addresses.
_ADDR_FALSE = ("家裡", "這裡", "那裡", "哪裡", "心裡", "公里", "千里", "號碼", "幾號", "記號", "信號", "訊號",
               "家里", "这里", "那里", "哪里", "心里", "里面")
# 路/街 inside an ordinary word is not an address: 走路, 迷路, 網路, 馬路, 道路, 逛街… (red-team H5).
_ROAD_WORD_FIRST = set("走迷網网趕赶帶带問问出活退馬马道鐵铁電电線线思半繞绕修讓让找認认夜開开上")
_STREET_WORD_FIRST = set("逛上過过掃扫")
_YILU_NEXT = set("上走來来過过平順顺")          # 一路上, 一路走來, 一路平安
_NOT_NAME_BEFORE_YI = set("我你妳他她們们就也都還还這这那的了在是")
# The place part of an address: a road name of 2 characters before 路/街/大道 (3 after a direction or a
# section number: 中山北路, 民族一路), a village name of 2 before 村/里/鄉/鎮 (4 before 部落), the number
# before 巷/弄. Anything in front (a name, 「我叫」, 「李大同家」) never leaves the desk (red-team H3).
_ROAD_ANCHORS = ("大道", "路", "街")
_AREA_ANCHORS = ("部落", "村", "里", "鄉", "乡", "鎮", "镇")
_LANE_ANCHORS = ("巷", "弄")
_ROAD_LEAD = set("東西南北一二三四五六七八九十")
_NOT_PLACE = set("我你妳您他她它們们的了嗎吗呢吧啊個个這这那哪些條条在有要")
_NUM_RE = re.compile(r"[0-9" + CN_DIGITS + r"]+$")


def _clean_address(s):
    cut = max(s.rfind(ch) for ch in _ADDR_BOUNDARY)
    if cut >= 0:
        s = s[cut + 1:]
    return s


def _mask_false_words(text):
    """`text` with the 路/街 of non-address words replaced by NUL (same length, so spans still line up)."""
    chars = list(text)
    for i, ch in enumerate(chars):
        if i == 0:
            continue
        prev = text[i - 1]
        if ch == "路":
            if prev in _ROAD_WORD_FIRST:
                chars[i] = "\x00"
            elif prev == "一" and text[i + 1:i + 2] in _YILU_NEXT and (i < 2 or text[i - 2] in _NOT_NAME_BEFORE_YI
                                                                     or not ("一" <= text[i - 2] <= "鿿")):
                chars[i] = "\x00"
        elif ch == "街" and prev in _STREET_WORD_FIRST:
            chars[i] = "\x00"
    return "".join(chars)


def _first_anchor(seg, anchors):
    best = None
    for a in anchors:
        i = seg.find(a)
        if i > 0 and (best is None or i < best[0]):
            best = (i, a)
    return best


def _place_part(text, masked, s, e):
    """(start, address) for the place part of text[s:e], or None when it holds no real address."""
    seg_m = masked[s:e]
    hit = _first_anchor(seg_m, _ROAD_ANCHORS + _AREA_ANCHORS)
    if hit is not None:
        pos, anchor = hit
        prefix = seg_m[:pos]
        if anchor == "部落":
            keep = 4
        elif anchor in _ROAD_ANCHORS and prefix[-1:] in _ROAD_LEAD:
            keep = 3
        else:
            keep = 2
        name = prefix[-keep:]
        if not name or any(ch in _NOT_PLACE for ch in name) or _NUM_RE.fullmatch(name):
            return None
        start = s + pos - len(name)
        return start, text[start:e]
    hit = _first_anchor(seg_m, _LANE_ANCHORS)
    if hit is not None:
        pos, _anchor = hit
        num = _NUM_RE.search(seg_m[:pos])
        if not num:
            return None
        start = s + pos - len(num.group(0))
        return start, text[start:e]
    return None   # 號 alone is not an address (三號床 is a bed; 中正路28號 has its 路)


def parse_location(text):
    """Parse a zone/bed or an external address from text.

    Returns (zone_code, location_text, span) where span is (start, end) in `text` or None.
    Order: 'X區N床/號' -> 'B12床' -> external address -> 'N床' / 'N號床' -> 'X區'.
    A bare 'N號' counts as a bed only after 'X區' (so 中正路28號 is an address).
    The external address keeps only its place part (road/lane/number/village): a name in front
    of it never becomes location text, and 走路/迷路/網路… are not addresses.
    """
    if not text:
        return ZONE_NONE, "", None
    m = _ZONE_BED_RE.search(text)
    if m:
        bed = _small_number(m.group(2))
        if bed is not None:
            return zone_code(m.group(1), bed), m.group(0), m.span()
    m = _LETTER_BED_RE.search(text)
    if m:
        return zone_code(m.group(1), int(m.group(2))), m.group(0), m.span()
    masked = _mask_false_words(text)
    for m in _ADDR_RE.finditer(masked):
        raw = text[m.start():m.end()]
        if any(f in raw for f in _ADDR_FALSE) and not re.search(r"(路|街|巷|弄|村|鄉|鎮|部落)", raw):
            continue
        addr = _clean_address(raw)
        if len(addr) < 2 or re.fullmatch(r"[0-9" + CN_DIGITS + r"]+(號|号)", addr):
            continue
        found = _place_part(text, masked, m.start() + (len(raw) - len(addr)), m.end())
        if found is None:
            continue
        start, place = found
        if len(place) >= 2:
            return ZONE_EXTERNAL, place, (start, m.end())
    m = _BED_RE.search(text)
    if m:
        bed = _small_number(m.group(1))
        if bed:
            return zone_code(0, bed), m.group(0), m.span()
    m = _ZONE_ONLY_RE.search(text)
    if m:
        return zone_code(m.group(1), 0), m.group(0), m.span()
    return ZONE_NONE, "", None


def parse_zone_input(text):
    """Clerk-typed zone like 'B12', 'B區12床', 'B-12' -> zone code (0 if none)."""
    if not text:
        return ZONE_NONE
    t = str(text).strip()
    m = re.fullmatch(r"([A-Za-z" + EXTRA_LETTERS + r"])\s*區?\s*-?\s*(\d{0,3})\s*(床|號)?", t)
    if m:
        return zone_code(m.group(1), int(m.group(2) or 0))
    code, _, _ = parse_location(t)
    return code

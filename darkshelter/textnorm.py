"""Text normalisation for Mandarin intake text (Traditional and Simplified).

- to_traditional: OpenCC s2twp when importable (HAS_OPENCC), else identity.
  The lexicon carries Simplified forms too, so matching works without OpenCC.
- normalize: to_traditional, full-width -> half-width digits/letters, whitespace
  removed around CJK, punctuation unified to full-width Chinese marks.
"""
import re
import unicodedata

HAS_OPENCC = None
_CC = None
_SIMP_CACHE = {}
# Characters that are valid in Traditional text but that s2t would rewrite (里 -> 裡 breaks
# village names, 台 -> 臺, ...). They never trigger a conversion on their own.
_AMBIGUOUS = set("里台干后面发只余丑卷几谷系表松划冲斗才克制向御致准咸胡须范征症借尽历")


def _opencc():
    global HAS_OPENCC, _CC
    if HAS_OPENCC is None:
        try:
            from opencc import OpenCC  # opencc-python-reimplemented
            _CC = OpenCC("s2twp")
            HAS_OPENCC = True
        except Exception:
            _CC = None
            HAS_OPENCC = False
    return _CC


def has_opencc():
    _opencc()
    return bool(HAS_OPENCC)


def _looks_simplified(s):
    cc = _opencc()
    if cc is None:
        return False
    for ch in s:
        if "一" <= ch <= "鿿" and ch not in _AMBIGUOUS:
            r = _SIMP_CACHE.get(ch)
            if r is None:
                try:
                    r = cc.convert(ch) != ch
                except Exception:
                    r = False
                _SIMP_CACHE[ch] = r
            if r:
                return True
    return False


def to_traditional(s):
    """Simplified -> Traditional (Taiwan phrasing) when OpenCC is available and needed."""
    if not s:
        return s or ""
    if not _looks_simplified(s):
        return s
    try:
        return _CC.convert(s)
    except Exception:
        return s


_PUNCT = {",": "，", ";": "；", "!": "！", "?": "？", ":": "：", "(": "（", ")": "）"}
_CJK = r"　-〿一-鿿＀-￯"


def strip_format_chars(s):
    """Drop invisible format characters (Unicode Cf: zero-width space/joiner, BOM, bidi marks).

    「洗\\u200b腎」 must match 洗腎, and a phone number must not hide behind a zero-width space."""
    if not s:
        return s or ""
    return "".join(ch for ch in str(s) if unicodedata.category(ch) != "Cf")


# Whisper's sound-alike errors for words the desk depends on (heard in the real-model rehearsal): 「B區12床」 came
# back as 避區/必趨十二床 and 「中正路三巷」 as 中正路三項. Each is repaired only where the misheard form can't be a
# real word: a zone letter right before a bed number, a lane right after a road/street/section number.
_BED_AHEAD = r"(?=[0-9一二三四五六七八九十兩]+號?床)"
_ASR_REPAIRS = [
    (re.compile(r"(?:避|必|逼|比|筆|畢|閉|蔽)(?:區|趨|驅|曲)" + _BED_AHEAD), "B區"),
    (re.compile(r"(?:西|吸|希|溪|稀)(?:區|趨|驅|曲)" + _BED_AHEAD), "C區"),
    (re.compile(r"(?:欸|誒|埃)(?:區|趨|驅|曲)" + _BED_AHEAD), "A區"),
    (re.compile(r"(?<=[路街段])([0-9一二三四五六七八九十百兩]+)(?:項|象|向)(?!目)"), r"\1巷"),
]


def repair_asr(s):
    """Fix the misheard zone letters and lanes above (already normalized text)."""
    for pat, rep in _ASR_REPAIRS:
        s = pat.sub(rep, s)
    return s


def normalize(s):
    """Cf removal + to_traditional + NFKC width folding + whitespace/punctuation cleanup + ASR repairs."""
    if not s:
        return ""
    s = to_traditional(strip_format_chars(str(s)))
    s = unicodedata.normalize("NFKC", s)
    s = "".join(_PUNCT.get(ch, ch) for ch in s)
    # a period that is not a decimal point becomes 。
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", "。", s)
    # remove whitespace next to CJK characters, collapse the rest
    s = re.sub(r"\s+(?=[" + _CJK + "])", "", s)
    s = re.sub(r"(?<=[" + _CJK + r"])\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return repair_asr(s)


_DIG = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "兩": 2, "两": 2, "貳": 2, "三": 3, "參": 3,
        "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG = {"萬": 10000, "万": 10000}


def _parse_section(s):
    """Parse a numeral below 10000 (no 萬). Returns int or None."""
    total, cur, last_unit = 0, None, 1
    prev_ascii = False
    for ch in s:
        if ch in _DIG:
            cur = _DIG[ch]
            prev_ascii = False
        elif ch in _UNIT:
            u = _UNIT[ch]
            total += (1 if cur is None else cur) * u
            cur = None
            last_unit = u
            prev_ascii = False
        elif "0" <= ch <= "9":
            cur = cur * 10 + int(ch) if (prev_ascii and cur is not None) else int(ch)
            prev_ascii = True
        else:
            return None
    if cur is not None:
        # 一百五 = 150 (trailing digit after a unit scales down one place)
        if last_unit >= 100 and len(s) >= 2 and s[-2] in _UNIT and cur < 10:
            total += cur * (last_unit // 10)
        else:
            total += cur
    return total


def parse_cn_number(s):
    """Chinese or Arabic numeral -> (int | None, approx).

    兩 -> 2, 十二 -> 12, 二十五 -> 25, 一百零五 -> 105, 十幾 -> (15, True),
    幾個 -> (None, True), 兩三 -> (3, True), 一百多 -> (150, True).
    """
    if s is None:
        return None, False
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = re.sub(r"[個个位人包瓶罐條条件台支份盒箱片顆颗張张雙双套組组次天小時时]+$", "", s)
    if not s:
        return None, False
    if s.isdigit():
        return int(s), False
    approx = False
    if s[0] in "幾几":
        return None, True
    if s[-1] in "幾几多來来":
        approx = True
        body = s[:-1]
        if not body:
            return None, True
        last = body[-1]
        base = parse_cn_number(body)[0]
        if base is None:
            return None, True
        step = _UNIT.get(last, 1)
        if last in _DIG or last.isdigit():
            return base, True
        return base + step // 2, True
    # 兩三 / 三四 / 一兩 : adjacent digits mean "about", take the larger
    if len(s) == 2 and s[0] in _DIG and s[1] in _DIG and _DIG[s[1]] > _DIG[s[0]]:
        return _DIG[s[1]], True
    m = re.fullmatch(r"(.*?)([萬万])(.*)", s)
    if m:
        hi = _parse_section(m.group(1)) if m.group(1) else 1
        lo = _parse_section(m.group(3)) if m.group(3) else 0
        if hi is None or lo is None:
            return None, approx
        return hi * 10000 + lo, approx
    mixed = re.fullmatch(r"(\d+)([十百千])", s)
    if mixed:
        return int(mixed.group(1)) * _UNIT[mixed.group(2)], approx
    v = _parse_section(s)
    return v, approx


# National ID: a letter, then 1/2/8/9 and 8 more digits; spaces or dashes may sit between them
# (「A 123 456 789」), and full-width forms count too.
_ID_RE = re.compile(r"(?<![A-Za-zＡ-Ｚａ-ｚ0-9０-９])[A-Za-zＡ-Ｚａ-ｚ][\s\-]?[1289１２８９](?:[\s\-]?[0-9０-９]){8}(?![0-9０-９])")
PHONE_RE = re.compile(r"09\d{2}-?\d{3}-?\d{3}")
# Digits as Arabic, full-width or Chinese numerals (零九一二…): a run of 7 or more of them is a phone
# number (mobile, +886, (02) landline) even with spaces, dots, dashes or brackets between the groups.
_DIGITS = "0123456789０１２３４５６７８９零〇一二三四五六七八九"
_DIGIT_SET = frozenset(_DIGITS)
_NUM_RUN = re.compile(r"[+＋(（]*[" + _DIGITS + r"](?:[\s\-‐‑–—_.．・･()（）]{0,2}[" + _DIGITS + r"])+[)）]?")


def scrub_ids(s):
    """Replace national ID numbers ([A-Z][1289]\\d{8}, spaced or full-width too) with ＊＊＊. Phones stay (desk only)."""
    if not s:
        return s or ""
    return _ID_RE.sub("＊＊＊", s)


def _drop_long_number(m):
    txt = m.group(0)
    if sum(1 for ch in txt if ch in _DIGIT_SET) < 7:
        return txt
    if m.string[m.end():m.end() + 1] in ("/", ":", "："):
        return txt  # a date or a time follows (「10000 10/28」), not a phone number
    return ""


def strip_contact_numbers(s):
    """Remove phone numbers, ID numbers and any run of 7+ digits (used for wire text).

    Digits may be Arabic, full-width or Chinese numerals, with spaces, dots, dashes or brackets
    between them: 0912 345 678, +886-912-345-678, (02)2345-6789, 零九一二三四五六七八."""
    if not s:
        return ""
    s = strip_format_chars(s)
    s = _ID_RE.sub("", s)
    s = PHONE_RE.sub("", s)
    s = _NUM_RUN.sub(_drop_long_number, s)
    return s


_NON_TEXT = re.compile(r"[\s，。、；：！？,.;:!?（）()「」『』\"'～~…—\-]+")


def _bigrams(s):
    s = _NON_TEXT.sub("", normalize(s or ""))
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def bigram_jaccard(a, b):
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)

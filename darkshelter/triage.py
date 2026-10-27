"""Triage rules (SPEC 6.6): deadlines, priority table, ask-back questions.

Lower number = more urgent. The priority is the minimum over every rule that fires.
Times are local wall-clock (Taiwan has no DST); `now` is an epoch float.
"""
import math
import re
import time

from . import codebook
from .lexicon import get_lexicon
from .textnorm import parse_cn_number

# ------------------------------------------------------------------ helpers

_NUM = r"(\d+|[零〇一二兩两三四五六七八九十百半]+)"


def _tp():
    return get_lexicon().patterns["time_phrases"]


def _alt(words):
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _local_at(now, day_offset, hour, minute=0):
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, hour, minute, 0, 0, 0, -1))


def format_deadline(ts):
    """Absolute local 'MM/DD HH:MM'."""
    if ts is None:
        return ""
    return time.strftime("%m/%d %H:%M", time.localtime(ts))


def _num_value(s):
    s = s.strip()
    if s == "半":
        return 0.5
    v, _approx = parse_cn_number(s)
    return v


_RX = {}


def _regexes():
    if _RX:
        return _RX
    tp = _tp()
    _RX["now"] = re.compile(_alt(tp["now"]))
    _RX["soon"] = re.compile(_alt(tp["soon_12h"]))
    _RX["today"] = re.compile(_alt(tp["today"]))
    _RX["tmr_morning"] = re.compile(_alt(tp["tomorrow_morning"]))
    _RX["tmr"] = re.compile(_alt(tp["tomorrow"]))
    _RX["day_after"] = re.compile(_alt(tp["day_after"]))
    hour_u = _alt(tp["hour_units"])
    _RX["hours"] = re.compile(_NUM + r"\s*(?:" + hour_u + r")")
    _RX["minutes"] = re.compile(_NUM + r"\s*(?:" + _alt(tp["minute_units"]) + r")")
    _RX["days"] = re.compile(_NUM + r"\s*(?:個|个)?\s*天")
    week = _alt(tp["week_prefixes"])
    wd = "".join(tp["weekday_chars"].keys())
    _RX["weekday"] = re.compile(r"(下個|下个|下)?(?:這個|这个|這|这)?(?:" + week + r")([" + wd + r"])")
    _RX["weeks"] = re.compile(_NUM + r"\s*(?:個|个)?\s*(?:" + week + r")(?![" + wd + r"])")
    _RX["elapsed_pre"] = tuple(tp["elapsed_prefixes"])
    _RX["elapsed_suf"] = tuple(tp["elapsed_suffixes"])
    return _RX


def _elapsed(clause, start, end):
    rx = _regexes()
    before = clause[:start]
    if before.endswith(rx["elapsed_pre"]) or any(p in before[-4:] for p in rx["elapsed_pre"]):
        return True
    after = clause[end:]
    return after.startswith(rx["elapsed_suf"])


def deadline_matches(clause, now):
    """Every time phrase in `clause` -> [(deadline_at, phrase, start)]. Non-overlapping."""
    if not clause:
        return []
    rx = _regexes()
    lt = time.localtime(now)
    night = lt.tm_hour < 6
    out, taken = [], []

    def free(s, e):
        return not any(s < te and ts < e for ts, te in taken)

    def add(m, dl):
        s, e = m.span()
        if free(s, e):
            taken.append((s, e))
            out.append((dl, m.group(0), s))

    for m in rx["tmr_morning"].finditer(clause):
        add(m, _local_at(now, 0 if night else 1, 8))
    for m in rx["tmr"].finditer(clause):
        add(m, _local_at(now, 0 if night else 1, 12))
    for m in rx["day_after"].finditer(clause):
        add(m, now + 48 * 3600)
    for m in rx["soon"].finditer(clause):
        add(m, now + 12 * 3600)
    for m in rx["today"].finditer(clause):
        add(m, max(_local_at(now, 0, 23), now + 2 * 3600))
    for m in rx["now"].finditer(clause):
        add(m, now)
    for m in rx["weekday"].finditer(clause):
        s = m.start()
        if s > 0 and re.match(r"[0-9零〇一二兩两三四五六七八九十個个]", clause[s - 1]):
            continue  # 一週一次 = once a week, not a weekday
        target = _tp()["weekday_chars"][m.group(2)]
        delta = (target - lt.tm_wday) % 7
        if delta == 0:
            today8 = _local_at(now, 0, 8)
            dl = today8 if now < today8 else now
        else:
            dl = _local_at(now, delta, 8)
        if m.group(1):
            dl = dl + 7 * 86400
        add(m, dl)
    for m in rx["hours"].finditer(clause):
        if _elapsed(clause, m.start(), m.end()):
            continue
        v = _num_value(m.group(1))
        if v is not None:
            add(m, now + float(v) * 3600)
    for m in rx["minutes"].finditer(clause):
        if _elapsed(clause, m.start(), m.end()):
            continue
        v = _num_value(m.group(1))
        if v is not None:
            add(m, now + float(v) * 60)
    for m in rx["weeks"].finditer(clause):
        if _elapsed(clause, m.start(), m.end()):
            continue
        v = _num_value(m.group(1))
        if v:
            add(m, now + float(v) * 7 * 86400)
    for m in rx["days"].finditer(clause):
        if _elapsed(clause, m.start(), m.end()):
            continue
        v = _num_value(m.group(1))
        if v:
            add(m, now + float(v) * 86400)
    return out


def parse_deadline_ex(clause, now):
    """(deadline_at | None, deadline_text, phrase) — the minimum over all matches."""
    ms = deadline_matches(clause, now)
    if not ms:
        return None, "", ""
    dl, phrase, _ = min(ms, key=lambda x: x[0])
    return dl, format_deadline(dl), phrase


def parse_deadline(clause, now):
    """(deadline_at | None, deadline_text) — SPEC 6.6 table, minimum over matches."""
    dl, text, _ = parse_deadline_ex(clause, now)
    return dl, text


def ran_out(clause):
    """Ran-out rule cue: any of 沒/無/完/光/忘/斷/留在/沒帶 and none of 快/剩/還有 (/才/會用完...)."""
    if not clause:
        return False
    p = get_lexicon().patterns
    if not any(w in clause for w in p["ran_out_words"]):
        return False
    return not any(b in clause for b in p["ran_out_blockers"])


def ran_out_categories():
    return set(get_lexicon().patterns["ran_out_categories"])


# ------------------------------------------------------------------ priority

def R(code, zh, en):
    return {"code": code, "zh": zh, "en": en}


REASONS = {
    "medical_emergency": R("medical_emergency", "緊急醫療", "Medical emergency"),
    "person_at_risk": R("person_at_risk", "人員受困／失聯／走失", "Person at risk"),
    "family_tracing": R("family_tracing", "尋找家人（成人、無危險字詞）", "Family tracing (adult, no danger words)"),
    "life_sustaining_36h": R("life_sustaining_36h", "維生醫療36小時內", "Life-sustaining care within 36 h"),
    "life_sustaining_unknown": R("life_sustaining_unknown", "維生醫療，時間不明", "Life-sustaining care, time unknown"),
    "life_sustaining_later": R("life_sustaining_later", "維生醫療，36小時後", "Life-sustaining care after 36 h"),
    "critical_med_12h": R("critical_med_12h", "關鍵藥品12小時內", "Critical medicine within 12 h"),
    "critical_med_unknown": R("critical_med_unknown", "關鍵藥品，用完時間不明", "Critical medicine, time unknown"),
    "critical_med_later": R("critical_med_later", "關鍵藥品，12小時後", "Critical medicine after 12 h"),
    "med_cold_chain": R("med_cold_chain", "藥品冷藏", "Medicine refrigeration"),
    "medication_48h": R("medication_48h", "藥品48小時內或時間不明", "Medicine within 48 h or time unknown"),
    "medication_later": R("medication_later", "藥品，48小時後", "Medicine after 48 h"),
    "infant_feed_6h": R("infant_feed_6h", "嬰兒奶粉／飲水6小時內", "Infant formula or water within 6 h"),
    "infant_child": R("infant_child", "嬰幼兒需求", "Infant / child need"),
    "suicide": R("suicide", "自殺風險", "Suicide risk"),
    "mental_health": R("mental_health", "心理支持", "Mental-health support"),
    "protection": R("protection", "保護個案", "Protection case"),
    "mobility": R("mobility", "行動協助", "Mobility help"),
    "infrastructure": R("infrastructure", "災情通報", "Infrastructure damage"),
    "special_diet": R("special_diet", "特殊飲食", "Special diet"),
    "vulnerable_subject": R("vulnerable_subject", "長者／嬰兒／孕婦／身障", "Vulnerable person"),
    "food_water": R("food_water", "飲食", "Food and water"),
    "supplies": R("supplies", "一般物資", "General supplies"),
    "comm_access": R("comm_access", "助聽器或手語／翻譯", "Hearing aid or interpreter"),
    "communication": R("communication", "通訊", "Communication"),
    "pet": R("pet", "寵物", "Pet"),
    "other": R("other", "其他", "Other"),
    "negated_symptom": R("negated_symptom", "民眾否認的症狀，請確認", "Denied symptom: please confirm"),
}

VULNERABLE = ("elderly", "infant", "pregnant", "disabled")


def _g(need, key, default=None):
    if isinstance(need, dict):
        v = need.get(key, default)
    else:
        v = getattr(need, key, default)
    return default if v is None else v


def union_emergency(*parts):
    out = []
    for p in parts:
        for x in (p or "").split(","):
            x = x.strip()
            if x and x not in out:
                out.append(x)
    return ",".join(out) or None


def assign_priority(need, now):
    """-> (priority, reasons [{code, zh, en}], emergency | None). Minimum over all firing rules."""
    lex = get_lexicon()
    cat = int(_g(need, "category", 0) or 0)
    item_code = int(_g(need, "item_code", 0) or 0)
    sg = _g(need, "subject_group", "unknown") or "unknown"
    red_flags = list(_g(need, "red_flags", []) or [])
    flags = list(_g(need, "flags", []) or [])
    deadline = _g(need, "deadline_at", None)
    hours = None if deadline is None else (float(deadline) - now) / 3600.0
    fired = []  # (priority, reason, emergency)

    for rid in red_flags:
        e = lex.by_id.get(rid)
        if not e or not e.get("red_flag"):
            continue
        kw = e["keywords"][0]
        ev = _g(need, "evidence", {}) or {}
        quote = ev.get("red_flag") or ev.get("category") or kw
        fired.append((1, R("red_flag", f"危險字詞「{quote}」", f"Red-flag words: {quote}"), e.get("emergency")))

    if cat == 1:
        if "negated" in flags and not red_flags:
            fired.append((2, REASONS["negated_symptom"], None))
        else:
            fired.append((1, REASONS["medical_emergency"], "medical"))
    elif cat == 4:
        if item_code == 405 and not red_flags and sg in ("adult", "unknown"):
            fired.append((2, REASONS["family_tracing"], None))
        else:
            fired.append((1, REASONS["person_at_risk"], "rescue"))
    elif cat == 2:
        if hours is None:
            fired.append((1, REASONS["life_sustaining_unknown"], None))
        elif hours <= 36:
            fired.append((1, REASONS["life_sustaining_36h"], None))
        else:
            fired.append((2, REASONS["life_sustaining_later"], None))
    elif cat == 3:
        it = codebook.item(item_code)
        if it and it.get("critical_med"):
            if hours is None:
                fired.append((1, REASONS["critical_med_unknown"], None))
            elif hours <= 12:
                fired.append((1, REASONS["critical_med_12h"], None))
            else:
                fired.append((2, REASONS["critical_med_later"], None))
        elif item_code == 308:
            fired.append((2, REASONS["med_cold_chain"], None))
        elif hours is None or hours <= 48:
            fired.append((2, REASONS["medication_48h"], None))
        else:
            fired.append((3, REASONS["medication_later"], None))
    elif cat == 6:
        if item_code == 601 and hours is not None and hours <= 6:
            fired.append((1, REASONS["infant_feed_6h"], None))
        else:
            fired.append((2, REASONS["infant_child"], None))
    elif cat == 9:
        suicide = item_code == 902 or any((lex.by_id.get(r) or {}).get("emergency") == "suicide" for r in red_flags)
        if suicide:
            fired.append((1, REASONS["suicide"], "suicide"))
        else:
            fired.append((2, REASONS["mental_health"], None))
    elif cat == 13:
        fired.append((2, REASONS["protection"], "protection"))
    elif cat == 5:
        fired.append((2, REASONS["mobility"], None))
    elif cat == 12:
        fired.append((2, REASONS["infrastructure"], None))
    elif cat in (7, 8):
        if item_code == 701 and sg == "infant" and hours is not None and hours <= 6:
            fired.append((1, REASONS["infant_feed_6h"], None))
        if item_code == 703:
            fired.append((2, REASONS["special_diet"], None))
        elif sg in VULNERABLE:
            fired.append((2, REASONS["vulnerable_subject"], None))
        else:
            fired.append((3, REASONS["food_water"] if cat == 7 else REASONS["supplies"], None))
    elif cat == 10:
        if item_code in (1003, 1004):
            fired.append((2, REASONS["comm_access"], None))
        else:
            fired.append((3, REASONS["communication"], None))
    elif cat == 11:
        fired.append((3, REASONS["pet"], None))
    else:
        fired.append((3, REASONS["other"], None))

    priority = min(p for p, _, _ in fired)
    fired.sort(key=lambda x: x[0])
    reasons, seen = [], set()
    for p, r, _ in fired:
        if r["code"] + r["zh"] not in seen:
            seen.add(r["code"] + r["zh"])
            reasons.append(r)
    emergency = union_emergency(*[e for _, _, e in fired])
    return priority, reasons, emergency


# ------------------------------------------------------------------ ask-back

Q = {
    "med_which": "請問是哪一種藥？",
    "med_when": "藥什麼時候會用完？",
    "med_who": "是誰需要？在哪一床？",
    "dialysis_when": "下次洗腎是什麼時候？",
    "battery": "氧氣／機器電池還能撐多久？",
    "where_person": "人在哪裡？（地址或地標）",
    "how_many": "有幾個人？",
    "injured": "有人受傷嗎？",
    "looks": "孩子／長輩的外觀特徵？",
    "baby_age": "寶寶幾個月大？",
    "portions": "需要幾份？",
    "bed": "請問在哪一區、幾號床？",
}


def missing_info(need):
    """Follow-up questions for the required information that is missing (SPEC 6.6)."""
    cat = int(_g(need, "category", 0) or 0)
    item_code = int(_g(need, "item_code", 0) or 0)
    has_loc = bool(int(_g(need, "zone_code", 0) or 0)) or bool(_g(need, "location", ""))
    has_deadline = _g(need, "deadline_at", None) is not None
    qty = _g(need, "quantity", None)
    sg = _g(need, "subject_group", "unknown") or "unknown"
    out = []
    if cat == 3:
        if item_code in (0, 310):
            out.append(Q["med_which"])
        if not has_deadline:
            out.append(Q["med_when"])
        if not has_loc:
            out.append(Q["med_who"])
    elif cat == 2:
        if not has_deadline:
            out.append(Q["dialysis_when"] if item_code == 201 else Q["battery"])
    elif cat == 4:
        if not has_loc:
            out.append(Q["where_person"])
        if not qty:
            out.append(Q["how_many"])
        if item_code in (0, 401):
            out.append(Q["injured"])
        if item_code in (403, 404, 406):
            out.append(Q["looks"])
    elif cat == 6:
        if sg not in ("child",):
            out.append(Q["baby_age"])
    if cat in (7, 8) and not qty:
        out.append(Q["portions"])
    if codebook.is_person_specific(cat) and cat not in (3, 4, 13) and not has_loc:
        out.append(Q["bed"])
    return out


def hours_left(need, now):
    d = _g(need, "deadline_at", None)
    return None if d is None else (float(d) - now) / 3600.0


def is_finite(x):
    return x is not None and not (isinstance(x, float) and math.isinf(x))

"""Rules extraction (instant draft) and the LLM merge (SPEC 6.5).

analyze(text, now)        -> rules needs, triaged, each with rules_index (what the pipeline calls first)
refine(rules, llm, t, now) -> merge_llm + re-triage + invariants (what the pipeline calls after the LLM)

Invariants kept by refine():
- priority = min(rules-only, merged); deadline_at = min; emergency = union  (the AI only raises urgency)
- a rise caused only by ungrounded LLM fields is flagged raised_by_ai_guess + ask-back
- every non-suppressed red-flag hit belongs to a P1 need (else an OTHER P1 'unclassified_red_flag')
- an LLM need that maps to a symptom the speaker denied becomes P2, flag negated, no emergency
"""
import copy
import dataclasses
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from . import codebook
from . import triage
from .lexicon import GENERIC_ITEMS, get_lexicon
from .textnorm import normalize, parse_cn_number, scrub_ids

MAX_NEEDS = 5
SUMMARY_MAX = 40
# item -> items that make it redundant in the same utterance (charging the phone IS how they
# contact family: 「手機沒電了，想聯絡他爸爸」 is one need, as in the few-shot)
ABSORBED_BY = {1002: (1001,)}


@dataclass
class Need:
    category: int = 0
    item: str = ""
    item_code: int = 0
    quantity: Optional[int] = None
    quantity_approx: bool = False
    subject: str = ""
    subject_group: str = "unknown"
    location: str = ""
    zone_code: int = 0
    time_phrase: str = ""
    deadline_at: Optional[float] = None
    deadline_text: str = ""
    emergency: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    field_sources: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    red_flags: list = field(default_factory=list)
    suppressed: list = field(default_factory=list)
    ask_back: list = field(default_factory=list)
    priority: int = 3
    priority_reasons: list = field(default_factory=list)
    rule_priority: int = 3
    missing_info: list = field(default_factory=list)
    summary: str = ""
    rules_index: Optional[int] = None
    dup_candidates: list = field(default_factory=list)

    def to_dict(self):
        return dataclasses.asdict(self)

    def add_flag(self, f):
        if f not in self.flags:
            self.flags.append(f)

    def add_ask(self, q):
        if q and q not in self.ask_back:
            self.ask_back.append(q)


NEED_FIELDS = {f.name for f in dataclasses.fields(Need)}
FIELD_ZH = {"category": "類別", "item": "物品", "quantity": "數量", "subject": "對象",
            "subject_group": "對象類型", "location": "地點", "time_phrase": "時間"}


# Whisper's sound-alikes are matched as keywords, but the clerk is asked about the proper word.
_SOUNDALIKE_CANON = {"兇統": "胸痛", "兇痛": "胸痛", "凶痛": "胸痛", "凶統": "胸痛", "胸統": "胸痛", "胸通": "胸痛",
                     "凶统": "胸痛", "胸统": "胸痛", "遺島素": "胰島素", "依島素": "胰島素", "喜腎": "洗腎"}


def askback_negated(keyword):
    keyword = _SOUNDALIKE_CANON.get(keyword, keyword)
    return get_lexicon().patterns["askback_negated"].format(keyword=keyword)


def askback_exaggeration(keyword):
    p = get_lexicon().patterns
    return p.get("askback_exaggeration", "確認：是真的緊急狀況嗎？（聽到「{keyword}」）").format(keyword=keyword)


def askback_ai_guess(field_name, value):
    return f"確認：{FIELD_ZH.get(field_name, field_name)}是否為「{value}」？（AI推測）"


# ------------------------------------------------------------------ clauses

# ：/: too — Whisper writes a pause as a colon (「…要洗腎：他的胰島素今天也用完了」).
# Not 、: 「奶粉、尿布都沒了」 must stay one clause so the ran-out word reaches both items.
_PUNCT = re.compile(r"[，,。；;！!？?：:\n\r]+")


def split_clauses(text):
    """Split on ，,。；;！!？?：: and on 還有 / 另外 / 然後 (not 還有+number: 電池還有兩小時)."""
    lex = get_lexicon()
    conns = sorted(lex.patterns["clause_connectives"], key=len, reverse=True)
    keep = re.compile(lex.patterns["connective_keep_before"])
    out = []
    for part in _PUNCT.split(text or ""):
        pieces, i, last = [], 0, 0
        while i < len(part):
            for c in conns:
                if part.startswith(c, i):
                    if keep.match(part[i + len(c):]):
                        i += len(c)
                    else:
                        pieces.append(part[last:i])
                        last = i = i + len(c)
                    break
            else:
                i += 1
        pieces.append(part[last:])
        out.extend(pieces)
    return [c for c in (x.strip() for x in out) if c]


_QTY_RE = re.compile(
    r"(?<!第)(\d+|[零〇一二兩两三四五六七八九十百千萬万幾几]+(?:多|來|来)?)\s*"
    r"(個人|个人|個|个|位|人|包|瓶|罐|條|条|件|台|支|份|盒|箱|片|顆|颗|張|张|雙|双|套|組|组|袋|桶)"
    r"(?!小時|小时|鐘頭|钟头|星期|禮拜|礼拜|月|半小時|語|语|灣|湾|北|南|中)")


def find_quantities(clause):
    """[(value, approx, start, end, text)] for 'number + unit' and 'N個人'."""
    out = []
    for m in _QTY_RE.finditer(clause or ""):
        v, approx = parse_cn_number(m.group(1))
        if v is None or v <= 0:
            continue
        out.append((min(int(v), 10000), bool(approx), m.start(), m.end(), m.group(0)))
    return out


@dataclass
class _Clause:
    idx: int
    text: str
    hits: list
    subjects: list
    zone: int
    location: str
    quantities: list
    deadlines: list
    ran_out: bool


def _classify(clause_text, hit, lex):
    """active | suppressed | doubt (doubt = keep P1 and add the ask-back)."""
    e = hit.entry
    if not e.get("negatable"):
        return "active"
    p = lex.patterns
    prefix = clause_text[:hit.start]
    negated = bool(lex.negation_re.search(prefix))
    doubt = any(w in clause_text for w in p["doubt_words"])
    # past words count only before the keyword (「胸痛以前有過」 stays P1: safer)
    past = any(w in prefix for w in p["past_words"])
    present = any(w in clause_text for w in p["present_words"])
    if doubt:
        return "doubt"
    if negated:
        return "suppressed"
    if past and not present:
        return "suppressed"
    return "active"


def _scan(idx, text, now, lex):
    zone, loc, _span = codebook.parse_location(text)
    cl = _Clause(idx=idx, text=text, hits=lex.find_hits(text), subjects=lex.find_subjects(text),
                 zone=zone, location=loc, quantities=find_quantities(text),
                 deadlines=triage.deadline_matches(text, now), ran_out=triage.ran_out(text))
    for h in cl.hits:
        h.status = _classify(text, h, lex)
    return cl


def _mark_exaggeration(norm, clauses, lex):
    """Everyday exaggeration keeps P1 but asks back, never drops urgency (red-team round 2):
    - a lone 救命 next to a mundane complaint: 「救命喔，排隊排好久」, 「救命啊這也太難吃了」;
    - a soft symptom said as "almost" or "so … that": 「我快中暑了」, 「累到快昏倒」, 「嚇到冒冷汗」;
    - breathing trouble while laughing: 「笑到不能呼吸」.
    The hit becomes 'doubt' (P1 stays) and the card asks 「確認：是真的緊急狀況嗎？」."""
    p = lex.patterns
    cues = p.get("exaggeration_cues") or ()
    prefixes = tuple(p.get("exaggeration_prefixes") or ())
    soft_ids = set(p.get("exaggeration_soft_ids") or ())
    laughing = any(w in norm for w in (p.get("laugh_words") or ()))
    has_cue = any(c in norm for c in cues)
    # another real emergency said in the same breath keeps 救命 fully active
    other_red = any(h.status == "active" and h.entry.get("red_flag") and h.id != "generic_emergency"
                    for cl in clauses for h in cl.hits)
    for cl in clauses:
        for h in cl.hits:
            if h.status != "active" or not h.entry.get("red_flag"):
                continue
            prefix = cl.text[:h.start]
            interjection = (h.id == "generic_emergency" and h.keyword in ("救命", "救救") and has_cue
                            and not other_red)
            softened = h.id in soft_ids and bool(prefixes) and prefix.endswith(prefixes)
            laughed = h.id == "sym_dyspnea" and laughing
            if interjection or softened or laughed:
                h.status = "doubt"
                h.exaggerated = True


def _clauses(text, now):
    lex = get_lexicon()
    norm = scrub_ids(normalize(text or ""))
    clauses = [_scan(i, c, now, lex) for i, c in enumerate(split_clauses(norm))]
    _mark_exaggeration(norm, clauses, lex)
    return norm, clauses


# ------------------------------------------------------------------ entity pickers

def _closest(items, pos):
    """items sorted by start (index 2 = start for subjects, 2 for quantities): last before pos, else first after."""
    before = [x for x in items if x[2] < pos]
    if before:
        return before[-1]
    return items[0] if items else None


def _nearest(clauses, home, attr, need_clauses=(), skip=()):
    """Nearest clause that has `attr`: earlier clauses first (a topic carries forward in Chinese),
    then later clauses that hold no other need (so 「…毛毯，還有我兒子手機沒電」 keeps 毛毯 subject-free).
    Clauses in `skip` (another person's need for the same item) are never borrowed from."""
    for ci in range(home - 1, -1, -1):
        if ci in skip:
            continue
        if getattr(clauses[ci], attr):
            return clauses[ci], "last"
    for ci in range(home + 1, len(clauses)):
        if ci in need_clauses or ci in skip:
            continue
        if getattr(clauses[ci], attr):
            return clauses[ci], "first"
    return None, None


def _pick_subject_ex(clauses, own, pos, need_clauses=(), skip=()):
    """(subject tuple, clause) — the subject the need belongs to, and the clause it was said in."""
    home = own[0]
    s = _closest(clauses[home].subjects, pos)
    if s:
        return s, clauses[home]
    for ci in own[1:]:
        if clauses[ci].subjects:
            return clauses[ci].subjects[0], clauses[ci]
    cl, where = _nearest(clauses, home, "subjects", need_clauses, skip)
    if cl:
        return (cl.subjects[-1] if where == "last" else cl.subjects[0]), cl
    return None, None


def _pick_subject(clauses, own, pos, need_clauses=()):
    return _pick_subject_ex(clauses, own, pos, need_clauses)[0]


# 「陳家的阿嬤」「林家阿嬤」「隔壁的阿公」: the qualifier tells two people apart (red-team H4: dedupe must not
# see two families' 阿嬤 as one person). 我家/大家/人家/老家 are not qualifiers.
_SUBJ_QUAL = re.compile(r"(?:([一-鿿])家|(隔壁|對面|对面|樓上|楼上|樓下|楼下))(的)?$")
_NOT_FAMILY = set("我你妳他她們们大人自回老住全")


def _qualified_subject(s, cl):
    """The subject word with a family/neighbour qualifier said right before it, if any."""
    word = s[0]
    if cl is None:
        return word
    m = _SUBJ_QUAL.search(cl.text[:s[2]])
    if not m:
        return word
    if m.group(1) and m.group(1) in _NOT_FAMILY:
        return word
    return m.group(0) + word


_REDUP = re.compile(r"(.)\1")
_SUBJ_CANON = str.maketrans({"妈": "媽", "嬷": "嬤", "爷": "爺", "儿": "兒", "孙": "孫", "宝": "寶"})


def subject_key(word):
    """Comparable form of a subject: 爸爸 = 我爸 = 爸, 阿嬷 = 阿嬤; 陳家的阿嬤 != 林家的阿嬤."""
    w = normalize(word or "").replace(" ", "").replace("的", "").translate(_SUBJ_CANON)
    w = re.sub(r"^(我們|我们|我|他們|他们|他|她|你)", "", w)
    return _REDUP.sub(r"\1", w)


def _place_key(cl):
    if not cl.zone:
        return None
    return (cl.zone, cl.location if cl.zone == codebook.ZONE_EXTERNAL else "")


def _compatible(g, subj, place):
    """Can a hit with this subject/place join group g? None (not said) fits anything."""
    if subj is not None and g["subject"] is not None and subj != g["subject"]:
        return False
    if place is not None and g["place"] is not None and place != g["place"]:
        return False
    return True


def _pick_location(clauses, own, need_clauses=(), skip=()):
    for ci in own:
        if clauses[ci].zone:
            return clauses[ci].zone, clauses[ci].location
    cl, _ = _nearest(clauses, own[0], "zone", need_clauses, skip)
    if cl:
        return cl.zone, cl.location
    return 0, ""


def _ranout_phrase(text):
    words = sorted(get_lexicon().patterns["ran_out_words"], key=len, reverse=True)
    best = None
    for w in words:
        i = text.find(w)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, w)
    if best is None:
        return ""
    i, w = best
    tail = text[i + len(w): i + len(w) + 1]
    return w + (tail if tail in ("了", "帶", "带") else "")


def _attach_time(n, clauses, own, need_clauses, now):
    cands = []
    ran = None
    for ci in own:
        cl = clauses[ci]
        cands.extend((dl, ph) for dl, ph, _ in cl.deadlines)
        if cl.ran_out and ran is None:
            ran = cl.text
    if not cands and ran is None:
        home = own[0]
        for ci in (home + 1, home - 1):
            if 0 <= ci < len(clauses) and ci not in need_clauses:
                cl = clauses[ci]
                if cl.deadlines or cl.ran_out:
                    cands.extend((dl, ph) for dl, ph, _ in cl.deadlines)
                    if cl.ran_out:
                        ran = cl.text
                    break
    if ran is not None and n.category in triage.ran_out_categories():
        cands.append((now, _ranout_phrase(ran) or "用完"))
    if cands:
        dl, ph = min(cands, key=lambda x: x[0])
        n.deadline_at = dl
        n.deadline_text = triage.format_deadline(dl)
        n.time_phrase = ph
        n.evidence["time_phrase"] = ph


def _attach_quantity(n, clauses, own, need_clauses, pos):
    q = _closest(clauses[own[0]].quantities, pos)
    if q is None:
        for ci in own[1:]:
            if clauses[ci].quantities:
                q = clauses[ci].quantities[0]
                break
    if q is None:
        home = own[0]
        for ci in (home + 1, home - 1):
            if 0 <= ci < len(clauses) and ci not in need_clauses and clauses[ci].quantities:
                q = clauses[ci].quantities[0]
                break
    if q is not None:
        n.quantity, n.quantity_approx = q[0], q[1]
        n.evidence["quantity"] = q[4]
        if q[1]:
            n.add_flag("approx")


def _adjust_item(h, clauses, ci):
    """Subject-dependent items: 走失 of an elder -> 404; 尿布 of an elder -> 804, 紙尿褲 of a baby -> 602."""
    e = h.entry
    code, cat = e["item_code"], e["category"]
    if e["id"] not in ("rescue_lost_child", "rescue_lost_way", "baby_diaper", "adult_diaper"):
        return code, cat
    s = _pick_subject(clauses, [ci], h.start)
    group = s[1] if s else "unknown"
    word = s[0] if s else ""
    if e["id"] in ("rescue_lost_child", "rescue_lost_way") and h.keyword in ("走丟", "走丢", "走失", "迷路"):
        if group == "elderly":
            return 404, 4
        if group == "unknown" and word:
            return 402, 4
    if e["id"] == "baby_diaper" and not any(x in h.keyword for x in ("嬰兒", "婴儿")):
        if group in ("elderly", "disabled") or (group == "unknown" and word):
            return 804, 8
    if e["id"] == "adult_diaper" and h.keyword in ("紙尿褲", "纸尿裤") and group in ("infant", "child"):
        return 602, 6
    return code, cat


def make_summary(n):
    parts = []
    if n.subject:
        parts.append(n.subject)
    loc = codebook.zone_label(n.zone_code, n.location) if n.zone_code else (n.location or "")
    if loc:
        parts.append(loc)
    it = codebook.item(n.item_code)
    parts.append(it["zh"] if it else codebook.cat(n.category)["zh"].split("（")[0])
    if n.quantity:
        parts.append(("約" if n.quantity_approx else "") + str(n.quantity))
    if n.time_phrase:
        parts.append(n.time_phrase)
    return " ".join(parts)[:SUMMARY_MAX]


# ------------------------------------------------------------------ rules extraction

def _build(text, now):
    """Untriaged needs from the rules. Returns (needs, clauses)."""
    lex = get_lexicon()
    _norm, clauses = _clauses(text, now)
    # One group per (item, subject, place): 「我兒子被困在中正路，我女兒被困在和平國小」 is two needs
    # (red-team C2). A hit whose clause names no subject/place joins the earlier group of its item.
    groups = []
    suppressed = []
    for cl in clauses:
        for h in cl.hits:
            if h.status == "suppressed":
                suppressed.append(h)
                continue
            code, cat = _adjust_item(h, clauses, cl.idx)
            key = code if code else ("cat", cat)
            # only a subject said before the hit splits a need (「…洗腎要找爸爸帶她去」: 爸爸 is not the patient)
            before = [x for x in cl.subjects if x[2] < h.start]
            s = before[-1] if before else None
            subj = subject_key(_qualified_subject(s, cl)) if s else None
            place = _place_key(cl)
            g = next((g for g in groups if g["key"] == key and _compatible(g, subj, place)), None)
            if g is None:
                g = {"key": key, "item_code": code, "category": cat, "hits": [], "clauses": [], "sensitive": False,
                     "subject": None, "place": None}
                groups.append(g)
            if subj is not None and g["subject"] is None:
                g["subject"] = subj
            if place is not None and g["place"] is None:
                g["place"] = place
            g["hits"].append((cl.idx, h))
            if cl.idx not in g["clauses"]:
                g["clauses"].append(cl.idx)
            if h.entry.get("sensitive"):
                g["sensitive"] = True
    # every clause that holds a need (even one absorbed below) is not a source for another need's fields
    need_clauses = {ci for g in groups for ci in g["clauses"]}
    # generic items give way to a specific item of the same category for the same person/place
    # (sensitivity carries over); 「阿公的藥沒了，阿嬤的胰島素也沒了」 keeps both
    for gen_code, gen_cat in GENERIC_ITEMS.items():
        for gen in [g for g in groups if g["key"] == gen_code]:
            specific = [g for g in groups if g["key"] != gen_code and g["category"] == gen_cat
                        and _compatible(g, gen["subject"], gen["place"])]
            if specific:
                groups.remove(gen)
                for g in specific:
                    g["sensitive"] = g["sensitive"] or gen["sensitive"]
    for code, by in ABSORBED_BY.items():
        if any(o["key"] in by for o in groups):
            groups[:] = [g for g in groups if g["key"] != code]
    # 救命 / 叫救護車 next to a real emergency is that emergency, not a second one (its red flag goes along,
    # so the coverage invariant still sees it on a P1 need)
    for g in [g for g in groups if all(h.id == "generic_emergency" for _ci, h in g["hits"])]:
        target = next((o for o in groups if o is not g and any(h.entry.get("red_flag") and h.id != "generic_emergency"
                                                                 for _ci, h in o["hits"])), None)
        if target is not None:
            target["hits"].extend(g["hits"])
            groups.remove(g)
    needs = []
    for g in groups:
        n = Need(category=g["category"], item_code=g["item_code"])
        it = codebook.item(n.item_code)
        n.item = it["zh"] if it else ""
        first_ci, first = g["hits"][0]
        own = g["clauses"]
        n.evidence["category"] = first.keyword
        n.evidence["item"] = first.keyword
        reds = []
        for _ci, h in g["hits"]:
            if h.entry.get("red_flag") and h.id not in reds:
                reds.append(h.id)
                n.evidence.setdefault("red_flag", h.keyword)
            if h.status == "doubt":
                n.add_ask(askback_exaggeration(h.keyword) if getattr(h, "exaggerated", False)
                          else askback_negated(h.keyword))
        n.red_flags = reds
        if g["sensitive"]:
            n.add_flag("sensitive")
        # the son's address is not the daughter's: never borrow from the other need of the same item
        siblings = {ci for o in groups if o is not g and o["key"] == g["key"] for ci in o["clauses"]}
        s, s_cl = _pick_subject_ex(clauses, own, first.start, need_clauses, siblings)
        if s:
            n.subject, n.subject_group = _qualified_subject(s, s_cl), s[1]
            n.evidence["subject"] = n.subject
        zone, loc = _pick_location(clauses, own, need_clauses, siblings)
        if zone:
            n.zone_code, n.location = zone, loc
            n.evidence["location"] = loc
        _attach_time(n, clauses, own, need_clauses, now)
        _attach_quantity(n, clauses, own, need_clauses, first.start)
        needs.append(n)
    if not needs and clauses:
        n = Need(category=0)
        own = [0]
        s = _pick_subject(clauses, own, len(clauses[0].text))
        if s:
            n.subject, n.subject_group = s[0], s[1]
            n.evidence["subject"] = s[0]
        zone, loc = _pick_location(clauses, own)
        if zone:
            n.zone_code, n.location = zone, loc
            n.evidence["location"] = loc
        _attach_time(n, clauses, own, set(), now)
        _attach_quantity(n, clauses, own, set(), len(clauses[0].text))
        needs.append(n)
    if suppressed:
        for n in needs:
            for h in suppressed:
                n.add_ask(askback_negated(h.keyword))
                if h.id not in n.suppressed:
                    n.suppressed.append(h.id)
            n.add_flag("negated")
    for n in needs:
        n.field_sources = {k: "rules" for k in ("category", "item", "subject", "location", "time_phrase", "quantity")
                           if k in n.evidence}
    return needs, clauses


def _cap(needs):
    """At most MAX_NEEDS needs: every P1 stays, then the most urgent of the rest (original order kept)."""
    if len(needs) <= MAX_NEEDS:
        return needs
    ranked = sorted(range(len(needs)), key=lambda i: (needs[i].priority, i))
    keep = {i for i in ranked if needs[i].priority == 1}
    for i in ranked:
        if len(keep) >= MAX_NEEDS:
            break
        keep.add(i)
    return [n for i, n in enumerate(needs) if i in keep]


def rules_extract(text, now):
    """Rules-only needs (triaged, ≤ 5, every P1 kept). < 50 ms for a 400-character story."""
    needs, _ = _build(text, now)
    for n in needs:
        n.priority, n.priority_reasons, n.emergency = triage.assign_priority(n, now)
        n.rule_priority = n.priority
    needs = _cap(needs)
    for n in needs:
        n.missing_info = triage.missing_info(n)
        n.summary = make_summary(n)
    return needs


def analyze(text, now):
    """rules_extract + rules_index: what the pipeline turns into drafts first."""
    needs = rules_extract(text, now)
    for i, n in enumerate(needs):
        n.rules_index = i
    return needs


# ------------------------------------------------------------------ grounding

class _Ctx:
    """Everything the merge needs to know about one utterance."""

    def __init__(self, transcript, now):
        lex = get_lexicon()
        self.norm, self.clauses = _clauses(transcript, now)
        self.low = self.norm.lower()
        self.active_red = OrderedDict()   # id -> keyword (non-suppressed red flags)
        self.active_ids = set()
        self.suppressed = OrderedDict()   # id -> keyword
        self.active_categories = set()
        for cl in self.clauses:
            for h in cl.hits:
                if h.status == "suppressed":
                    self.suppressed.setdefault(h.id, h.keyword)
                else:
                    self.active_ids.add(h.id)
                    self.active_categories.add(h.entry["category"])
                    if h.entry.get("red_flag"):
                        self.active_red.setdefault(h.id, h.keyword)
        self.zones = {cl.zone for cl in self.clauses if cl.zone}
        # which clauses hold which need (item_code, or ("cat", c)) — LLM fields may not be taken
        # from another need's clause (the real 1.5B model merges needs and borrows their times)
        self.item_clauses = {}
        for cl in self.clauses:
            for h in cl.hits:
                if h.status == "suppressed":
                    continue
                code, cat = _adjust_item(h, self.clauses, cl.idx)
                self.item_clauses.setdefault(code if code else ("cat", cat), set()).add(cl.idx)
        # counts: a number followed by a counting unit (個/位/人/包/瓶...) — 「剩兩天」 is a duration,
        # 「E區1床」 a bed: the 1.5B model turns those into quantities
        self.clause_numbers = [{q[0] for q in cl.quantities} for cl in self.clauses]
        self.numbers = set().union(*self.clause_numbers) if self.clause_numbers else set()
        self.any_numbers = set()
        for m in _NUM_TOKEN.finditer(self.norm):
            v, _ = parse_cn_number(m.group(0))
            if v is not None:
                self.any_numbers.add(int(v))
        self.lex = lex


_NUM_TOKEN = re.compile(r"\d+|[零〇一二兩两三四五六七八九十百千萬万幾几]+")


def _need_key(n):
    return n.item_code if n.item_code else ("cat", n.category)


def _value_clauses(f, v, ctx):
    """Indexes of the clauses in which an LLM field value was said (empty: not per-clause)."""
    if f == "quantity":
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return set()
        return {i for i, nums in enumerate(ctx.clause_numbers) if iv in nums}
    vn = _nval(v)
    if f == "location":
        z, loc, _ = codebook.parse_location(vn)
        if z and z != codebook.ZONE_EXTERNAL:
            return {cl.idx for cl in ctx.clauses if cl.zone == z}
        if z == codebook.ZONE_EXTERNAL and loc:
            return {cl.idx for cl in ctx.clauses if loc in cl.text}
    return {cl.idx for cl in ctx.clauses if vn and vn in cl.text}


def _owned_ok(f, v, ctx, own, other):
    """A grounded value is usable if it was said in this need's clause or in a clause of no other need."""
    cs = _value_clauses(f, v, ctx)
    if not cs:
        return True
    return any(c in own or c not in other for c in cs)


def _nval(v):
    return normalize(str(v)).strip("「」『』\"' ") if v is not None else ""


_PRONOUN_PREFIX = re.compile(r"^(我們的|我们的|我的|他的|她的|我們|我们|我|他|她|你)")


def _person_only(vn, lex):
    """True when the text is only a person (我爸, 阿嬤, 她) with nothing else."""
    s = _PRONOUN_PREFIX.sub("", vn or "")
    if not s:
        return bool(vn)
    covered = sum(e - st for _w, _g, st, e in lex.find_subjects(s))
    return covered >= len(s)


def grounded(field_name, value, transcript, ctx=None):
    """Was `value` for `field_name` actually said? (normalized substring; items via any keyword of
    their lexicon entry; quantities via digits or Chinese numerals; zones via parsed equality)."""
    if ctx is None:
        ctx = _Ctx(transcript or "", 0.0)
    if value is None or value == "" or value == 0:
        return False
    lex = ctx.lex
    if field_name == "quantity":   # only a number said with a counting unit grounds a quantity
        try:
            return int(value) in ctx.numbers
        except (TypeError, ValueError):
            return False
    if field_name in ("location", "zone", "zone_code"):
        if field_name == "zone_code" or isinstance(value, int):
            z, loc = int(value), ""
        else:
            vn = _nval(value)
            if vn and vn in ctx.norm:
                return True
            z, loc, _ = codebook.parse_location(vn)
        if z == 0:
            return False
        if z == codebook.ZONE_EXTERNAL:
            return bool(loc) and loc in ctx.norm
        return z in ctx.zones
    if field_name == "item":
        vn = _nval(value)
        if _person_only(vn, lex):
            return False   # real qwen puts the person in 'item' (「我爸」): a person is never an item
        if vn and vn.lower() in ctx.low:
            return True
        for h in lex.find_hits(vn):
            if any(kw.lower() in ctx.low for kw in h.entry["keywords"]):
                return True
        return False
    if field_name == "subject_group":
        if value in ("unknown", ""):
            return False
        if value == "adult":
            return any(w in ctx.norm for w in lex.person_words)
        return any(w in ctx.norm for w in lex.subject_group_words(value))
    if field_name == "category":
        code = value if isinstance(value, int) else codebook.cat_code(value)
        return int(code) in ctx.active_categories
    vn = _nval(value)
    return bool(vn) and vn in ctx.norm


# ------------------------------------------------------------------ LLM merge

def _clean_llm(d):
    d = d if isinstance(d, dict) else {}
    out = {}
    for k in ("category", "item", "subject", "subject_group", "location", "time_phrase", "summary"):
        v = d.get(k)
        out[k] = "" if v is None else str(v).strip()
    try:
        q = int(d.get("quantity") or 0)
    except (TypeError, ValueError):
        q = 0
    out["quantity"] = max(0, min(10000, q))
    out["category"] = (out["category"] or "OTHER").upper()
    out["subject_group"] = (out["subject_group"] or "unknown").lower()
    return out


def _map_item(value, ctx):
    """LLM item text -> best lexicon hit (grounded hits first, specific before generic, longest)."""
    hits = ctx.lex.find_hits(_nval(value)) if value else []
    if not hits:
        return None
    def key(h):
        g = any(kw.lower() in ctx.low for kw in h.entry["keywords"])
        return (not g, h.entry["item_code"] in GENERIC_ITEMS, -(h.end - h.start), h.start)
    return sorted(hits, key=key)[0]


def _llm_info(d, ctx):
    llm_cat = codebook.cat_code(d["category"])
    exact = codebook.item_by_zh(_nval(d["item"])) if d["item"] else None
    hit = _map_item(d["item"], ctx)
    if exact is not None:   # the canonical codebook name (e.g. 失智長輩走失) wins over a keyword inside it
        return {"hit": hit, "item_code": exact["code"], "cat": exact["category"], "llm_cat": llm_cat}
    return {"hit": hit, "item_code": hit.entry["item_code"] if hit else 0,
            "cat": hit.entry["category"] if hit else llm_cat, "llm_cat": llm_cat}


def _match(rules, matched, info):
    ic = info["item_code"]
    if ic and ic not in GENERIC_ITEMS:
        same = [pos for pos, rn in enumerate(rules) if rn.item_code == ic]
        if same:
            # two people with the same item are two rules needs: take the next free one
            free = [pos for pos in same if pos not in matched]
            return free[0] if free else "dup"
    cat = info["cat"]
    for pos, rn in enumerate(rules):
        if pos in matched or rn.category != cat:
            continue
        if not ic or ic in GENERIC_ITEMS or not rn.item_code or rn.item_code in GENERIC_ITEMS:
            return pos
        # one emergency event, two labels (走失兒童 vs 失智長輩走失): merge, never a second alarm
        if rn.red_flags and rn.category in (1, 4):
            return pos
    if cat != 0:
        for pos, rn in enumerate(rules):
            if pos not in matched and rn.category == 0 and not rn.red_flags:
                return pos
    else:
        for pos, rn in enumerate(rules):
            if pos not in matched and rn.category == 0:
                return pos
    return None


def _set_field(m, f, v, now):
    if f == "quantity":
        m.quantity, m.quantity_approx = int(v), False
    elif f == "location":
        z, loc, _ = codebook.parse_location(_nval(v))
        m.location, m.zone_code = (loc or str(v)), z
    elif f == "time_phrase":
        m.time_phrase = str(v)
        dl, txt = triage.parse_deadline(_nval(v), now)
        if dl is not None:
            m.deadline_at, m.deadline_text = dl, txt
    else:
        setattr(m, f, str(v))


def _apply_llm_fields(m, d, ctx, now, src, ev, flags, own=frozenset(), other=frozenset()):
    for f in ("quantity", "subject", "location", "time_phrase"):
        lv = d[f]
        if not lv:
            continue
        if f == "location":
            z, _, _ = codebook.parse_location(_nval(lv))
            if not z:
                continue   # 「明天早上」 is not a place: treat as not said (no flag)
        rv = getattr(m, f)
        if f == "subject" and rv and not _PRONOUN_PREFIX.sub("", str(lv)).strip():
            continue   # 「我」 is the speaker, not the patient: keep the rules' 兒子 / 女兒 / 阿嬤
        g = grounded(f, lv, None, ctx)
        if f == "quantity" and not g and int(lv) in ctx.any_numbers:
            continue   # the number was said, but as a duration / bed / age: not a quantity
        if g:
            if not _owned_ok(f, lv, ctx, own, other):
                continue   # said, but in another need's clause: never steal it
            if lv != rv:
                _set_field(m, f, lv, now)
                src[f] = "llm"
                ev[f] = str(lv)
        elif rv in (None, "", 0):
            _set_field(m, f, lv, now)
            src[f] = "llm_ungrounded"
            ev[f] = ""
            flags.append(f"ungrounded:{f}")
    lg = d["subject_group"]
    if lg and lg != "unknown" and lg != m.subject_group:
        if grounded("subject_group", lg, None, ctx):
            m.subject_group = lg
            src["subject_group"] = "llm"
        elif m.subject_group in ("", "unknown"):
            m.subject_group = lg
            src["subject_group"] = "llm_ungrounded"
            flags.append("ungrounded:subject_group")
    if d["summary"]:
        m.summary = d["summary"][:SUMMARY_MAX]
        src["summary"] = "llm"


def _merge_one(rn, d, info, ctx, now, own=frozenset(), other=frozenset()):
    m = copy.deepcopy(rn)
    src, ev, flags = dict(m.field_sources), dict(m.evidence), list(m.flags)
    item_g = bool(d["item"]) and grounded("item", d["item"], None, ctx)
    if not m.red_flags:   # rules keep item/category when a red flag fired
        ic = info["item_code"]
        rules_generic = (not m.item_code) or (m.item_code in GENERIC_ITEMS)
        if ic and ic != m.item_code and rules_generic and ic not in GENERIC_ITEMS:
            # a specific LLM item refines a missing/generic rules item (grounded > rules > ungrounded,
            # and a generic rules item counts as 'not said')
            old_cat = m.category
            m.item_code, m.category = ic, info["cat"]
            m.item = (codebook.item(ic) or {}).get("zh", d["item"])
            if item_g:
                src["item"] = src["category"] = "llm"
                ev["item"] = ev["category"] = d["item"]
            else:
                src["item"] = "llm_ungrounded"
                ev["item"] = ""
                flags.append("ungrounded:item")
                if m.category != old_cat and not grounded("category", m.category, None, ctx):
                    src["category"] = "llm_ungrounded"
                    flags.append("ungrounded:category")
        if m.category == 0 and info["cat"] != 0:
            g = _category_grounded(info["cat"], info, item_g, ctx)
            m.category = info["cat"]
            src["category"] = "llm" if g else "llm_ungrounded"
            ev["category"] = d["item"] or d["category"]
            if not g:
                flags.append("ungrounded:category")
        if not m.item and d["item"]:
            m.item = d["item"]
    _apply_llm_fields(m, d, ctx, now, src, ev, flags, own, other)
    m.field_sources, m.evidence = src, ev
    m.flags = list(dict.fromkeys(flags))
    m._llm_item = d["item"]
    return m


def _category_grounded(cat, info, item_g, ctx):
    """A category is 'said' when a lexicon hit of that category was heard, or the grounded LLM item
    maps to it. A grounded but unmapped item (「頭暈」) does not ground the LLM's category."""
    if not cat:
        return False
    if grounded("category", cat, None, ctx):
        return True
    hit = info.get("hit")
    return bool(item_g and hit is not None and hit.entry["category"] == cat)


def _from_llm(d, info, ctx, now, other=frozenset()):
    """An LLM-only need, or None when it carries no evidence (no grounded item and an OTHER or
    ungrounded category): such needs only add a useless draft."""
    m = Need(category=info["cat"], item_code=info["item_code"])
    m.item = (codebook.item(m.item_code) or {}).get("zh", "") or d["item"]
    src, ev, flags = {}, {}, ["ai_only"]
    item_g = bool(d["item"]) and grounded("item", d["item"], None, ctx)
    cat_g = _category_grounded(m.category, info, item_g, ctx)
    if not item_g and not cat_g:
        return None
    if d["item"]:
        src["item"] = "llm" if item_g else "llm_ungrounded"
        ev["item"] = d["item"] if item_g else ""
        if not item_g:
            flags.append("ungrounded:item")
    src["category"] = "llm" if cat_g else "llm_ungrounded"
    ev["category"] = (d["item"] or d["category"]) if cat_g else ""
    if not cat_g:
        flags.append("ungrounded:category")
    if item_g and m.item_code:
        m.red_flags = [rid for rid in ctx.active_red if ctx.lex.by_id[rid]["item_code"] == m.item_code]
    own = set(ctx.item_clauses.get(_need_key(m), set()))
    if info["hit"] is not None:
        kws = [k.lower() for k in info["hit"].entry["keywords"]]
        own |= {cl.idx for cl in ctx.clauses if any(k in cl.text.lower() for k in kws)}
    _apply_llm_fields(m, d, ctx, now, src, ev, flags, own, other)
    m.field_sources, m.evidence = src, ev
    m.flags = list(dict.fromkeys(flags))
    m.rules_index = None
    m._llm_item = d["item"]
    if not m.summary:
        m.summary = make_summary(m)
    return m


def merge_llm(rules_needs, llm_needs, transcript, now, _ctx=None):
    """Merge validated LLM needs into the rules needs (SPEC 6.5). No triage here."""
    ctx = _ctx or _Ctx(transcript or "", now)
    rules = []
    for i, n in enumerate(rules_needs or []):
        m = copy.deepcopy(n) if isinstance(n, Need) else Need(**{k: v for k, v in dict(n).items() if k in NEED_FIELDS})
        if m.rules_index is None:
            m.rules_index = i
        rules.append(m)
    matched, extra = {}, []
    for raw in llm_needs or []:
        d = _clean_llm(raw)
        info = _llm_info(d, ctx)
        if info["cat"] == 0 and not any(d[f] for f in ("item", "subject", "location", "time_phrase", "quantity")):
            continue   # an empty OTHER need carries nothing
        pos = _match(rules, matched, info)
        if pos == "dup":
            continue
        if pos is None:
            extra.append((d, info))
        else:
            matched[pos] = (d, info)
    keys = [_need_key(rn) for rn in rules]
    out = []
    for pos, rn in enumerate(rules):
        if pos in matched:
            d, info = matched[pos]
            own = set(ctx.item_clauses.get(keys[pos], set()))
            other = set()
            for j, k in enumerate(keys):
                if j != pos and k != keys[pos]:
                    other |= ctx.item_clauses.get(k, set())
            out.append(_merge_one(rn, d, info, ctx, now, own, other - own))
        else:
            out.append(rn)
    all_owned = set()
    for k in keys:
        all_owned |= ctx.item_clauses.get(k, set())
    for d, info in extra:
        m = _from_llm(d, info, ctx, now, all_owned)
        if m is not None:
            out.append(m)
    return out


# ------------------------------------------------------------------ refine (merge + invariants)

def _min_opt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _ungrounded_fields(m):
    return [f.split(":", 1)[1] for f in m.flags if f.startswith("ungrounded:")]


def _revert(m, rn, f):
    if f in ("item", "category"):
        m.item, m.item_code, m.category = rn.item, rn.item_code, rn.category
    elif f == "location":
        m.location, m.zone_code = rn.location, rn.zone_code
    elif f == "time_phrase":
        m.time_phrase, m.deadline_at, m.deadline_text = rn.time_phrase, rn.deadline_at, rn.deadline_text
    elif f == "quantity":
        m.quantity, m.quantity_approx = rn.quantity, rn.quantity_approx
    else:
        setattr(m, f, getattr(rn, f))


def _clear(m, f):
    if f == "item":
        m.item, m.item_code = "", 0
    elif f == "category":
        m.category = 0
    elif f == "location":
        m.location, m.zone_code = "", 0
    elif f == "time_phrase":
        m.time_phrase, m.deadline_at, m.deadline_text = "", None, ""
    elif f == "quantity":
        m.quantity, m.quantity_approx = None, False
    elif f == "subject_group":
        m.subject_group = "unknown"
    else:
        setattr(m, f, "")


def _suppressed_match(m, ctx):
    """Keyword of a symptom the speaker denied that this (LLM-derived) need maps to, else None."""
    if not ctx.suppressed:
        return None
    text = getattr(m, "_llm_item", "") or m.evidence.get("item") or ""
    hit = ctx.lex.lookup(_nval(text)) if text else None
    if hit is not None:
        if hit.id in ctx.suppressed and hit.id not in ctx.active_ids:
            return ctx.suppressed[hit.id]
        hit_ids = {h.id for h in ctx.lex.find_hits(_nval(text))}
        for sid, kw in ctx.suppressed.items():
            if sid in hit_ids and sid not in ctx.active_ids:
                return kw
    for sid, kw in ctx.suppressed.items():
        e = ctx.lex.by_id[sid]
        if e["category"] == m.category and e["category"] not in ctx.active_categories \
                and (hit is None or hit.entry["category"] == m.category) and not m.red_flags:
            return kw
    # the speaker denied a symptom and the AI put an urgent category on the utterance that nothing
    # said supports (real qwen: 「我沒有昏倒，只是頭暈」 -> PERSON_AT_RISK): treat it as the denied symptom
    if not m.red_flags and m.category and m.category not in ctx.active_categories \
            and m.field_sources.get("category") in ("llm", "llm_ungrounded"):
        return next(iter(ctx.suppressed.values()))
    return None


def _llm_driven(m):
    return any(m.field_sources.get(k) in ("llm", "llm_ungrounded") for k in ("item", "category"))


def _finish_triage(m, rn, ctx, now):
    supp_kw = _suppressed_match(m, ctx) if (rn is None or _llm_driven(m)) else None
    if supp_kw:
        m.add_flag("negated")
        m.add_ask(askback_negated(supp_kw))
    p, reasons, emerg = triage.assign_priority(m, now)
    if supp_kw and not m.red_flags:
        p, emerg = 2, None
        reasons = [triage.REASONS["negated_symptom"]] + [r for r in reasons if r["code"] not in
                                                         ("medical_emergency", "red_flag")]
    ung = _ungrounded_fields(m)
    if rn is not None:
        p_final = min(int(rn.priority), p)
        dl = _min_opt(rn.deadline_at, m.deadline_at)
        if dl != m.deadline_at:
            m.deadline_at, m.deadline_text = dl, rn.deadline_text or triage.format_deadline(dl)
            if rn.time_phrase:
                m.time_phrase = rn.time_phrase
        emerg = triage.union_emergency(rn.emergency, emerg)
        if p_final < int(rn.priority) and ung:
            g = copy.deepcopy(m)
            for f in ung:
                _revert(g, rn, f)
            pg, _, _ = triage.assign_priority(g, now)
            if pg >= int(rn.priority):
                m.add_flag("raised_by_ai_guess")
                for f in ung:
                    m.add_ask(askback_ai_guess(f, _field_value(m, f)))
        merged_reasons = list(rn.priority_reasons or [])
        for r in reasons:
            if r not in merged_reasons:
                merged_reasons.append(r)
        if p_final < int(rn.priority):
            merged_reasons = [r for r in reasons] + [r for r in merged_reasons if r not in reasons]
        m.priority = p_final
        m.rule_priority = min(int(rn.rule_priority or rn.priority), p_final)
        m.priority_reasons = merged_reasons
    else:
        if ung and not supp_kw:
            g = copy.deepcopy(m)
            for f in ung:
                _clear(g, f)
            pg, _, _ = triage.assign_priority(g, now)
            if pg > p:
                m.add_flag("raised_by_ai_guess")
                for f in ung:
                    m.add_ask(askback_ai_guess(f, _field_value(m, f)))
        m.priority = p
        m.rule_priority = p
        m.priority_reasons = reasons
    m.emergency = emerg
    m.missing_info = triage.missing_info(m)
    if not m.summary:
        m.summary = make_summary(m)
    return m


def _field_value(m, f):
    if f in ("item", "category"):
        return getattr(m, "_llm_item", "") or m.item or codebook.cat(m.category)["zh"]
    v = getattr(m, f, "")
    return "" if v is None else v


def _coverage(out, ctx, now):
    """Every non-suppressed red-flag hit must belong to a P1 need."""
    for rid, kw in ctx.active_red.items():
        if any(rid in (n.red_flags or []) and n.priority == 1 for n in out):
            continue
        n = Need(category=0, red_flags=[rid], flags=["unclassified_red_flag"],
                 evidence={"red_flag": kw, "category": kw}, field_sources={"category": "rules"})
        n.priority, n.priority_reasons, n.emergency = triage.assign_priority(n, now)
        n.priority = 1
        n.priority_reasons.insert(0, triage.R("unclassified_red_flag", f"危險字詞「{kw}」未歸類，請確認",
                                              f"Unclassified red-flag words: {kw}"))
        n.rule_priority = 1
        n.summary = f"未歸類危險字詞：{kw}"
        n.missing_info = []
        out.append(n)
    return out


def refine(rules_needs, llm_needs, transcript, now):
    """merge_llm + re-triage + the pipeline invariant + coverage + suppressed-symptom rule.

    Returned needs carry rules_index (index into rules_needs) or None for AI-only/unclassified needs.
    """
    ctx = _Ctx(transcript or "", now)
    rules_list = list(rules_needs or [])
    by_idx = {}
    for i, rn in enumerate(rules_list):
        if not isinstance(rn, Need):
            rn = Need(**{k: v for k, v in dict(rn).items() if k in NEED_FIELDS})
        by_idx[rn.rules_index if rn.rules_index is not None else i] = rn
    merged = merge_llm(rules_list, llm_needs, transcript, now, _ctx=ctx)
    out = []
    for m in merged:
        rn = by_idx.get(m.rules_index) if m.rules_index is not None else None
        out.append(_finish_triage(m, rn, ctx, now))
    return _coverage(out, ctx, now)

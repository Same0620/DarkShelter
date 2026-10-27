"""Duplicate suggestions (SPEC 6.7).

A candidate must meet all three:
  1. same item_code (or the same category when item_code is 0)
  2. same zone (both zone codes not in {0, 65535}; two external locations only if their location
     text is equal) OR same subject (both non-empty) OR bigram_jaccard(summaries) >= 0.5
  3. created within 12 h
Person-specific needs: suggest only (never auto-merge). Supplies: "add to existing".
PROTECTION is never deduped.

Stricter than the three conditions (red-team H4: one wrong merge tap loses a dialysis patient):
- two different subjects are never a candidate (陳家的阿嬤 vs 林家的阿嬤, 我爸 vs 我媽);
- a person-specific need is a candidate only at the same place (bed, or the same external
  address) or in the same household; a similar sentence or the same kinship word is not enough.
"""
from . import codebook
from .textnorm import bigram_jaccard, normalize

WINDOW_S = 12 * 3600
JACCARD_MIN = 0.5
OPEN = ("draft", "confirmed")


def _g(t, k, default=None):
    v = t.get(k, default) if isinstance(t, dict) else getattr(t, k, default)
    return default if v is None else v


def _norm(s):
    return normalize(s or "").replace(" ", "")


def same_item(a, b):
    ia, ib = int(_g(a, "item_code", 0) or 0), int(_g(b, "item_code", 0) or 0)
    if ia or ib:
        return ia == ib
    return int(_g(a, "category", 0) or 0) == int(_g(b, "category", 0) or 0)


def same_place(a, b):
    za, zb = int(_g(a, "zone_code", 0) or 0), int(_g(b, "zone_code", 0) or 0)
    ext = codebook.ZONE_EXTERNAL
    if za == ext and zb == ext:
        la, lb = _norm(_g(a, "location", "")), _norm(_g(b, "location", ""))
        return bool(la) and la == lb
    if za in (0, ext) or zb in (0, ext):
        return False
    return za == zb


def _subject_key(t):
    from .extract import subject_key   # late import: extract imports this module's neighbours
    return subject_key(_g(t, "subject", ""))


def same_subject(a, b):
    sa, sb = _subject_key(a), _subject_key(b)
    return bool(sa) and bool(sb) and sa == sb


def subjects_differ(a, b):
    """Both name a person and they are different people (陳家的阿嬤 vs 林家的阿嬤, 爸 vs 媽)."""
    sa, sb = _subject_key(a), _subject_key(b)
    return bool(sa) and bool(sb) and sa != sb


def same_household(a, b):
    ha, hb = _g(a, "household_id", None), _g(b, "household_id", None)
    return ha not in (None, "", 0) and hb not in (None, "", 0) and str(ha) == str(hb)


def similar_summary(a, b):
    return bigram_jaccard(_g(a, "summary", ""), _g(b, "summary", "")) >= JACCARD_MIN


def find_candidates(ticket, open_tickets, now):
    """[{seq, kind: 'suggest'|'add', reason}] for `ticket` among `open_tickets` (store-style dicts)."""
    cat = int(_g(ticket, "category", 0) or 0)
    if cat == codebook.PROTECTION:
        return []
    person = codebook.is_person_specific(cat)
    kind = "suggest" if person else "add"
    t_created = _g(ticket, "created_wall", None)
    t_created = now if t_created is None else float(t_created)
    t_seq = _g(ticket, "seq", None)
    out = []
    for o in open_tickets or []:
        o_seq = _g(o, "seq", None)
        if o_seq is not None and t_seq is not None and str(o_seq) == str(t_seq):
            continue
        if _g(o, "status", "draft") not in OPEN:
            continue
        if int(_g(o, "category", 0) or 0) == codebook.PROTECTION:
            continue
        if not same_item(ticket, o):
            continue
        if subjects_differ(ticket, o):
            continue
        reasons = []
        place = same_place(ticket, o)
        if place:
            reasons.append("same_zone")
        if same_subject(ticket, o):
            reasons.append("same_subject")
        if similar_summary(ticket, o):
            reasons.append("similar_summary")
        if not reasons:
            continue
        if person and not (place or same_household(ticket, o)):
            continue
        o_created = _g(o, "created_wall", None)
        if o_created is None or abs(t_created - float(o_created)) > WINDOW_S:
            continue
        out.append({"seq": o_seq, "kind": kind, "reason": "+".join(reasons)})
    return out

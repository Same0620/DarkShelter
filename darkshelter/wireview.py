"""wire_view: the ONLY code that decides what leaves the desk (SPEC 6.8).

The returned dict is what the codec packs as a DATA_SUMMARY record. The CSV export
and the board's print view use the same summary builder (hidden categories excluded).
Free-text subject, item and transcript never leave the desk.
"""
import math

from . import codebook
from .textnorm import scrub_ids, strip_contact_numbers

CLOSED_STATUSES = ("discarded", "merged", "resolved")

# Flag bits (SPEC 8.1)
F_SENSITIVE = 1 << 0   # sensitive or protection
F_UNVERIFIED = 1 << 1
F_ESCALATED = 1 << 2
F_CLOSED = 1 << 3
F_APPROX = 1 << 4
F_EMERGENCY = 1 << 5
F_EXTERNAL = 1 << 6
F_MULTI = 1 << 7       # report_count > 1

FLAG_NAMES = {F_SENSITIVE: "sensitive", F_UNVERIFIED: "unverified", F_ESCALATED: "escalated",
              F_CLOSED: "closed", F_APPROX: "approx", F_EMERGENCY: "emergency",
              F_EXTERNAL: "external", F_MULTI: "multi"}

SUMMARY_MAX_BYTES = 60


def flag_names(bits):
    return [name for bit, name in FLAG_NAMES.items() if bits & bit]


def truncate_utf8(s, max_bytes=SUMMARY_MAX_BYTES):
    """Cut on a character boundary so the UTF-8 encoding is <= max_bytes."""
    s = s or ""
    out, n = [], 0
    for ch in s:
        b = len(ch.encode("utf-8"))
        if n + b > max_bytes:
            break
        out.append(ch)
        n += b
    return "".join(out).rstrip()


def _ticket_flags(t):
    f = t.get("flags") or []
    return set(f) if isinstance(f, (list, tuple, set)) else set()


def _age_min(t, now):
    created = t.get("created_wall")
    if created is None:
        return 0
    return int(max(0, min(65535, (now - created) // 60)))


def _deadline_h(t, now):
    d = t.get("deadline_at")
    if d is None:
        return 255
    return int(max(0, min(254, math.floor((d - now) / 3600.0))))


def location_label(t):
    """Location text for the wire: zone label, or the parsed external address."""
    zone = int(t.get("zone_code") or 0)
    if zone == codebook.ZONE_EXTERNAL:
        loc = strip_contact_numbers(scrub_ids(t.get("location") or "")).replace("＊＊＊", "").strip()
        return loc or "外部地點"
    return codebook.zone_label(zone)


def build_summary(t, desk_letter="B"):
    """Summary built only from codes and parsed fields, location first, <= 60 UTF-8 bytes.

    e.g. 「B區12床 長者 洗腎 10/28 08:00」 or 「中正路三巷 受困人員」.
    """
    category = int(t.get("category") or 0)
    if category == codebook.PROTECTION:
        return truncate_utf8(f"保護個案，請社工聯絡{desk_letter}站")
    if "sensitive" in _ticket_flags(t):
        return truncate_utf8(f"機密醫療需求，請聯絡{desk_letter}站")
    parts = []
    loc = location_label(t)
    if loc:
        parts.append(loc)
    sg = t.get("subject_group") or "unknown"
    if sg != "unknown":
        parts.append(codebook.subject_group_zh(sg))
    it = codebook.item(t.get("item_code"))
    if it:
        parts.append(it["zh"])
    else:
        parts.append(codebook.cat(category)["zh"].split("（")[0])
    q = t.get("quantity")
    if q:
        unit = "人" if category == 4 else "份"
        parts.append(("約" if t.get("quantity_approx") else "") + f"{int(q)}{unit}")
    if t.get("deadline_text"):
        parts.append(str(t["deadline_text"]))
    return truncate_utf8(" ".join(p for p in parts if p))


def _view(t, now, **kw):
    v = {
        "seq": int(t["seq"]),
        "version": int(t.get("version") or 0),
        "level": 2,
        "priority": int(kw["priority"]),
        "category": int(kw["category"]),
        "item_code": int(kw["item_code"]),
        "quantity": int(kw["quantity"]),
        "subject_group": int(kw["subject_group"]),
        "flags": int(kw["flags"]),
        "age_min": _age_min(t, now),
        "deadline_h": kw.get("deadline_h", _deadline_h(t, now)),
        "zone": int(kw["zone"]),
        "summary": kw["summary"],
    }
    return v


def wire_view(t, now, desk_letter="B"):
    """dict | None — see SPEC 6.8 for the order of the checks."""
    if t is None:
        return None
    status = t.get("status")
    closed = status in CLOSED_STATUSES
    # 1. a ticket closed before it was ever sent is never sent
    if closed and not t.get("sent_once"):
        return None
    closed_bit = F_CLOSED if closed else 0
    escalated_bit = F_ESCALATED if t.get("escalated_wall") else 0
    # 2. never confirmed: only the content-free alert may leave
    if t.get("confirmed_wall") is None:
        if t.get("alert_sent"):
            return _view(t, now, priority=1, category=0, item_code=0, quantity=0, subject_group=0,
                         flags=F_UNVERIFIED | closed_bit, zone=0, deadline_h=255,
                         summary=truncate_utf8(f"{desk_letter}站有未確認的緊急需求"))
        return None
    priority = int(t.get("priority") or 3)
    category = int(t.get("category") or 0)
    emergency_bit = F_EMERGENCY if t.get("emergency") else 0
    multi_bit = F_MULTI if int(t.get("report_count") or 1) > 1 else 0
    # 3. protection: generic, no item, no quantity, no zone
    if category == codebook.PROTECTION:
        return _view(t, now, priority=priority, category=codebook.PROTECTION, item_code=0, quantity=0,
                     subject_group=0, flags=F_SENSITIVE | closed_bit | escalated_bit | emergency_bit,
                     zone=0, summary=build_summary(t, desk_letter))
    # 4. sensitive medical need: real category, no item, generic summary
    if "sensitive" in _ticket_flags(t):
        zone = int(t.get("zone_code") or 0)
        ext_bit = F_EXTERNAL if zone == codebook.ZONE_EXTERNAL else 0
        return _view(t, now, priority=priority, category=category, item_code=0, quantity=0,
                     subject_group=0,
                     flags=F_SENSITIVE | closed_bit | escalated_bit | emergency_bit | multi_bit | ext_bit,
                     zone=zone, summary=build_summary(t, desk_letter))
    # 5. normal
    zone = int(t.get("zone_code") or 0)
    flags = closed_bit | escalated_bit | emergency_bit | multi_bit
    if t.get("quantity_approx"):
        flags |= F_APPROX
    if zone == codebook.ZONE_EXTERNAL:
        flags |= F_EXTERNAL
    return _view(t, now, priority=priority, category=category,
                 item_code=int(t.get("item_code") or 0),
                 quantity=max(0, min(65535, int(t.get("quantity") or 0))),
                 subject_group=codebook.subject_group_code(t.get("subject_group")),
                 flags=flags, zone=zone, summary=build_summary(t, desk_letter))


def view_key(v):
    """The part of a view that R-V compares (everything but the version)."""
    if v is None:
        return None
    return {k: val for k, val in v.items() if k != "version"}


def export_row(t, now, desk_letter="B"):
    """Row for /export.csv and the print view — the same builder as the wire. None if hidden."""
    if codebook.is_hidden(t.get("category")):
        return None
    c = codebook.cat(t.get("category"))
    it = codebook.item(t.get("item_code"))
    sensitive = "sensitive" in _ticket_flags(t)
    return {
        "code": f"{desk_letter}-{int(t['seq']):03d}",
        "priority": f"P{int(t.get('priority') or 3)}",
        "status": t.get("status"),
        "category": c["zh"].split("（")[0],
        "item": "" if sensitive else (it["zh"] if it else ""),
        "quantity": "" if sensitive or not t.get("quantity") else int(t["quantity"]),
        "location": location_label(t),
        "summary": build_summary(t, desk_letter),
        "deadline": t.get("deadline_text") or "",
        "eoc_status": codebook.eoc_status_zh(t.get("eoc_status") or 0),
        "reports": int(t.get("report_count") or 1),
    }

"""Lexicon (data/lexicon.json): keyword entries plus pattern tables.

Entry: {id, keywords[], not_followed_by[], category, item_code, red_flag, negatable,
        emergency (null|medical|rescue|suicide|protection), sensitive, preceded_by[] (optional)}.

find_hits(): longest match first, non-overlapping, respecting not_followed_by and
preceded_by (the keyword counts only right after one of those words; an optional
他/她/的 may sit between). Latin letters match case-insensitively.
"""
import json
import re
from dataclasses import dataclass
from functools import lru_cache

from .config import ROOT

_LINKERS = ("他", "她", "的")


@dataclass
class Hit:
    entry: dict
    keyword: str        # the matched text as it appears in the clause
    start: int
    end: int
    status: str = "active"   # active | suppressed | doubt (set by extract)

    @property
    def id(self):
        return self.entry["id"]


class Lexicon:
    def __init__(self, data):
        self.data = data
        self.entries = data["entries"]
        self.by_id = {e["id"]: e for e in self.entries}
        self.patterns = data["patterns"]
        kws = []
        for order, e in enumerate(self.entries):
            for kw in e["keywords"]:
                kws.append((kw.lower(), order, e))
        # longest first; ties keep entry order (sorted() is stable)
        self._keywords = [(kw, e) for kw, _, e in sorted(kws, key=lambda x: (-len(x[0]), x[1]))]
        self.negation_re = re.compile(self.patterns["negation_regex"])
        self.subject_words = sorted(self.patterns["subject_words"].items(), key=lambda x: -len(x[0]))
        self.person_words = sorted(self.patterns["person_words"], key=len, reverse=True)
        self.items_index = {}
        for e in self.entries:
            self.items_index.setdefault(e["item_code"], []).append(e)

    # ------------------------------------------------------------ keyword hits
    @staticmethod
    def _accept(e, low, start, end):
        for nf in e.get("not_followed_by") or ():
            if low.startswith(nf.lower(), end):
                return False
        pre = e.get("preceded_by")
        if pre:
            prefix = low[:start]
            stripped = prefix[:-1] if prefix.endswith(_LINKERS) else prefix
            # 「他倒在地上」: 他 itself may be the required word, so check before stripping the linker too
            if not any(stripped.endswith(p.lower()) or prefix.endswith(p.lower()) for p in pre):
                return False
        return True

    def find_hits(self, text):
        """All accepted keyword hits in `text`, longest first, non-overlapping, sorted by position."""
        if not text:
            return []
        low = text.lower()
        cands = []
        for kw, e in self._keywords:
            start = low.find(kw)
            while start >= 0:
                end = start + len(kw)
                if self._accept(e, low, start, end):
                    cands.append((start, end, e))
                start = low.find(kw, start + 1)
        cands.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
        taken, hits = [], []
        for s, en, e in cands:
            if any(s < te and ts < en for ts, te in taken):
                continue
            taken.append((s, en))
            hits.append(Hit(e, text[s:en], s, en))
        hits.sort(key=lambda h: h.start)
        return hits

    def lookup(self, text):
        """Best entry hit for a short phrase (e.g. an LLM 'item'): the longest non-generic hit."""
        hits = self.find_hits(text or "")
        if not hits:
            return None
        hits.sort(key=lambda h: (h.entry["item_code"] in GENERIC_ITEMS, -(h.end - h.start), h.start))
        return hits[0]

    def keywords_of(self, entry_id):
        e = self.by_id.get(entry_id)
        return list(e["keywords"]) if e else []

    def entries_for_item(self, item_code):
        return list(self.items_index.get(int(item_code or 0), []))

    # ------------------------------------------------------------ subjects
    def find_subjects(self, text):
        """[(word, group, start, end)] for subject words (with group) and person words (unknown)."""
        if not text:
            return []
        found = []
        taken = []

        def scan(word, group):
            start = text.find(word)
            while start >= 0:
                end = start + len(word)
                if not any(start < te and ts < end for ts, te in taken):
                    taken.append((start, end))
                    found.append((word, group, start, end))
                start = text.find(word, start + 1)

        for word, group in self.subject_words:
            scan(word, group)
        for word in self.person_words:
            scan(word, "unknown")
        found.sort(key=lambda x: x[2])
        return found

    def subject_group_words(self, group):
        return [w for w, g in self.patterns["subject_words"].items() if g == group]


# Items that are only a fallback when a more specific item of the same category exists.
GENERIC_ITEMS = {310: 3, 503: 5, 1102: 11}


@lru_cache(maxsize=1)
def get_lexicon():
    data = json.loads((ROOT / "data" / "lexicon.json").read_text(encoding="utf-8"))
    return Lexicon(data)

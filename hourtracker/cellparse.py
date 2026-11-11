"""Parse and format the week-grid shorthand.

A grid cell holds the hours for one project on one day, written the same way
as in the old spreadsheet::

    8               8 h of the default category (Work)
    8+2 drive       8 h Work + 2 h Drive
    4+2 admin+1.5d  4 h Work + 2 h Admin + 1.5 h Drive
    1 TS            1 h Tech Support
    1:30            1.5 h Work
    45m / 45 min    0.75 h Work

Category words are matched against each category's name and short code plus a
few built-in aliases (d/driving, ts, ot...).  Text that cannot be understood is
returned so the caller can keep it as a note.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .model import Category, Entry, fmt_hours

BUILTIN_ALIASES = {
    "d": "Drive", "drive": "Drive", "driving": "Drive", "travel": "Drive",
    "admin": "Admin", "adm": "Admin",
    "ts": "Tech Support", "tech support": "Tech Support", "support": "Tech Support",
    "presale": "Presale", "pre-sale": "Presale", "sales": "Presale",
    "ot": "Overtime", "overtime": "Overtime",
    "work": "Work", "working": "Work", "w": "Work",
}

_TOKEN = re.compile(
    r"""^\s*
    (?:(?P<hh>\d+):(?P<mm>\d{1,2})          # 1:30
      |(?P<num>\d+(?:[.,]\d+)?|[.,]\d+)     # 8, 1.5, .5, 1,5
    )?
    \s*(?:(?P<unit>mins?|minutes?|m|hours?|hrs?|h)(?![a-z]))?
    \s*(?P<word>.*?)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class ParseResult:
    amounts: List[Tuple[str, float]] = field(default_factory=list)  # (category, hours)
    unknown: List[str] = field(default_factory=list)  # text that was not understood

    @property
    def total(self) -> float:
        return sum(h for _, h in self.amounts)

    @property
    def note(self) -> str:
        return "; ".join(self.unknown)


def build_aliases(categories: Iterable[Category]) -> Dict[str, str]:
    cats = list(categories)
    names = {c.name for c in cats}
    aliases = {k: v for k, v in BUILTIN_ALIASES.items() if v in names}
    for c in cats:
        aliases[c.name.lower()] = c.name
        if c.code:
            aliases[c.code.lower()] = c.name
    return aliases


def default_category(categories: Iterable[Category]) -> str:
    cats = list(categories)
    for c in cats:
        if not c.code:
            return c.name
    return cats[0].name if cats else "Work"


def parse_cell(text: str, categories: Iterable[Category]) -> ParseResult:
    cats = list(categories)
    aliases = build_aliases(cats)
    default = default_category(cats)
    result = ParseResult()
    text = (text or "").replace("*", "").strip()
    if not text:
        return result
    for raw in text.split("+"):
        token = raw.strip()
        if not token:
            continue
        m = _TOKEN.match(token)
        hours = None
        word = token
        if m and (m.group("num") or m.group("hh")):
            if m.group("hh"):
                hours = int(m.group("hh")) + int(m.group("mm")) / 60.0
            else:
                hours = float(m.group("num").replace(",", "."))
                unit = (m.group("unit") or "").lower()
                if unit.startswith("m"):
                    hours /= 60.0
            word = m.group("word")
        key = word.lower().strip()
        if hours is None:
            # no number at all ("x", "drive", "bethel") -> keep as note
            result.unknown.append(token)
            continue
        if not key:
            result.amounts.append((default, hours))
        elif key in aliases:
            result.amounts.append((aliases[key], hours))
        else:
            # "4?" or "4 something" -> keep the hours, remember the text
            result.amounts.append((default, hours))
            result.unknown.append(token)
    return result


def format_cell(entries: Iterable[Entry], categories: Iterable[Category]) -> str:
    """Turn the entries of one project/day back into shorthand ("8+2 drive")."""
    cats = list(categories)
    order = {c.name: i for i, c in enumerate(cats)}
    codes = {c.name: c.code for c in cats}
    totals: Dict[str, float] = {}
    for e in entries:
        totals[e.category] = totals.get(e.category, 0.0) + e.hours
    parts = []
    for cat in sorted(totals, key=lambda c: (order.get(c, 999), c)):
        h = totals[cat]
        if h == 0:
            continue
        code = codes.get(cat, cat)
        parts.append(fmt_hours(h) if not code else f"{fmt_hours(h)} {code}")
    return "+".join(parts)

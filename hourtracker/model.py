"""In-memory data model: projects, time entries and hour categories."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

STATUSES = ["Not Started", "Active", "Waiting", "On Hold", "Done"]

# Extra free-text project fields (attribute name, label) - mirrors the
# columns of the original spreadsheet.
PROJECT_TEXT_FIELDS: List[Tuple[str, str]] = [
    ("so", "SO"),
    ("po", "PO"),
    ("location", "Location"),
    ("contact", "Contact"),
    ("spec", "Spec"),
    ("code", "Code"),
    ("dates", "Dates"),
]


@dataclass
class Category:
    """A kind of hours (Work, Drive, Admin...).

    ``code`` is the short word used in the week grid shorthand, e.g. the
    ``drive`` in ``8+2 drive``.  The default category has an empty code, so a
    bare number means hours of that category.
    """

    name: str
    code: str = ""


DEFAULT_CATEGORIES = [
    Category("Work", ""),
    Category("Drive", "drive"),
    Category("Admin", "admin"),
    Category("Tech Support", "TS"),
    Category("Presale", "presale"),
    Category("Overtime", "OT"),
]


@dataclass
class Project:
    id: int
    company: str
    name: str
    status: str = "Active"
    archived: bool = False
    budget: Optional[float] = None  # quoted / planned hours
    so: str = ""
    po: str = ""
    location: str = ""
    contact: str = ""
    spec: str = ""
    code: str = ""
    dates: str = ""
    notes: str = ""

    @property
    def display(self) -> str:
        if self.company and self.name:
            return f"{self.company} - {self.name}"
        return self.name or self.company


@dataclass
class Entry:
    id: int
    date: dt.date
    project_id: int
    hours: float
    category: str = "Work"
    note: str = ""
    source: str = ""  # e.g. "excel:2026!M14" for imported entries


@dataclass
class TimeData:
    projects: Dict[int, Project] = field(default_factory=dict)
    entries: Dict[int, Entry] = field(default_factory=dict)
    categories: List[Category] = field(default_factory=lambda: [Category(c.name, c.code) for c in DEFAULT_CATEGORIES])
    next_project_id: int = 1
    next_entry_id: int = 1

    # ------------------------------------------------------------------ categories
    @property
    def default_category(self) -> str:
        for c in self.categories:
            if not c.code:
                return c.name
        return self.categories[0].name if self.categories else "Work"

    def category_names(self) -> List[str]:
        names = [c.name for c in self.categories]
        # categories used by entries but no longer configured
        for e in self.entries.values():
            if e.category not in names:
                names.append(e.category)
        return names

    def category_code(self, name: str) -> str:
        for c in self.categories:
            if c.name == name:
                return c.code
        return name

    # ------------------------------------------------------------------ projects
    def add_project(self, company: str, name: str, **kwargs) -> Project:
        p = Project(id=self.next_project_id, company=company.strip(), name=name.strip(), **kwargs)
        self.projects[p.id] = p
        self.next_project_id += 1
        return p

    def find_project(self, company: str, name: str) -> Optional[Project]:
        key = (company.strip().lower(), name.strip().lower())
        for p in self.projects.values():
            if (p.company.lower(), p.name.lower()) == key:
                return p
        return None

    def delete_project(self, project_id: int) -> None:
        for eid in [e.id for e in self.entries.values() if e.project_id == project_id]:
            del self.entries[eid]
        self.projects.pop(project_id, None)

    def companies(self) -> List[str]:
        return sorted({p.company for p in self.projects.values() if p.company}, key=str.lower)

    def sorted_projects(self, include_archived: bool = True) -> List[Project]:
        ps = [p for p in self.projects.values() if include_archived or not p.archived]
        return sorted(ps, key=lambda p: (p.company.lower(), p.name.lower()))

    def project_hours(self, project_id: int) -> float:
        return sum(e.hours for e in self.entries.values() if e.project_id == project_id)

    def project_last_date(self, project_id: int) -> Optional[dt.date]:
        dates = [e.date for e in self.entries.values() if e.project_id == project_id]
        return max(dates) if dates else None

    # ------------------------------------------------------------------ entries
    def add_entry(self, date: dt.date, project_id: int, hours: float, category: Optional[str] = None,
                  note: str = "", source: str = "") -> Entry:
        e = Entry(id=self.next_entry_id, date=date, project_id=project_id, hours=float(hours),
                  category=category or self.default_category, note=note, source=source)
        self.entries[e.id] = e
        self.next_entry_id += 1
        return e

    def delete_entry(self, entry_id: int) -> None:
        self.entries.pop(entry_id, None)

    def entries_for(self, project_id: int, date: dt.date) -> List[Entry]:
        return sorted((e for e in self.entries.values() if e.project_id == project_id and e.date == date),
                      key=lambda e: e.id)

    def entries_between(self, start: dt.date, end: dt.date) -> List[Entry]:
        return [e for e in self.entries.values() if start <= e.date <= end]

    def has_source(self, source: str) -> bool:
        return any(e.source == source for e in self.entries.values())

    def set_cell(self, project_id: int, date: dt.date, amounts: Iterable[Tuple[str, float]],
                 extra_note: str = "") -> None:
        """Replace the hours of one project/day (one grid cell).

        ``amounts`` is a list of (category, hours).  Existing entries of the same
        category are kept (so their notes survive) and their hours updated.
        """
        totals: Dict[str, float] = {}
        for cat, hours in amounts:
            totals[cat] = totals.get(cat, 0.0) + hours
        existing = self.entries_for(project_id, date)
        by_cat: Dict[str, List[Entry]] = {}
        for e in existing:
            by_cat.setdefault(e.category, []).append(e)
        for cat, hours in totals.items():
            olds = by_cat.pop(cat, [])
            if olds:
                keep = olds[0]
                keep.hours = hours
                notes = [o.note for o in olds if o.note]
                keep.note = "; ".join(notes)
                for o in olds[1:]:
                    self.delete_entry(o.id)
            else:
                keep = self.add_entry(date, project_id, hours, cat)
            if extra_note:
                keep.note = f"{keep.note}; {extra_note}" if keep.note else extra_note
                extra_note = ""
        for olds in by_cat.values():
            for o in olds:
                self.delete_entry(o.id)


def week_start(d: dt.date) -> dt.date:
    """Monday of the week containing ``d``."""
    return d - dt.timedelta(days=d.weekday())


def fmt_hours(h: float) -> str:
    """Format hours without pointless decimals: 8 -> '8', 1.5 -> '1.5'."""
    h = round(h, 2)
    if h == int(h):
        return str(int(h))
    return f"{h:.2f}".rstrip("0").rstrip(".")

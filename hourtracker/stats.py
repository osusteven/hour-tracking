"""Statistics: computed for the Statistics tab and written to <data>-statistics.xml."""
from __future__ import annotations

import calendar
import datetime as dt
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import MARKS, WEEKDAY_ORDER, TimeData, fmt_hours, week_start


@dataclass
class Bucket:
    """Hours for some grouping (a month, a project...)."""

    hours: float = 0.0
    by_category: Dict[str, float] = field(default_factory=lambda: defaultdict(float))
    dates: set = field(default_factory=set)
    entries: int = 0

    def add(self, e) -> None:
        self.hours += e.hours
        self.by_category[e.category] += e.hours
        self.dates.add(e.date)
        self.entries += 1

    @property
    def days(self) -> int:
        return len(self.dates)

    @property
    def first(self) -> Optional[dt.date]:
        return min(self.dates) if self.dates else None

    @property
    def last(self) -> Optional[dt.date]:
        return max(self.dates) if self.dates else None

    @property
    def avg_per_day(self) -> float:
        return self.hours / self.days if self.days else 0.0


@dataclass
class Stats:
    year: Optional[int]
    categories: List[str]
    total: Bucket
    years: Dict[int, Bucket]
    months: Dict[tuple, Bucket]  # (year, month)
    weeks: Dict[dt.date, Bucket]  # sunday that starts the week
    companies: Dict[str, Bucket]
    projects: Dict[int, Bucket]
    weekdays: Dict[int, Bucket]  # 0 = Monday
    marks: Dict[str, Bucket]  # "" = not coloured
    summary: Dict[str, float]


def compute(data: TimeData, year: Optional[int] = None, today: Optional[dt.date] = None) -> Stats:
    """Compute statistics, optionally limited to one calendar year.

    The ``summary`` (this week / month / year...) is always relative to ``today``.
    """
    today = today or dt.date.today()
    total = Bucket()
    years: Dict[int, Bucket] = defaultdict(Bucket)
    months: Dict[tuple, Bucket] = defaultdict(Bucket)
    weeks: Dict[dt.date, Bucket] = defaultdict(Bucket)
    companies: Dict[str, Bucket] = defaultdict(Bucket)
    projects: Dict[int, Bucket] = defaultdict(Bucket)
    weekdays: Dict[int, Bucket] = defaultdict(Bucket)
    marks: Dict[str, Bucket] = defaultdict(Bucket)

    for e in data.entries.values():
        if year is not None and e.date.year != year:
            continue
        p = data.projects.get(e.project_id)
        total.add(e)
        years[e.date.year].add(e)
        months[(e.date.year, e.date.month)].add(e)
        weeks[week_start(e.date)].add(e)
        companies[p.company if p else "?"].add(e)
        projects[e.project_id].add(e)
        weekdays[e.date.weekday()].add(e)
        marks[e.mark].add(e)

    def hours_between(a: dt.date, b: dt.date) -> float:
        return sum(e.hours for e in data.entries.values() if a <= e.date <= b)

    ws = week_start(today)
    month_start = today.replace(day=1)
    last_month_end = month_start - dt.timedelta(days=1)
    summary = {
        "today": hours_between(today, today),
        "thisWeek": hours_between(ws, ws + dt.timedelta(days=6)),
        "lastWeek": hours_between(ws - dt.timedelta(days=7), ws - dt.timedelta(days=1)),
        "thisMonth": hours_between(month_start, today.replace(day=calendar.monthrange(today.year, today.month)[1])),
        "lastMonth": hours_between(last_month_end.replace(day=1), last_month_end),
        "thisYear": hours_between(dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)),
    }

    return Stats(
        year=year,
        categories=data.category_names(),
        total=total,
        years=dict(years),
        months=dict(months),
        weeks=dict(weeks),
        companies=dict(companies),
        projects=dict(projects),
        weekdays=dict(weekdays),
        marks=dict(marks),
        summary=summary,
    )


# ---------------------------------------------------------------------- XML output
def _h(x: float) -> str:
    return fmt_hours(x)


def _bucket_attrs(b: Bucket) -> Dict[str, str]:
    attrs = {"hours": _h(b.hours), "days": str(b.days), "entries": str(b.entries),
             "avgPerDay": _h(b.avg_per_day)}
    if b.first:
        attrs["first"] = b.first.isoformat()
        attrs["last"] = b.last.isoformat()
    return attrs


def _add_categories(parent: ET.Element, b: Bucket, categories: List[str]) -> None:
    for cat in categories:
        if b.by_category.get(cat):
            ET.SubElement(parent, "category", name=cat, hours=_h(b.by_category[cat]))


def stats_xml(data: TimeData, today: Optional[dt.date] = None) -> ET.Element:
    s = compute(data, today=today)
    cats = s.categories
    root = ET.Element("hourTrackerStatistics",
                      generated=dt.datetime.now().replace(microsecond=0).isoformat())

    summ = ET.SubElement(root, "summary", {k: _h(v) for k, v in s.summary.items()})
    summ.set("totalHours", _h(s.total.hours))
    summ.set("daysWorked", str(s.total.days))
    summ.set("avgPerWorkedDay", _h(s.total.avg_per_day))
    if s.weeks:
        summ.set("avgPerWorkedWeek", _h(s.total.hours / len(s.weeks)))
    if s.total.first:
        summ.set("firstDate", s.total.first.isoformat())
        summ.set("lastDate", s.total.last.isoformat())

    by_cat = ET.SubElement(root, "byCategory")
    _add_categories(by_cat, s.total, cats)

    years_el = ET.SubElement(root, "years")
    for y in sorted(s.years):
        y_el = ET.SubElement(years_el, "year", value=str(y), **_bucket_attrs(s.years[y]))
        _add_categories(y_el, s.years[y], cats)
        for (yy, m) in sorted(k for k in s.months if k[0] == y):
            mb = s.months[(yy, m)]
            m_el = ET.SubElement(y_el, "month", value=str(m), name=calendar.month_name[m], **_bucket_attrs(mb))
            _add_categories(m_el, mb, cats)

    weeks_el = ET.SubElement(root, "weeks")
    for sunday in sorted(s.weeks):
        wb = s.weeks[sunday]
        w_el = ET.SubElement(weeks_el, "week", start=sunday.isoformat(),
                             end=(sunday + dt.timedelta(days=6)).isoformat(), **_bucket_attrs(wb))
        _add_categories(w_el, wb, cats)

    comp_el = ET.SubElement(root, "companies")
    for name in sorted(s.companies, key=str.lower):
        c_el = ET.SubElement(comp_el, "company", name=name, **_bucket_attrs(s.companies[name]))
        _add_categories(c_el, s.companies[name], cats)

    proj_el = ET.SubElement(root, "projects")
    for p in data.sorted_projects():
        b = s.projects.get(p.id, Bucket())
        attrs = {"id": str(p.id), "company": p.company, "name": p.name, "status": p.status, **_bucket_attrs(b)}
        if p.budget is not None:
            attrs["budget"] = _h(p.budget)
            attrs["remaining"] = _h(p.budget - b.hours)
            if p.budget:
                attrs["percentUsed"] = _h(100.0 * b.hours / p.budget)
        p_el = ET.SubElement(proj_el, "project", attrs)
        _add_categories(p_el, b, cats)
        for y in sorted({d.year for d in b.dates}):
            ET.SubElement(p_el, "yearTotal", value=str(y),
                          hours=_h(sum(e.hours for e in data.entries.values()
                                       if e.project_id == p.id and e.date.year == y)))

    marks_el = ET.SubElement(root, "byMark")
    for m in MARKS + [""]:
        b = s.marks.get(m, Bucket())
        ET.SubElement(marks_el, "mark", value=m or "none", hours=_h(b.hours), entries=str(b.entries))

    wd_el = ET.SubElement(root, "weekdays")
    for i in WEEKDAY_ORDER:
        b = s.weekdays.get(i, Bucket())
        ET.SubElement(wd_el, "weekday", name=calendar.day_name[i], **_bucket_attrs(b))
    return root

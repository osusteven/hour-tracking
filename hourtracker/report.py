"""Plain text statistics report (<data>-statistics.txt), easy to read in Notepad."""
from __future__ import annotations

import calendar
import datetime as dt
from typing import List, Optional, Sequence

from .model import MARK_LABELS, MARKS, WEEKDAY_ORDER, TimeData, week_start
from .stats import Bucket, compute

FULL_DAY = 8.0
WIDTH = 78


def h(x: float) -> str:
    """Hours with one decimal so columns line up: 8 -> '8.0'."""
    return f"{x:,.1f}"


def table(headers: Sequence[str], rows: List[Sequence], right: Optional[Sequence[bool]] = None,
          total: Optional[Sequence] = None) -> List[str]:
    """Simple fixed-width text table. First column left aligned, the rest right aligned."""
    right = right or [i > 0 for i in range(len(headers))]
    all_rows = [list(map(str, r)) for r in rows]
    tot = list(map(str, total)) if total else None
    widths = [len(x) for x in headers]
    for r in all_rows + ([tot] if tot else []):
        widths = [max(w, len(c)) for w, c in zip(widths, r)]

    def fmt(r):
        cells = [c.rjust(w) if rt else c.ljust(w) for c, w, rt in zip(r, widths, right)]
        return "  " + "  ".join(cells).rstrip()

    rule = "  " + "  ".join("-" * w for w in widths)
    lines = [fmt(headers), rule] + [fmt(r) for r in all_rows]
    if tot:
        lines += [rule, fmt(tot)]
    return lines


def section(title: str) -> List[str]:
    return ["", "", title.upper(), "=" * len(title)]


def _sum(entries) -> float:
    return sum(e.hours for e in entries)


def stats_text(data: TimeData, today: Optional[dt.date] = None, data_file: str = "") -> str:
    today = today or dt.date.today()
    year = today.year
    now = dt.datetime.now()
    entries = list(data.entries.values())
    cats_all = data.category_names()

    def between(a, b):
        return [e for e in entries if a <= e.date <= b]

    def pname(pid):
        p = data.projects.get(pid)
        return p.display if p else "?"

    ws = week_start(today)
    we = ws + dt.timedelta(days=6)
    lws, lwe = ws - dt.timedelta(days=7), ws - dt.timedelta(days=1)
    ms = today.replace(day=1)
    me = today.replace(day=calendar.monthrange(year, today.month)[1])
    lme = ms - dt.timedelta(days=1)
    lms = lme.replace(day=1)
    ys, ye = dt.date(year, 1, 1), dt.date(year, 12, 31)

    out: List[str] = []
    title = "HOUR TRACKER - STATISTICS"
    out += [title, "=" * len(title),
            f"Generated: {now:%A, %B %d, %Y  %I:%M %p}".replace(" 0", " "),
            ]
    if data_file:
        out.append(f"Data file: {data_file}")

    # ------------------------------------------------------------------ summary
    out += section("Summary")
    all_s = compute(data, today=today)

    def days_worked(es):
        return len({e.date for e in es})

    def full_days(es):
        per = {}
        for e in es:
            per[e.date] = per.get(e.date, 0) + e.hours
        return sum(1 for v in per.values() if v >= FULL_DAY)

    rows = []
    for label, a, b in [
        (f"Today ({today:%a %m/%d})", today, today),
        (f"This week ({ws:%m/%d} - {we:%m/%d})", ws, we),
        (f"Last week ({lws:%m/%d} - {lwe:%m/%d})", lws, lwe),
        (f"This month ({today:%B})", ms, me),
        (f"Last month ({lme:%B})", lms, lme),
        (f"This year ({year})", ys, ye),
        ("All time", dt.date.min, dt.date.max),
    ]:
        es = between(a, b)
        tot = _sum(es)
        dw = days_worked(es)
        not_green = _sum(e for e in es if data.entry_mark(e) != "green")
        rows.append([label, h(tot), dw, h(tot / dw) if dw else "-", full_days(es), h(not_green)])
    out += table(["Period", "Hours", "Days", "Avg/day", f"{FULL_DAY:g}h+ days", "Not green"], rows)
    if all_s.total.first:
        out.append("")
        out.append(f"  First entry {all_s.total.first:%m/%d/%Y}, last entry {all_s.total.last:%m/%d/%Y}.  "
                   f"Average per week worked: {h(all_s.total.hours / max(1, len(all_s.weeks)))} h")

    # ------------------------------------------------------------------ this week
    out += section(f"This week: {ws:%a %m/%d} - {we:%a %m/%d/%Y}")
    days = [ws + dt.timedelta(days=i) for i in range(7)]
    week_es = between(ws, we)
    pids = sorted({e.project_id for e in week_es}, key=lambda p: pname(p).lower())
    if pids:
        rows = []
        for pid in pids:
            per = [_sum(e for e in week_es if e.project_id == pid and e.date == d) for d in days]
            rows.append([pname(pid)] + [h(x) if x else "." for x in per] + [h(sum(per))])
        per_day = [_sum(e for e in week_es if e.date == d) for d in days]
        total = ["TOTAL"] + [h(x) + ("*" if x >= FULL_DAY else " ") if x else ". " for x in per_day] + \
                [h(sum(per_day))]
        rows = [r[:1] + [c + " " for c in r[1:8]] + r[8:] for r in rows]
        out += table(["Project"] + [f"{d:%a} " for d in days] + ["Total"], rows, total=total)
        out.append(f"  * = {FULL_DAY:g} hours or more that day")
    else:
        out.append("  No hours yet this week.")

    # ------------------------------------------------------------------ colors
    out += section(f"Colors ({year})")
    yes = between(ys, ye)
    rows = []
    for m in MARKS + [""]:
        es = [e for e in yes if data.entry_mark(e) == m]
        rows.append([MARK_LABELS[m], h(_sum(es)), days_worked(es)])
    out += table(["Color", "Hours", "Days"], rows)
    open_weeks = {}
    for e in yes:
        if data.entry_mark(e) != "green" and e.date <= today:
            ws_e = week_start(e.date)
            open_weeks[ws_e] = open_weeks.get(ws_e, 0) + e.hours
    if open_weeks:
        out.append("")
        out.append("  Weeks with hours that are not green (not submitted):")
        out += table(["Week", "Hours"],
                     [[f"{w:%m/%d} - {w + dt.timedelta(days=6):%m/%d/%Y}", h(x)]
                      for w, x in sorted(open_weeks.items(), reverse=True)])

    # ------------------------------------------------------------------ year by month
    ys_stats = compute(data, year=year, today=today)
    cats = [c for c in cats_all if ys_stats.total.by_category.get(c)]

    def catcols(b: Bucket):
        return [h(b.by_category.get(c, 0)) if b.by_category.get(c) else "-" for c in cats]

    out += section(f"{year} by month")
    rows = []
    for m in range(1, 13):
        b = ys_stats.months.get((year, m))
        if b:
            rows.append([calendar.month_name[m], h(b.hours), b.days, h(b.avg_per_day)] + catcols(b))
    if rows:
        t = ys_stats.total
        out += table(["Month", "Hours", "Days", "Avg/day"] + cats, rows,
                     total=["Total", h(t.hours), t.days, h(t.avg_per_day)] + catcols(t))
    else:
        out.append(f"  No hours in {year}.")

    # ------------------------------------------------------------------ year by week
    out += section(f"{year} by week")
    rows = []
    for w, b in sorted(ys_stats.weeks.items(), reverse=True):
        rows.append([f"{w:%m/%d} - {w + dt.timedelta(days=6):%m/%d}", h(b.hours), b.days, h(b.avg_per_day),
                     "#" * int(round(b.hours / 2))])
    if rows:
        out += table(["Week (Sun-Sat)", "Hours", "Days", "Avg/day", "(# = 2 h)"], rows,
                     right=[False, True, True, True, False])

    # ------------------------------------------------------------------ year by project / company
    out += section(f"{year} by project")
    rows = []
    for pid, b in sorted(ys_stats.projects.items(), key=lambda kv: -kv[1].hours):
        rows.append([pname(pid), h(b.hours), b.days, f"{b.last:%m/%d}"])
    if rows:
        out += table(["Project", "Hours", "Days", "Last"], rows, total=["Total", h(ys_stats.total.hours), "", ""])

    out += section(f"{year} by company")
    rows = [[name, h(b.hours), b.days, f"{100 * b.hours / ys_stats.total.hours:.0f}%"]
            for name, b in sorted(ys_stats.companies.items(), key=lambda kv: -kv[1].hours)]
    if rows:
        out += table(["Company", "Hours", "Days", "Share"], rows)

    out += section(f"{year} by weekday")
    rows = []
    for i in WEEKDAY_ORDER:
        b = ys_stats.weekdays.get(i, Bucket())
        rows.append([calendar.day_name[i], h(b.hours), b.days, h(b.avg_per_day) if b.days else "-"])
    out += table(["Weekday", "Hours", "Days", "Avg/day"], rows)

    # ------------------------------------------------------------------ all time
    out += section("All years")
    rows = [[y, h(b.hours), b.days, h(b.avg_per_day)] for y, b in sorted(all_s.years.items(), reverse=True)]
    if rows:
        out += table(["Year", "Hours", "Days", "Avg/day"], rows,
                     total=["Total", h(all_s.total.hours), all_s.total.days, h(all_s.total.avg_per_day)])

    out += section("Projects - all time (active projects first)")
    rows = []
    for p in sorted(data.projects.values(), key=lambda p: (p.archived, p.company.lower(), p.name.lower())):
        b = all_s.projects.get(p.id)
        if not b and p.budget is None:
            continue
        hours = b.hours if b else 0.0
        budget = h(p.budget) if p.budget is not None else "-"
        left = h(p.budget - hours) if p.budget is not None else "-"
        used = f"{100 * hours / p.budget:.0f}%" if p.budget else "-"
        rows.append([p.display + (" (archived)" if p.archived else ""), p.status, h(hours), budget, left, used,
                     f"{b.last:%m/%d/%Y}" if b else "-"])
    if rows:
        out += table(["Project", "Status", "Hours", "Budget", "Left", "Used", "Last worked"], rows,
                     right=[False, False, True, True, True, True, True])

    out.append("")
    return "\n".join(out)

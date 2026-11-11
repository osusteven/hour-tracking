"""Import the hours grid from the old Excel workbook (needs ``openpyxl``).

Recognised sheet layout (the "2026", "2025" and "old" sheets):

* one row near the top holds dates (one column per day),
* a header row contains "Project" plus optional "Hours", "SO", "PO",
  "Location", "Contact", "Spec", "Code", "Dates", "Status" columns,
* the column left of "Project" holds the company (or a section label such as
  "IAS" that applies to the rows below it),
* every cell below a date is the hours for that project/day, either a number
  or shorthand such as ``8+2 drive``.

Re-importing the same workbook is safe: every imported entry remembers the
sheet and cell it came from and is not imported twice.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .cellparse import parse_cell
from .model import PROJECT_TEXT_FIELDS, TimeData

HEADER_MAP = {label.lower(): attr for attr, label in PROJECT_TEXT_FIELDS}
HEADER_MAP.update({"hours": "budget", "status": "status", "project": "name"})


ARCHIVE_AFTER_DAYS = 60


class ImportError_(Exception):
    pass


@dataclass
class SheetInfo:
    name: str
    is_grid: bool
    first_date: Optional[dt.date] = None
    last_date: Optional[dt.date] = None
    filled_cells: int = 0


@dataclass
class ImportReport:
    sheets: List[str] = field(default_factory=list)
    projects_added: int = 0
    entries_added: int = 0
    hours_added: float = 0.0
    already_imported: int = 0
    skipped: List[str] = field(default_factory=list)  # "sheet!A1: text"
    notes_kept: int = 0
    archived: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Sheets imported: {', '.join(self.sheets) or 'none'}",
            f"Projects added: {self.projects_added}",
            f"Entries added: {self.entries_added} ({self.hours_added:g} h)",
        ]
        if self.already_imported:
            lines.append(f"Cells already imported earlier (skipped): {self.already_imported}")
        if self.archived:
            lines.append(f"Projects archived because they have no hours in the last {ARCHIVE_AFTER_DAYS} days "
                         f"(Projects tab > Archive / Restore to bring one back): {len(self.archived)}")
        if self.notes_kept:
            lines.append(f"Cells with extra text kept as an entry note: {self.notes_kept}")
        if self.skipped:
            lines.append(f"Cells without any hours (not imported): {len(self.skipped)}")
            lines.extend("    " + s for s in self.skipped[:25])
            if len(self.skipped) > 25:
                lines.append(f"    ... and {len(self.skipped) - 25} more")
        return "\n".join(lines)


def _openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError_(
            "Importing Excel files needs the 'openpyxl' package.\n\n"
            "Install it once with:\n    py -m pip install openpyxl\n"
            "(or: python3 -m pip install openpyxl) and try again."
        ) from exc
    return openpyxl


def _as_date(v) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return None


@dataclass
class _Layout:
    date_row: int
    header_row: int
    project_col: int
    company_col: Optional[int]
    meta_cols: Dict[int, str]
    date_cols: Dict[int, dt.date]


def _find_layout(ws_values, ws_formulas) -> Optional[_Layout]:
    max_scan = min(ws_values.max_row, 8)
    header_row = project_col = None
    for r in range(1, max_scan + 1):
        for c in range(1, min(ws_values.max_column, 40) + 1):
            v = ws_values.cell(r, c).value
            if isinstance(v, str) and v.strip().lower() == "project":
                header_row, project_col = r, c
                break
        if header_row:
            break
    if not header_row:
        return None

    # the date row: the row (near the top) with the most date values
    best_row, best_cols = None, {}
    for r in range(1, max_scan + 1):
        cols: Dict[int, dt.date] = {}
        prev: Optional[dt.date] = None
        for c in range(project_col + 1, ws_values.max_column + 1):
            d = _as_date(ws_values.cell(r, c).value)
            if d is None and prev is not None:
                # value not cached (file not saved by Excel): "=M2+1" style formula
                f = ws_formulas.cell(r, c).value
                if isinstance(f, str) and f.startswith("=") and f.replace(" ", "").endswith("+1"):
                    d = prev + dt.timedelta(days=1)
            if d is not None:
                cols[c] = d
                prev = d
        if len(cols) > len(best_cols):
            best_row, best_cols = r, cols
    if not best_row or len(best_cols) < 3:
        return None

    first_date_col = min(best_cols)
    meta: Dict[int, str] = {}
    for c in range(project_col + 1, first_date_col):
        v = ws_values.cell(header_row, c).value
        if isinstance(v, str) and v.strip().lower() in HEADER_MAP:
            meta[c] = HEADER_MAP[v.strip().lower()]
    return _Layout(best_row, header_row, project_col, project_col - 1 if project_col > 1 else None,
                   meta, best_cols)


def list_sheets(path: str) -> List[SheetInfo]:
    openpyxl = _openpyxl()
    wb_v = openpyxl.load_workbook(path, data_only=True)
    wb_f = openpyxl.load_workbook(path)
    infos = []
    for name in wb_v.sheetnames:
        lay = _find_layout(wb_v[name], wb_f[name])
        if not lay:
            infos.append(SheetInfo(name, False))
            continue
        ws = wb_v[name]
        filled = 0
        for row in ws.iter_rows(min_row=lay.header_row + 1, min_col=min(lay.date_cols), max_col=max(lay.date_cols)):
            filled += sum(1 for c in row if c.value not in (None, ""))
        infos.append(SheetInfo(name, True, min(lay.date_cols.values()), max(lay.date_cols.values()), filled))
    return infos


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def import_workbook(path: str, data: TimeData, sheets: Optional[List[str]] = None,
                    today: Optional[dt.date] = None) -> ImportReport:
    openpyxl = _openpyxl()
    from openpyxl.utils import get_column_letter

    wb_v = openpyxl.load_workbook(path, data_only=True)
    wb_f = openpyxl.load_workbook(path)
    report = ImportReport()
    new_projects: List[int] = []
    for name in wb_v.sheetnames:
        if sheets is not None and name not in sheets:
            continue
        ws = wb_v[name]
        lay = _find_layout(ws, wb_f[name])
        if not lay:
            continue
        report.sheets.append(name)

        section = ""
        if lay.company_col:
            v = ws.cell(lay.header_row, lay.company_col).value
            if isinstance(v, str) and v.strip().lower() != "company":
                section = v.strip()

        for r in range(lay.header_row + 1, ws.max_row + 1):
            company = section
            own_company = ""
            if lay.company_col:
                v = ws.cell(r, lay.company_col).value
                if isinstance(v, str) and v.strip():
                    company = section = own_company = v.strip()
            pname = _text(ws.cell(r, lay.project_col).value)

            cells = []
            for c, day in lay.date_cols.items():
                v = ws.cell(r, c).value
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                cells.append((c, day, v))
            if not cells:
                continue
            if not pname and not own_company:
                # a free-text row below the projects (reminders etc.)
                for c, day, v in cells:
                    report.skipped.append(f"{name}!{get_column_letter(c)}{r} (no project, {day.isoformat()}): {v}")
                continue
            if not pname:
                pname = own_company

            project = None
            for c, day, v in cells:
                ref = f"{get_column_letter(c)}{r}"
                source = f"excel:{name}!{ref}"
                if data.has_source(source):
                    report.already_imported += 1
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    amounts, note = [(data.default_category, float(v))], ""
                else:
                    res = parse_cell(str(v), data.categories)
                    amounts = res.amounts
                    note = f"Imported '{v}'" if res.unknown else ""
                amounts = [(cat, h) for cat, h in amounts if h]
                if not amounts:
                    report.skipped.append(f"{name}!{ref} ({pname}, {day.isoformat()}): {v}")
                    continue
                if project is None:
                    project = _get_project(data, report, company, pname, ws, r, lay, new_projects)
                if note:
                    report.notes_kept += 1
                for i, (cat, h) in enumerate(amounts):
                    data.add_entry(day, project.id, h, cat, note=note if i == 0 else "", source=source)
                    report.entries_added += 1
                    report.hours_added += h

    # keep the week grid tidy: hide newly created projects nobody worked on lately
    cutoff = (today or dt.date.today()) - dt.timedelta(days=ARCHIVE_AFTER_DAYS)
    for pid in new_projects:
        last = data.project_last_date(pid)
        if last is not None and last < cutoff:
            data.projects[pid].archived = True
            report.archived.append(data.projects[pid].display)
    return report


def _get_project(data: TimeData, report: ImportReport, company: str, pname: str, ws, r: int, lay: _Layout,
                 new_projects: List[int]):
    project = data.find_project(company, pname)
    if project is None:
        project = data.add_project(company, pname, status="Active")
        report.projects_added += 1
        new_projects.append(project.id)
    for c, attr in lay.meta_cols.items():
        v = ws.cell(r, c).value
        if v in (None, ""):
            continue
        if attr == "budget":
            if project.budget is None and isinstance(v, (int, float)):
                project.budget = float(v)
        elif attr == "status":
            if project.status in ("", "Active"):
                project.status = _text(v)
        elif not getattr(project, attr):
            setattr(project, attr, _text(v))
    return project

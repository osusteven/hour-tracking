"""Dialog windows: project editor, entry editor, cell details, categories, import."""
from __future__ import annotations

import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from .cellparse import parse_cell
from .model import MARKS, PROJECT_TEXT_FIELDS, STATUSES, Category, Entry, Project, TimeData, fmt_hours

MARK_CHOICES = ["", *MARKS]


def parse_date(text: str) -> dt.date:
    """Accept 2026-11-11, 11/11/2026, 11/11/26 and 11/11 (current year)."""
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return dt.datetime.strptime(f"{text}/{dt.date.today().year}", "%m/%d/%Y").date()
    except ValueError:
        raise ValueError(f"Can't read the date '{text}'. Use YYYY-MM-DD or MM/DD/YYYY.")


def parse_hours(text: str, categories: List[Category]) -> float:
    res = parse_cell(text, categories)
    if not res.amounts or res.unknown:
        raise ValueError(f"Can't read the hours '{text}'. Examples: 2, 1.5, 1:30, 45m")
    return res.total


class Dialog(tk.Toplevel):
    """Small modal dialog base class with OK/Cancel buttons."""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, False)
        self.result = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.build(self.body)
        btns = ttk.Frame(self, padding=(12, 0, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok, default="active").pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - self.winfo_reqwidth()) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.grab_set()
        self.focus_initial()

    def build(self, body):  # pragma: no cover - overridden
        pass

    def focus_initial(self):
        pass

    def validate(self):
        """Return the result or raise ValueError with a message for the user."""
        return True

    def _ok(self, event=None):
        try:
            self.result = self.validate()
        except ValueError as exc:
            messagebox.showerror("Please check", str(exc), parent=self)
            return
        self.destroy()

    def run(self):
        self.wait_window()
        return self.result


# ---------------------------------------------------------------------- project
class ProjectDialog(Dialog):
    def __init__(self, parent, data: TimeData, project: Optional[Project] = None, company: str = ""):
        self.data = data
        self.project = project
        self.default_company = company
        super().__init__(parent, "Edit project" if project else "New project")

    def build(self, body):
        p = self.project
        body.columnconfigure(1, weight=1)
        row = 0

        def label(text):
            ttk.Label(body, text=text).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)

        label("Company")
        self.company = ttk.Combobox(body, values=self.data.companies(), width=40)
        self.company.set(p.company if p else self.default_company)
        self.company.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        label("Project")
        self.name = ttk.Entry(body, width=42)
        self.name.insert(0, p.name if p else "")
        self.name.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        label("Status")
        self.status = ttk.Combobox(body, values=STATUSES, width=40)
        self.status.set(p.status if p else "Active")
        self.status.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        label("Budget hours")
        self.budget = ttk.Entry(body, width=42)
        self.budget.insert(0, fmt_hours(p.budget) if p and p.budget is not None else "")
        self.budget.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        self.fields = {}
        for attr, text in PROJECT_TEXT_FIELDS:
            label(text)
            e = ttk.Entry(body, width=42)
            e.insert(0, getattr(p, attr) if p else "")
            e.grid(row=row, column=1, sticky="ew", pady=2)
            self.fields[attr] = e
            row += 1
        label("Notes")
        self.notes = tk.Text(body, width=42, height=4, wrap="word")
        self.notes.insert("1.0", p.notes if p else "")
        self.notes.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        self.archived = tk.BooleanVar(value=bool(p and p.archived))
        ttk.Checkbutton(body, text="Archived (hide from the week grid)", variable=self.archived).grid(
            row=row, column=1, sticky="w", pady=(6, 0))
        self.bind("<Return>", lambda e: None if isinstance(e.widget, tk.Text) else self._ok())

    def focus_initial(self):
        (self.name if self.company.get() else self.company).focus_set()

    def validate(self):
        company = self.company.get().strip()
        name = self.name.get().strip()
        if not name and not company:
            raise ValueError("Enter a project name.")
        other = self.data.find_project(company, name)
        if other and (self.project is None or other.id != self.project.id):
            raise ValueError(f"The project '{other.display}' already exists.")
        btxt = self.budget.get().strip()
        budget = None
        if btxt:
            try:
                budget = float(btxt)
            except ValueError:
                raise ValueError("Budget hours must be a number.")
        values = {
            "company": company,
            "name": name or company,
            "status": self.status.get().strip() or "Active",
            "budget": budget,
            "archived": self.archived.get(),
            "notes": self.notes.get("1.0", "end").strip(),
        }
        for attr, e in self.fields.items():
            values[attr] = e.get().strip()
        if self.project is None:
            p = self.data.add_project(values.pop("company"), values.pop("name"), **values)
        else:
            p = self.project
            for k, v in values.items():
                setattr(p, k, v)
        return p


# ---------------------------------------------------------------------- one entry
def project_choices(data: TimeData, include: Optional[int] = None):
    projects = [p for p in data.sorted_projects() if not p.archived or p.id == include]
    return projects, [p.display for p in projects]


class EntryDialog(Dialog):
    def __init__(self, parent, data: TimeData, entry: Optional[Entry] = None, date: Optional[dt.date] = None,
                 project_id: Optional[int] = None):
        self.data = data
        self.entry = entry
        self.default_date = date or dt.date.today()
        self.default_project = project_id
        super().__init__(parent, "Edit entry" if entry else "New entry")

    def build(self, body):
        e = self.entry
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Date").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        df = ttk.Frame(body)
        df.grid(row=0, column=1, sticky="ew", pady=2)
        self.date = ttk.Entry(df, width=14)
        self.date.insert(0, (e.date if e else self.default_date).isoformat())
        self.date.pack(side="left")
        self.weekday = ttk.Label(df, foreground="gray")
        self.weekday.pack(side="left", padx=6)
        ttk.Button(df, text="Today", width=7, command=self._today).pack(side="left")
        self.date.bind("<KeyRelease>", lambda ev: self._show_weekday())
        self._show_weekday()

        ttk.Label(body, text="Project").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        pid = e.project_id if e else self.default_project
        self.projects, names = project_choices(self.data, pid)
        self.project = ttk.Combobox(body, values=names, state="readonly", width=48)
        for i, p in enumerate(self.projects):
            if p.id == pid:
                self.project.current(i)
        self.project.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(body, text="Category").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        self.category = ttk.Combobox(body, values=self.data.category_names(), width=20)
        self.category.set(e.category if e else self.data.default_category)
        self.category.grid(row=2, column=1, sticky="w", pady=2)

        ttk.Label(body, text="Hours").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        hf = ttk.Frame(body)
        hf.grid(row=3, column=1, sticky="ew", pady=2)
        self.hours = ttk.Entry(hf, width=10)
        self.hours.insert(0, fmt_hours(e.hours) if e else "")
        self.hours.pack(side="left")
        ttk.Label(hf, text="e.g. 2, 1.5, 1:30, 45m", foreground="gray").pack(side="left", padx=6)

        ttk.Label(body, text="Color").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=2)
        mf = ttk.Frame(body)
        mf.grid(row=4, column=1, sticky="ew", pady=2)
        self.mark = ttk.Combobox(mf, values=MARK_CHOICES, state="readonly", width=10)
        self.mark.set(self.data.entry_mark(e) if e else "")
        self.mark.pack(side="left")
        ttk.Label(mf, text="(colors this project's whole day)", foreground="gray").pack(side="left", padx=6)

        ttk.Label(body, text="Note").grid(row=5, column=0, sticky="nw", padx=(0, 8), pady=2)
        self.note = tk.Text(body, width=50, height=5, wrap="word")
        self.note.insert("1.0", e.note if e else "")
        self.note.grid(row=5, column=1, sticky="ew", pady=2)
        self.bind("<Return>", lambda ev: None if isinstance(ev.widget, tk.Text) else self._ok())

    def focus_initial(self):
        (self.hours if self.project.get() else self.project).focus_set()

    def _today(self):
        self.date.delete(0, "end")
        self.date.insert(0, dt.date.today().isoformat())
        self._show_weekday()

    def _show_weekday(self):
        try:
            self.weekday.config(text=parse_date(self.date.get()).strftime("%A"))
        except ValueError:
            self.weekday.config(text="")

    def validate(self):
        date = parse_date(self.date.get())
        idx = self.project.current()
        if idx < 0:
            raise ValueError("Choose a project.")
        hours = parse_hours(self.hours.get(), self.data.categories)
        category = self.category.get().strip() or self.data.default_category
        note = self.note.get("1.0", "end").strip()
        pid = self.projects[idx].id
        self.data.set_mark(pid, date, self.mark.get())
        if self.entry is None:
            return self.data.add_entry(date, pid, hours, category, note)
        e = self.entry
        e.date, e.project_id, e.hours, e.category, e.note = date, pid, hours, category, note
        return e


# ---------------------------------------------------------------------- one grid cell
class CellDialog(Dialog):
    """Edit every entry (with notes) of one project on one day."""

    def __init__(self, parent, data: TimeData, project: Project, date: dt.date):
        self.data = data
        self.project = project
        self.date = date
        self.rows = []
        super().__init__(parent, f"{project.display} - {date:%a %m/%d/%Y}")

    def build(self, body):
        ttk.Label(body, text=f"{self.project.display}\n{self.date:%A, %B %d, %Y}",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.grid_frame = ttk.Frame(body)
        self.grid_frame.pack(fill="both", expand=True)
        for c, text in enumerate(["Category", "Hours", "Note", ""]):
            ttk.Label(self.grid_frame, text=text, foreground="gray").grid(row=0, column=c, sticky="w")
        self.grid_frame.columnconfigure(2, weight=1)
        for e in self.data.entries_for(self.project.id, self.date):
            self._add_row(e.category, fmt_hours(e.hours), e.note)
        if not self.rows:
            self._add_row(self.data.default_category, "", "")
        ttk.Button(body, text="+ Add line", command=lambda: self._add_row(self.data.default_category, "", "")).pack(
            anchor="w", pady=(6, 0))
        mf = ttk.Frame(body)
        mf.pack(anchor="w", pady=(10, 0))
        ttk.Label(mf, text="Color").pack(side="left")
        self.mark = ttk.Combobox(mf, values=MARK_CHOICES, state="readonly", width=10)
        self.mark.set(self.data.get_mark(self.project.id, self.date))
        self.mark.pack(side="left", padx=6)
        self.bind("<Return>", lambda ev: self._ok())

    def _add_row(self, cat, hours, note):
        r = len(self.rows) + 1
        cb = ttk.Combobox(self.grid_frame, values=self.data.category_names(), width=14)
        cb.set(cat)
        he = ttk.Entry(self.grid_frame, width=8)
        he.insert(0, hours)
        ne = ttk.Entry(self.grid_frame, width=50)
        ne.insert(0, note)
        row = {"cat": cb, "hours": he, "note": ne, "alive": True}
        rm = ttk.Button(self.grid_frame, text="x", width=2, command=lambda: self._remove(row))
        row["rm"] = rm
        for c, w in enumerate((cb, he, ne, rm)):
            w.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
        self.rows.append(row)
        he.focus_set()

    def _remove(self, row):
        row["alive"] = False
        for k in ("cat", "hours", "note", "rm"):
            row[k].destroy()

    def validate(self):
        new = []
        for row in self.rows:
            if not row["alive"]:
                continue
            htxt = row["hours"].get().strip()
            note = row["note"].get().strip()
            if not htxt and not note:
                continue
            if not htxt:
                raise ValueError("Every line with a note needs hours.")
            new.append((row["cat"].get().strip() or self.data.default_category,
                        parse_hours(htxt, self.data.categories), note))
        for e in self.data.entries_for(self.project.id, self.date):
            self.data.delete_entry(e.id)
        for cat, h, note in new:
            self.data.add_entry(self.date, self.project.id, h, cat, note)
        self.data.set_mark(self.project.id, self.date, self.mark.get())
        return True


# ---------------------------------------------------------------------- categories
class CategoriesDialog(Dialog):
    def __init__(self, parent, data: TimeData):
        self.data = data
        super().__init__(parent, "Hour categories")

    def build(self, body):
        ttk.Label(body, justify="left", text=(
            "One category per line:  Name = shorthand\n"
            "The shorthand is the word used in the week grid (\"8+2 drive\").\n"
            "Exactly one category should have no shorthand: it is used for plain numbers."
        )).pack(anchor="w")
        self.text = tk.Text(body, width=40, height=10)
        self.text.pack(fill="both", expand=True, pady=(8, 0))
        self.text.insert("1.0", "\n".join(f"{c.name} = {c.code}" for c in self.data.categories))

    def validate(self):
        cats = []
        seen = set()
        for line in self.text.get("1.0", "end").splitlines():
            if not line.strip():
                continue
            name, _, code = line.partition("=")
            name, code = name.strip(), code.strip()
            if not name:
                continue
            if name.lower() in seen or (code and code.lower() in seen):
                raise ValueError(f"'{name}' / '{code}' is used twice.")
            seen.add(name.lower())
            if code:
                seen.add(code.lower())
            cats.append(Category(name, code))
        if not cats:
            raise ValueError("Keep at least one category.")
        if sum(1 for c in cats if not c.code) != 1:
            raise ValueError("Exactly one category must have an empty shorthand.")
        self.data.categories = cats
        return True


# ---------------------------------------------------------------------- import
class ImportSheetsDialog(Dialog):
    def __init__(self, parent, sheets):
        self.sheets = sheets
        super().__init__(parent, "Import from Excel")

    def build(self, body):
        ttk.Label(body, text="Choose the sheets to import:").pack(anchor="w", pady=(0, 6))
        self.vars = {}
        for s in self.sheets:
            if s.is_grid:
                text = (f"{s.name}   ({s.first_date:%m/%d/%Y} - {s.last_date:%m/%d/%Y}, "
                        f"{s.filled_cells} filled cells)")
                v = tk.BooleanVar(value=True)
                ttk.Checkbutton(body, text=text, variable=v).pack(anchor="w")
                self.vars[s.name] = v
            else:
                ttk.Label(body, text=f"{s.name}   (not an hours grid - skipped)", foreground="gray").pack(
                    anchor="w", padx=(22, 0))
        ttk.Label(body, foreground="gray", justify="left", text=(
            "\nCells already imported earlier are skipped, so importing again is safe."
        )).pack(anchor="w")

    def validate(self):
        chosen = [n for n, v in self.vars.items() if v.get()]
        if not chosen:
            raise ValueError("Choose at least one sheet.")
        return chosen


class TextDialog(Dialog):
    """Read-only text (import report, help)."""

    def __init__(self, parent, title: str, text: str):
        self.text_value = text
        super().__init__(parent, title)

    def build(self, body):
        t = tk.Text(body, width=90, height=min(30, self.text_value.count("\n") + 2), wrap="word")
        t.insert("1.0", self.text_value)
        t.config(state="disabled")
        t.pack(fill="both", expand=True)

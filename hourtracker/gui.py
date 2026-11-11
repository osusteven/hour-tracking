"""Main window: Week grid, Log, Projects and Statistics tabs."""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import __version__, storage
from .cellparse import format_cell, parse_cell
from .dialogs import (CategoriesDialog, CellDialog, EntryDialog, ImportSheetsDialog, ProjectDialog, TextDialog,
                      parse_date)
from .model import TimeData, fmt_hours, week_start
from .stats import Bucket, compute

SHORTHAND_HELP = """Week grid shorthand

Click a day cell and type the hours, the same way as in the spreadsheet:

    8                   8 hours of Work
    8+2 drive           8 h Work + 2 h Drive
    4+2 admin+1.5 drive 4 h Work, 2 h Admin, 1.5 h Drive
    1 TS                1 h Tech Support
    1 presale / 2 OT    Presale / Overtime
    1:30   45m          1.5 h / 0.75 h

Enter = save and go down,  Tab / Shift+Tab = save and go right / left,
Esc = cancel.  Clear the text to delete the hours of that cell.

A dot (•) in a cell means at least one entry has a note.  Right-click (or
double-click) a cell to see and edit the individual entries and their notes.

The category words come from Edit > Hour categories.
"""


def open_path(path: Path) -> None:
    path = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as exc:
        messagebox.showerror("Hour Tracker", f"Could not open {path}:\n{exc}")


def make_tree(parent, columns, headings, widths, anchors=None, height=20, show="headings"):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show=show, height=height, selectmode="browse")
    for col, head, w in zip(columns, headings, widths):
        tree.heading(col, text=head)
        anchor = (anchors or {}).get(col, "w")
        tree.column(col, width=w, minwidth=30, anchor=anchor, stretch=(anchor == "w" and col != "status"))
    vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vs.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


# ====================================================================== Week tab
class WeekTab(ttk.Frame):
    DAY_COLS = [f"d{i}" for i in range(7)]

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        self.monday = week_start(dt.date.today())
        self.editor: Optional[ttk.Entry] = None
        self.edit_pos = None
        self._committing = False
        self.collapsed = set()

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="◀ Prev week", command=lambda: self.shift(-7)).pack(side="left")
        ttk.Button(bar, text="This week", command=self.go_today).pack(side="left", padx=4)
        ttk.Button(bar, text="Next week ▶", command=lambda: self.shift(7)).pack(side="left")
        self.title = ttk.Label(bar, font=("TkDefaultFont", 11, "bold"))
        self.title.pack(side="left", padx=12)
        ttk.Button(bar, text="Go to date…", command=self.goto_date).pack(side="left")
        ttk.Button(bar, text="New project…", command=self.new_project).pack(side="right")
        self.show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Show archived projects", variable=self.show_all,
                        command=self.refresh).pack(side="right", padx=8)

        cols = ["status"] + self.DAY_COLS + ["total"]
        frame, self.tree = make_tree(self, cols, ["Status"] + [""] * 7 + ["Total"],
                                     [90] + [92] * 7 + [70],
                                     anchors={c: "center" for c in self.DAY_COLS + ["total"]}, show="tree headings")
        self.tree.heading("#0", text="Company / Project")
        self.tree.column("#0", width=300, minwidth=150, stretch=True)
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("company", background="#e6eef7", font=("TkDefaultFont", 9, "bold"))
        self.tree.tag_configure("total", background="#d9e7d5", font=("TkDefaultFont", 9, "bold"))
        self.tree.tag_configure("archived", foreground="gray")

        ttk.Label(self, foreground="gray", text=(
            "Click a day to type hours (e.g. 8  or  8+2 drive  or  1 TS).  "
            "Right-click / double-click a cell for entries & notes.  Help > Grid shorthand for more.")
        ).pack(anchor="w", pady=(4, 0))

        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Button-2>", self.on_right_click)  # macOS
        self.tree.bind("<<TreeviewOpen>>", lambda e: self.collapsed.discard(self.tree.focus()))
        self.tree.bind("<<TreeviewClose>>", lambda e: self.collapsed.add(self.tree.focus()))
        self.tree.bind("<Configure>", lambda e: self.commit_edit())
        self.tree.bind("<MouseWheel>", lambda e: self.commit_edit())

    # ---- navigation
    def shift(self, days):
        self.commit_edit()
        self.monday += dt.timedelta(days=days)
        self.refresh()

    def go_today(self):
        self.commit_edit()
        self.monday = week_start(dt.date.today())
        self.refresh()

    def goto_date(self):
        from tkinter import simpledialog
        text = simpledialog.askstring("Go to date", "Date (YYYY-MM-DD or MM/DD/YYYY):", parent=self)
        if not text:
            return
        try:
            self.monday = week_start(parse_date(text))
        except ValueError as exc:
            messagebox.showerror("Go to date", str(exc), parent=self)
            return
        self.refresh()

    @property
    def days(self):
        return [self.monday + dt.timedelta(days=i) for i in range(7)]

    # ---- display
    def refresh(self):
        self.cancel_edit()
        data = self.app.data
        days = self.days
        today = dt.date.today()
        iso = self.monday.isocalendar()
        self.title.config(text=f"Week {iso[1]}:  {days[0]:%b %d} – {days[6]:%b %d, %Y}")
        for i, d in enumerate(days):
            mark = " ●" if d == today else ""
            self.tree.heading(self.DAY_COLS[i], text=f"{d:%a %m/%d}{mark}")

        week_entries = data.entries_between(days[0], days[6])
        used = {e.project_id for e in week_entries}
        projects = [p for p in data.sorted_projects() if self.show_all.get() or not p.archived or p.id in used]

        cells = {}
        for e in week_entries:
            cells.setdefault((e.project_id, e.date), []).append(e)

        sel = self.tree.selection()
        yview = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        groups = {}
        for p in projects:
            groups.setdefault(p.company or "(no company)", []).append(p)
        cats = data.categories
        grand = [0.0] * 7
        for company, plist in groups.items():
            cid = f"c:{company}"
            sub = [0.0] * 7
            self.tree.insert("", "end", iid=cid, text=company, open=cid not in self.collapsed, tags=("company",))
            for p in plist:
                vals = [p.status]
                total = 0.0
                for i, d in enumerate(days):
                    es = cells.get((p.id, d), [])
                    h = sum(e.hours for e in es)
                    total += h
                    sub[i] += h
                    txt = format_cell(es, cats)
                    if any(e.note for e in es):
                        txt += " •"
                    vals.append(txt)
                vals.append(fmt_hours(total) if total else "")
                self.tree.insert(cid, "end", iid=f"p:{p.id}", text=p.name, values=vals,
                                 tags=("archived",) if p.archived else ())
            for i in range(7):
                grand[i] += sub[i]
            self.tree.item(cid, values=[""] + [fmt_hours(h) if h else "" for h in sub] +
                           [fmt_hours(sum(sub)) if sum(sub) else ""])
        self.tree.insert("", "end", iid="total", text="TOTAL", tags=("total",),
                         values=[""] + [fmt_hours(h) for h in grand] + [fmt_hours(sum(grand))])
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.tree.yview_moveto(yview)

    # ---- helpers
    def _cell_at(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None, None
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)  # '#0', '#1', ...
        idx = int(col[1:]) - 2  # #1 is status, #2.. are days
        if not item.startswith("p:") or not 0 <= idx < 7:
            return item, None
        return item, idx

    def _project(self, item):
        return self.app.data.projects.get(int(item[2:]))

    # ---- mouse
    def on_click(self, event):
        item, idx = self._cell_at(event)
        if self.editor is not None:
            if self.edit_pos == (item, idx):
                return "break"
            if not self.commit_edit():
                return "break"
        if item and idx is not None:
            self.tree.selection_set(item)
            self.after_idle(lambda: self.start_edit(item, idx))

    def on_double(self, event):
        item, idx = self._cell_at(event)
        if item and item.startswith("p:") and idx is None:
            self.edit_project(item)
            return "break"
        if item and idx is not None:
            self.cancel_edit()
            self.open_cell_dialog(item, idx)
            return "break"

    def on_right_click(self, event):
        self.commit_edit()
        item, idx = self._cell_at(event)
        if not item:
            item = self.tree.identify_row(event.y)
        if not item or not item.startswith("p:"):
            return
        self.tree.selection_set(item)
        menu = tk.Menu(self, tearoff=False)
        if idx is not None:
            d = self.days[idx]
            menu.add_command(label=f"Entries && notes for {d:%a %m/%d}…",
                             command=lambda: self.open_cell_dialog(item, idx))
            menu.add_command(label="Clear this day", command=lambda: self.clear_cell(item, idx))
            menu.add_separator()
        menu.add_command(label="Edit project…", command=lambda: self.edit_project(item))
        p = self._project(item)
        if p:
            menu.add_command(label="Restore project" if p.archived else "Archive project (hide)",
                             command=lambda: self.toggle_archive(p))
        menu.tk_popup(event.x_root, event.y_root)

    # ---- inline editing
    def start_edit(self, item, idx):
        self.cancel_edit()
        if not self.tree.exists(item):
            return
        self.tree.see(item)
        self.update_idletasks()
        col = self.DAY_COLS[idx]
        bbox = self.tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox
        p = self._project(item)
        es = self.app.data.entries_for(p.id, self.days[idx])
        self.editor = ttk.Entry(self.tree, justify="center")
        self.editor.insert(0, format_cell(es, self.app.data.categories))
        self.editor.select_range(0, "end")
        self.editor.place(x=x, y=y, width=max(w, 110), height=h)
        self.editor.focus_set()
        self.edit_pos = (item, idx)
        self.editor.bind("<Return>", lambda e: self._commit_and_move(1, 0))
        self.editor.bind("<KP_Enter>", lambda e: self._commit_and_move(1, 0))
        self.editor.bind("<Down>", lambda e: self._commit_and_move(1, 0))
        self.editor.bind("<Up>", lambda e: self._commit_and_move(-1, 0))
        self.editor.bind("<Tab>", lambda e: self._commit_and_move(0, 1))
        self.editor.bind("<Shift-Tab>", lambda e: self._commit_and_move(0, -1))
        self.editor.bind("<ISO_Left_Tab>", lambda e: self._commit_and_move(0, -1))
        self.editor.bind("<Escape>", lambda e: self.cancel_edit())
        self.editor.bind("<FocusOut>", lambda e: self.after_idle(self.commit_edit))

    def cancel_edit(self):
        if self.editor is not None:
            ed, self.editor = self.editor, None
            ed.destroy()
            self.edit_pos = None

    def commit_edit(self) -> bool:
        """Save the open editor. Returns False if the text was not understood."""
        if self.editor is None or self._committing:
            return self.editor is None
        self._committing = True
        try:
            return self._commit_edit()
        finally:
            self._committing = False

    def _commit_edit(self) -> bool:
        item, idx = self.edit_pos
        text = self.editor.get()
        data = self.app.data
        p = self._project(item)
        res = parse_cell(text, data.categories)
        if text.strip() and not res.amounts:
            messagebox.showerror("Hours", f"Can't read '{text}'.\n\n" + SHORTHAND_HELP.split("\n\n")[1],
                                 parent=self)
            if self.editor is not None:
                self.editor.focus_set()
            return False
        old = format_cell(data.entries_for(p.id, self.days[idx]), data.categories)
        self.cancel_edit()
        new_amounts = [(c, h) for c, h in res.amounts if h]
        if text.strip() == old and not res.unknown:
            return True
        data.set_cell(p.id, self.days[idx], new_amounts, extra_note=res.note)
        self.app.changed()
        return True

    def _commit_and_move(self, drow, dcol):
        if self.editor is None:
            return "break"
        item, idx = self.edit_pos
        if not self.commit_edit():
            return "break"
        rows = [c for cid in self.tree.get_children("") for c in self.tree.get_children(cid)
                if self.tree.item(cid, "open")]
        if item not in rows:
            return "break"
        r = rows.index(item) + drow
        idx += dcol
        if idx > 6:
            idx, r = 0, r + 1
        if idx < 0:
            idx, r = 6, r - 1
        if 0 <= r < len(rows):
            self.tree.selection_set(rows[r])
            self.start_edit(rows[r], idx)
        return "break"

    # ---- actions
    def open_cell_dialog(self, item, idx):
        p = self._project(item)
        if p and CellDialog(self, self.app.data, p, self.days[idx]).run():
            self.app.changed()

    def clear_cell(self, item, idx):
        p = self._project(item)
        self.app.data.set_cell(p.id, self.days[idx], [])
        self.app.changed()

    def edit_project(self, item):
        p = self._project(item)
        if p and ProjectDialog(self, self.app.data, p).run():
            self.app.changed()

    def toggle_archive(self, p):
        p.archived = not p.archived
        self.app.changed()

    def new_project(self):
        sel = self.tree.selection()
        company = ""
        if sel:
            if sel[0].startswith("c:"):
                company = sel[0][2:]
            elif sel[0].startswith("p:"):
                company = self._project(sel[0]).company
        if ProjectDialog(self, self.app.data, company=company).run():
            self.app.changed()


# ====================================================================== Log tab
class LogTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="From").pack(side="left")
        self.start = ttk.Entry(bar, width=11)
        self.start.pack(side="left", padx=(4, 8))
        ttk.Label(bar, text="To").pack(side="left")
        self.end = ttk.Entry(bar, width=11)
        self.end.pack(side="left", padx=(4, 8))
        ttk.Label(bar, text="Project").pack(side="left")
        self.project = ttk.Combobox(bar, state="readonly", width=32)
        self.project.pack(side="left", padx=(4, 8))
        ttk.Label(bar, text="Search").pack(side="left")
        self.search = ttk.Entry(bar, width=16)
        self.search.pack(side="left", padx=(4, 8))
        ttk.Button(bar, text="Apply", command=self.refresh).pack(side="left")
        for e in (self.start, self.end, self.search):
            e.bind("<Return>", lambda ev: self.refresh())
        self.project.bind("<<ComboboxSelected>>", lambda ev: self.refresh())

        quick = ttk.Frame(self)
        quick.pack(fill="x", pady=(0, 6))
        for text, fn in [("This week", self.this_week), ("Last week", self.last_week),
                         ("This month", self.this_month), ("Last month", self.last_month),
                         ("This year", self.this_year), ("Everything", self.everything)]:
            ttk.Button(quick, text=text, command=fn).pack(side="left", padx=(0, 4))
        ttk.Button(quick, text="Delete", command=self.delete).pack(side="right")
        ttk.Button(quick, text="Edit…", command=self.edit).pack(side="right", padx=4)
        ttk.Button(quick, text="New entry…", command=self.add).pack(side="right")

        cols = ["date", "day", "company", "project", "category", "hours", "note"]
        frame, self.tree = make_tree(self, cols, ["Date", "Day", "Company", "Project", "Category", "Hours", "Note"],
                                     [90, 45, 110, 220, 100, 60, 380], anchors={"hours": "e", "day": "center"})
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.tree.bind("<Delete>", lambda e: self.delete())
        self.footer = ttk.Label(self, font=("TkDefaultFont", 10, "bold"))
        self.footer.pack(anchor="e", pady=(4, 0))
        self.this_month(refresh=False)

    def _set_range(self, a: Optional[dt.date], b: Optional[dt.date], refresh=True):
        for w, d in ((self.start, a), (self.end, b)):
            w.delete(0, "end")
            if d:
                w.insert(0, d.isoformat())
        if refresh:
            self.refresh()

    def this_week(self):
        m = week_start(dt.date.today())
        self._set_range(m, m + dt.timedelta(days=6))

    def last_week(self):
        m = week_start(dt.date.today()) - dt.timedelta(days=7)
        self._set_range(m, m + dt.timedelta(days=6))

    def this_month(self, refresh=True):
        t = dt.date.today()
        self._set_range(t.replace(day=1), t.replace(day=calendar.monthrange(t.year, t.month)[1]), refresh)

    def last_month(self):
        end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
        self._set_range(end.replace(day=1), end)

    def this_year(self):
        y = dt.date.today().year
        self._set_range(dt.date(y, 1, 1), dt.date(y, 12, 31))

    def everything(self):
        self._set_range(None, None)

    def refresh(self):
        data = self.app.data
        self.projects = data.sorted_projects()
        names = ["(all projects)"] + [p.display for p in self.projects]
        current = self.project.get()
        self.project.config(values=names)
        self.project.set(current if current in names else names[0])
        try:
            a = parse_date(self.start.get()) if self.start.get().strip() else dt.date.min
            b = parse_date(self.end.get()) if self.end.get().strip() else dt.date.max
        except ValueError as exc:
            messagebox.showerror("Log", str(exc), parent=self)
            return
        pidx = self.project.current()
        pid = self.projects[pidx - 1].id if pidx > 0 else None
        q = self.search.get().strip().lower()
        rows = []
        for e in data.entries.values():
            if not a <= e.date <= b or (pid is not None and e.project_id != pid):
                continue
            p = data.projects.get(e.project_id)
            company, pname = (p.company, p.name) if p else ("?", "?")
            if q and q not in f"{company} {pname} {e.category} {e.note}".lower():
                continue
            rows.append((e, company, pname))
        rows.sort(key=lambda r: (r[0].date, r[1].lower(), r[2].lower(), r[0].id), reverse=True)
        self.tree.delete(*self.tree.get_children())
        total = 0.0
        for e, company, pname in rows:
            total += e.hours
            self.tree.insert("", "end", iid=str(e.id), values=[
                e.date.isoformat(), e.date.strftime("%a"), company, pname, e.category, fmt_hours(e.hours),
                e.note.replace("\n", " ")])
        days = len({r[0].date for r in rows})
        self.footer.config(text=f"{len(rows)} entries   •   {days} days   •   {fmt_hours(total)} hours")

    def _selected(self):
        sel = self.tree.selection()
        return self.app.data.entries.get(int(sel[0])) if sel else None

    def add(self):
        if EntryDialog(self, self.app.data).run():
            self.app.changed()

    def edit(self):
        e = self._selected()
        if e and EntryDialog(self, self.app.data, e).run():
            self.app.changed()

    def delete(self):
        e = self._selected()
        if e and messagebox.askyesno("Delete entry", f"Delete {fmt_hours(e.hours)} h on {e.date}?", parent=self):
            self.app.data.delete_entry(e.id)
            self.app.changed()


# ====================================================================== Projects tab
class ProjectsTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="New project…", command=self.add).pack(side="left")
        ttk.Button(bar, text="Edit…", command=self.edit).pack(side="left", padx=4)
        ttk.Button(bar, text="Archive / Restore", command=self.toggle_archive).pack(side="left")
        ttk.Button(bar, text="Delete…", command=self.delete).pack(side="left", padx=4)
        self.show_archived = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Show archived", variable=self.show_archived, command=self.refresh).pack(
            side="right")
        cols = ["company", "name", "status", "budget", "logged", "remaining", "so", "po", "location", "contact",
                "last"]
        heads = ["Company", "Project", "Status", "Budget", "Logged", "Remaining", "SO", "PO", "Location", "Contact",
                 "Last worked"]
        frame, self.tree = make_tree(self, cols, heads, [100, 260, 90, 70, 70, 85, 70, 70, 90, 90, 95],
                                     anchors={"budget": "e", "logged": "e", "remaining": "e", "last": "center"})
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("archived", foreground="gray")
        self.tree.tag_configure("over", foreground="#b00020")
        self.tree.bind("<Double-1>", lambda e: self.edit())
        for c in cols:
            self.tree.heading(c, command=lambda c=c: self.sort_by(c))
        self.sort_col, self.sort_rev = "company", False

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self.refresh()

    def refresh(self):
        data = self.app.data
        hours = {}
        last = {}
        for e in data.entries.values():
            hours[e.project_id] = hours.get(e.project_id, 0.0) + e.hours
            if e.project_id not in last or e.date > last[e.project_id]:
                last[e.project_id] = e.date
        rows = []
        for p in data.sorted_projects(include_archived=self.show_archived.get()):
            h = hours.get(p.id, 0.0)
            rem = p.budget - h if p.budget is not None else None
            rows.append((p, {
                "company": p.company, "name": p.name, "status": p.status + (" (archived)" if p.archived else ""),
                "budget": p.budget, "logged": h, "remaining": rem, "so": p.so, "po": p.po,
                "location": p.location, "contact": p.contact, "last": last.get(p.id)}))

        def key(r):
            v = r[1][self.sort_col]
            if v is None:
                return (1, "")
            return (0, v.lower() if isinstance(v, str) else v)

        rows.sort(key=key, reverse=self.sort_rev)
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for p, r in rows:
            vals = []
            for c in self.tree["columns"]:
                v = r[c]
                if isinstance(v, float):
                    v = fmt_hours(v)
                elif isinstance(v, dt.date):
                    v = v.isoformat()
                vals.append("" if v is None else v)
            tags = ("archived",) if p.archived else ("over",) if r["remaining"] is not None and r["remaining"] < 0 \
                else ()
            self.tree.insert("", "end", iid=str(p.id), values=vals, tags=tags)
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])

    def _selected(self):
        sel = self.tree.selection()
        return self.app.data.projects.get(int(sel[0])) if sel else None

    def add(self):
        if ProjectDialog(self, self.app.data).run():
            self.app.changed()

    def edit(self):
        p = self._selected()
        if p and ProjectDialog(self, self.app.data, p).run():
            self.app.changed()

    def toggle_archive(self):
        p = self._selected()
        if p:
            p.archived = not p.archived
            self.app.changed()

    def delete(self):
        p = self._selected()
        if not p:
            return
        n = sum(1 for e in self.app.data.entries.values() if e.project_id == p.id)
        msg = f"Delete project '{p.display}'?"
        if n:
            msg += (f"\n\nThis also deletes its {n} time entries ({fmt_hours(self.app.data.project_hours(p.id))} h)."
                    "\nTip: use Archive instead to just hide it.")
        if messagebox.askyesno("Delete project", msg, icon="warning", parent=self):
            self.app.data.delete_project(p.id)
            self.app.changed()


# ====================================================================== Stats tab
class StatsTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Year").pack(side="left")
        self.year = ttk.Combobox(bar, state="readonly", width=8)
        self.year.pack(side="left", padx=4)
        self.year.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(bar, text="Open statistics file", command=lambda: open_path(
            storage.stats_path_for(self.app.data_path))).pack(side="right")
        ttk.Button(bar, text="Open data folder", command=lambda: open_path(self.app.data_path.parent)).pack(
            side="right", padx=4)

        self.summary = ttk.Frame(self)
        self.summary.pack(fill="x", pady=(0, 8))
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.tables = {}
        for key, title in [("months", "By month"), ("weeks", "By week"), ("projects", "By project"),
                           ("companies", "By company"), ("weekdays", "By weekday")]:
            f = ttk.Frame(self.nb)
            self.nb.add(f, text=title)
            self.tables[key] = f

    def _table(self, key, first_cols, first_widths, cats, rows):
        holder = self.tables[key]
        for w in holder.winfo_children():
            w.destroy()
        cols = [f"c{i}" for i in range(len(first_cols) + len(cats))]
        heads = first_cols + cats
        widths = first_widths + [95] * len(cats)
        anchors = {c: "e" for c in cols[1:]}
        frame, tree = make_tree(holder, cols, heads, widths, anchors=anchors)
        frame.pack(fill="both", expand=True)
        for r in rows:
            tree.insert("", "end", values=r)

    def refresh(self):
        data = self.app.data
        years = sorted({e.date.year for e in data.entries.values()}, reverse=True)
        choices = ["All"] + [str(y) for y in years]
        cur = self.year.get() or str(dt.date.today().year)
        self.year.config(values=choices)
        self.year.set(cur if cur in choices else "All")
        year = None if self.year.get() == "All" else int(self.year.get())
        s = compute(data, year=year)
        cats = [c for c in s.categories if s.total.by_category.get(c)]

        for w in self.summary.winfo_children():
            w.destroy()
        items = [("Today", s.summary["today"]), ("This week", s.summary["thisWeek"]),
                 ("Last week", s.summary["lastWeek"]), ("This month", s.summary["thisMonth"]),
                 ("Last month", s.summary["lastMonth"]), ("This year", s.summary["thisYear"]),
                 (f"Total {year or 'all years'}", s.total.hours), ("Days worked", s.total.days),
                 ("Avg / worked day", s.total.avg_per_day),
                 ("Avg / worked week", s.total.hours / len(s.weeks) if s.weeks else 0)]
        for i, (label, value) in enumerate(items):
            box = ttk.Frame(self.summary, padding=(10, 4), relief="groove")
            box.grid(row=0, column=i, sticky="nsew", padx=2)
            ttk.Label(box, text=label, foreground="gray").pack()
            ttk.Label(box, text=fmt_hours(value) if isinstance(value, float) else str(value),
                      font=("TkDefaultFont", 13, "bold")).pack()
            self.summary.columnconfigure(i, weight=1)

        def catvals(b: Bucket):
            return [fmt_hours(b.by_category.get(c, 0)) if b.by_category.get(c) else "" for c in cats]

        rows = [[f"{calendar.month_name[m]} {y}", fmt_hours(b.hours), b.days, fmt_hours(b.avg_per_day)] + catvals(b)
                for (y, m), b in sorted(s.months.items(), reverse=True)]
        self._table("months", ["Month", "Hours", "Days", "Avg/day"], [140, 70, 50, 70], cats, rows)

        rows = []
        for monday, b in sorted(s.weeks.items(), reverse=True):
            iso = monday.isocalendar()
            rows.append([f"{iso[0]} wk {iso[1]:02d}  ({monday:%m/%d})", fmt_hours(b.hours), b.days,
                         fmt_hours(b.avg_per_day)] + catvals(b))
        self._table("weeks", ["Week", "Hours", "Days", "Avg/day"], [170, 70, 50, 70], cats, rows)

        rows = []
        for pid, b in sorted(s.projects.items(), key=lambda kv: -kv[1].hours):
            p = data.projects.get(pid)
            budget = fmt_hours(p.budget) if p and p.budget is not None else ""
            pct = f"{100 * b.hours / p.budget:.0f}%" if p and p.budget else ""
            rows.append([p.display if p else "?", fmt_hours(b.hours), b.days, budget, pct,
                         b.first.isoformat(), b.last.isoformat()] + catvals(b))
        self._table("projects", ["Project", "Hours", "Days", "Budget", "Used", "First", "Last"],
                    [280, 70, 50, 60, 50, 90, 90], cats, rows)

        rows = [[name, fmt_hours(b.hours), b.days, fmt_hours(b.avg_per_day)] + catvals(b)
                for name, b in sorted(s.companies.items(), key=lambda kv: -kv[1].hours)]
        self._table("companies", ["Company", "Hours", "Days", "Avg/day"], [160, 70, 50, 70], cats, rows)

        rows = []
        for i in range(7):
            b = s.weekdays.get(i, Bucket())
            rows.append([calendar.day_name[i], fmt_hours(b.hours), b.days, fmt_hours(b.avg_per_day)] + catvals(b))
        self._table("weekdays", ["Weekday", "Hours", "Days", "Avg/day"], [120, 70, 50, 70], cats, rows)


# ====================================================================== App
class App(tk.Tk):
    def __init__(self, data_path: Optional[Path] = None):
        super().__init__()
        self.title("Hour Tracker")
        self.geometry("1250x720")
        self.minsize(900, 500)
        try:
            ttk.Style(self).theme_use("vista" if sys.platform.startswith("win") else "clam")
        except tk.TclError:
            pass
        ttk.Style(self).configure("Treeview", rowheight=24)

        self.cfg = storage.load_config()
        self.data_path = Path(data_path or self.cfg.get("data_file") or storage.default_data_path())
        self.backed_up = False
        self.data = self._load(self.data_path)

        self._build_menu()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.week = WeekTab(self.nb, self)
        self.log = LogTab(self.nb, self)
        self.projects = ProjectsTab(self.nb, self)
        self.stats = StatsTab(self.nb, self)
        self.nb.add(self.week, text="  Week  ")
        self.nb.add(self.log, text="  Log  ")
        self.nb.add(self.projects, text="  Projects  ")
        self.nb.add(self.stats, text="  Statistics  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.week.commit_edit())
        self.status = ttk.Label(self, anchor="w", padding=(8, 2), relief="sunken")
        self.status.pack(fill="x", side="bottom")
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.refresh_all()
        self._set_status("Loaded")

    # ---- data file
    def _load(self, path: Path) -> TimeData:
        if path.exists():
            try:
                return storage.load(path)
            except Exception as exc:  # noqa: BLE001 - show anything to the user
                bak = path.with_name(path.name + ".bak")
                messagebox.showerror(
                    "Hour Tracker",
                    f"Could not read the data file\n{path}\n\n{exc}\n\n"
                    "The file was not changed. Fix it in a text editor, or restore the backup "
                    f"({bak.name}) by copying it over the data file, then start Hour Tracker again.")
                raise SystemExit(1)
        data = TimeData()
        try:
            storage.save(data, path)
        except OSError as exc:
            messagebox.showerror("Hour Tracker", f"Could not create {path}:\n{exc}")
        return data

    def _set_status(self, what: str):
        self.status.config(text=f"{what} {dt.datetime.now():%H:%M:%S}   •   Data: {self.data_path}   •   "
                                f"Statistics: {storage.stats_path_for(self.data_path).name}")

    def save(self):
        try:
            storage.save(self.data, self.data_path, backup=not self.backed_up)
            self.backed_up = True
            self._set_status("Saved")
        except OSError as exc:
            messagebox.showerror("Hour Tracker", f"Saving failed:\n{exc}")

    def changed(self):
        self.save()
        self.refresh_all()

    def refresh_all(self):
        self.title(f"Hour Tracker - {self.data_path.name}")
        self.week.refresh()
        self.log.refresh()
        self.projects.refresh()
        self.stats.refresh()

    def switch_to(self, path: Path, data: TimeData):
        self.data_path = Path(path)
        self.data = data
        self.backed_up = False
        self.cfg["data_file"] = str(self.data_path)
        storage.save_config(self.cfg)
        self.refresh_all()
        self._set_status("Opened")

    # ---- menu
    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=False)
        fm.add_command(label="New data file…", command=self.new_file)
        fm.add_command(label="Open data file…", command=self.open_file)
        fm.add_command(label="Save a copy as…", command=self.save_copy)
        fm.add_separator()
        fm.add_command(label="Import from Excel…", command=self.import_excel)
        fm.add_command(label="Export entries to CSV…", command=self.export_csv)
        fm.add_separator()
        fm.add_command(label="Open data folder", command=lambda: open_path(self.data_path.parent))
        fm.add_separator()
        fm.add_command(label="Exit", command=self.quit_app)
        mb.add_cascade(label="File", menu=fm)
        em = tk.Menu(mb, tearoff=False)
        em.add_command(label="New entry…", command=lambda: self.log.add())
        em.add_command(label="New project…", command=lambda: self.projects.add())
        em.add_separator()
        em.add_command(label="Hour categories…", command=self.edit_categories)
        mb.add_cascade(label="Edit", menu=em)
        hm = tk.Menu(mb, tearoff=False)
        hm.add_command(label="Grid shorthand", command=lambda: TextDialog(self, "Grid shorthand", SHORTHAND_HELP))
        hm.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"Hour Tracker {__version__}\n\nData file:\n{self.data_path}\n\nStatistics file:\n"
                     f"{storage.stats_path_for(self.data_path)}"))
        mb.add_cascade(label="Help", menu=hm)
        self.config(menu=mb)

    def new_file(self):
        self.week.commit_edit()
        path = filedialog.asksaveasfilename(parent=self, title="New data file", defaultextension=".xml",
                                            initialdir=str(self.data_path.parent), initialfile="hours.xml",
                                            filetypes=[("XML files", "*.xml")])
        if not path:
            return
        data = TimeData()
        storage.save(data, Path(path))
        self.switch_to(Path(path), data)

    def open_file(self):
        self.week.commit_edit()
        path = filedialog.askopenfilename(parent=self, title="Open data file", initialdir=str(self.data_path.parent),
                                          filetypes=[("XML files", "*.xml"), ("All files", "*.*")])
        if not path:
            return
        try:
            data = storage.load(Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open", f"Could not read {path}:\n{exc}", parent=self)
            return
        self.switch_to(Path(path), data)

    def save_copy(self):
        path = filedialog.asksaveasfilename(parent=self, title="Save a copy", defaultextension=".xml",
                                            initialdir=str(self.data_path.parent),
                                            initialfile=f"hours-{dt.date.today().isoformat()}.xml",
                                            filetypes=[("XML files", "*.xml")])
        if path:
            storage.write_xml(storage.to_xml(self.data), Path(path))
            self._set_status(f"Copy saved to {path} -")

    def import_excel(self):
        from . import excel_import
        self.week.commit_edit()
        path = filedialog.askopenfilename(parent=self, title="Import hours from Excel",
                                          filetypes=[("Excel workbooks", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.config(cursor="watch")
            self.update()
            sheets = excel_import.list_sheets(path)
        except excel_import.ImportError_ as exc:
            messagebox.showerror("Import", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import", f"Could not read {path}:\n{exc}", parent=self)
            return
        finally:
            self.config(cursor="")
        if not any(s.is_grid for s in sheets):
            messagebox.showinfo("Import", "No sheet with a project/day hours grid was found.", parent=self)
            return
        chosen = ImportSheetsDialog(self, sheets).run()
        if not chosen:
            return
        self.config(cursor="watch")
        self.update()
        try:
            report = excel_import.import_workbook(path, self.data, chosen)
        finally:
            self.config(cursor="")
        self.changed()
        TextDialog(self, "Import finished", report.summary())

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export entries", defaultextension=".csv",
                                            initialdir=str(self.data_path.parent), initialfile="hours.csv",
                                            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Weekday", "Company", "Project", "Category", "Hours", "Note"])
            for e in sorted(self.data.entries.values(), key=lambda e: (e.date, e.id)):
                p = self.data.projects.get(e.project_id)
                w.writerow([e.date.isoformat(), e.date.strftime("%A"), p.company if p else "", p.name if p else "",
                            e.category, fmt_hours(e.hours), e.note])
        self._set_status(f"Exported {path} -")

    def edit_categories(self):
        if CategoriesDialog(self, self.data).run():
            self.changed()

    def quit_app(self):
        self.week.commit_edit()
        self.destroy()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]).expanduser() if argv else None
    app = App(path)
    if path:
        app.cfg["data_file"] = str(app.data_path)
        storage.save_config(app.cfg)
    app.mainloop()


if __name__ == "__main__":
    main()

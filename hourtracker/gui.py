"""Main window: Week grid, Log, Projects and Statistics tabs."""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import __version__, storage
from .cellparse import format_cell, parse_cell
from .dialogs import (CategoriesDialog, CellDialog, EntryDialog, ImportSheetsDialog, ProjectDialog, TextDialog,
                      parse_date)
from .model import MARK_LABELS, MARKS, WEEKDAY_ORDER, TimeData, fmt_hours, week_start
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
GRID_LINE = "#c8c8c8"
CELL_BG = "#ffffff"
WEEKEND_BG = "#f3f3f3"
TODAY_HEAD_BG = "#cfe3ff"
HEAD_BG = "#e4e4e4"
COMPANY_BG = "#e6eef7"
TOTAL_BG = "#e4e4e4"
FULL_DAY_BG = "#7fcf78"  # daily total of 8 h or more
FULL_DAY_HOURS = 8.0
MARK_COLORS = {"green": "#b9e4b0", "yellow": "#fff1a6", "red": "#f5b7b1"}


class WeekTab(ttk.Frame):
    MIN_WIDTHS = [240, 85] + [95] * 7 + [70]

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        self.sunday = week_start(dt.date.today())
        self.editor: Optional[tk.Entry] = None
        self.edit_pos = None  # (project_id, day index)
        self._committing = False
        self.collapsed = set()
        self.cells = {}
        self.rows = []
        self.font = tkfont.nametofont("TkDefaultFont")
        self.bold = self.font.copy()
        self.bold.configure(weight="bold")

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

        # fixed header + scrolling body, both with the same column widths
        self.header = tk.Frame(self, bg=GRID_LINE, bd=0)
        self.header.pack(fill="x", anchor="w")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0, bg=CELL_BG, bd=0)
        vs = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=GRID_LINE, bd=0)
        self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Configure>", lambda e: self.commit_edit())

        legend = ttk.Frame(self)
        legend.pack(fill="x", pady=(4, 0))
        for color, text in [(MARK_COLORS["green"], "Green = submitted"), (MARK_COLORS["yellow"], "Yellow"),
                            (MARK_COLORS["red"], "Red"), (FULL_DAY_BG, f"Day total ≥ {FULL_DAY_HOURS:g} h")]:
            tk.Label(legend, bg=color, width=2, relief="solid", bd=1).pack(side="left", padx=(0, 4))
            ttk.Label(legend, text=text).pack(side="left", padx=(0, 14))
        ttk.Label(legend, foreground="gray", text=(
            "Click a day to type hours (8, 8+2 drive, 1 TS).  Right-click to color / add notes.  "
            "While typing: Ctrl+1 green, Ctrl+2 yellow, Ctrl+3 red, Ctrl+0 clear.")).pack(side="left")

    def _on_wheel(self, event):
        self.commit_edit()
        self.canvas.yview_scroll(int(-event.delta / 120) * 3 if event.delta else 0, "units")

    # ---- navigation
    def shift(self, days):
        self.commit_edit()
        self.sunday += dt.timedelta(days=days)
        self.refresh()

    def go_today(self):
        self.commit_edit()
        self.sunday = week_start(dt.date.today())
        self.refresh()

    def goto_date(self):
        from tkinter import simpledialog
        self.commit_edit()
        text = simpledialog.askstring("Go to date", "Date (YYYY-MM-DD or MM/DD/YYYY):", parent=self)
        if not text:
            return
        try:
            self.sunday = week_start(parse_date(text))
        except ValueError as exc:
            messagebox.showerror("Go to date", str(exc), parent=self)
            return
        self.refresh()

    @property
    def days(self):
        return [self.sunday + dt.timedelta(days=i) for i in range(7)]

    # ---- building the grid
    def _label(self, parent, row, col, text, bg, anchor="center", bold=False, fg="black"):
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, anchor=anchor, font=self.bold if bold else self.font,
                       padx=6, pady=3, bd=0)
        lbl.grid(row=row, column=col, sticky="nsew", padx=(0, 1), pady=(0, 1))
        return lbl

    def refresh(self):
        self.cancel_edit()
        data = self.app.data
        days = self.days
        today = dt.date.today()
        self.title.config(text=f"Week of {days[0]:%a %b %d} – {days[6]:%a %b %d, %Y}")
        yview = self.canvas.yview()[0]

        for w in self.header.winfo_children() + self.inner.winfo_children():
            w.destroy()
        self.cells = {}
        self.rows = []
        texts = [[] for _ in self.MIN_WIDTHS]  # for column widths

        heads = ["Company / Project", "Status"] + [f"{d:%a %m/%d}" for d in days] + ["Total"]
        for c, text in enumerate(heads):
            bg = HEAD_BG
            if 2 <= c <= 8:
                d = days[c - 2]
                bg = TODAY_HEAD_BG if d == today else HEAD_BG
            lbl = self._label(self.header, 0, c, text, bg, anchor="w" if c == 0 else "center", bold=True)
            texts[c].append(text)
            if 2 <= c <= 8:
                idx = c - 2
                for seq in ("<Button-3>", "<Button-2>"):
                    lbl.bind(seq, lambda e, i=idx: self.day_menu(e, i))

        week_entries = data.entries_between(days[0], days[6])
        used = {e.project_id for e in week_entries}
        projects = [p for p in data.sorted_projects() if self.show_all.get() or not p.archived or p.id in used]
        cells = {}
        for e in week_entries:
            cells.setdefault((e.project_id, e.date), []).append(e)
        groups = {}
        for p in projects:
            groups.setdefault(p.company or "(no company)", []).append(p)

        cats = data.categories
        grand = [0.0] * 7
        row = 0
        for company, plist in groups.items():
            sub = [0.0] * 7
            for p in plist:
                for i, d in enumerate(days):
                    sub[i] += sum(e.hours for e in cells.get((p.id, d), []))
            for i in range(7):
                grand[i] += sub[i]
            closed = company in self.collapsed
            vals = [("▸ " if closed else "▾ ") + company, ""] + [fmt_hours(h) if h else "" for h in sub] + \
                   [fmt_hours(sum(sub)) if sum(sub) else ""]
            for c, text in enumerate(vals):
                lbl = self._label(self.inner, row, c, text, COMPANY_BG, anchor="w" if c == 0 else "center",
                                  bold=True)
                texts[c].append(text)
                lbl.bind("<Button-1>", lambda e, co=company: self.toggle_company(co))
            row += 1
            if closed:
                continue
            for p in plist:
                self.rows.append(p.id)
                fg = "gray" if p.archived else "black"
                name = self._label(self.inner, row, 0, "      " + p.name, CELL_BG, anchor="w", fg=fg)
                status = self._label(self.inner, row, 1, p.status, CELL_BG, fg=fg)
                texts[0].append("      " + p.name)
                texts[1].append(p.status)
                for w in (name, status):
                    w.bind("<Double-1>", lambda e, pid=p.id: self.edit_project(pid))
                    for seq in ("<Button-3>", "<Button-2>"):
                        w.bind(seq, lambda e, pid=p.id: self.cell_menu(e, pid, None))
                total = 0.0
                for i, d in enumerate(days):
                    es = cells.get((p.id, d), [])
                    total += sum(e.hours for e in es)
                    text = format_cell(es, cats) + (" •" if any(e.note for e in es) else "")
                    mark = data.get_mark(p.id, d)
                    bg = MARK_COLORS.get(mark) or (WEEKEND_BG if d.weekday() >= 5 else CELL_BG)
                    lbl = self._label(self.inner, row, 2 + i, text, bg, fg=fg)
                    texts[2 + i].append(text)
                    self.cells[(p.id, i)] = lbl
                    lbl.bind("<Button-1>", lambda e, pid=p.id, idx=i: self.on_click(pid, idx))
                    lbl.bind("<Double-1>", lambda e, pid=p.id, idx=i: self.open_cell_dialog(pid, idx))
                    for seq in ("<Button-3>", "<Button-2>"):
                        lbl.bind(seq, lambda e, pid=p.id, idx=i: self.cell_menu(e, pid, idx))
                text = fmt_hours(total) if total else ""
                self._label(self.inner, row, 9, text, CELL_BG, bold=True, fg=fg)
                texts[9].append(text)
                row += 1

        vals = ["TOTAL", ""] + [fmt_hours(h) for h in grand] + [fmt_hours(sum(grand))]
        for c, text in enumerate(vals):
            bg = TOTAL_BG
            if 2 <= c <= 8 and grand[c - 2] >= FULL_DAY_HOURS:
                bg = FULL_DAY_BG
            lbl = self._label(self.inner, row, c, text, bg, anchor="w" if c == 0 else "center", bold=True)
            texts[c].append(text)
            if 2 <= c <= 8:
                for seq in ("<Button-3>", "<Button-2>"):
                    lbl.bind(seq, lambda e, i=c - 2: self.day_menu(e, i))

        for c, minw in enumerate(self.MIN_WIDTHS):
            w = max([minw] + [self.bold.measure(t) + 16 for t in texts[c]])
            self.header.columnconfigure(c, minsize=w)
            self.inner.columnconfigure(c, minsize=w)
        self.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(yview)

    def toggle_company(self, company):
        if not self.commit_edit():
            return
        if company in self.collapsed:
            self.collapsed.discard(company)
        else:
            self.collapsed.add(company)
        self.refresh()

    def _see(self, widget):
        self.update_idletasks()
        total = max(1, self.inner.winfo_height())
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        y, h = widget.winfo_y(), widget.winfo_height()
        if y < top:
            self.canvas.yview_moveto(y / total)
        elif y + h > top + height:
            self.canvas.yview_moveto((y + h - height) / total)

    # ---- mouse
    def on_click(self, pid, idx):
        if self.editor is not None:
            if self.edit_pos == (pid, idx):
                return "break"
            if not self.commit_edit():
                return "break"
        self.after_idle(lambda: self.start_edit(pid, idx))
        return "break"

    def cell_menu(self, event, pid, idx):
        if not self.commit_edit():
            return
        p = self.app.data.projects.get(pid)
        if not p:
            return
        menu = tk.Menu(self, tearoff=False)
        if idx is not None:
            d = self.days[idx]
            has = bool(self.app.data.entries_for(pid, d))
            state = "normal" if has else "disabled"
            for m in MARKS + [""]:
                menu.add_command(label=MARK_LABELS[m], command=lambda m=m: self.mark_cells([(pid, d)], m))
            menu.add_separator()
            menu.add_command(label=f"Entries && notes for {d:%a %m/%d}…",
                             command=lambda: self.open_cell_dialog(pid, idx))
            menu.add_command(label="Clear this day", state=state, command=lambda: self.clear_cell(pid, idx))
            menu.add_separator()
            self._add_bulk_menus(menu, idx)
            menu.add_separator()
        menu.add_command(label="Edit project…", command=lambda: self.edit_project(pid))
        menu.add_command(label="Restore project" if p.archived else "Archive project (hide)",
                         command=lambda: self.toggle_archive(p))
        menu.tk_popup(event.x_root, event.y_root)

    def day_menu(self, event, idx):
        if not self.commit_edit():
            return
        menu = tk.Menu(self, tearoff=False)
        self._add_bulk_menus(menu, idx)
        menu.tk_popup(event.x_root, event.y_root)

    def _add_bulk_menus(self, menu, idx):
        d = self.days[idx]
        day_menu = tk.Menu(menu, tearoff=False)
        week_menu = tk.Menu(menu, tearoff=False)
        for m in MARKS + [""]:
            day_menu.add_command(label=MARK_LABELS[m], command=lambda m=m: self.mark_cells(self._cells_with_hours(
                d, d), m))
            week_menu.add_command(label=MARK_LABELS[m], command=lambda m=m: self.mark_cells(self._cells_with_hours(
                self.days[0], self.days[6]), m))
        menu.add_cascade(label=f"Color all hours on {d:%a %m/%d}", menu=day_menu)
        menu.add_cascade(label="Color all hours this week", menu=week_menu)

    def _cells_with_hours(self, a, b):
        """(project, day) of every cell with hours between a and b, plus any cell already colored."""
        cells = {(e.project_id, e.date) for e in self.app.data.entries_between(a, b)}
        cells |= {k for k in self.app.data.marks if a <= k[1] <= b}
        return sorted(cells)

    def mark_cells(self, cells, mark):
        for pid, d in cells:
            self.app.data.set_mark(pid, d, mark)
        self.app.changed()

    # ---- inline editing
    def start_edit(self, pid, idx):
        self.cancel_edit()
        lbl = self.cells.get((pid, idx))
        if lbl is None or not lbl.winfo_exists():
            return
        self._see(lbl)
        es = self.app.data.entries_for(pid, self.days[idx])
        ed = tk.Entry(self.inner, justify="center", relief="solid", bd=1, font=self.font)
        ed.insert(0, format_cell(es, self.app.data.categories))
        ed.select_range(0, "end")
        ed.place(in_=lbl, x=0, y=0, relwidth=1, relheight=1)
        ed.focus_set()
        self.editor = ed
        self.edit_pos = (pid, idx)
        for seq, move in [("<Return>", (1, 0)), ("<KP_Enter>", (1, 0)), ("<Down>", (1, 0)), ("<Up>", (-1, 0)),
                          ("<Tab>", (0, 1)), ("<Shift-Tab>", (0, -1)), ("<ISO_Left_Tab>", (0, -1))]:
            ed.bind(seq, lambda e, mv=move: self._commit_and_move(*mv))
        for key, m in [("1", "green"), ("2", "yellow"), ("3", "red"), ("0", "")]:
            ed.bind(f"<Control-Key-{key}>", lambda e, m=m: self._mark_from_editor(m))
        ed.bind("<Escape>", lambda e: self.cancel_edit())
        ed.bind("<FocusOut>", lambda e: self.after_idle(self.commit_edit))

    def cancel_edit(self):
        if self.editor is not None:
            ed, self.editor = self.editor, None
            self.edit_pos = None
            try:
                ed.destroy()
            except tk.TclError:
                pass

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
        pid, idx = self.edit_pos
        text = self.editor.get()
        data = self.app.data
        res = parse_cell(text, data.categories)
        if text.strip() and not res.amounts:
            messagebox.showerror("Hours", f"Can't read '{text}'.\n\n" + SHORTHAND_HELP.split("\n\n")[1],
                                 parent=self)
            if self.editor is not None:
                self.editor.focus_set()
            return False
        old = format_cell(data.entries_for(pid, self.days[idx]), data.categories)
        self.cancel_edit()
        if text.strip() == old and not res.unknown:
            return True
        data.set_cell(pid, self.days[idx], [(c, h) for c, h in res.amounts if h], extra_note=res.note)
        self.app.changed()
        return True

    def _commit_and_move(self, drow, dcol):
        if self.editor is None:
            return "break"
        pid, idx = self.edit_pos
        if not self.commit_edit():
            return "break"
        if pid not in self.rows:
            return "break"
        r = self.rows.index(pid) + drow
        idx += dcol
        if idx > 6:
            idx, r = 0, r + 1
        if idx < 0:
            idx, r = 6, r - 1
        if 0 <= r < len(self.rows):
            self.start_edit(self.rows[r], idx)
        return "break"

    def _mark_from_editor(self, mark):
        if self.editor is None:
            return "break"
        pid, idx = self.edit_pos
        if not self.commit_edit():
            return "break"
        self.mark_cells([(pid, self.days[idx])], mark)
        self.start_edit(pid, idx)
        return "break"

    # ---- actions
    def open_cell_dialog(self, pid, idx):
        self.cancel_edit()
        p = self.app.data.projects.get(pid)
        if p and CellDialog(self, self.app.data, p, self.days[idx]).run():
            self.app.changed()
        return "break"

    def clear_cell(self, pid, idx):
        self.app.data.set_cell(pid, self.days[idx], [])
        self.app.changed()

    def edit_project(self, pid):
        self.cancel_edit()
        p = self.app.data.projects.get(pid)
        if p and ProjectDialog(self, self.app.data, p).run():
            self.app.changed()

    def toggle_archive(self, p):
        p.archived = not p.archived
        self.app.changed()

    def new_project(self):
        self.commit_edit()
        if ProjectDialog(self, self.app.data).run():
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

        cols = ["date", "day", "company", "project", "category", "hours", "mark", "note"]
        frame, self.tree = make_tree(self, cols, ["Date", "Day", "Company", "Project", "Category", "Hours", "Color",
                                                  "Note"],
                                     [90, 45, 110, 220, 100, 60, 70, 360],
                                     anchors={"hours": "e", "day": "center", "mark": "center"})
        frame.pack(fill="both", expand=True)
        self.tree.configure(selectmode="extended")
        for m in MARKS:
            self.tree.tag_configure(m, background=MARK_COLORS[m])
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.tree.bind("<Delete>", lambda e: self.delete())
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(4, 0))
        ttk.Label(bottom, text="Color selected entries (Ctrl/Shift+click to pick several):").pack(side="left")
        for m in MARKS + [""]:
            ttk.Button(bottom, text=m.capitalize() if m else "Clear color",
                       command=lambda m=m: self.mark(m)).pack(side="left", padx=(4, 0))
        self.footer = ttk.Label(bottom, font=("TkDefaultFont", 10, "bold"))
        self.footer.pack(side="right")
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
            mark = data.entry_mark(e)
            self.tree.insert("", "end", iid=str(e.id), tags=(mark,) if mark else (), values=[
                e.date.isoformat(), e.date.strftime("%a"), company, pname, e.category, fmt_hours(e.hours),
                mark, e.note.replace("\n", " ")])
        days = len({r[0].date for r in rows})
        self.footer.config(text=f"{len(rows)} entries   •   {days} days   •   {fmt_hours(total)} hours")

    def _selected(self):
        sel = self.tree.selection()
        return self.app.data.entries.get(int(sel[0])) if sel else None

    def mark(self, mark):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Color", "Select one or more entries first (Ctrl/Shift+click).", parent=self)
            return
        for iid in sel:
            e = self.app.data.entries.get(int(iid))
            if e:
                self.app.data.set_mark(e.project_id, e.date, mark)
        self.app.changed()
        for iid in sel:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def add(self):
        if EntryDialog(self, self.app.data).run():
            self.app.changed()

    def edit(self):
        e = self._selected()
        if e and EntryDialog(self, self.app.data, e).run():
            self.app.changed()

    def delete(self):
        sel = [self.app.data.entries[int(i)] for i in self.tree.selection() if int(i) in self.app.data.entries]
        if not sel:
            return
        msg = (f"Delete {fmt_hours(sel[0].hours)} h on {sel[0].date}?" if len(sel) == 1 else
               f"Delete {len(sel)} entries ({fmt_hours(sum(e.hours for e in sel))} h)?")
        if messagebox.askyesno("Delete", msg, parent=self):
            for e in sel:
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
        ttk.Button(bar, text="Open statistics (text)", command=lambda: open_path(
            storage.stats_text_path_for(self.app.data_path))).pack(side="right")
        ttk.Button(bar, text="Open statistics (XML)", command=lambda: open_path(
            storage.stats_path_for(self.app.data_path))).pack(side="right", padx=4)
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
        for sunday, b in sorted(s.weeks.items(), reverse=True):
            rows.append([f"{sunday:%m/%d/%Y} – {sunday + dt.timedelta(days=6):%m/%d}", fmt_hours(b.hours), b.days,
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
        for i in WEEKDAY_ORDER:
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
        self._migrate_old_default()
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
    def _migrate_old_default(self):
        """Move hours.xml from the first version's default folder to the new default folder."""
        old = storage.old_default_data_path()
        if self.data_path.exists() or not old.exists() or old == self.data_path:
            return
        try:
            import shutil
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, self.data_path)
            old.rename(old.with_name(old.name + ".moved"))
        except OSError:
            pass

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
                                f"Statistics: {storage.stats_text_path_for(self.data_path).name}, "
                                f"{storage.stats_path_for(self.data_path).name}")

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

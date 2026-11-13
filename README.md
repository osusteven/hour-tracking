# Hour Tracker

A small desktop app for logging work hours per company and project. It replaces
the `Hours.xlsx` spreadsheet but keeps its layout: a grid of projects × days
where you type hours such as `8` or `8+2 drive`.

* **Week** – the familiar grid, Sunday to Saturday. Projects are grouped by
  company with daily, company and weekly totals. Click a day and type the
  hours. A day's total turns green at 8 hours or more.
* **Colors** – color any cell (one project on one day) green, yellow or red
  at any time, e.g. green once you have submitted the hours. Empty cells can
  be colored too. Right-click a cell to color it, or color all hours of a day
  or week at once. While typing in a cell: Ctrl+1 green, Ctrl+2 yellow,
  Ctrl+3 red, Ctrl+0 clear. In the Log tab you can color several selected
  entries at once.
* **Log** – every entry as a list (date, project, category, hours, note) with
  date range, project and text filters. Use it to add entries with longer notes.
* **Projects** – company, project, status, budget hours, logged and remaining
  hours, SO, PO, location, contact, spec, code, dates and notes. Archive
  finished projects to hide them from the week grid.
* **Statistics** – today, this and last week, this and last month, this year,
  averages, and tables by month, week, project, company and weekday, split by
  hour category.

## Files

Everything is saved automatically after each change, as plain XML that you
can open in a text editor, a browser or Excel (*Data > Get Data > From XML*):

| File | Contents |
|---|---|
| `hours.xml` | the data: categories, projects, time entries |
| `hours-statistics.txt` | statistics report to read in Notepad: summary, this week, hours not yet green, by month / week / project / company / weekday, budgets |
| `hours-statistics.xml` | the same statistics as XML, regenerated after every save |
| `hours.xml.bak` | copy of the data file from before the first save of each session |

The files go in their own folder, `D:\OneDrive - Motor Systems\Document_s\HourTracker\`.
If `D:\OneDrive - Motor Systems\Document_s` doesn't exist, they go in
`Documents\HourTracker\` instead. *File > New data file* and
*File > Open data file* switch to another file, and the app remembers the last
one. *File > Export entries to CSV* writes a list for Excel.

Example of `hours.xml`:

```xml
<hourTracker version="1">
  <categories>
    <category name="Work" code="" />
    <category name="Drive" code="drive" />
    ...
  </categories>
  <projects>
    <project id="3" company="IAS" name="Sierra 9693" status="Active" archived="false"
             budget="40" so="41835" po="" location="" contact="Joe" spec="" code="" dates="" />
  </projects>
  <entries>
    <entry id="17" date="2026-05-04" project="3" hours="8" category="Work" />
    <entry id="18" date="2026-05-04" project="3" hours="2" category="Drive" note="Drove to site" />
  </entries>
  <cellColors>
    <cell project="3" date="2026-05-04" color="green" />
  </cellColors>
</hourTracker>
```

## Typing hours in the week grid

| You type | Saved as |
|---|---|
| `8` | 8 h Work |
| `8+2 drive` (or `8+2d`) | 8 h Work + 2 h Drive |
| `4+2 admin+1.5 drive` | 4 h Work, 2 h Admin, 1.5 h Drive |
| `1 TS` | 1 h Tech Support |
| `1 presale`, `2 OT` | Presale, Overtime |
| `1:30`, `45m` | 1.5 h, 0.75 h |

**Enter** or **↓** saves and moves down, **Tab** saves and moves right, **Esc**
cancels. Clearing a cell deletes its hours. Right-click or double-click a cell
to see and edit the separate entries for that day and add notes. A `•` in a
cell means it has a note. You can rename the categories and their shorthand
words in *Edit > Hour categories*.

## Install and run (Windows)

1. Install Python 3 from <https://www.python.org/downloads/>. The default
   installer includes Tkinter.
2. Download this repository (or `git clone` it).
3. Double-click **`HourTracker.pyw`**.

To import the old spreadsheet, install `openpyxl` once:

```bash
py -m pip install openpyxl
```

Then use *File > Import from Excel…*.

### Optional: a stand-alone .exe

Run `build_exe.bat`. It produces `dist\HourTracker.exe`, which runs without
Python installed. You can pin it to the taskbar or Start menu.

### macOS / Linux

```bash
python3 HourTracker.pyw
```

On Linux you may need the Tk package first, e.g. `sudo apt install python3-tk`.

You can also start it with a specific data file:
`python3 -m hourtracker path/to/hours.xml`.

## Importing `Hours.xlsx`

*File > Import from Excel…* reads every sheet laid out like the year sheets
(`2026`, `2025`, `old`): a date row at the top, a header row containing
`Project`, the company to the left of the project, and hours in the day
columns.

* Shorthand in cells (`8+3 drive`, `2 admin`, `1 TS`, `10 OT`) is split into
  categories.
* Cells with text that isn't understood (e.g. `4?`) keep the hours and get the
  original text as a note.
* Cells without any hours (`x`, `bethel`, reminders below the project rows)
  are listed in the import report and not imported.
* `Hours`, `SO`, `PO`, `Location`, `Contact`, `Spec`, `Code`, `Dates` and
  `Status` columns are copied to the project.
* Imported projects with no hours in the last 60 days are archived, so only
  current work shows in the week grid.
* Each imported entry remembers its sheet and cell, so importing the same
  workbook again only adds new cells.

The `Time` and `Tracking` sheets aren't hour grids and are skipped.

## Development

```bash
python3 -m unittest discover -s tests
```

The code is in the `hourtracker` package:

* `model.py` – data classes
* `cellparse.py` – the grid shorthand
* `storage.py` – XML load and save
* `stats.py` – statistics
* `excel_import.py` – the Excel importer
* `gui.py` and `dialogs.py` – the Tkinter UI

The app uses only the Python standard library, plus `openpyxl` for the import.

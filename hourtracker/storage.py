"""Load/save the data file (XML) and the app's small settings file.

Data file layout (hours.xml)::

    <hourTracker version="1">
      <categories>
        <category name="Work" code="" />
        <category name="Drive" code="drive" />
      </categories>
      <projects>
        <project id="3" company="IAS" name="Sierra 9693" status="Active"
                 archived="false" budget="40" so="" po="" ... >
          <notes>free text</notes>
        </project>
      </projects>
      <entries>
        <entry id="17" date="2026-05-01" project="3" hours="1.5"
               category="Work" note="..." />
      </entries>
      <cellColors>
        <cell project="3" date="2026-05-01" color="green" />
      </cellColors>
    </hourTracker>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .model import PROJECT_TEXT_FIELDS, Category, Entry, Project, TimeData, fmt_hours

FORMAT_VERSION = "1"


# ---------------------------------------------------------------------- paths
def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "HourTracker"


# Preferred parent folders for the data, first existing one wins.  The files
# go into a "HourTracker" subfolder there, never loose in the folder itself.
DATA_SUBFOLDER = "HourTracker"
PREFERRED_DATA_DIRS = [
    Path(r"D:\OneDrive - Motor Systems\Document_s"),
    Path(r"D:\OneDrive - Motor Systems\Documents"),
]


def old_default_data_path() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / DATA_SUBFOLDER / "hours.xml"


def default_data_path() -> Path:
    if sys.platform.startswith("win"):
        for d in PREFERRED_DATA_DIRS:
            if d.is_dir():
                return d / DATA_SUBFOLDER / "hours.xml"
    return old_default_data_path()


def companion_files(data_path: Path):
    """The data file and every file the app writes next to it."""
    data_path = Path(data_path)
    return [data_path, data_path.with_name(data_path.name + ".bak"), stats_path_for(data_path),
            stats_text_path_for(data_path)]


def _is_data_file(path: Path) -> bool:
    try:
        return ET.parse(str(path)).getroot().tag == "hourTracker"
    except (OSError, ET.ParseError):
        return False


def migrate_to(target: Path, candidates) -> Optional[Path]:
    """Move the data file (and its statistics/backup files) from an earlier
    location to ``target`` if ``target`` doesn't exist yet.  Returns the old
    data file path when something was moved."""
    target = Path(target)
    if target.exists():
        return None
    for old in candidates:
        old = Path(old)
        if old == target or not old.is_file() or not _is_data_file(old):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        for src, dst in zip(companion_files(old), companion_files(target)):
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))
        return old
    return None


def earlier_default_paths():
    """Where earlier versions of the app put the data file."""
    return [d / "hours.xml" for d in PREFERRED_DATA_DIRS] + [old_default_data_path()]


def stats_path_for(data_path: Path) -> Path:
    """statistics file that sits next to the data file: hours.xml -> hours-statistics.xml"""
    data_path = Path(data_path)
    return data_path.with_name(f"{data_path.stem}-statistics.xml")


def stats_text_path_for(data_path: Path) -> Path:
    """plain text statistics report: hours.xml -> hours-statistics.txt"""
    data_path = Path(data_path)
    return data_path.with_name(f"{data_path.stem}-statistics.txt")


def load_config() -> dict:
    try:
        with open(config_dir() / "config.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------- XML
def _num(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip() == "":
        return None
    return float(value)


def load(path: Path) -> TimeData:
    tree = ET.parse(str(path))
    root = tree.getroot()
    if root.tag != "hourTracker":
        raise ValueError(f"{path} is not an Hour Tracker data file")
    data = TimeData()

    cats_el = root.find("categories")
    if cats_el is not None and len(cats_el):
        data.categories = [Category(c.get("name", ""), c.get("code", "")) for c in cats_el.findall("category")
                           if c.get("name")]

    for p in root.iterfind("projects/project"):
        kwargs = {attr: p.get(attr, "") for attr, _ in PROJECT_TEXT_FIELDS}
        notes_el = p.find("notes")
        proj = Project(
            id=int(p.get("id")),
            company=p.get("company", ""),
            name=p.get("name", ""),
            status=p.get("status", "Active"),
            archived=p.get("archived", "false").lower() == "true",
            budget=_num(p.get("budget")),
            notes=(notes_el.text or "") if notes_el is not None else "",
            **kwargs,
        )
        data.projects[proj.id] = proj

    for e in root.iterfind("entries/entry"):
        entry = Entry(
            id=int(e.get("id")),
            date=dt.date.fromisoformat(e.get("date")),
            project_id=int(e.get("project")),
            hours=float(e.get("hours", "0")),
            category=e.get("category") or data.default_category,
            note=e.get("note", ""),
            source=e.get("source", ""),
        )
        data.entries[entry.id] = entry
        if e.get("mark"):  # files from the version that colored entries
            data.marks.setdefault((entry.project_id, entry.date), e.get("mark"))

    for c in root.iterfind("cellColors/cell"):
        if c.get("color"):
            data.marks[(int(c.get("project")), dt.date.fromisoformat(c.get("date")))] = c.get("color")

    data.next_project_id = max(data.projects, default=0) + 1
    data.next_entry_id = max(data.entries, default=0) + 1
    return data


def to_xml(data: TimeData) -> ET.Element:
    root = ET.Element("hourTracker", version=FORMAT_VERSION,
                      saved=dt.datetime.now().replace(microsecond=0).isoformat())
    cats = ET.SubElement(root, "categories")
    for c in data.categories:
        ET.SubElement(cats, "category", name=c.name, code=c.code)

    projects = ET.SubElement(root, "projects")
    for p in data.sorted_projects():
        attrs = {
            "id": str(p.id),
            "company": p.company,
            "name": p.name,
            "status": p.status,
            "archived": "true" if p.archived else "false",
            "budget": "" if p.budget is None else fmt_hours(p.budget),
        }
        for attr, _ in PROJECT_TEXT_FIELDS:
            attrs[attr] = getattr(p, attr)
        el = ET.SubElement(projects, "project", attrs)
        if p.notes:
            ET.SubElement(el, "notes").text = p.notes

    entries = ET.SubElement(root, "entries")
    for e in sorted(data.entries.values(), key=lambda e: (e.date, e.project_id, e.id)):
        attrs = {
            "id": str(e.id),
            "date": e.date.isoformat(),
            "project": str(e.project_id),
            "hours": fmt_hours(e.hours),
            "category": e.category,
        }
        if e.note:
            attrs["note"] = e.note
        if e.source:
            attrs["source"] = e.source
        ET.SubElement(entries, "entry", attrs)

    colors = ET.SubElement(root, "cellColors")
    for (pid, day), color in sorted(data.marks.items()):
        if pid in data.projects:
            ET.SubElement(colors, "cell", project=str(pid), date=day.isoformat(), color=color)
    return root


def write_xml(root: ET.Element, path: Path, backup: bool = False) -> None:
    """Write atomically (temp file + rename) so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(ET, "indent"):
        ET.indent(root, space="  ")
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".xml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(data: TimeData, path: Path, backup: bool = False) -> None:
    """Save the data file and regenerate the statistics files next to it."""
    from .report import stats_text  # local imports to avoid a cycle
    from .stats import stats_xml

    write_xml(to_xml(data), path, backup=backup)
    write_xml(stats_xml(data), stats_path_for(path))
    write_text(stats_text(data, data_file=Path(path).name), stats_text_path_for(path))


def write_text(text: str, path: Path) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=str(path.parent))
    try:
        # utf-8 with BOM so Notepad shows it right; CRLF line ends on Windows
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline=None) as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

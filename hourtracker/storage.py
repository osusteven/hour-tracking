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


def default_data_path() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "HourTracker" / "hours.xml"


def stats_path_for(data_path: Path) -> Path:
    """statistics file that sits next to the data file: hours.xml -> hours-statistics.xml"""
    data_path = Path(data_path)
    return data_path.with_name(f"{data_path.stem}-statistics.xml")


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
    """Save the data file and regenerate the statistics file next to it."""
    from .stats import stats_xml  # local import to avoid a cycle

    write_xml(to_xml(data), path, backup=backup)
    write_xml(stats_xml(data), stats_path_for(path))

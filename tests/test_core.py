import datetime as dt
import tempfile
import unittest
from pathlib import Path

from hourtracker import stats, storage
from hourtracker.cellparse import format_cell, parse_cell
from hourtracker.model import DEFAULT_CATEGORIES, TimeData, fmt_hours, week_start

D = dt.date


class CellParseTests(unittest.TestCase):
    def p(self, text):
        r = parse_cell(text, DEFAULT_CATEGORIES)
        return r.amounts, r.unknown

    def test_plain_and_shorthand(self):
        self.assertEqual(self.p("8"), ([("Work", 8.0)], []))
        self.assertEqual(self.p("8+2 drive"), ([("Work", 8.0), ("Drive", 2.0)], []))
        self.assertEqual(self.p("4+2 admin+1.5 drive"), ([("Work", 4.0), ("Admin", 2.0), ("Drive", 1.5)], []))
        self.assertEqual(self.p("8+2drive"), ([("Work", 8.0), ("Drive", 2.0)], []))
        self.assertEqual(self.p("8+2D"), ([("Work", 8.0), ("Drive", 2.0)], []))
        self.assertEqual(self.p("1 TS"), ([("Tech Support", 1.0)], []))
        self.assertEqual(self.p("10 OT"), ([("Overtime", 10.0)], []))

    def test_time_formats(self):
        self.assertEqual(self.p("1:30")[0], [("Work", 1.5)])
        self.assertEqual(self.p("45m")[0], [("Work", 0.75)])
        self.assertEqual(self.p("30 min")[0], [("Work", 0.5)])
        self.assertEqual(self.p("2 hours")[0], [("Work", 2.0)])
        self.assertEqual(self.p(".5")[0], [("Work", 0.5)])
        self.assertEqual(self.p("1,5")[0], [("Work", 1.5)])

    def test_unknown_text(self):
        self.assertEqual(self.p("4?"), ([("Work", 4.0)], ["4?"]))
        self.assertEqual(self.p("x"), ([], ["x"]))
        self.assertEqual(self.p("7+drive"), ([("Work", 7.0)], ["drive"]))
        self.assertEqual(self.p(""), ([], []))

    def test_format_roundtrip(self):
        data = TimeData()
        p = data.add_project("IAS", "Test")
        day = D(2026, 5, 4)
        data.set_cell(p.id, day, parse_cell("4+2 admin+1.5 drive", data.categories).amounts)
        text = format_cell(data.entries_for(p.id, day), data.categories)
        self.assertEqual(text, "4+1.5 drive+2 admin")
        self.assertEqual(parse_cell(text, data.categories).total, 7.5)


class ModelTests(unittest.TestCase):
    def test_set_cell_keeps_notes(self):
        data = TimeData()
        p = data.add_project("IAS", "Sierra")
        day = D(2026, 1, 5)
        e = data.add_entry(day, p.id, 3, "Work", note="commissioning")
        data.set_cell(p.id, day, [("Work", 5.0), ("Drive", 2.0)])
        es = data.entries_for(p.id, day)
        self.assertEqual(len(es), 2)
        work = [x for x in es if x.category == "Work"][0]
        self.assertEqual((work.id, work.hours, work.note), (e.id, 5.0, "commissioning"))
        data.set_cell(p.id, day, [])
        self.assertEqual(data.entries_for(p.id, day), [])

    def test_delete_project_removes_entries(self):
        data = TimeData()
        p = data.add_project("A", "B")
        data.add_entry(D(2026, 1, 1), p.id, 1)
        data.delete_project(p.id)
        self.assertEqual(data.entries, {})

    def test_helpers(self):
        self.assertEqual(week_start(D(2026, 11, 11)), D(2026, 11, 8))
        self.assertEqual(week_start(D(2026, 11, 8)), D(2026, 11, 8))
        self.assertEqual(week_start(D(2026, 11, 14)), D(2026, 11, 8))
        self.assertEqual(fmt_hours(8.0), "8")
        self.assertEqual(fmt_hours(1.25), "1.25")
        self.assertEqual(fmt_hours(1 / 3), "0.33")


class StorageTests(unittest.TestCase):
    def test_roundtrip_and_stats_file(self):
        data = TimeData()
        p = data.add_project("IAS", "Sierra <9693> & co", budget=40, so="41835", notes="line1\nline2")
        data.add_entry(D(2026, 5, 4), p.id, 8, "Work", note='said "hi"')
        data.add_entry(D(2026, 5, 4), p.id, 2, "Drive")
        data.add_entry(D(2026, 6, 1), p.id, 1.5, "Work")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hours.xml"
            storage.save(data, path)
            storage.save(data, path, backup=True)
            self.assertTrue((Path(tmp) / "hours.xml.bak").exists())
            self.assertTrue(storage.stats_path_for(path).exists())
            loaded = storage.load(path)
            lp = loaded.projects[p.id]
            self.assertEqual((lp.name, lp.budget, lp.so, lp.notes), (p.name, 40.0, "41835", "line1\nline2"))
            self.assertEqual(sorted((e.date, e.hours, e.category, e.note) for e in loaded.entries.values()),
                             sorted((e.date, e.hours, e.category, e.note) for e in data.entries.values()))
            self.assertEqual(loaded.next_entry_id, 4)
            import xml.etree.ElementTree as ET
            root = ET.parse(storage.stats_path_for(path)).getroot()
            self.assertEqual(root.find("summary").get("totalHours"), "11.5")
            proj = root.find("projects/project")
            self.assertEqual((proj.get("remaining"), proj.get("days")), ("28.5", "2"))

    def test_stats_compute(self):
        data = TimeData()
        p = data.add_project("IAS", "X")
        q = data.add_project("Pulforce", "Y")
        data.add_entry(D(2026, 11, 9), p.id, 8)
        data.add_entry(D(2026, 11, 10), q.id, 4)
        data.add_entry(D(2026, 11, 10), p.id, 2, "Drive")
        data.add_entry(D(2025, 12, 31), p.id, 3)
        s = stats.compute(data, today=D(2026, 11, 11))
        self.assertEqual(s.summary["thisWeek"], 14)
        self.assertEqual(s.summary["thisYear"], 14)
        self.assertEqual(s.total.hours, 17)
        self.assertEqual(s.total.days, 3)
        self.assertEqual(s.companies["IAS"].hours, 13)
        s26 = stats.compute(data, year=2026, today=D(2026, 11, 11))
        self.assertEqual(s26.total.hours, 14)
        self.assertEqual(s26.total.by_category["Drive"], 2)


if __name__ == "__main__":
    unittest.main()

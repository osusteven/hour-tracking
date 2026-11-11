import datetime as dt
import tempfile
import unittest
from pathlib import Path

from hourtracker.model import TimeData

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None


@unittest.skipIf(openpyxl is None, "openpyxl not installed")
class ExcelImportTests(unittest.TestCase):
    def make_workbook(self, path):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "2026"
        ws["A1"] = "Work Projects"
        ws["B2"] = "Company"
        ws["E2"] = dt.datetime(2026, 1, 5)
        ws["F2"] = "=E2+1"  # no cached value, like a file not saved by Excel
        ws["G2"] = "=F2+1"
        for col, head in zip("CD", ["Project", "Hours"]):
            ws[f"{col}3"] = head
        ws["B4"], ws["C4"], ws["D4"], ws["E4"], ws["F4"] = "IAS", "Sierra 9693", 40, 8, "4+2 drive"
        ws["B5"], ws["C5"], ws["G5"] = "IAS", "Tech Support", "1 TS"
        ws["B6"], ws["C6"], ws["E6"] = "Pulforce", "Polyply", "4?"
        ws["F8"] = "ask about the PO"  # free text row without a project
        wb.create_sheet("Notes")["A1"] = "nothing here"
        wb.save(path)

    def test_import(self):
        from hourtracker.excel_import import import_workbook, list_sheets
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "Hours.xlsx")
            self.make_workbook(path)
            infos = {s.name: s for s in list_sheets(path)}
            self.assertTrue(infos["2026"].is_grid)
            self.assertFalse(infos["Notes"].is_grid)
            self.assertEqual(infos["2026"].last_date, dt.date(2026, 1, 7))

            data = TimeData()
            rep = import_workbook(path, data, today=dt.date(2026, 1, 31))
            self.assertEqual(rep.projects_added, 3)
            self.assertEqual(rep.hours_added, 8 + 4 + 2 + 1 + 4)
            sierra = data.find_project("IAS", "Sierra 9693")
            self.assertEqual(sierra.budget, 40)
            cats = sorted((e.date.day, e.category, e.hours) for e in data.entries.values()
                          if e.project_id == sierra.id)
            self.assertEqual(cats, [(5, "Work", 8), (6, "Drive", 2), (6, "Work", 4)])
            ts = data.find_project("IAS", "Tech Support")
            self.assertEqual([(e.date, e.category) for e in data.entries.values() if e.project_id == ts.id],
                             [(dt.date(2026, 1, 7), "Tech Support")])
            poly = data.find_project("Pulforce", "Polyply")
            self.assertIn("4?", [e for e in data.entries.values() if e.project_id == poly.id][0].note)
            self.assertEqual(len(rep.skipped), 1)
            self.assertEqual(rep.archived, [])

            again = import_workbook(path, data)
            self.assertEqual(again.entries_added, 0)
            self.assertEqual(again.already_imported, 4)

    def test_old_projects_archived(self):
        from hourtracker.excel_import import import_workbook
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "Hours.xlsx")
            self.make_workbook(path)
            data = TimeData()
            rep = import_workbook(path, data, today=dt.date(2026, 6, 1))
            self.assertEqual(len(rep.archived), 3)
            self.assertTrue(all(p.archived for p in data.projects.values()))


if __name__ == "__main__":
    unittest.main()

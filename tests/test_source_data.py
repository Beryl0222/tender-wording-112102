"""检查交换资料的标识、文本和跨表引用。"""

import json
from pathlib import Path
import unittest


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class SourceDataTest(unittest.TestCase):
    def setUp(self):
        self.tables = {}
        for path in sorted(DATA_DIR.glob("*.json")):
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(rows, list)
            self.assertTrue(rows, path.name)
            by_id = {}
            for row in rows:
                identifier = row["id"]
                self.assertIsInstance(identifier, str, path.name)
                self.assertNotIn(identifier, by_id, path.name)
                if "text" in row:
                    self.assertIsInstance(row["text"], str, path.name)
                by_id[identifier] = row
            self.tables[path.stem] = by_id

    def test_exchange_tables_present(self):
        self.assertEqual(set(self.tables), {'briefs', 'submissions'})

    def test_related_source_records(self):
        data = self.tables
        briefs = data["briefs"]
        self.assertIn(briefs["Q25"]["pair"], briefs)
        self.assertIn(briefs["Q27"]["pair"], briefs)
        tenders = data["submissions"]
        for key in ("T1", "T2", "T3", "T4"):
            self.assertTrue(set(tenders[key]["proof"]) <= tenders.keys())
        self.assertIn("E1", set(tenders["T1"]["proof"]) & set(tenders["T2"]["proof"]))
        self.assertLess(tenders["H1"]["deadline"], briefs["Q25"]["received_at"])

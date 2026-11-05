"""证据链、持久化、图表规格的回归测试。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

# 测试必须在 import storage 前切到独立 SQLite，避免污染本地 data/app.db
_TEST_DB_DIR = tempfile.mkdtemp(prefix="tanyuan-feature-db-")
os.environ.setdefault("APP_DB_PATH", str(Path(_TEST_DB_DIR) / "app.db"))

from api import evidence, storage
from tools.chart_report import build_chart_report
from tools.chart_utils import build_chart_spec


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_db = storage._DB_PATH
        storage._DB_PATH = self.root / "app.db"
        storage.init_db()
        evidence.reset("unit")

    def tearDown(self):
        storage._DB_PATH = self.old_db
        evidence.clear("unit")
        self.tmp.cleanup()

    def test_evidence_ids_and_snapshot(self):
        first = evidence.add("unit", {"type": "web", "title": "A", "snippet": "s1"})
        second = evidence.add("unit", {"type": "database", "title": "B"})
        self.assertEqual((first, second), ("S1", "S2"))
        duplicate = evidence.add("unit", {"type": "web", "title": "A", "snippet": "s1"})
        self.assertEqual(duplicate, "S1")
        items = evidence.snapshot("unit")
        self.assertEqual([item["id"] for item in items], ["S1", "S2"])
        evidence.clear("unit")
        self.assertEqual(evidence.snapshot("unit"), [])

    def test_storage_lifecycle(self):
        storage.create_run("t1", "原始问题", "原始问题\n提示", True)
        storage.append_event("t1", {
            "event": "session_created", "message": "created",
            "data": {"path": "/tmp/session_t1"}, "timestamp": "2026-01-01T00:00:00+00:00",
        })
        storage.append_event("t1", {
            "event": "task_result", "message": "done",
            "data": {"result": "# 报告", "degraded": False,
                     "evidence": [{"id": "S1", "type": "database", "title": "SQL"}]},
            "timestamp": "2026-01-01T00:00:01+00:00",
        })
        run = storage.get_run("t1")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["result"], "# 报告")
        self.assertEqual(run["evidence"][0]["id"], "S1")
        self.assertEqual(len(run["events"]), 2)
        self.assertEqual(storage.list_runs()[0]["event_count"], 2)
        storage.delete_run("t1")
        self.assertIsNone(storage.get_run("t1"))

    def test_chart_spec_line_and_bar(self):
        bar = build_chart_spec(["类别", "数量"], [["A", 3], ["B", 5]], "测试")
        self.assertEqual(bar["type"], "bar")
        self.assertEqual(bar["xAxis"], ["A", "B"])
        self.assertEqual(bar["series"][0]["data"], [3, 5])
        line = build_chart_spec(["日期", "金额"], [["2026-01-01", 3], ["2026-01-02", 5]])
        self.assertEqual(line["type"], "line")
        donut = build_chart_spec(["类别", "数量"], [["A", 3], ["B", 5]], query="各分类占比")
        self.assertEqual(donut["type"], "donut")
        heatmap = build_chart_spec(
            ["治疗领域", "剂型", "数量"],
            [["心血管", "片剂", 2], ["抗生素", "胶囊剂", 1]],
            query="做热力图",
        )
        self.assertEqual(heatmap["type"], "heatmap")
        self.assertEqual(heatmap["fields"][2]["role"], "metric")
        self.assertEqual(heatmap["heatmap"]["xField"], "治疗领域")

    def test_chart_report_pdf(self):
        run = {
            "query": "生成各类别数量图表",
            "evidence": [{
                "title": "SQL", "sql": "SELECT 1", "columns": ["类别", "数量"],
                "rows": [["A", 3], ["B", 5]], "rowCount": 2,
                "chart": {"type": "bar", "title": "测试图表", "xAxis": ["A", "B"],
                          "series": [{"name": "数量", "data": [3, 5]}]},
            }],
        }
        target = self.root / "chart.pdf"
        build_chart_report(run, target)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes()[:5], b"%PDF-")


if __name__ == "__main__":
    unittest.main()

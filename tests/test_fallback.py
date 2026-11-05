"""反思上限后的确定性兜底输出测试。"""
import unittest

from api import evidence
from tools.fallback import build_fallback, clean_placeholders


class FallbackTests(unittest.TestCase):
    def tearDown(self):
        evidence.clear("fallback-test")

    def test_clean_placeholders(self):
        cleaned = clean_placeholders("结论：{{销售额}}，TODO 待补充，正文保留。")
        self.assertNotIn("TODO", cleaned)
        self.assertNotIn("待补充", cleaned)
        self.assertIn("正文保留", cleaned)

    def test_fallback_includes_evidence_and_reasons(self):
        evidence.reset("fallback-test")
        evidence.add("fallback-test", {
            "type": "database",
            "title": "SQL 查询结果",
            "columns": ["therapeutic_area", "drug_count"],
            "rows": [["心血管", 2]],
            "sql": "SELECT therapeutic_area, COUNT(*) FROM drugs",
        })
        text = build_fallback(
            thread_id="fallback-test",
            query="统计药品数量",
            best_report="## 初步结果\n\n待补充",
            issues=["报告存在未填充占位符", "缺少 knowledge 的检索结果"],
            require_pdf=True,
            pdf_produced=False,
        )
        self.assertIn("兜底结果说明", text)
        self.assertIn("心血管", text)
        self.assertIn("未达标说明", text)
        self.assertIn("PDF 附件未生成", text)
        self.assertNotIn("待补充", text)


if __name__ == "__main__":
    unittest.main()

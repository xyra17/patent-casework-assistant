"""语义层配置、字段角色、层级与图表能力的测试。"""
import unittest
from pathlib import Path

from tools.chart_utils import build_chart_spec
from tools.semantic_layer import SemanticLayer
from tools.semantic_query import SemanticQueryEngine


class SemanticLayerTests(unittest.TestCase):
    def setUp(self):
        self.layer = SemanticLayer(Path("tests/fixtures/legacy_semantic.yaml"))
        self.layer._introspect_enabled = False

    def test_catalog_roles_metrics_relationships(self):
        snapshot = self.layer.snapshot()
        self.assertIn("drugs", snapshot["tables"])
        self.assertIn("therapeutic_area", snapshot["tables"]["drugs"]["columns"])
        self.assertGreaterEqual(len(snapshot["metrics"]), 4)
        self.assertEqual(snapshot["relationships"][0]["type"], "many_to_one")

    def test_annotate_hierarchy_and_capabilities(self):
        annotated = self.layer.annotate_columns(["therapeutic_area", "drug_count"], table="drugs")
        self.assertEqual(annotated["therapeutic_area"]["role"], "dimension")
        self.assertEqual(annotated["therapeutic_area"]["label"], "治疗领域")
        self.assertEqual(annotated["drug_count"]["role"], "metric")
        hierarchy = self.layer.hierarchy_for("drugs", "therapeutic_area")
        self.assertEqual(hierarchy["name"], "product")
        self.assertEqual(hierarchy["next"], "dosage_form")
        caps = {item["type"]: item for item in self.layer.capabilities()}
        self.assertTrue(caps["heatmap"]["available"])
        self.assertFalse(caps["map"]["available"])
        self.assertFalse(caps["funnel"]["available"])

    def test_semantic_query_plan_join_and_metric_expression(self):
        engine = SemanticQueryEngine(self.layer)
        plan = engine.plan_query("total_sales", ["therapeutic_area"])
        self.assertIn("SUM(t0.total_amount)", plan["sql"])
        self.assertIn("LEFT JOIN `drugs`", plan["sql"])
        self.assertIn("GROUP BY", plan["sql"])
        plan2 = engine.plan_query("药品数量", ["治疗领域"])
        self.assertIn("COUNT(DISTINCT t0.drug_id)", plan2["sql"])
        self.assertEqual(plan2["dimensions"][0]["name"], "therapeutic_area")

    def test_semantic_hierarchy_drilldown_plan(self):
        engine = SemanticQueryEngine(self.layer)
        plan, hierarchy = engine.plan_drilldown("product", "therapeutic_area", "心血管", "drug_count")
        self.assertIn("`dosage_form`", plan["sql"])
        self.assertEqual(plan["params"][0], "心血管")
        self.assertEqual(hierarchy["next"], "dosage_form")
        plan_time, time_meta = engine.plan_drilldown("time", "year", "2025", "total_sales")
        self.assertIn("QUARTER(t0.sale_date)", plan_time["sql"])
        self.assertEqual(time_meta["next"], "quarter")

    def test_multi_metric_plan(self):
        engine = SemanticQueryEngine(self.layer)
        plan = engine.plan_multi(["drug_count", "total_sales"], ["therapeutic_area"])
        self.assertIn("WITH", plan["sql"])
        self.assertIn("dim_keys", plan["sql"])
        self.assertIn("COUNT(DISTINCT", plan["sql"])
        self.assertIn("SUM(t0.total_amount)", plan["sql"])
        same_table = engine.plan_multi(["sales_quantity", "total_sales"], ["region"])
        self.assertTrue(same_table["sql"].startswith("SELECT * FROM ("))

    def test_semantic_roles_feed_chart_spec(self):
        columns = ["therapeutic_area", "dosage_form", "drug_count"]
        rows = [["心血管", "片剂", 2], ["抗生素", "胶囊剂", 1]]
        meta = self.layer.annotate_columns(columns, table="drugs")
        spec = build_chart_spec(columns, rows, "测试", query="热力图", field_meta=meta)
        self.assertEqual(spec["type"], "heatmap")
        self.assertEqual(spec["fields"][0]["label"], "治疗领域")
        self.assertTrue(spec["heatmap"])


if __name__ == "__main__":
    unittest.main()

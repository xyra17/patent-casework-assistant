"""语义层：把物理表结构翻译成业务角色、指标、关系、层级与图表能力。

配置文件 semantic/model.yaml 是显式语义（优先）；MySQL 实际结构用于补全
未知表/字段。图表、数据库助手和前端数据字典都读取同一个 catalog，避免
“列名猜图”与“SQL 助手各猜一套”。
"""
from __future__ import annotations

import os
import json
import re
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
from utils.path_utils import PROJECT_ROOT, RESOURCE_ROOT
_DEFAULT_MODEL = (PROJECT_ROOT / 'semantic' / 'model.yaml' if (PROJECT_ROOT / 'semantic' / 'model.yaml').is_file()
                  else RESOURCE_ROOT / 'semantic' / 'model.yaml')

_ID_ROLES = {"id", "foreign_key"}
_GEO_TOKENS = ("region", "province", "city", "area", "region", "地区", "省份", "城市", "区域", "仓库", "门店")
_STAGE_TOKENS = ("stage", "step", "阶段", "环节")
_SOURCE_TOKENS = ("source", "from", "来源", "上游")
_TARGET_TOKENS = ("target", "to", "去向", "下游")
_TIME_TOKENS = ("date", "time", "日期", "时间", "月份", "年份", "year", "month", "day")


def _resolve_model_path() -> Path:
    raw = os.getenv("SEMANTIC_MODEL_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else _DEFAULT_MODEL


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "name": "业务数据", "tables": {}, "metrics": {}, "relationships": [], "hierarchies": {}}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("tables", {})
    data.setdefault("metrics", {})
    data.setdefault("relationships", [])
    data.setdefault("hierarchies", {})
    experience = data.get('experience', {})
    if experience.get('task_examples_catalog') == 'department':
        catalog = json.loads((RESOURCE_ROOT / 'config' / 'session-examples.json').read_text(encoding='utf-8'))
        experience['quick_templates'] = catalog['templates']
        experience['example_groups'] = catalog['groups']
        experience['featured_template_ids'] = catalog.get('featured_template_ids', [])
    return data


def _infer_type(sql_type: str) -> str:
    text = (sql_type or "").lower()
    if any(token in text for token in ("int", "decimal", "numeric", "float", "double", "bigint")):
        return "number"
    if any(token in text for token in ("date", "time", "timestamp")):
        return "datetime"
    return "string"


def _infer_role(name: str, sql_type: str = "") -> str:
    text = name.lower()
    if text.endswith("_id") or text == "id":
        return "foreign_key" if text != "id" and "_id" in text else "id"
    if any(token in text for token in _TIME_TOKENS) or _infer_type(sql_type) == "datetime":
        return "time"
    if any(token in text for token in _GEO_TOKENS):
        return "geo"
    if any(token in text for token in _STAGE_TOKENS):
        return "stage"
    if any(token in text for token in _SOURCE_TOKENS):
        return "source"
    if any(token in text for token in _TARGET_TOKENS):
        return "target"
    if _infer_type(sql_type) == "number":
        return "metric"
    return "dimension"


class SemanticLayer:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _resolve_model_path()
        self._lock = threading.RLock()
        self._config = _load_yaml(self.path)
        self._tables = deepcopy(self._config.get("tables", {}))
        self._introspected_at = 0.0
        self._introspect_error = ""
        self._introspect_enabled = os.getenv("SEMANTIC_AUTO_INTROSPECT", "1") != "0"

    def reload(self) -> None:
        with self._lock:
            self._config = _load_yaml(self.path)
            self._tables = deepcopy(self._config.get("tables", {}))
            self._introspected_at = 0.0
            self._introspect_error = ""

    def _introspect(self, max_age: float = 60.0) -> None:
        if not self._introspect_enabled:
            return
        from api import context, source_policy
        policy = source_policy.current_policy()
        if context.get_thread_id() and (policy is None or "db_query" not in policy.active):
            return  # Planning uses configured definitions; introspection requires an active query source.
        if self._introspected_at and time.monotonic() - self._introspected_at < max_age:
            return
        try:
            from tools.mysql_tools import describe_schema

            schema = describe_schema()
            for table, columns in schema.items():
                table_cfg = self._tables.setdefault(
                    table,
                    {
                        "label": table,
                        "description": "由实际数据库自动发现",
                        "columns": {},
                        "source": "introspected",
                    },
                )
                table_cfg.setdefault("source", "config+introspected" if table in self._config.get("tables", {}) else "introspected")
                table_cfg.setdefault("columns", {})
                for col in columns:
                    name = col["name"]
                    existing = table_cfg["columns"].get(name)
                    if existing:
                        existing.setdefault("type", _infer_type(col.get("type", "")))
                        existing.setdefault("nullable", col.get("nullable", True))
                        continue
                    role = _infer_role(name, col.get("type", ""))
                    table_cfg["columns"][name] = {
                        "label": name,
                        "role": role,
                        "type": _infer_type(col.get("type", "")),
                        "sql_type": col.get("type", ""),
                        "nullable": col.get("nullable", True),
                        "description": "由实际数据库自动发现",
                    }
            self._introspected_at = time.monotonic()
            self._introspect_error = ""
        except Exception as exc:  # noqa: BLE001
            self._introspect_error = str(exc)
            self._introspected_at = time.monotonic()

    def _column_index(self) -> dict[str, dict]:
        """列名 → 语义定义；同名列优先配置表，否则取第一个。"""
        index: dict[str, dict] = {}
        configured = self._config.get("tables", {})
        for table, table_cfg in self._tables.items():
            for column, column_cfg in (table_cfg.get("columns") or {}).items():
                item = {
                    "name": column,
                    "table": table,
                    "label": column_cfg.get("label", column),
                    "role": column_cfg.get("role", "dimension"),
                    "type": column_cfg.get("type", ""),
                    "aggregation": column_cfg.get("aggregation"),
                    "unit": column_cfg.get("unit"),
                    "description": column_cfg.get("description", ""),
                }
                if table in configured or column not in index:
                    index[column] = item
        return index

    def annotate_columns(self, columns: list[str], table: str | None = None) -> dict[str, dict]:
        """给 SQL 结果列补语义；别名（cnt、drug_count）按指标/角色推断。"""
        with self._lock:
            self._introspect()
            table_cfg = self._tables.get(table, {}) if table else {}
            table_cols = table_cfg.get("columns", {})
            index = self._column_index()
            metrics = self._config.get("metrics", {})
            result: dict[str, dict] = {}
            for column in columns:
                cfg = table_cols.get(column) or index.get(column)
                if cfg:
                    item = {
                        "name": column,
                        "table": table or cfg.get("table", ""),
                        "label": cfg.get("label", column),
                        "role": cfg.get("role", "dimension"),
                        "type": cfg.get("type", ""),
                        "aggregation": cfg.get("aggregation"),
                        "unit": cfg.get("unit"),
                        "description": cfg.get("description", ""),
                    }
                else:
                    metric = self._metric_for(column, metrics)
                    if metric:
                        item = {
                            "name": column,
                            "table": metric.get("table", table or ""),
                            "label": metric.get("label", column),
                            "role": "metric",
                            "type": metric.get("format", "number"),
                            "aggregation": metric.get("expression", ""),
                            "unit": metric.get("unit"),
                            "description": metric.get("description", ""),
                        }
                    else:
                        role = _infer_role(column)
                        item = {
                            "name": column,
                            "table": table or "",
                            "label": column,
                            "role": role,
                            "type": "number" if role == "metric" else "",
                            "aggregation": None,
                            "unit": None,
                            "description": "",
                        }
                result[column] = item
            return result

    def _metric_for(self, name: str, metrics: dict) -> dict | None:
        lower = name.lower()
        for key, cfg in metrics.items():
            names = [key.lower(), str(cfg.get("label", "")).lower(), *[str(x).lower() for x in cfg.get("synonyms", [])]]
            if lower in names:
                return {**cfg, "key": key}
        return None

    def find_metric(self, name_or_label: str) -> dict | None:
        """按 key、中文标签或同义词查找指标。"""
        text = str(name_or_label or "").strip().lower()
        for key, cfg in self._config.get("metrics", {}).items():
            names = [key.lower(), str(cfg.get("label", "")).lower(), *[str(x).lower() for x in cfg.get("synonyms", [])]]
            if text in names:
                return {**cfg, "key": key}
        return None

    def resolve_field(self, name: str, table: str | None = None) -> dict | None:
        """解析字段（支持 table.column）；返回表、列、角色、标签与表达式。"""
        text = str(name or "").strip()
        explicit_table = table
        if "." in text:
            explicit_table, text = text.split(".", 1)
        self._introspect()
        candidates = []
        for tbl, cfg in self._tables.items():
            if explicit_table and tbl != explicit_table:
                continue
            for col_name, col in (cfg.get("columns") or {}).items():
                names = [col_name.lower(), str(col.get("label", "")).lower(),
                         *[str(x).lower() for x in col.get("synonyms", [])]]
                if text.lower() not in names:
                    continue
                candidates.append({
                    "name": col_name,
                    "table": tbl,
                    "label": col.get("label", col_name),
                    "role": col.get("role", "dimension"),
                    "type": col.get("type", ""),
                    "aggregation": col.get("aggregation"),
                    "unit": col.get("unit"),
                })
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return None
        return None

    def hierarchy_by_name(self, name: str) -> dict | None:
        for key, cfg in self._config.get("hierarchies", {}).items():
            if key == name or cfg.get("label") == name:
                return {"key": key, **cfg}
        return None

    def level_expression(self, hierarchy: dict, level: str) -> str | None:
        expressions = hierarchy.get("level_expressions") or {}
        return expressions.get(level)

    def metric_labels(self) -> list[dict]:
        return [
            {
                "key": key,
                "label": cfg.get("label", key),
                "table": cfg.get("table", ""),
                "expression": cfg.get("expression", ""),
                "format": cfg.get("format", ""),
                "unit": cfg.get("unit", ""),
                "synonyms": cfg.get("synonyms", []),
            }
            for key, cfg in self._config.get("metrics", {}).items()
        ]

    def hierarchy_for(self, table: str, field: str) -> dict | None:
        for name, cfg in self._config.get("hierarchies", {}).items():
            levels = list(cfg.get("levels", []))
            if field in levels and (not cfg.get("table") or cfg.get("table") == table):
                index = levels.index(field)
                return {
                    "name": name,
                    "label": cfg.get("label", name),
                    "levels": levels,
                    "next": levels[index + 1] if index + 1 < len(levels) else None,
                    "description": cfg.get("description", ""),
                }
        return None

    def capabilities(self) -> list[dict]:
        all_tables = self._tables.values()
        roles = {
            col.get("role")
            for table in all_tables
            for col in (table.get("columns") or {}).values()
        }
        has_geo = "geo" in roles
        has_geo_mapping = bool(self._config.get("geo_mapping"))
        has_stage = "stage" in roles
        has_source_target = "source" in roles and "target" in roles
        capabilities = [
            ("bar", "柱状图", True, "分类维度 + 指标"),
            ("hbar", "条形图", True, "分类维度 + 指标"),
            ("line", "折线图", True, "时间/有序维度 + 指标"),
            ("area", "面积图", True, "时间/有序维度 + 指标"),
            ("stackedBar", "堆叠柱状图", True, "两个维度 + 指标"),
            ("pie", "饼图", True, "分类维度 + 指标"),
            ("donut", "环形图", True, "分类维度 + 指标"),
            ("scatter", "散点图", True, "两个数值指标"),
            ("heatmap", "热力图", True, "两个分类维度 + 指标"),
            ("map", "地图", has_geo and has_geo_mapping, "需要 geo 角色 + geo_mapping/GeoJSON" if not has_geo_mapping else "已配置 geo 字段与地图映射"),
            ("funnel", "漏斗图", has_stage, "需要有序 stage 字段/阶段事件表" if not has_stage else "已发现 stage 字段"),
            ("sankey", "桑基图", has_source_target, "需要 source/target 两个角色字段" if not has_source_target else "已发现 source/target 字段"),
        ]
        return [
            {"type": key, "label": label, "available": available, "reason": reason}
            for key, label, available, reason in capabilities
        ]

    def snapshot(self) -> dict:
        with self._lock:
            self._introspect()
            return {
                "version": self._config.get("version", 1),
                "name": self._config.get("name", "业务数据"),
                "description": self._config.get("description", ""),
                "experience": deepcopy(self._config.get("experience", {})),
                "source": "semantic/model.yaml + MySQL introspection" if self._introspected_at else "semantic/model.yaml",
                "introspection_error": self._introspect_error,
                "tables": deepcopy(self._tables),
                "metrics": self.metric_labels(),
                "relationships": deepcopy(self._config.get("relationships", [])),
                "hierarchies": deepcopy(self._config.get("hierarchies", {})),
                "capabilities": self.capabilities(),
                "roles": self._config.get("roles", {}),
            }

    def prompt_summary(self) -> str:
        """给数据库助手/规划器的精简语义说明，避免把整份 catalog 塞进 prompt。"""
        self._introspect()
        lines = ["【数据语义层（来自当前配置）】"]
        lines.append(f"数据集：{self._config.get('name', '业务数据')}。优先使用已定义指标，字段口径如下：")
        lines.append("业务定义（不授予数据源权限）：" + str(self._config.get("description", ""))[:1500])
        for table, cfg in list(self._tables.items())[:8]:
            cols = []
            for name, col in list((cfg.get("columns") or {}).items())[:24]:
                cols.append(f"{name}:{col.get('label', name)}/{col.get('role', 'dimension')}" + ("（" + str(col["description"])[:300] + "）" if col.get("description") else ""))
            if cfg.get("description"): lines.append("表定义：" + str(cfg["description"])[:700])
            lines.append(f"- 表 {table}（{cfg.get('label', table)}）：" + "，".join(cols))
        metrics = self.metric_labels()
        if metrics:
            lines.append("- 指标口径：" + "；".join(
                f"{m['label']}={m['expression']}" for m in metrics[:8]
            ))
        relationships = self._config.get("relationships", [])
        if relationships:
            lines.append("- 关系：" + "；".join(
                f"{r.get('from')} → {r.get('to')}" for r in relationships[:8]
            ))
        lines.append("- 不要假设未列出的表/字段；先 list_sql_tables 确认实际结构。")
        return "\n".join(lines)


semantic_layer = SemanticLayer()

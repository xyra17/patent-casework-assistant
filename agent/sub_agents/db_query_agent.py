"""数据库查询子智能体（TDD §6.3）。"""
from __future__ import annotations

from deepagents.middleware.filesystem import FilesystemPermission

from agent.load_prompt import sub_agents_config
from tools.mysql_tools import execute_sql_query, get_table_data, list_sql_tables
from tools.semantic_layer import semantic_layer
from tools.semantic_query import semantic_drilldown, semantic_query
from tools.casework_tools import query_department_cases

_cfg = sub_agents_config["db_query"]

db_query_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"] + "\n\n" + semantic_layer.prompt_summary(),
    "permissions": [
        # 子智能体只负责检索，不允许写/改/删工作目录文件。
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ],
    "tools": [query_department_cases,list_sql_tables, get_table_data, execute_sql_query, semantic_query, semantic_drilldown],
}

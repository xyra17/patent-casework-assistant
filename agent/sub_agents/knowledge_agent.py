"""内部知识库检索子智能体（TDD §6.4）。"""
from __future__ import annotations

from deepagents.middleware.filesystem import FilesystemPermission

from agent.load_prompt import sub_agents_config
from tools.knowledge_base_tool import search_knowledge_base, list_knowledge_versions, read_knowledge_document
from tools.casework_tools import search_case_archive, read_case_archive_document
from tools.visual_document_tool import analyze_visual_document

_cfg = sub_agents_config["knowledge"]

knowledge_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"],
    "permissions": [
        # 子智能体只负责检索，不允许写/改/删工作目录文件。
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ],
    "tools": [analyze_visual_document,search_case_archive,read_case_archive_document,search_knowledge_base, list_knowledge_versions, read_knowledge_document],
}

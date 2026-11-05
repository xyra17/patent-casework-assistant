"""网络检索子智能体（TDD §6.2）。"""
from __future__ import annotations

from deepagents.middleware.filesystem import FilesystemPermission

from agent.load_prompt import sub_agents_config
from tools.internet_search_tool import internet_search

_cfg = sub_agents_config["web_search"]

web_search_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"],
    "permissions": [
        # 子智能体只负责检索，不允许写/改/删工作目录文件。
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ],
    "tools": [internet_search],
}

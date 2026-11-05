"""全网检索工具 internet_search（TDD §7.1）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

from api import context, evidence, source_policy

# 加载项目根目录的 .env
from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
load_dotenv(_PROJECT_ROOT / ".env")

# 本轮所有网络检索共享预算，默认最多3次；不设最低检索次数。
# 复杂问题按证据缺口交叉检索，简单事实取得充分依据即可停止。
_WEB_SEARCH_LIMIT = int(os.getenv("WEB_SEARCH_LIMIT", "3"))

_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))


def _result_text(result, include_raw_content):
    """Keep the search summary and return exactly the text registered as evidence."""
    summary=str(result.get('content') or '').strip()
    raw=str(result.get('raw_content') or '').strip() if include_raw_content else ''
    # Search providers can return PDF transport bytes or navigation before body.
    # Neither is a replacement for the provider's readable search excerpt.
    if raw.startswith('%PDF-') or ('endobj' in raw[:1500] and 'stream' in raw[:1500]):raw=''
    if raw:
        import re
        body=re.search(r'(?im)^#{1,3}\s+(?:Abstract|Overview|Introduction|README|摘要|概述|介绍)|^Abstract\s*:',raw)
        if body:raw=raw[body.start():]
    parts=[summary[:2400]] if summary else []
    if raw and raw!=summary:parts.append(raw[:3600])
    return '\n\n'.join(parts)


@tool
def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> str:
    """从互联网检索公开信息，返回结构化检索结果。"""
    denial = source_policy.source_denial("web_search")
    if denial:
        return denial
    from casework.privacy import ensure_web_allowed
    try:
        ensure_web_allowed()
    except PermissionError as exc:
        source_policy.record_gap('web_search',str(exc),reason='confidentiality_denied')
        return '禁止执行：'+str(exc)
    if not context.consume_tool_budget("web_search", _WEB_SEARCH_LIMIT):
        source_policy.record_gap("web_search", "网络检索预算已耗尽，尚需核对原问题的证据覆盖", reason="budget_exhausted")
        return (
            f"本轮网络检索已达到上限（{_WEB_SEARCH_LIMIT} 次），"
            "请停止检索并基于已有结果回答。"
        )
    try:
        response = _client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content,
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("web_search", "网络检索服务失败，未取得请求的公开资料", reason="tool_error")
        return f"网络检索失败：{e}"
    results = response.get("results", [])
    if not results:
        source_policy.record_gap("web_search", "网络检索未找到相关资料：" + query, reason="no_hits")
        return "未检索到相关内容"
    blocks = []
    thread_id = context.get_thread_id()
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = _result_text(r, include_raw_content)
        evidence_id = evidence.add(thread_id, {
            "type": "web",
            "title": title or f"网页结果 {i}",
            "url": url,
            "query": query,
            "snippet": content,
        })
        prefix = f"[证据 {evidence_id}] " if evidence_id else ""
        blocks.append(f"{prefix}[{i}] {title}\nURL: {url}\n内容: {content}")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    print(internet_search.invoke({"query": "人工智能 最新进展", "max_results": 2}))

"""反思轮次耗尽后的确定性兜底输出。

不依赖再次调用 LLM：清理占位符，保留当前最优正文，并从证据 store 汇总
已拿到的 SQL 行/网页/知识库片段，说明结果尚未核对完成；详细诊断留在执行记录。即使软校验始终
不通过，用户仍能看到可追溯的部分结果，而不是一段带 TODO 的正文。
"""
from __future__ import annotations

import re

from api import evidence

from tools.placeholders import replace_placeholders
from tools.evidence_refs import citation_issues, normalize_citations


def clean_placeholders(text: str) -> str:
    """去掉模板占位符，保留可读正文。"""
    cleaned = replace_placeholders(text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _table_preview(columns: list, rows: list, limit: int = 3) -> str:
    columns = [str(col) for col in (columns or [])]
    if not columns:
        return ""
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in (rows or [])[:limit]:
        values = []
        for value in row:
            text = "—" if value is None else str(value)
            values.append(text.replace("|", "\\|")[:60])
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _evidence_section(thread_id: str) -> str:
    items = evidence.snapshot(thread_id) if thread_id else []
    if not items:
        return ""
    blocks: list[str] = ["### 已找到的资料"]
    for item in items[:8]:
        title = str(item.get("title") or item.get("type") or "来源")
        kind = item.get("type")
        if kind == "database":
            columns = item.get("columns") or []
            rows = item.get("rows") or []
            if columns:
                blocks.append(f"#### {title} [{item.get('id', '')}]\n\n{_table_preview(columns, rows)}")
                if len(rows)>3 or (item.get('completeness') or {}).get('has_more') is not False:
                    blocks.append('以下为部分查询结果。')
            else:
                blocks.append(f"#### {title} [{item.get('id', '')}]\n\n未取得可展示的记录。")
        elif kind in ("knowledge", "web"):
            snippet = clean_placeholders(str(item.get("snippet") or "")[:700])
            url = item.get("url") or ""
            blocks.append(f"#### {title} [{item.get('id', '')}]\n\n{snippet}" + (f"\n\n来源：{url}" if url else ""))
        else:
            snippet = str(item.get("snippet") or item.get("text") or "")[:400]
            if snippet:
                blocks.append(f"#### {title} [{item.get('id', '')}]\n\n{snippet}")
    return "\n\n".join(blocks)


def build_fallback(
    *,
    thread_id: str,
    query: str,
    best_report: str,
    issues: list[str],
    require_pdf: bool = False,
    pdf_produced: bool = False,
    stop_reason: str = "max_rounds",
    rounds_used: int = 3,
    max_rounds: int = 3,
    evidence_gaps: list[dict] | None = None,
) -> str:
    """保留可查看的部分结果；内部纠错指令不进入业务答复。"""
    items = evidence.snapshot(thread_id) if thread_id else []
    base = normalize_citations(clean_placeholders(best_report), items)
    if citation_issues(base, items):
        base = "部分内容的引用未通过核验，暂不展示。"
    parts: list[str] = []
    if base:
        parts.append(base)
    else:
        parts.append("本次未能完成回答。")

    parts.append("\n\n---\n\n## 兜底结果说明\n")
    reason = {
        "review_unavailable": "核对服务暂时不可用，以下结果尚未核对完成。",
    }.get(stop_reason, "以下结果尚未核对通过。")
    parts.append(reason)
    if require_pdf and not pdf_produced:
        parts.append("\nPDF 附件未生成，正文与数据表仍可查看。")

    # Detailed review issues/gaps remain in reflection events, not in the answer.
    if any('统计' in issue for issue in issues):parts.append("\n部分统计结果尚未核实。")
    section = _evidence_section(thread_id)
    if section:parts.append("\n\n" + section)
    # server.py 用这个标记区分「已完成」与「完成但未达标」，必须保留。
    parts.append("\n\n【未达标说明】")
    result = "".join(parts)
    # Diagnostic text and source snippets can quote the same malformed markup.
    # Display it as an explicit diagnostic, never as a clickable evidence cite.
    from tools.evidence_refs import _CANDIDATE
    available = {item.get('id') for item in items}
    def diagnostic(match):
        raw = match[0]
        return raw if re.fullmatch(r'\[S[1-9]\d*\]', raw) and raw[1:-1] in available else '（无效引用已移除）'
    return _CANDIDATE.sub(diagnostic, result)

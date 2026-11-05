"""Pause a task when the user must supply a business definition."""
import json
from langchain_core.tools import tool

@tool
def ask_task_clarification(question: str, detail: str, evidence_ids: list[str]) -> str:
    """统计口径或关键字段不明确时暂停追问。只问缺失条件，不猜业务含义；来源为本次证据编号。"""
    from api.clarification import record_task_question
    try:
        held=record_task_question(question,detail,evidence_ids)
        return json.dumps({'status':'awaiting_input','question':held,'instruction':'任务已暂停，向用户展示问题，不继续猜测统计。'},ensure_ascii=False)
    except ValueError as exc:return str(exc)

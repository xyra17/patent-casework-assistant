"""数据库结果范围协议；不推测未执行的 COUNT，也不改写自由 SQL。"""
from __future__ import annotations

import json


def sql_is_limited(sql: str) -> bool | None:
    try:
        from tools.sql_ast import bound_tree
        return bound_tree(sql).args.get('limit') is not None
    except Exception:
        return None


def result_scope(sql: str, returned: int, has_more: bool, limit: int,
                 scope: str = 'sql_result') -> dict:
    return {
        'version': 1, 'scope': scope, 'requested_limit': limit,
        'sql_limited': sql_is_limited(sql),
        'returned_rows': returned, 'has_more': has_more,
        'fetch_truncated': has_more,
        # query_rows 指该 SQL 结果行数；绝不是基础业务记录数。
        'query_rows': None if has_more else returned,
        'matched_rows': None,
        'evidence_rows': 0,
    }


def format_table(result: dict, labels: list[str] | None = None) -> str:
    """有界模型视图。完整值保存在证据中，文本中的裁剪必须显式报告。"""
    rows = result.get('rows') or []
    if not isinstance(result.get('completeness'), dict):
        result['completeness'] = {}
    meta = result['completeness']
    defaults = {
        'version': 1, 'scope': 'legacy_unknown', 'returned_rows': len(rows),
        'query_rows': None, 'matched_rows': None, 'has_more': None,
        'fetch_truncated': None, 'sql_limited': None, 'evidence_rows': 0,
    }
    for key, value in defaults.items():
        meta.setdefault(key, value)
    body = [json.dumps(labels or result.get('columns') or [], ensure_ascii=False, default=str)]
    used, shown, cells_cut = len(body[0]), 0, False
    for row in rows[:30]:
        values = []
        row_cut = False
        for value in row:
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + '…[单元格截断]'
                row_cut = True
            values.append(value)
        line = json.dumps(values, ensure_ascii=False, default=str)
        if used + len(line) > 16000:
            break
        body.append(line)
        cells_cut = cells_cut or row_cut
        used += len(line)
        shown += 1
    meta.update(model_rows=shown, model_truncated=shown < len(rows),
                model_cells_truncated=cells_cut)
    notes = [
        '结果范围：' + json.dumps(meta, ensure_ascii=False),
        '范围说明：query_rows 是 SQL 结果行数（聚合时为分组/指标行），matched_rows 为过滤后的基础记录数；null 表示未知。',
        '统计约束：不可从预览/可见样本推算全量 COUNT、SUM、占比；请在相同过滤范围用 SQL 聚合。SQL 自身 LIMIT/Top N 不代表限额前总量；已在 SQL 内计算的聚合值按其过滤与分组解释。',
    ]
    if meta.get('has_more'):
        notes.append('取数已截断：还有未返回的结果。需补查聚合或细分条件；回读证据只能恢复已取回部分。')
    if shown < len(rows) or cells_cut:
        notes.append(f'模型视图已截断：展示 {shown}/{len(rows)} 行；可按证据编号核对保存范围并回读其中的完整值。')
    if not rows:
        body.append('结果没有数据')
    return '\n'.join(notes + body)


def query_error(exc: Exception, *, connection: bool = False) -> str:
    """稳定错误码与纠错建议；不让未知字段被当作连接失败反复重试。"""
    errno = getattr(exc, 'errno', None)
    if isinstance(exc, PermissionError) or errno in {1044, 1045, 1142, 1143}:
        code, retryable, action = 'permission_denied', False, '检查当前来源/规则权限；不得绕过限制'
    elif errno in {1054, 1146}:
        code, retryable, action = 'unknown_schema', False, '核对实际表结构与语义定义；不得擅自替换业务字段'
    elif errno in {1205, 3024} or isinstance(exc, TimeoutError):
        code, retryable, action = 'query_timeout', True, '缩小范围或优化查询后有限重试'
    elif connection or errno in {2003, 2006, 2013}:
        code, retryable, action = 'connection_error', True, '检查连接后有限重试'
    else:
        code, retryable, action = 'query_error', False, '检查查询参数、SQL 或语义定义'
    prefix = '连接数据库失败：' if code == 'connection_error' else '数据库查询失败：'
    return prefix + json.dumps({'status': 'error', 'code': code, 'retryable': retryable,
                               'message': str(exc), 'next_action': action}, ensure_ascii=False)

"""数据库三工具（TDD §7.2）。

- 连接配置从环境变量读取（TDD §12）：charset=utf8mb4，autocommit=True；
- 区分查询错误与连接错误，结果带范围与截断信息。
"""
from __future__ import annotations

import os
import json
import re
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

from api import context, evidence, source_policy
from tools.chart_utils import build_chart_spec, plain_value
from tools.semantic_layer import semantic_layer
from tools.result_contract import result_scope, format_table, query_error

# 加载项目根目录的 .env（保证直接运行本脚本也能读到数据库配置）
from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
load_dotenv(_PROJECT_ROOT / ".env")

# 写操作关键字拦截（TDD §14，仅兜底；主防线是只读账号 + 参数化查询）
_FORBIDDEN_WRITE_PATTERN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|CREATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

# 表名白名单：仅允许字母/数字/下划线，防 SQL 注入（TDD §14）
_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def _record_evidence(title: str, text: str, *, sql: str | None = None,
                     columns: list[str] | None = None, rows: list[tuple] | None = None,
                     chart: dict | None = None, table: str | None = None,
                     completeness: dict | None = None) -> str:
    """登记数据库证据，返回展示编号；无会话上下文时返回空字符串。"""
    thread_id = context.get_thread_id()
    if not thread_id:
        return ""
    safe_rows = [[plain_value(v) for v in row] for row in (rows or [])[:500]]
    if completeness is not None:
        completeness.update(evidence_rows=len(safe_rows), evidence_truncated=len(safe_rows) < len(rows or []))
    snippet_rows = safe_rows[:5]
    snippet = "\n".join(
        " | ".join(str(v) for v in row) for row in snippet_rows
    )[:360]
    semantic = semantic_layer.annotate_columns(columns or [], table)
    return evidence.add(thread_id, {
        "type": "database",
        "title": title,
        "sql": sql or "",
        "columns": columns or [],
        "rows": safe_rows,
        "rowCount": len(rows or []),
        "completeness": completeness,
        "snippet": snippet or text[:800],
        "chart": chart,
        "semantic": semantic,
        "statistics": _statistics(sql or '', columns or [], safe_rows, semantic),
    })


def _statistics(sql, columns, rows, semantic):
    from tools.statistical_facts import build_contract
    schema = {key: col for table in semantic_layer._tables.values() for key, col in table.get('columns', {}).items()}
    definitions = [{**metric, 'key': key} for key, metric in semantic_layer._config.get('metrics', {}).items()]
    return build_contract({'sql': sql, 'columns': columns, 'rows': rows, 'semantic': semantic}, definitions, schema)


def _get_connection() -> mysql.connector.MySQLConnection:
    """按 TDD §12 建立数据库连接：utf8mb4 + autocommit。

    use_pure=True 走纯 Python 实现，规避 C 扩展在 asyncio 事件循环里的崩溃。
    """
    source_policy.require_source("db_query")
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "department_workbench"),
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        autocommit=True,
        use_pure=True,
        connection_timeout=15,
        read_timeout=30,
        write_timeout=30,
    )


def check_mysql_connection() -> tuple[bool, str]:
    """启动页状态卡使用：只做最小连接检查，不读取业务表。"""
    try:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            conn.close()
        return True, "已连接"
    except Exception:  # noqa: BLE001
        return False, "未连接"


def list_sql_tables() -> str:
    """列出数据库所有表：有表返回「可用的表：a,b,c」，无表返回「没有可用的表」。"""
    denial = source_policy.source_denial("db_query")
    if denial:
        return denial
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e, connection=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        result = "没有可用的表" if not tables else "可用的表：" + ",".join(tables)
        evidence_id = _record_evidence("数据库表清单", result)
        return (f"[证据 {evidence_id}] " if evidence_id else "") + result
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e)
    finally:
        conn.close()


def get_table_data(table_name: str) -> str:
    """预览指定表前 100 行，返回范围信息及最多 30 行的 JSON 行视图。"""
    from rules.engine import raw_query_denial
    rule_denial = raw_query_denial()
    if rule_denial:
        return rule_denial
    denial = source_policy.source_denial("db_query")
    if denial:
        return denial
    if not _TABLE_NAME_PATTERN.match(table_name):
        return f"非法的表名：{table_name}"
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e, connection=True)
    try:
        cursor = conn.cursor()
        sql = f"SELECT * FROM `{table_name}` LIMIT 101"
        from casework.mirror import check_read
        check_read(sql)
        cursor.execute(sql)
        fetched = cursor.fetchmany(101)
        rows = [[plain_value(v) for v in row] for row in fetched[:100]]
        col_names = [desc[0] for desc in cursor.description]
        meta = result_scope(sql, len(rows), len(fetched) > 100, 100, 'table_preview')
        # 探测多取一行，meta 表述的是预览前的匹配范围。
        text = format_table({'columns': col_names, 'rows': rows, 'completeness': meta})
        evidence_id = _record_evidence(
            f"表预览：{table_name}", text, sql=sql,
            columns=col_names, rows=rows, table=table_name, completeness=meta,
        )
        text = format_table({"columns": col_names, "rows": rows, "completeness": meta})
        text += "\n统计核验定义（资料）：" + json.dumps(_statistics(sql, col_names, rows, semantic_layer.annotate_columns(col_names, table_name)), ensure_ascii=False)
        return (f"[证据 {evidence_id}] " if evidence_id else "") + text
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e)
    finally:
        conn.close()


def describe_schema() -> dict[str, list[dict]]:
    """读取实际表结构，供语义层自动发现未知表/字段。"""
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        schema: dict[str, list[dict]] = {}
        for table in tables:
            if not _TABLE_NAME_PATTERN.match(table):
                continue
            cursor.execute(f"DESCRIBE `{table}`")
            schema[table] = [
                {
                    "name": row[0],
                    "type": str(row[1]),
                    "nullable": str(row[2]).upper() == "YES",
                    "key": row[3],
                    "default": row[4],
                    "extra": row[5],
                }
                for row in cursor.fetchall()
            ]
        return schema
    finally:
        conn.close()


def run_read_query(sql: str, params: list | tuple | None = None, limit: int = 200) -> dict:
    """执行语义层生成的只读 SELECT，返回列/行；不做自然语言 SQL 拼接。"""
    from rules.engine import raw_query_denial
    denial = raw_query_denial()
    if denial:
        raise PermissionError(denial)
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
        raise ValueError("只允许 SELECT 查询")
    if ";" in sql or "--" in sql or "/*" in sql:
        raise ValueError("禁止多语句/注释")
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        from casework.mirror import check_read
        check_read(sql)
        cursor.execute(sql, tuple(params or ()))
        columns = [desc[0] for desc in cursor.description]
        cap = max(1, min(int(limit), 500))
        fetched = cursor.fetchmany(cap + 1)
        rows = fetched[:cap]
        return {
            "columns": columns,
            "rows": [[plain_value(v) for v in row] for row in rows],
            "rowCount": len(rows),
            "sql": sql,
            "completeness": result_scope(sql, len(rows), len(fetched) > cap, cap),
        }
    finally:
        conn.close()


def fetch_table_rows(table: str, filters: dict[str, str], limit: int = 100) -> dict:
    """按图表点击的维度值读取明细行（表/字段白名单 + 参数化查询）。"""
    from rules.engine import raw_query_denial
    denial = raw_query_denial()
    if denial:
        raise PermissionError(denial)
    if not _TABLE_NAME_PATTERN.match(table):
        raise ValueError("非法表名")
    for field in filters:
        if not _TABLE_NAME_PATTERN.match(field):
            raise ValueError("非法字段名")
    limit = max(1, min(int(limit), 200))
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        available = {row[0] for row in cursor.fetchall()}
        if table not in available:
            raise ValueError(f"表不存在：{table}")
        where = " AND ".join(f"`{field}` = %s" for field in filters)
        sql = f"SELECT * FROM `{table}`"
        params: list = []
        if where:
            sql += " WHERE " + where
            params.extend(str(value) for value in filters.values())
        sql += " LIMIT %s"
        params.append(limit + 1)
        from casework.mirror import check_read
        check_read(sql)
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        fetched = cursor.fetchmany(limit + 1)
        rows = fetched[:limit]
        return {
            "table": table,
            "columns": columns,
            "rows": [[plain_value(v) for v in row] for row in rows],
            "rowCount": len(rows),
            "filters": filters,
            "sql": sql,
            "params": params,
            "completeness": result_scope(sql, len(rows), len(fetched) > limit, limit, "filtered_detail"),
        }
    finally:
        conn.close()


def execute_sql_query(sql: str) -> str:
    """执行只读 SQL：最多取回 500 行，模型视图最多 30 行，显式返回范围与截断。"""
    from rules.engine import raw_query_denial
    rule_denial = raw_query_denial()
    if rule_denial:
        return rule_denial
    denial = source_policy.source_denial("db_query")
    if denial:
        return denial
    # 仅允许单条只读查询，拒绝注释、多语句、文件导出、锁及存储过程。
    statement = sql.strip().removesuffix(";").strip()
    if (not re.match(r"^(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", statement, re.I)
        or re.search(r";|--|/\*|#|\b(INTO|OUTFILE|DUMPFILE|LOAD_FILE|SLEEP|BENCHMARK|FOR\s+UPDATE|LOCK)\b", statement, re.I)):
        return "禁止执行非只读或多语句查询"
    sql = statement
    # 写操作关键字拦截（TDD §14 兜底，禁 DROP/DELETE/UPDATE 等）
    matched = _FORBIDDEN_WRITE_PATTERN.search(sql)
    if matched:
        return f"禁止执行写操作语句（含 {matched.group(1)}）"
    try:
        conn = _get_connection()
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e, connection=True)
    try:
        cursor = conn.cursor()
        from casework.mirror import check_read
        check_read(sql)
        cursor.execute(sql)
        fetched = cursor.fetchmany(501)
        rows = [[plain_value(v) for v in row] for row in fetched[:500]]
        col_names = [desc[0] for desc in cursor.description]
        meta = result_scope(sql, len(rows), len(fetched) > 500, 500)
        text = format_table({'columns': col_names, 'rows': rows, 'completeness': meta})
        match = re.search(r"\bFROM\s+`?([A-Za-z0-9_]+)`?", sql, re.IGNORECASE)
        table_name = match.group(1) if match else None
        field_meta = semantic_layer.annotate_columns(col_names, table_name)
        chart = build_chart_spec(col_names, rows, "SQL 查询结果", field_meta=field_meta)
        evidence_id = _record_evidence(
            "SQL 查询结果", text, sql=sql,
            columns=col_names, rows=rows, chart=chart, table=table_name, completeness=meta,
        )
        text = format_table({"columns": col_names, "rows": rows, "completeness": meta})
        text += "\n统计核验定义（资料）：" + json.dumps(_statistics(sql, col_names, rows, semantic_layer.annotate_columns(col_names, table_name)), ensure_ascii=False)
        return (f"[证据 {evidence_id}] " if evidence_id else "") + text
    except Exception as e:  # noqa: BLE001
        source_policy.record_gap("db_query", "数据库连接或查询失败，未取得请求的业务事实", reason="tool_error")
        return query_error(e)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=== list_sql_tables() ===")
    print(list_sql_tables())

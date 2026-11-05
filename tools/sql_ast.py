"""只为核验解析实际 SQL；绑定参数后的文本从不用于执行。"""
from decimal import Decimal
import sqlglot
from sqlglot import exp


def bound_tree(sql: str, params=None):
    tokens = sqlglot.tokenize(sql, read='mysql')
    spans = [(a.start, b.end + 1) for a, b in zip(tokens, tokens[1:])
             if a.text == '%' and b.text == 's' and a.end + 1 == b.start
             and a.token_type.name == 'MOD' and b.token_type.name == 'VAR']
    values = [None] * len(spans) if params is None else list(params)
    if len(values) != len(spans):
        raise ValueError('SQL 参数数量不匹配，无法核验范围')
    parts, previous = [], 0
    for (start, end), value in zip(spans, values):
        if isinstance(value, Decimal):
            literal = exp.Literal.number(str(value))
        else:
            literal = exp.convert(value.isoformat() if hasattr(value, 'isoformat') else value)
        parts.extend([sql[previous:start], literal.sql(dialect='mysql')])
        previous = end
    parts.append(sql[previous:])
    return sqlglot.parse_one(''.join(parts), read='mysql')

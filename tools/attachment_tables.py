"""Read-only, bounded spreadsheet operations shared by the attachment tool."""
from __future__ import annotations

import json
import hashlib
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from pathlib import Path
import pandas as pd

MAX_ROWS = 100_000
MAX_CELLS = 2_000_000


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('文本编码无法识别，请转换为 UTF-8')


def load_table(path: Path, sheet=None, *, list_sheets=False):
    """Read a complete bounded table, shared by queries and keyed comparisons."""
    if path.suffix.lower() == '.json':
        sheets=['JSON'];selected=sheet or 'JSON'
        if selected!='JSON':raise ValueError('JSON 的工作表名称为 JSON')
        data=json.loads(read_text(path))
        if not isinstance(data,list) or any(not isinstance(row,dict) for row in data):
            raise ValueError('结构化 JSON 查询仅支持对象数组；其他 JSON 请读取原文')
        if any(isinstance(value,(list,dict)) for row in data for value in row.values()):
            # Keep scalar columns queryable; nested cells remain explicit serialized values.
            data=[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()} for row in data]
        if list_sheets:return {'sheets':sheets}
        frame=pd.DataFrame(data,dtype=object)
    elif path.suffix.lower() == '.csv':
        sheets = ['CSV']
        selected = sheet or 'CSV'
        if selected != 'CSV':
            raise ValueError('CSV 的工作表名称为 CSV')
        if list_sheets:
            return {'sheets': sheets}
        import io, csv
        raw = read_text(path)
        headers = next(csv.reader(io.StringIO(raw)), [])
        if len(headers) != len(set(headers)):
            raise ValueError('表头存在重名，请修正表头')
        frame = pd.read_csv(io.StringIO(raw), nrows=MAX_ROWS + 1, dtype=object)
    else:
        with pd.ExcelFile(path) as book:
            sheets = book.sheet_names
            if list_sheets:
                return {'sheets': sheets}
            selected = sheet or sheets[0]
            if selected not in sheets:
                raise ValueError(f'工作表不存在，可选：{sheets}')
            header = pd.read_excel(book,sheet_name=selected,header=None,nrows=1,dtype=object)
            headers = [str(value) for value in header.iloc[0] if not pd.isna(value)] if len(header) else []
            if len(headers) != len(set(headers)):
                raise ValueError('表头存在重名，请修正表头')
            frame = pd.read_excel(book, sheet_name=selected, nrows=MAX_ROWS + 1, dtype=object)
    if len(frame) > MAX_ROWS or frame.size > MAX_CELLS:
        raise ValueError('工作表超过 10 万行或 200 万单元格，请拆分文件或导入业务数据库后查询；未返回不完整统计')
    frame.columns = [str(c) for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError('表头存在重名，请修正表头')
    return frame, selected, sheets


def query_table(path: Path, sheet=None, offset=0, limit=100, columns=None, filters=None,
                group_by=None, aggregates=None, list_sheets=False, deduplicate_by=None) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('offset 必须为非负整数，limit 必须为 1～500')
    loaded = load_table(path, sheet, list_sheets=list_sheets)
    if list_sheets:
        return loaded
    frame, selected, sheets = loaded
    def numeric(value):
        if pd.isna(value): return None
        try:
            number = Decimal(str(value))
            if not number.is_finite(): raise ValueError('数值必须是有限数字')
            return number
        except InvalidOperation as exc:
            raise ValueError('数值筛选或汇总遇到非数值单元格') from exc
    total = len(frame)
    quality={'null_counts':{str(c):int(frame[c].isna().sum()) for c in frame.columns},
             'duplicate_row_numbers':[int(i)+2 for i in frame.index[frame.duplicated(keep=False)]]}
    fields = list(frame.columns)
    # Key deduplication is explicit and precedes filtering. Contradictory rows
    # cannot silently be selected by filter order or keep='first'.
    row_lineage = {int(i): [int(i)+2] for i in frame.index}
    deduplication = None
    if deduplicate_by:
        if (not isinstance(deduplicate_by, list) or not deduplicate_by
            or len(set(deduplicate_by)) != len(deduplicate_by)
            or any(c not in fields for c in deduplicate_by)):
            raise ValueError('去重键必须是实际存在且不重复的字段')
        if any(frame[c].isna().any() or frame[c].astype(str).str.strip().eq('').any() for c in deduplicate_by):
            raise ValueError('去重键缺失，不能自动认定案件身份；请确认口径或补齐数据')
        groups = frame.groupby(deduplicate_by, dropna=False, sort=False).indices
        kept=[]; members=[]
        for indices in groups.values():
            subset=frame.iloc[indices]
            if len(subset.drop_duplicates()) != 1:
                raise ValueError('相同主键存在非完全相同的记录；须确认冲突处理口径，未任选一行')
            first=int(subset.index[0]);kept.append(first)
            row_lineage[first]=[int(i)+2 for i in subset.index]
            members.append({'key': [str(subset.iloc[0][c]) for c in deduplicate_by],
                            'source_rows': row_lineage[first]})
        frame=frame.loc[kept]
        deduplication={'keys':deduplicate_by,'input_rows':total,'unique_rows':len(frame),
                       'policy':'identical_rows_only_before_filter','members':members}
    def validate(names):
        if any(name not in fields for name in names):
            raise ValueError(f'未知列，可选：{fields}')
    for condition in filters or []:
        field, op, value = condition.get('field'), condition.get('op', 'eq'), condition.get('value')
        validate([field]); series = frame[field]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and op in ('eq','ne','gt','gte','lt','lte'):
            series = series.map(numeric); value = numeric(value)
        if op == 'eq': mask = series.eq(value)
        elif op == 'ne': mask = series.ne(value)
        elif op == 'contains': mask = series.astype('string').str.contains(str(value), regex=False, na=False)
        elif op == 'in':
            if not isinstance(value, list): raise ValueError('in 筛选需要数组')
            mask = series.isin(value)
        elif op == 'is_null': mask = series.isna()
        elif op == 'not_null': mask = series.notna()
        elif op in ('gt', 'gte', 'lt', 'lte'):
            mask = getattr(series, {'gt':'gt','gte':'ge','lt':'lt','lte':'le'}[op])(value)
        else: raise ValueError('不支持的筛选运算符')
        frame = frame.loc[mask]
    matched = len(frame)
    row_numbers = [int(n) + 2 for n in frame.index[offset:offset + limit]]
    lineage=[]
    aggregation_proof=[]
    if aggregates:
        groups = group_by or []
        validate(groups)
        named = {}
        for item in aggregates:
            field, op = item.get('field'), item.get('op')
            validate([field])
            if op not in ('sum', 'count', 'mean', 'min', 'max', 'nunique'):
                raise ValueError('汇总支持 sum/count/mean/min/max/nunique')
            if op in ('sum','mean'):
                frame = frame.copy()
                frame[field] = frame[field].map(numeric)
            alias = item.get('alias') or f'{field}_{op}'
            if alias in named or alias in groups: raise ValueError('汇总结果列名重复')
            named[alias] = (field, op)
        grouped_frames = list(frame.groupby(groups,dropna=False,sort=False)) if groups else [((),frame)]
        for _, part in grouped_frames:
            metrics=[]
            for alias,(field,op) in named.items():
                if op in ('count','nunique'):
                    ids=list(dict.fromkeys(v.item() if hasattr(v, 'item') else v for v in part[field] if not pd.isna(v)))
                    metrics.append({'column':alias,'field':field,'op':op,'unique_values':ids,
                                    'non_null_count':int(part[field].count()),'null_count':int(part[field].isna().sum()),
                                    'value':len(ids) if op=='nunique' else int(part[field].count())})
                else:
                    values=[None if pd.isna(v) else v.item() if hasattr(v,'item') else v for v in part[field]]
                    metrics.append({'column':alias,'field':field,'op':op,'source_values':values})
            aggregation_proof.append({'group':{c:None if pd.isna(part.iloc[0][c]) else str(part.iloc[0][c]) for c in groups},
                'source_rows':[r for i in part.index for r in row_lineage[int(i)]], 'metrics':metrics})
        if groups:
            grouped=frame.groupby(groups,dropna=False,sort=False)
            lineage=[[r for i in part.index for r in row_lineage[int(i)]] for _,part in grouped_frames]
            frame = frame.groupby(groups, dropna=False, sort=False).agg(**{alias: (field, (lambda series: series.sum()/series.count() if series.count() else None) if op == 'mean' else op) for alias,(field,op) in named.items()}).reset_index()
        else:
            lineage=[[r for i in frame.index for r in row_lineage[int(i)]]]
            frame = pd.DataFrame([{alias: (frame[field].sum() / frame[field].count() if op == 'mean' and frame[field].count() else None if op == 'mean' else getattr(frame[field], op)()) for alias, (field, op) in named.items()}])
        row_numbers = []
    elif group_by:
        raise ValueError('group_by 需要同时指定 aggregates')
    if columns:
        if any(c not in frame.columns for c in columns): raise ValueError('选择的结果列不存在')
        frame = frame[columns]
    result_count = len(frame)
    page = frame.iloc[offset:offset + limit]
    def cell(value):
        if value is None or pd.isna(value): return None
        if isinstance(value, Decimal): return format(value, 'f')
        if isinstance(value, (datetime, date)): return value.isoformat()
        if hasattr(value, 'item'): value = value.item()
        if isinstance(value, int) and abs(value) > 9_007_199_254_740_991: return str(value)
        return value
    rows = [[cell(value) for value in row] for row in page.itertuples(index=False, name=None)]
    for record,row in zip(aggregation_proof[offset:offset+limit],rows):
        for metric in record['metrics']:
            if metric['column'] in page.columns:
                metric['value']=row[list(page.columns).index(metric['column'])]
            if 'source_values' in metric:
                metric['source_values']=[cell(v) for v in metric['source_values']]

    return {'sheets': sheets, 'sheet': selected, 'total_rows': total, 'matched_rows': matched,
            'result_rows': result_count, 'offset': offset, 'limit': limit, 'truncated': offset + len(page) < result_count,
            'row_numbers': row_numbers, 'source_row_groups':lineage[offset:offset+limit], 'quality':quality, 'columns': list(page.columns), 'rows': rows,
            'filters': filters or [], 'group_by': group_by or [], 'aggregates': aggregates or [],
            'deduplication':deduplication,
            'aggregation_proof':{'version':1,'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'sheet':selected,'filters':filters or [],'deduplicate_by':deduplicate_by or [],
                'groups':aggregation_proof[offset:offset+limit],
                'complete':offset==0 and offset+len(page)==result_count},
            'note': 'row_numbers 为含表头的原始工作表行号；汇总结果不对应单行。'}

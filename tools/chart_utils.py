"""从 SQL 结果生成前端 ECharts 规格。

支持：柱状 bar、条形 hbar、折线 line、面积 area、堆叠柱 stackedBar、
饼图 pie、环形 donut、散点 scatter。先按数据形状选择，再用用户任务中的
“趋势/占比/排名/关系”等意图修正，最后前端仍允许手动切换。
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_CHART_TYPES = ("bar", "hbar", "line", "area", "stackedBar", "pie", "donut", "scatter", "heatmap")


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def plain_value(value: Any) -> Any:
    """SQL 值转 JSON 安全类型，供证据面板持久化/展示。"""
    return _plain(value)


def _role(name: str, values: list[Any]) -> str:
    text = str(name).lower()
    if any(token in text for token in ("date", "time", "日期", "时间", "月份", "年份", "year", "month", "day")):
        return "time"
    if any(token in text for token in ("province", "city", "region", "地区", "省份", "城市", "区域", "仓库")):
        return "geo"
    if any(token in text for token in ("stage", "step", "阶段", "环节")):
        return "stage"
    if any(token in text for token in ("source", "from", "来源", "上游")):
        return "source"
    if any(token in text for token in ("target", "to", "去向", "下游")):
        return "target"
    if any(_looks_date(v) for v in values[:3] if v is not None):
        return "time"
    return "dimension"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _looks_date(value: Any) -> bool:
    text = str(_plain(value) or "")
    return bool(
        len(text) >= 7
        and (
            (len(text) >= 10 and text[4] in "-/")
            or (len(text) == 7 and text[4] in "-/")
            or (len(text) >= 4 and text[:4].isdigit() and ("月" in text or "年" in text))
        )
    )


def chart_types() -> tuple[str, ...]:
    return _CHART_TYPES


def build_chart_spec(
    columns: list[str],
    rows: list[tuple],
    title: str = "查询结果",
    query: str = "",
    field_meta: dict | None = None,
) -> dict | None:
    """根据列/行 + 任务意图选择图表类型；不适合图表时返回 None。"""
    if not columns or not rows:
        return None
    normalized = [[_plain(v) for v in row] for row in rows]
    width = min(len(columns), max(len(r) for r in normalized))
    numeric_cols = [
        i for i in range(width)
        if any(_is_number(r[i]) for r in normalized if i < len(r))
        and all(r[i] is None or _is_number(r[i]) for r in normalized if i < len(r))
    ]
    if not numeric_cols:
        return None
    dimension_cols = [i for i in range(width) if i not in numeric_cols]
    x_idx = dimension_cols[0] if dimension_cols else None
    y_cols = numeric_cols[:4]
    limited = normalized[:40]
    if x_idx is None:
        x_values = [f"第 {i + 1} 行" for i in range(len(limited))]
    else:
        x_values = [str(row[x_idx]) if row[x_idx] is not None else "—" for row in limited]
    if len(dimension_cols) > 1:
        x_values = [" · ".join(str(row[i]) if row[i] is not None else "—" for i in dimension_cols) for row in limited]
    series = []
    for idx in y_cols:
        series.append({
            "name": str(columns[idx]),
            "data": [row[idx] if idx < len(row) else None for row in limited],
        })
    if not series:
        return None

    fields = []
    for i, name in enumerate(columns):
        vals = [row[i] for row in limited if i < len(row)]
        meta = (field_meta or {}).get(str(name), {})
        if i in numeric_cols:
            role = "metric"
        else:
            role = meta.get("role") or _role(str(name), vals)
            if role in ("id", "foreign_key", "attribute", "text"):
                role = "dimension"
        fields.append({
            "name": str(name),
            "label": meta.get("label") or str(name),
            "role": role,
            "index": i,
            "table": meta.get("table") or "",
            "unit": meta.get("unit"),
            "aggregation": meta.get("aggregation"),
        })

    # 展示名优先使用语义层，派生的合计与占比字段按维度名补充标题。
    for field in fields:
        if field['role'] != 'metric':
            continue
        if not field.get('label') or field['label'] == field['name']:
            name = field['name'].lower()
            dimensions = [f for f in fields if f['role'] != 'metric']
            dimension = next((f for f in dimensions if name.endswith('_' + f['name'].lower().split('_')[-1])), None)
            if name.startswith(('pct_', 'percent_', 'percentage_')):
                field['label'] = (dimension['label'] + '内占比') if dimension else '占比'
                field['unit'] = '%'
            elif name.endswith('_total'):
                prefix = name[:-6]
                dimension = next((f for f in dimensions if f['name'].lower().split('_')[-1] == prefix), None)
                if dimension:
                    field['label'] = dimension['label'] + '合计'
    for entry, idx in zip(series, y_cols):
        field = fields[idx]
        entry.update(name=field['label'], field=field['name'], unit=field.get('unit'))

    heatmap = None
    if x_idx is not None and len(dimension_cols) >= 2 and numeric_cols:
        x_col, y_col, value_col = dimension_cols[0], dimension_cols[1], numeric_cols[0]
        x_vals, y_vals = [], []
        for row in limited:
            xv = str(row[x_col]).strip() if row[x_col] is not None else "—"
            yv = str(row[y_col]).strip() if row[y_col] is not None else "—"
            if xv not in x_vals:
                x_vals.append(xv)
            if yv not in y_vals:
                y_vals.append(yv)
        if len(x_vals) > 1 and len(y_vals) > 1:
            data = []
            row_indices = []
            for row_index, row in enumerate(limited):
                xv = str(row[x_col]).strip() if row[x_col] is not None else "—"
                yv = str(row[y_col]).strip() if row[y_col] is not None else "—"
                val = row[value_col] if value_col < len(row) else None
                if val is not None:
                    data.append([x_vals.index(xv), y_vals.index(yv), val])
                    row_indices.append(row_index)
            if data and len({(cell[0], cell[1]) for cell in data}) == len(data):
                heatmap = {
                    "xAxis": x_vals,
                    "yAxis": y_vals,
                    "data": data,
                    "xField": str(columns[x_col]),
                    "yField": str(columns[y_col]),
                    "metric": fields[value_col]['label'],
                    "rowIndices": row_indices,
                }

    text = (query or "").lower()
    wants_pie = any(word in text for word in ("占比", "构成", "结构", "份额", "比例", "分布"))
    wants_trend = any(word in text for word in ("趋势", "走势", "变化", "同比", "环比", "增长"))
    wants_rank = any(word in text for word in ("排名", "排行", "前几", "对比", "比较"))
    wants_relation = any(word in text for word in ("关系", "相关", "散点", "关联"))

    chart_type = "bar"
    reason = "分类对比"
    is_date = x_idx is not None and all(_looks_date(v) for v in x_values[:3])
    # An explicit chart form is a delivery requirement. Two descriptive columns
    # (for example component and part number) do not override a requested bar.
    explicit_bar = bool(re.search(r'柱状图|条形图|bar\s*chart', text))
    if heatmap is not None and not explicit_bar:
        chart_type, reason = "heatmap", "二维交叉统计"
    elif explicit_bar:
        chart_type, reason = ("hbar" if '条形图' in text else "bar"), "用户指定图形"
    elif is_date:
        chart_type, reason = ("area" if wants_trend else "line"), "日期趋势"
    elif wants_pie and 2 <= len(x_values) <= 8:
        chart_type, reason = "donut", "构成占比"
    elif wants_relation and x_idx is None and len(numeric_cols) >= 2:
        chart_type, reason = "scatter", "数值关系"
    elif wants_trend and len(x_values) >= 3:
        chart_type, reason = "area", "变化趋势"
    elif len(series) > 1 and wants_pie:
        chart_type, reason = "stackedBar", "结构堆叠"
    elif wants_rank and (len(x_values) > 8 or any(len(v) > 14 for v in x_values)):
        chart_type, reason = "hbar", "排名对比"
    elif len(x_values) > 14:
        chart_type, reason = "hbar", "类别较多"
    if chart_type == "bar" and not explicit_bar and (len(x_values) > 10 or any(len(v) > 18 for v in x_values)):
        chart_type, reason = "hbar", "类别较多/标签较长"

    dimension_labels = [fields[i]['label'] for i in dimension_cols]
    metric_labels = [fields[i]['label'] for i in y_cols]
    if title in ('查询结果', 'SQL 查询结果', '', None):
        metric_title = '、'.join(metric_labels)
        title = (' × '.join(dimension_labels) + '：' + metric_title) if len(dimension_labels) > 1 else (
            f"按{dimension_labels[0]}统计{metric_title}" if dimension_labels else metric_title)
    spec = {
        "type": chart_type,
        "title": title,
        "xAxis": x_values,
        "series": series,
        "rowCount": len(rows),
        "truncated": len(rows) > len(limited),
        "reason": reason,
        "fields": fields,
        "xField": str(columns[x_idx]) if x_idx is not None else None,
        "xLabel": (field_meta or {}).get(str(columns[x_idx]), {}).get("label") if x_idx is not None else None,
        "metricField": str(columns[numeric_cols[0]]) if numeric_cols else None,
        "metricLabel": fields[numeric_cols[0]]['label'] if numeric_cols else None,
        "heatmap": heatmap,
        "sourceRows": limited,
        "pointFilters": [[{"field": str(columns[i]), "value": row[i]} for i in dimension_cols] for row in limited],
    }
    if chart_type == "scatter":
        # 两条数值列：xAxis 存 x 值，series 存 y 值。
        spec["xAxis"] = [row[numeric_cols[0]] for row in limited]
        spec["series"] = [{
            "name": str(columns[numeric_cols[1]]),
            "data": [row[numeric_cols[1]] if numeric_cols[1] < len(row) else None for row in limited],
        }]
        spec["xName"] = str(columns[numeric_cols[0]])
        spec["yName"] = str(columns[numeric_cols[1]])
        spec["xField"] = str(columns[numeric_cols[0]])
        spec["metricField"] = str(columns[numeric_cols[1]])
    return spec


def task_needs_charts(query: str) -> bool:
    """按用户交付目标决定是否展示图表，数值证据和分析模式本身不构成需求。"""
    # 前端追加的模式提示不能替用户提出可视化要求。
    text = re.split(r'【任务类型[：:]', query or '', maxsplit=1)[0].lower()
    chart = r'(?:图表|可视化|柱状图|条形图|折线图|面积图|饼图|环形图|散点(?:图|关系)|热力图|绘图|画图|出图|charts?)'
    if re.search(r'(?:不要|不用|无需|不需要|禁止|不生成|不展示|不画|不)(?:再|生成|创建|输出|展示|绘制|提供|任何|数据|统计|业务|做|的|\s)*' + chart, text):
        return False
    if re.search(chart, text):
        return True
    # 推荐、核对和材料查找中的数量是依据，默认用文字与表格承载。
    if re.search(r'推荐|分案|候选|核对|待补材料', text):
        return False
    return bool(re.search(r'统计|趋势|走势|分布|占比|同比|环比|相关性', text))


def _confirmed_ocr_tables(evidence: list[dict]) -> list[dict]:
    """Chart only complete OCR tables whose cells were independently seen in the image."""
    result = []
    for item in evidence:
        if (item.get('type') != 'file' or item.get('extraction_method') != 'mineru'
                or not item.get('content_complete') or not item.get('id')):
            continue
        content = str(item.get('content') or item.get('snippet') or '')
        blocks = re.findall(r'(?m)^\[第\s*\d+\s*页[^\n]*· 表格\]\n((?:\|[^\n]*\n?)+)', content)
        # MinerU may emit a scanned table as OCR text blocks instead of a
        # markdown table. Parse only the conservative ``编号 部件 数量`` row
        # shape; unknown values stay out of numeric chart rows and are retained
        # as explicit metadata rather than becoming zero.
        if not blocks and re.search(r'编号\s*部件\s*数量', content):
            ocr_rows = []
            row_locations = {}
            for match in re.finditer(r'(?m)^\s*([A-Za-z][A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*)\s+([^\n\d]+?)\s+(\d+|未知|未记录|不明)\s*$', content):
                value = match.group(3)
                ocr_rows.append([match.group(1), match.group(2).strip(), int(value) if value.isdigit() else None])
                locations=list(re.finditer(r'(?m)^\[([^\n]+)\]$',content[:match.start()]))
                if locations:
                    row_locations[match.group(1)]=locations[-1].group(1)
                else:
                    row_locations[match.group(1)]=f'表头下第{len(ocr_rows)}条数据行'
            if ocr_rows:
                witnesses = []
                for other in evidence:
                    if (other.get('id') == item['id'] or not other.get('visual_observation')
                            or other.get('revision') != item.get('revision') or other.get('title') != item.get('title')):
                        continue
                    observed = str(other.get('content') or other.get('snippet') or '')
                    if all(any(all(re.search(r'(?<![A-Za-z0-9])'+re.escape(str(value))+r'(?![A-Za-z0-9])', line)
                                       for value in row) for line in observed.splitlines())
                           for row in [[r[0], r[1], r[2] if r[2] is not None else '未记录'] for r in ocr_rows]):
                        witnesses.append(other['id'])
                if witnesses:
                    known = [row for row in ocr_rows if isinstance(row[2], int)]
                    result.append({'id': item['id'], 'type': 'file', 'title': item.get('title'),
                                   'columns': ['编号','部件','数量'], 'rows': known,
                                   'unknown_rows': [row[:2] for row in ocr_rows if row[2] is None],
                                   'row_locations': row_locations,
                                   'rowCount': len(known), 'total_ocr_rows': len(ocr_rows), 'completeness': {'has_more': False, 'complete': True, 'scope': 'known numeric rows only; unknown rows retained separately'},
                                   'chart_source_ids': [item['id'], witnesses[0]],
                                   'chart_derivation': 'ocr_rows_confirmed_by_visual_observation'})
            continue
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip().startswith('|')]
            if len(lines) < 4:
                continue
            cells = [[part.strip() for part in line.strip('|').split('|')] for line in lines]
            headers, separator, *data = cells
            if not (len(headers) >= 2 and len(separator) == len(headers)
                    and all(re.fullmatch(r':?-{3,}:?', part) for part in separator)
                    and data and all(len(row) == len(headers) for row in data)):
                continue
            numeric = [idx for idx in range(len(headers)) if all(re.fullmatch(r'\d+', row[idx]) for row in data)]
            if not numeric:
                continue
            witnesses = []
            for other in evidence:
                if (other.get('id') == item['id'] or not other.get('visual_observation')
                        or other.get('revision') != item.get('revision')
                        or other.get('title') != item.get('title')):
                    continue
                observed = str(other.get('content') or other.get('snippet') or '')
                if all(any(all(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', line)
                                   for value in row) for line in observed.splitlines()) for row in data):
                    witnesses.append(other['id'])
            if not witnesses:
                continue
            rows = [[int(value) if index in numeric else value for index, value in enumerate(row)] for row in data]
            result.append({'id':item['id'],'type':'file','title':item.get('title'),
                           'columns':headers,'rows':rows,'rowCount':len(rows),
                           'row_locations': {str(r[0]):f'表头下第{idx+1}条数据行' for idx,r in enumerate(rows)},
                           'completeness':{'has_more':False,'complete':True},
                           'chart_source_ids':[item['id'],witnesses[0]],
                           'chart_derivation':'complete_mineru_table_confirmed_by_visual_observation'})
    return result


def _case_client_choices(evidence):
    clients=set()
    for item in evidence:
        columns=item.get('columns') or []
        if '客户简称' not in columns:continue
        index=columns.index('客户简称')
        clients.update(row[index] for row in item.get('rows',[]) if len(row)>index and isinstance(row[index],str))
    return clients


def build_chart_items(evidence: list[dict], query: str = '') -> list[dict]:
    """页面与报告共用：一项指标一张图，完全相同的数据视图合并，无数量截断。"""
    if not task_needs_charts(query):
        return []
    from tools.answer_contracts import workload_summary_scope_issues
    client_choices=_case_client_choices(evidence)
    result, seen = [], {}
    ocr_tables = _confirmed_ocr_tables(evidence)
    # Only witnessed known numeric rows enter the chart; unknown rows remain
    # in metadata and are never converted to zero or hidden as a total.
    for item in ocr_tables:
        spec = build_chart_spec(item['columns'], item['rows'], query=query)
        if spec:
            spec['completeness'] = item.get('completeness')
            spec['evidenceId'] = item.get('id')
            spec['sourceIds'] = item.get('chart_source_ids', [])
            key = json.dumps({'ocr': item.get('id'), 'spec': spec}, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                entry = {**item, 'id': f"{item.get('id', len(result))}:ocr", 'chart': spec}
                seen[key] = entry
                result.append(entry)
    for item in [*evidence]:
        if workload_summary_scope_issues(query,item,client_choices):
            continue
        if str(item.get('title', '')).startswith('表预览'):
            continue
        original = item.get('chart') or {}
        rows = original.get('sourceRows') or item.get('rows') or []
        columns = item.get('columns') or [f['name'] for f in original.get('fields', [])]
        total = original.get('rowCount', item.get('rowCount', len(rows)))
        if total < 1 or total < 2 and not item.get('aggregates') and (item.get('answer_contract') or {}).get('kind') != 'workload_summary':
            continue
        meta = {f['name']: f for f in original.get('fields', [])}
        meta.update(item.get('semantic') or {})
        # 旧会话可能只保留 20 行证据、但已有 40 行图表。不能缩短其原有图表数据。
        enough_rows = len(rows) >= min(total, 40)
        if columns and rows and enough_rows:
            base = build_chart_spec(columns, rows, query=query, field_meta=meta)
            if not base:
                continue
            base['rowCount'] = total
            base['truncated'] = total > len(base['sourceRows'])
        elif original:
            base = deepcopy(original)
        else:
            continue
        fields = base.get('fields') or []
        dimensions = [f for f in fields if f.get('role') != 'metric']
        metrics = [f for f in fields if f.get('role') == 'metric']
        projections = []
        records = base.get('sourceRows') or []
        if base.get('type') == 'scatter':
            projections.append(base)
        elif records and fields and metrics:
            field_meta = {f['name']: f for f in fields}
            for metric in metrics:
                selected = dimensions + [metric]
                projected_rows = [[row[f['index']] for f in selected] for row in records]
                projected = build_chart_spec([f['name'] for f in selected], projected_rows,
                                             query=query, field_meta=field_meta)
                if projected:
                    projected['rowCount'] = total
                    projected['truncated'] = base.get('truncated', False)
                    projections.append(projected)
        else:
            # 没有行快照的旧规格保留完整值；按各系列投影，避免数量与百分比共用纵轴。
            for series in base.get('series', []):
                projected = deepcopy(base)
                metric = next((f for f in metrics if f['name'] == series.get('field', series['name'])), None)
                projected['series'] = [deepcopy(series)]
                projected['metricField'] = metric['name'] if metric else series['name']
                projected['metricLabel'] = metric['label'] if metric else series['name']
                projected['title'] = ((' × '.join(f.get('label', f['name']) for f in dimensions) + '：') if dimensions else '') + projected['metricLabel']
                heatmap = projected.get('heatmap')
                if heatmap and metric:
                    indices = heatmap.get('rowIndices')
                    if indices is None and len(heatmap['data']) == len(series.get('data', [])):
                        indices = list(range(len(heatmap['data'])))
                    if indices is not None:
                        heatmap['data'] = [[cell[0], cell[1], series['data'][idx]] for cell, idx in zip(heatmap['data'], indices)]
                        heatmap['metric'] = projected['metricLabel']
                        projected['type'] = 'heatmap'
                projections.append(projected)
        for spec in projections:
            spec['completeness'] = item.get('completeness')
            spec['evidenceId'] = item.get('id')
            spec['sourceIds'] = item.get('chart_source_ids') or ([item.get('id')] if item.get('id') else [])
            # 维度、指标、来源表、实际值均参与去重；忽略 SQL 返回行的排序差异。
            identity_fields = [{k: f.get(k) for k in ('name', 'role', 'table', 'unit')} for f in spec.get('fields', [])]
            if spec.get('sourceRows'):
                records = sorted(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in spec['sourceRows'])
                identity = {'fields': identity_fields, 'type': spec['type'], 'records': records}
            else:
                identity = {k: spec.get(k) for k in ('type', 'xAxis', 'series', 'heatmap')}
                identity['fields'] = identity_fields
            identity['completeness'] = item.get('completeness')
            key = json.dumps(identity, ensure_ascii=False, sort_keys=True)
            if key in seen:
                prior = seen[key]['chart']['sourceIds']
                prior.extend(source for source in spec['sourceIds'] if source not in prior)
                continue
            entry = {**item, 'id': f"{item.get('id', len(result))}:{spec.get('metricField', '')}", 'chart': spec}
            seen[key] = entry
            result.append(entry)
    return result


def chart_delivery_issues(query, evidence, report=''):
    """Explicit chart delivery cannot be satisfied with prose/ASCII bars."""
    text=re.split(r'【任务类型[：:]',query or '',maxsplit=1)[0]
    explicit=bool(re.search(r'图表|可视化|柱状图|条形图|折线图|饼图|环形图|散点图|热力图|画图|绘图|出图',text))
    if not explicit or not task_needs_charts(text):return []
    charts=build_chart_items(evidence,text)
    if not charts:
        from tools.answer_contracts import workload_summary_scope_issues
        scope_issues=[issue for item in evidence for issue in workload_summary_scope_issues(text,item,_case_client_choices(evidence))]
        if scope_issues:return list(dict.fromkeys(scope_issues))
        return ['图表交付缺失：未取得可渲染的图表数据，文字或字符柱不能代替；按实际字段调用结构化分组汇总工具并保留来源，不要手算。']
    grouped=bool(re.search(r'按.+(?:承办人|代理师|人员|领域)|(?:承办人|代理师|人员).{0,8}统计',text))
    if grouped and not any(i.get('group_by') or (i.get('answer_contract') or {}).get('kind')=='workload_summary'
                           or 'group by' in str(i.get('sql','')).lower() for i in charts):
        return ['图表汇总缺失：需要程序按所问维度分组统计，逐案原始行或单人记录不能代替分组案量。']
    if any((i.get('completeness') or {}).get('has_more') or i.get('truncated') for i in charts):
        return ['图表范围尚不完整：先完成所问范围的汇总，不能把部分原始行当作全量图表。']
    if chart_presentation_spans(report):
        return ['图表呈现说明错误：本次已有前端可渲染图表，正文应引导到图表页；将字符柱及另行索要图片的说明改为真实图表入口，保留统计表和明细。']
    return []


def chart_presentation_spans(report):
    spans=[m.span() for m in re.finditer(r'```[^\n]*\n(?:(?!```).)*[█▇▆▅▄▃▂](?:(?!```).)*```',report,re.S)]
    spans += [m.span() for m in re.finditer(r'[^\n]*(?:柱状图|图表)[^\n]*(?:代码块示意|字符画|如需图片|图片版可再)[^\n]*',report)]
    return spans

"""Objective source-location delivery checks; no semantic answer grading."""
from __future__ import annotations
import re
from pathlib import Path


def requested_provenance_rows(query, items):
    from casework.parser import CASE_RE
    from casework.routing import affirmative
    query=affirmative(query or '')
    query='；'.join(c for c in query.split('；') if not re.match(r'\s*(?:暂时|本次)?(?:不需要|不必)',c))
    cases={m.group().upper() for m in CASE_RE.finditer(query)}
    if not cases or not re.search(r'原件.{0,8}(?:位置|出处|定位)|(?:出处|位置).{0,8}原件',query):
        return []
    fields=[]
    if re.search(r'实际收案|收案日期',query):fields.append('实际收案日期')
    if re.search(r'实际交局|交局日期',query):fields.append('实际交局日期')
    # Planned and actual dates have separate source fields. The request for
    # several planned deadlines needs each available original, not one path
    # somewhere in the response. No deadline is inferred from another field.
    if (re.search(r'(?:三(?:个|项)?|所有|各项)计划期限|三个期限|三项期限|三节点期限',query)
            or '计划期限' in query and not re.search(r'初稿|送审|提交',query)):
        fields.extend(['初稿期限／所限','客户送审期限','向局提交期限'])
    else:
        for pattern,label in [(r'初稿(?:计划)?期限','初稿期限／所限'),
                              (r'(?:客户)?送审(?:计划)?期限','客户送审期限'),
                              (r'(?:向局)?提交(?:计划)?期限','向局提交期限')]:
            if re.search(pattern,query):fields.append(label)
    result=[]
    schema=['所编号','字段','原件值','核对状态','原件路径','原件位置']
    for item in items:
        if item.get('columns')!=schema:continue
        for row in item.get('rows') or []:
            if len(row)==6 and str(row[0]).upper() in cases and row[1] in fields:
                result.append((item,row))
    return result


def original_location_issues(query, report, items):
    """Reject omission of available field-bound originals, not honest absence.

    A path somewhere else in a response cannot satisfy the requested case/field.
    All returned candidate values/locations remain visible when sources conflict.
    """
    missing=[]
    units=re.split(r'\n\s*\n|(?<=\|)\n(?=\|)',report)
    groups={}
    for item,row in requested_provenance_rows(query,items):
        if row[4] and row[5]:groups.setdefault(tuple(map(str,row[:4])),[]).append((item,row))
    for alternatives in groups.values():
        if not any(any(all(str(row[i]) in unit for i in (0,1,2,4,5)) and f"[{item['id']}]" in unit for unit in units)
                   for item,row in alternatives):
            missing.append('原件定位未完成：须交付同案字段、原件值、原件路径及物理位置，并绑定来源；已有原件不能用数据库记录键或“待确认”替代。')
    return list(dict.fromkeys(missing))


def append_requested_provenance(query, report, items):
    selected=requested_provenance_rows(query,items)
    if not selected or not original_location_issues(query,report,items):return report,[]
    # Every byte is supplied by an actual source row, never a guessed path.
    rows=[];seen=set()
    for item,row in selected:
        if not row[4] or not row[5]:continue
        key=tuple(map(str,row))+ (item['id'],)
        if key in seen:continue
        seen.add(key)
        cell=lambda v:str(v).replace('|','&#124;').replace('\n',' ')
        rows.append('| '+' | '.join(cell(v) for v in row)+f" | [{item['id']}] |")
    if not rows:return report,[]
    block='\n\n### 所查询字段的原件位置\n\n| 所编号 | 字段 | 原件值 | 核对状态 | 原件路径 | 原件位置 | 来源 |\n| --- | --- | --- | --- | --- | --- | --- |\n'+'\n'.join(rows)
    return report+block,[{'kind':'requested_field_provenance','rows':len(rows)}]


def text_line_map(item):
    if not item.get('content_complete') or Path(item.get('title') or '').suffix.lower() not in ('.md','.txt'):
        return {}
    text=item.get('content')
    if not isinstance(text,str):return {}
    return {i:line for i,line in enumerate(text.splitlines(),1)}


def source_line_issues(report, items):
    """Check explicitly quoted physical line claims against complete text.

    Unsupported paraphrases still require semantic review; never guess their
    line from a keyword. Titles and blank lines are included in physical lines.
    """
    maps={item['id']:text_line_map(item) for item in items if item.get('id')}
    issues=[]
    for line in report.splitlines():
        refs=set(re.findall(r'\[(S\d+)\]',line));available=[maps[sid] for sid in refs if maps.get(sid)]
        if not available:continue
        # Accept both common audit forms:
        #   第2行：“原文”
        #   “原文”（第2行）
        # The latter is emitted by the comparison renderer. Both forms are
        # checked against the exact physical line, including headings/blanks.
        quoted_lines=[]
        quoted_lines.extend((int(m[1]),m[2]) for m in re.finditer(
            r'第\s*(\d+)\s*行\s*[：:]?\s*[“「]([^”」]+)[”」]', line))
        quoted_lines.extend((int(m[2]),m[1]) for m in re.finditer(
            r'[“「]([^”」]+)[”」]\s*(?:\[S\d+\])?\s*[（(]\s*第\s*(\d+)\s*行\s*[）)]', line))
        for line_number,literal in quoted_lines:
            source=None
            # A comparison row can cite two files. Bind each quoted cell to
            # the citation immediately following that cell instead of
            # discarding the whole row as ambiguous.
            citation=re.search(r'[“「]'+re.escape(literal)+r'[”」]\s*\[(S\d+)\]',line)
            if citation and citation.group(1) in maps:
                source=maps[citation.group(1)]
            elif len(available)==1:
                source=available[0]
            if source is None:
                continue
            actual=source.get(line_number)
            clean=lambda v:re.sub(r'\s+','',v)
            pieces=[p for p in re.split(r'…+|\.\.\.',clean(literal)) if p]
            cursor=0;matched=actual is not None and bool(pieces)
            for piece in pieces:
                at=clean(actual or '').find(piece,cursor)
                if at<0:matched=False;break
                cursor=at+len(piece)
            if not matched:
                issues.append(f'原文行号与引文不符：第{line_number}行所引文字不在该物理行；标题和空行均计入，须按实际原件逐行核对。')
    return list(dict.fromkeys(issues))


def case_title_issues(report, items):
    """Bind an explicitly named case title to facts from this run's sources.

    Short contiguous titles are allowed. This checks identity fields only and
    never decides whether two technical mechanisms are similar.
    """
    from casework.parser import CASE_RE
    known={};documents={}
    def remember(case,title,sid):
        if (not isinstance(case,str) or not CASE_RE.fullmatch(case) or not isinstance(title,str)
            or not title.strip() or not isinstance(sid,str) or not re.fullmatch(r'S[1-9]\d*',sid)):return
        known.setdefault(case.upper(),set()).add((re.sub(r'\s+','',title),sid))
    for item in items:
        if item.get('type')=='knowledge':
            remember(item.get('case_id'),item.get('title'),item.get('id'))
            if isinstance(item.get('case_id'),str) and item.get('source'):
                documents.setdefault(item['case_id'].upper(),set()).add(Path(item['source']).name)
        columns=item.get('columns') or []
        if '所编号' in columns and '发明名称' in columns:
            for row in item.get('rows') or []:
                if len(row)>max(columns.index('所编号'),columns.index('发明名称')):
                    remember(row[columns.index('所编号')],row[columns.index('发明名称')],item.get('id'))
    errors=[]
    for match in CASE_RE.finditer(report):
        case=match.group().upper()
        title=re.match(r'[\s*·:：—-]*《([^》]+)》',report[match.end():])
        prefix=re.split(r'[。；\n]',report[:match.start()])[-1]
        if not title or case not in known or re.search(r'误称|误写|错称|错写|不要把|不应把|用户称|原问题',prefix):continue
        claimed=re.sub(r'\s+','',title[1]);facts=known[case]
        if any(claimed==name or len(claimed)>=4 and claimed in name for name in documents.get(case,())):continue
        if not any(claimed==name or len(claimed)>=4 and claimed in name for name,sid in facts):
            names={}
            for name,sid in sorted(facts):names.setdefault(name,[]).append(sid)
            expected='；'.join(name+''.join('['+sid+']' for sid in ids[:3]) for name,ids in names.items())
            errors.append(f'案号与名称不一致：{case}所称《{title[1]}》与已读原件不符；原件名称为{expected}。不得将技术概括改写成正式名称。')
    return list(dict.fromkeys(errors))


def dedup_aggregation_issues(query, items):
    """A COUNT over duplicated identifiers cannot prove a unique-case count.

    Nunique is one valid path. COUNT is equivalent when a complete same-file
    source proves the key unique and its counted field non-null.
    """
    text=re.sub(r'\s+','',query or '');errors=[]
    raw_fields={field for item in items for field in item.get('columns',[]) if isinstance(field,str)}
    raw_fields.update(a.get('field') for item in items for a in item.get('aggregates',[]) if isinstance(a.get('field'),str))
    raw_fields.update(field for item in items for field in (item.get('aggregation_proof') or {}).get('deduplicate_by',[]) if isinstance(field,str))
    keys=[field for field in raw_fields if re.search(r'按(?:照)?[“「`]?'+re.escape(field)+r'[”」`]?去(?:重|除(?:完全)?重复行?)',text)]
    if not keys:return []
    for item in items:
        aggregates=item.get('aggregates') or []
        for key in keys:
            if any(a.get('op')=='nunique' and a.get('field')==key for a in aggregates):continue
            counts=[a for a in aggregates if a.get('op')=='count']
            for agg in counts:
                proven=False
                proof=item.get('aggregation_proof') or {}
                if (proof.get('complete') and proof.get('file_sha256') == item.get('revision')
                    and proof.get('sheet') == item.get('sheet') and proof.get('filters') == item.get('filters')
                    and key in (proof.get('deduplicate_by') or [])
                    and all(any(m.get('field')==agg.get('field') and m.get('op')=='count'
                                and m.get('null_count')==0 for m in group.get('metrics',[]))
                            for group in proof.get('groups',[]))):
                    proven=True
                for raw in items:
                    columns=raw.get('columns') or [];rows=raw.get('rows') or []
                    if (raw.get('aggregates') or not item.get('revision') or raw.get('revision')!=item['revision']
                        or raw.get('title')!=item.get('title') or raw.get('sheet')!=item.get('sheet')
                        or raw.get('truncated') or raw.get('offset',0)!=0
                        or raw.get('matched_rows')!=len(rows) or raw.get('result_rows')!=len(rows)
                        or raw.get('filters') and raw.get('filters')!=item.get('filters')
                        or key not in columns or agg.get('field') not in columns):continue
                    if any(len(row)<len(columns) for row in rows):continue
                    selected_rows=rows
                    if item.get('source_row_groups') and raw.get('row_numbers'):
                        wanted={n for group in item['source_row_groups'] for n in group}
                        rowmap=dict(zip(raw['row_numbers'],rows))
                        if not wanted.issubset(rowmap):continue
                        selected_rows=[rowmap[n] for n in sorted(wanted)]
                    ids=[row[columns.index(key)] for row in selected_rows]
                    values=[row[columns.index(agg['field'])] for row in selected_rows]
                    try:unique=len(set(ids))==len(ids)
                    except TypeError:continue
                    if all(v is not None and str(v).strip() for v in ids+values) and unique:
                        proven=True;break
                if not proven:
                    errors.append(f'去重统计未核验：用户要求按{key}去重，但本次count没有同来源完整唯一集合证明；使用nunique或提供同快照唯一性及非空证明，不能仅在正文声称已去重。')
    return list(dict.fromkeys(errors))

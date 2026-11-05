"""Read-only attachment differences with two-sided, revision-bound evidence."""
from __future__ import annotations

import difflib
import hashlib
import json
from zipfile import BadZipFile
from datetime import date, datetime
from pathlib import Path

from langchain_core.tools import tool
from api import context, evidence
from utils import path_utils
from tools.document_parser import parse_document, location_label
from tools.attachment_tables import load_table
from pypdf.errors import PdfReadError
from docx.opc.exceptions import PackageNotFoundError

TABLES = {'.csv', '.xlsx', '.xls'}
DOCUMENTS = {'.txt', '.md', '.docx', '.pdf'}
MAX_TEXT_CHARS = 1_000_000
MAX_TEXT_LINES = 20_000
PAGE_CHARS = 18_000


def _attachment(name):
    root = Path(context.get_session_dir() or '') / 'attachments'
    if not context.get_session_dir():
        raise ValueError('未设置会话工作目录')
    path = path_utils.resolve_within(root, name.removeprefix('attachments/'))
    if not path.is_file():
        raise ValueError(f'会话附件不存在：{name}')
    if path.suffix.lower() not in TABLES | DOCUMENTS:
        raise ValueError('支持文本、Markdown、DOCX、PDF、CSV和Excel附件')
    return path


def _cell(value):
    import pandas as pd
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _table_changes(left, right, keys, left_sheet, right_sheet):
    if not keys or len(set(keys)) != len(keys):
        raise ValueError('表格对比须提供不重复的key_columns；先读取实际表头，再选择双方共有的案号列')
    frames = [load_table(left,left_sheet), load_table(right,right_sheet)]
    indexed = []
    for path, (frame,sheet,_) in zip((left,right),frames):
        if any(key not in frame.columns for key in keys):
            raise ValueError(f'{path.name}的关联列不存在；实际列：{list(frame.columns)}')
        rows = {}
        for index, row in enumerate(frame.itertuples(index=False,name=None),2):
            values = {str(field):_cell(value) for field,value in zip(frame.columns,row)}
            key = tuple(values[field] for field in keys)
            if any(value is None or not value.strip() for value in key):
                raise ValueError(f'{path.name} · 工作表 {sheet} · 第{index}行：关联键缺失，未按行位置对齐')
            if key in rows:
                raise ValueError(f'{path.name} · 工作表 {sheet}：关联键重复（第{rows[key]["row"]}、{index}行），未合并或任选一条')
            rows[key] = {'row':index,'location':f'工作表 {sheet} · 第{index}行','values':values}
        indexed.append(rows)
    columns = [list(frame.columns) for frame,_,_ in frames]
    changes = []
    if columns[0] != columns[1]:
        changes.append({'kind':'schema','left':{'columns':columns[0],'location':f'工作表 {frames[0][1]} · 表头'},
                        'right':{'columns':columns[1],'location':f'工作表 {frames[1][1]} · 表头'}})
    for key in dict.fromkeys([*indexed[0],*indexed[1]]):
        a,b = indexed[0].get(key),indexed[1].get(key)
        fields = list(dict.fromkeys([*columns[0],*columns[1]]))
        changed = [field for field in fields if a is None or b is None
                   or (field in a['values']) != (field in b['values'])
                   or a['values'].get(field) != b['values'].get(field)]
        if not changed:
            continue
        changes.append({'kind':'added' if a is None else 'deleted' if b is None else 'modified',
                        'key':dict(zip(keys,key)), 'changed_fields':changed,'left':a,'right':b})
    metadata = [{'file':path.name,'sheet':sheet,'columns':list(frame.columns),'total_rows':len(frame)}
                for path,(frame,sheet,_) in zip((left,right),frames)]
    return changes, metadata


def _document_lines(path):
    units = parse_document(path)
    if not units:
        raise ValueError(f'{path.name}未解析出可用文字；扫描件须先提供可读文本')
    if sum(len(unit.page_content) for unit in units) > MAX_TEXT_CHARS:
        raise ValueError(f'{path.name}超过100万字符，请按章节拆分后对比')
    lines = []
    for unit in units:
        for index,line in enumerate(unit.page_content.splitlines(),1):
            lines.append({'text':line,'location':location_label(unit.metadata)+f' · 片段内第{index}行'})
    if len(lines) > MAX_TEXT_LINES:
        raise ValueError(f'{path.name}超过2万文本行，请按章节拆分后对比')
    return lines


def _document_changes(left,right):
    a,b = _document_lines(left),_document_lines(right)
    matcher = difflib.SequenceMatcher(a=[line['text'] for line in a],b=[line['text'] for line in b],autojunk=True)
    changes = []
    for kind,i,j,k,l in matcher.get_opcodes():
        if kind == 'equal':
            continue
        # Count each aligned changed line once; surplus lines are additions or
        # deletions. A SequenceMatcher replace hunk is not one semantic change.
        for start in range(max(j-i,l-k)):
            old,new = a[i+start:min(j,i+start+1)],b[k+start:min(l,k+start+1)]
            changes.append({'kind':'modified' if old and new else 'added' if new else 'deleted',
                            'left':{'lines':old} if old else None,'right':{'lines':new} if new else None})
    return changes,[{'file':left.name,'total_lines':len(a)},{'file':right.name,'total_lines':len(b)}]


def _page(changes,offset,limit):
    selected = []
    used = 0
    for change in changes[offset:offset+limit]:
        size = len(json.dumps(change,ensure_ascii=False))
        if size > PAGE_CHARS:
            raise ValueError('单项差异片段过长，请按章节或字段拆分文件后对比；未截断后宣称完整')
        if selected and used+size > PAGE_CHARS:
            break
        selected.append(change)
        used += size
    return selected


@tool
def compare_uploaded_files(left_filename: str, right_filename: str, key_columns: list[str] | None = None,
                           left_sheet: str | None = None, right_sheet: str | None = None,
                           offset: int = 0, limit: int = 20) -> str:
    """只读对比当前会话的两份附件，返回可定位到左右原文的增删修改与真实证据编号。

    文档支持DOCX/PDF/TXT/MD，按解析文本比较，不判定法律或技术等价性，不比较图片和版式。
    两表支持CSV/XLSX/XLS，须先用read_file_content查看实际表头和工作表，再传双方共有且非空唯一的key_columns（如所编号），不按行号猜关联。
    left为基准、right为对比版；多工作表应明确指定，未指定时只比较各自第一张表，结果声明所选工作表。
    offset为差异条目偏移；limit为1～100。truncated=true时继续next_offset分页，不能把当前页当全量差异。
    """
    try:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('offset须为非负整数，limit须为1～100')
        left,right = _attachment(left_filename),_attachment(right_filename)
        if left == right:
            raise ValueError('须选择两份不同的附件')
        table_mode = left.suffix.lower() in TABLES and right.suffix.lower() in TABLES
        document_mode = left.suffix.lower() in DOCUMENTS and right.suffix.lower() in DOCUMENTS
        if not table_mode and not document_mode:
            raise ValueError('表格与文档不能直接按同一规则比较，请选择两表或两份文档')
        if document_mode and (key_columns or left_sheet or right_sheet):
            raise ValueError('文档比较不接受表格关联列或工作表参数')
        changes, metadata = (_table_changes(left,right,key_columns,left_sheet,right_sheet) if table_mode
                             else _document_changes(left,right))
        if offset > len(changes):
            raise ValueError(f'offset超过差异条目总数{len(changes)}')
        selected = _page(changes,offset,limit)
        counts={kind:sum(c['kind']==kind for c in changes) for kind in ('added','deleted','modified','schema')}
        refs = []
        for side,path,info in zip(('left','right'),(left,right),metadata):
            excerpts = [change[side] for change in selected if change[side] is not None]
            literal_lines=[];locations=[]
            if document_mode:
                for excerpt in excerpts:
                    for line in excerpt['lines']:
                        literal_lines.append(line['text']);locations.append(line['location'])
            refs.append(evidence.add(context.get_thread_id(),{
                'type':'file','title':path.name,'revision':hashlib.sha256(path.read_bytes()).hexdigest(),
                'location':f'工作表 {info["sheet"]}' if table_mode else '文档解析原文位置见各片段',
                'comparison_locations':locations,
                'snippet':'\n'.join(literal_lines) if document_mode else json.dumps({'comparison':{'total_changes':len(changes),'left_file':left.name,'right_file':right.name,
                                                     'returned_offset':offset,'returned_count':len(selected)},
                                      'excerpts':excerpts},ensure_ascii=False),
                'comparison_scope':info,'comparison_side':side,'comparison_counts':counts,
            }))
        next_offset = offset+len(selected)
        result = {'status':'compared','mode':'keyed_table' if table_mode else 'document_text',
                  'left':{**metadata[0],'evidence_id':refs[0]},'right':{**metadata[1],'evidence_id':refs[1]},
                  'total_changes':len(changes),'edit_counts':counts,'count_unit':'按对齐文本行计数；连续差异块不等于技术修改项数' if document_mode else '按关联记录计数',
                  'location_policy':'只报告解析器提供的页码、正文块或片段行号；未取得页码不代表原文件没有分页',
                  'offset':offset,'changes':selected,
                  'truncated':next_offset<len(changes),'next_offset':next_offset if next_offset<len(changes) else None,
                  'scope_note':'仅比较指定工作表的单元格值，不比较公式、格式或其他工作表' if table_mode
                  else '仅比较解析出的文字；不覆盖图片、版式或法律与技术等价性'}
        return json.dumps(result,ensure_ascii=False)
    except (ValueError,TypeError,OSError,KeyError,BadZipFile,PdfReadError,PackageNotFoundError) as exc:
        return json.dumps({'status':'error','message':str(exc)},ensure_ascii=False)

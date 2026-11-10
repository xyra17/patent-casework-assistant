"""Product responsibility contract; independent from frozen business scores.

A profile never grants permissions or turns failed execution into human review.
Only host events and reviews of the exact returned bytes can certify delivery.
"""
from __future__ import annotations
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from typing import Literal

VERSION = 'product-responsibility-v1.1.0'
PROFILES = ('case-materials', 'document-comparison', 'disclosure-search',
            'public-reference', 'uploaded-table-analysis')
_current = ContextVar('product_responsibility', default=None)

class ProductContext(BaseModel):
    profile: Literal['case-materials', 'document-comparison', 'disclosure-search',
                     'public-reference', 'uploaded-table-analysis']
    ordered_files: list[str] = Field(default_factory=list, max_length=20)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def request_contract(query, product):
    if not product:
        return None
    value=product.model_dump() if isinstance(product, ProductContext) else ProductContext.model_validate(product).model_dump()
    judgments=[]
    # Request routing only: never infer correctness or release state from prose.
    tests={
        'case-materials':(r'核心机制|技术解释|技术影响|技术作用', ['核心技术机制及专业解释']),
        'document-comparison':(r'机制|技术影响|技术作用|实质变化|替代|取代|触发条件', ['机制是否相同、替换、补充或不同', '技术影响、触发条件与适用范围']),
        'disclosure-search':(r'机制|最相近|最接近|借鉴|替代|技术影响|相似', ['技术机制的实质关系', '专业意义的最相近方案、可借鉴性及技术影响']),
    }
    pattern,labels=tests.get(value['profile'], ('(?!)', []))
    if re.search(pattern, query):
        judgments=labels
    from tools.chart_utils import task_needs_charts
    return {**value,'chart_required':task_needs_charts(query),'version':VERSION,'query_sha256':hashlib.sha256(query.encode()).hexdigest(),
            'professional_judgments':judgments,'human_confirmation':None}


@contextmanager
def scope(value):
    token=_current.set(value)
    try:yield
    finally:_current.reset(token)


def current():return _current.get()


def prompt():
    contract=current()
    if not contract:return ''
    instructions={
        'case-materials':'分别交付负责人及案件信息、会议明确待补事项和原文来源；技术解释为有据初步摘要，专业判断待人工确认。审查未完成不能称完整报告。',
        'document-comparison':'必须使用compare_uploaded_files作双侧文字比较（include_unchanged=true，分页完整）。文字差异只说明原句增删改及位置。不得由文字替换推出机制替换或同一性。技术影响、机制关系只能作为有据初步分析，待人工确认；保留语义越界拦截，不用免责声明掩盖错误。',
        'disclosure-search':'分开列出检索候选及两侧原文位置、可核验排序依据、适用条件与缺口；匹配分不是机制等价或专利专业结论。专业推荐仅为初步分析，待人工确认；上传原件定位失败必须未完成。',
        'public-reference':'真实检索公开来源，优先官方。区分网页原文、有据解释及推断，保留URL标题及引文定位。用户问RRF时完整说明公式、倒数贡献、原始相似分不直接求和；证据不足或关键解释缺失为未完成。',
        'uploaded-table-analysis':'全部筛选、去重、聚合及图表数据由结构化工具计算。按案号去除完全重复记录用read_file_content的deduplicate_by实际字段参数，先去重再筛选。提供程序aggregation_proof唯一值集合、来源行、文件哈希与统计口径，不能自行写去重证明。未知阶段另列，0和未知不同。仅字段/口径真有歧义时请求澄清，不能以人工审核替代计算失败。',
    }
    return '\n【本次产品职责契约 '+VERSION+'】\n'+instructions[contract['profile']]


async def prepare_objective(thread_id):
    """Produce a bounded, literal, two-sided view with the existing tool."""
    contract=current()
    if not contract or contract['profile']!='document-comparison':return
    from api.monitor import monitor
    from tools.attachment_compare import compare_uploaded_files
    names=contract['ordered_files']
    proof={'version':VERSION,'status':'INCOMPLETE','reason':'须明确两份附件的基准和修订顺序','records':[]}
    if len(names)==2 and len(set(names))==2 and all(Path(n).name==n for n in names):
        offset=0;pages=[]
        for _ in range(100):
            value=json.loads(await compare_uploaded_files.ainvoke({'left_filename':names[0],
                'right_filename':names[1],'include_unchanged':True,'offset':offset,'limit':100}))
            if value.get('status')!='compared' or value.get('mode')!='document_text':
                proof['reason']=value.get('message') or '未完成文字比较';break
            pages.append(value)
            if sum(len(json.dumps(p,ensure_ascii=False)) for p in pages)>500_000:
                proof['reason']='完整文字对照超过50万字符交付上限，须按章节拆分';break
            if not value['truncated']:
                records=[c for p in pages for c in p['changes']]
                proof={'version':VERSION,'status':'VERIFIED','reason':'完整解析文字由程序逐行对齐；不覆盖图片、版式及专业等价性',
                    'left':pages[0]['left'],'right':pages[0]['right'],'records':records,
                    'edit_counts':value['edit_counts'],'pages':len(pages),
                    'source_ids':list(dict.fromkeys(p[side]['evidence_id'] for p in pages for side in ('left','right')))}
                break
            offset=value['next_offset']
    proof['proof_sha256']=digest(proof)
    await monitor.report(thread_id,'product_objective','程序文字对照结果',proof)


def _sources(items, ids):
    result=[]
    for item in items:
        if item.get('id') not in ids:continue
        web=item.get('type')=='web'
        url=urlparse(str(item.get('url') or ''))
        location=item.get('location') or item.get('document_locations') or item.get('comparison_locations')
        revision=item.get('revision')
        if (not location and item.get('content_complete') and isinstance(item.get('content'),str)
            and Path(item.get('title') or '').suffix.lower() in ('.txt','.md') and revision):
            location={'kind':'physical_text_lines','first_line':1,
                'line_count':len(item['content'].splitlines()),'text_sha256':hashlib.sha256(item['content'].encode()).hexdigest()}
        if item.get('type')=='database' and item.get('casework'):
            coverage=item.get('coverage') or {};roots=coverage.get('roots') or []
            complete=item.get('completeness') or {}
            if (roots and coverage.get('complete_scan') and not coverage.get('issues')
                and all(r.get('state')=='ready' and r.get('generation') is not None
                        and r.get('mirror_generation')==r.get('generation') for r in roots)
                and complete.get('scope')=='department_published_view'
                and not any(complete.get(k) for k in ('has_more','fetch_truncated','evidence_truncated','model_truncated'))
                and complete.get('returned_rows')==len(item.get('rows') or [])):
                revision=digest(roots)
                location={'kind':'published_database_projection','roots':roots,
                    'columns':item.get('columns'),'rows_sha256':digest(item.get('rows')),
                    'note':'投影代次与返回行绑定；原件字段位置仍由原有来源校验验收'}
        if web:location={'url':item.get('url'),'excerpt_sha256':digest(item.get('content') or item.get('snippet') or '')}
        valid=bool(item.get('title') and (item.get('snippet') or item.get('content') or item.get('rows'))
                   and location and ((url.scheme in ('https','http') and url.hostname and not url.username) if web else revision))
        result.append({'id':item.get('id'),'type':item.get('type'),'title':item.get('title'),
                       'revision':revision,'location':location,'valid':valid,
                       'case_id':item.get('case_id'),'excerpt':item.get('snippet') or '',
                       'source':item.get('source') or item.get('url'),
                       'ranking':item.get('score') or item.get('rerank_score'),
                       'content_sha256':digest(item)})
    return result


def assessment(run, review, facts, completeness, delivery):
    events=run.get('events') or []
    contract=next((e.get('data') for e in reversed(events) if e.get('event')=='product_boundary_request'),None)
    if not contract:return None
    text=run.get('result') or ''
    profile=contract.get('profile')
    refs=set(re.findall(r'\[(S\d+)\]',text))
    items=run.get('evidence') or []
    sources=_sources(items,refs)
    issues=[]
    released=(run.get('status')=='completed' and delivery['status']=='released'
        and facts['status']=='passed' and completeness['status']=='passed'
        and not review.get('hard_issues') and not review.get('soft_issue'))
    if not released:issues.append('最终正文未完成对应的事实、完整性及交付审查，不能作为已核验终稿')
    if not sources or refs-set(s['id'] for s in sources) or any(not s['valid'] for s in sources):
        issues.append('关键来源、版本或定位未完整绑定')
    objective=None
    if profile=='document-comparison':
        objective=next((e.get('data') for e in reversed(events) if e.get('event')=='product_objective'),None)
        valid=bool(objective and objective.get('status')=='VERIFIED'
            and objective.get('proof_sha256')==digest({k:v for k,v in objective.items() if k!='proof_sha256'}))
        proof_sources=_sources(items,set((objective or {}).get('source_ids') or []))
        if not valid or not proof_sources or any(not s['valid'] for s in proof_sources):
            issues.append('程序双侧文字对照不完整或来源未绑定')
        elif len(proof_sources)!=len(set(objective['source_ids'])):
            issues.append('文字对照证据记录缺失')
    if profile=='disclosure-search':
        if not any(s['type']=='file' and s['valid'] for s in sources) or not any(s['type']=='knowledge' and s['case_id'] and s['valid'] for s in sources):
            issues.append('上传原件与历史候选的双侧定位未完成')
    if profile=='public-reference' and not any(s['type']=='web' and s['valid'] for s in sources):
        issues.append('无真实公开网页来源及可定位引文')
    program_tables=[]
    if profile=='uploaded-table-analysis':
        for item in items:
            if item.get('id') not in refs or not item.get('aggregates'):continue
            proof=item.get('aggregation_proof') or {}
            if (proof.get('file_sha256')!=item.get('revision') or proof.get('filters')!=item.get('filters')
                or proof.get('sheet')!=item.get('sheet') or not proof.get('complete')):
                issues.append('统计结果缺少同文件完整程序证明');continue
            cols=item.get('columns') or [];rows=item.get('rows') or [];groups=proof.get('groups') or []
            if len(groups)!=len(rows):issues.append('统计证明与结果行数不一致');continue
            required_metrics={a.get('alias') or f"{a['field']}_{a['op']}" for a in item['aggregates']}
            for row,group in zip(rows,groups):
                if required_metrics != {m.get('column') for m in group.get('metrics',[])}:
                    issues.append('聚合缺少可复核的完整指标证明')
                for field,value in group.get('group',{}).items():
                    if field not in cols or (None if row[cols.index(field)] is None else str(row[cols.index(field)]))!=value:
                        issues.append('统计分组与来源证明不一致')
                if not group.get('source_rows') and item.get('matched_rows'):
                    issues.append('统计结果缺少来源行')
                for metric in group.get('metrics',[]):
                    column=metric.get('column')
                    from decimal import Decimal, InvalidOperation
                    op=metric.get('op');values=[v for v in metric.get('source_values',[]) if v is not None]
                    try:
                        if op=='nunique':expected=len(set(metric['unique_values']))
                        elif op=='count':expected=metric.get('non_null_count')
                        elif op in ('sum','mean'):
                            numbers=[Decimal(str(v)) for v in values]
                            expected=sum(numbers,Decimal(0)) if op=='sum' else sum(numbers,Decimal(0))/len(numbers) if numbers else None
                        elif op in ('min','max'):expected=(min(values) if op=='min' else max(values)) if values else None
                        else:raise ValueError('unsupported aggregate')
                        def equal(actual):
                            if op in ('sum','mean') and expected is not None:
                                return Decimal(str(actual))==expected
                            return actual==expected
                        matches=column in cols and equal(row[cols.index(column)]) and equal(metric.get('value'))
                    except (KeyError, TypeError, ValueError, InvalidOperation):matches=False
                    if not matches:
                        issues.append('统计证明与数值不一致')
            program_tables.append({'evidence_id':item['id'],'file_sha256':item.get('revision'),
                'columns':cols,'rows':rows,'proof':proof,'deduplication':item.get('deduplication')})
        if not program_tables:issues.append('没有程序计算并绑定来源的统计交付')
        if contract.get('chart_required'):
            from tools.chart_utils import build_chart_items
            charts=run.get('charts')
            if charts is None:
                charts=next(((e.get('data') or {}).get('charts') for e in reversed(events) if e.get('event')=='task_result'),None)
            expected=build_chart_items([{k:v for k,v in item.items() if k!='chart'} for item in items
                if item.get('id') in {t['evidence_id'] for t in program_tables}], '柱状图')
            shape=lambda c: {k:(c.get('chart') or c).get(k) for k in ('xAxis','series','sourceRows')}
            if not expected or not charts or any(not any(shape(c)==shape(e) for c in charts) for e in expected):
                issues.append('实际图表的类别、系列、数值或来源行与程序统计不一致或交付缺失')
    judgments=contract.get('professional_judgments') or []
    state='INCOMPLETE' if issues else 'REVIEW_REQUIRED' if judgments else 'VERIFIED'
    if run.get('status') in ('failed','cancelled'):state='BLOCKED'
    return {'version':VERSION,'profile':profile,'status':state,
        'report_sha256':hashlib.sha256(text.encode()).hexdigest(),'contract_sha256':digest(contract),
        'objective_status':'VERIFIED' if objective and objective.get('status')=='VERIFIED' and not any('对照' in i for i in issues) else ('VERIFIED' if not issues else 'INCOMPLETE'),
        'objective_comparison':objective,'program_tables':program_tables,
        'sources':sources,'issues':list(dict.fromkeys(issues)),
        'professional_judgments':[{'judgment':j,'status':'REVIEW_REQUIRED','reviewer':None,
            'reason':'技术关系须由专业人员核对原件、条件与适用范围；Agent分析为初步建议'} for j in judgments],
        'human_confirmation':None,'independent_review':False,
        'evaluation_scope':'PRODUCT_AUXILIARY_MODE; does not change frozen Q1 scores'}


def publication_notice(text):
    contract=current()
    if not contract or not contract.get('professional_judgments'):return text
    notice=('**专业判断使用边界（系统说明）**：以下Agent分析为初步建议，技术机制关系、影响及借鉴结论待人工确认。'
            '该提示不代表事实、来源或交付已经通过核验；未完成项仍受原校验约束。\n\n')
    return text if text.startswith(notice) else notice+text


def render_statistics(report, items, query):
    """Publish program cells, never a model-authored numerical summary.

    Scope still needs the normal requirement Validator. A missing complete raw
    table cannot be cured by converting the result to professional review.
    """
    contract=current()
    if not contract or contract['profile']!='uploaded-table-analysis':return report, [], ''
    from tools.answer_contracts import fact_block, fact_token
    refs=set(re.findall(r'\[(S\d+)\]',report))
    aggregates=[e for e in items if e.get('aggregates') and (e.get('id') in refs or fact_token(e) in report)]
    if not aggregates:return report, [], '未取得本次答案实际引用的程序聚合结果'
    blocks=['## 程序统计结果\n\n以下数值与集合由结构化工具计算；模型原始草稿另存，不作为统计终稿。']
    def table(columns,rows,sid):
        escape=lambda v:('未记录' if v is None else str(v)).replace('|','\\|').replace('\n',' ')
        return '| '+' | '.join(columns+['来源'])+' |\n| '+' | '.join(['---']*(len(columns)+1))+' |\n'+'\n'.join('| '+' | '.join([escape(v) for v in row]+[f'[{sid}]'])+' |' for row in rows)
    for item in aggregates:
        proof=item.get('aggregation_proof') or {}
        if (not proof.get('complete') or proof.get('file_sha256')!=item.get('revision')
            or proof.get('filters')!=item.get('filters') or proof.get('sheet')!=item.get('sheet')):
            return report, [], '聚合缺少完整的同来源程序证明'
        blocks.append(fact_block(item) or table(item['columns'],item['rows'],item['id']))
        blocks.append('文件版本 SHA256：'+item['revision']+'；分组字段：'+json.dumps(item.get('group_by') or [],ensure_ascii=False))
        for group in proof['groups']:
            for metric in group['metrics']:
                if metric.get('op') in ('count','nunique'):
                    blocks.append('程序唯一值集合（字段 '+metric['field']+'；分组 '+json.dumps(group['group'],ensure_ascii=False)+'）：'+
                        '、'.join(str(v) for v in metric['unique_values'])+'；来源物理行：'+'、'.join(f'第{n}行' for n in group['source_rows'])+f"。[{item['id']}]")
    if re.search(r'未知.{0,4}(?:阶段|状态)|(?:阶段|状态).{0,4}未知',query):
        anchor=aggregates[0]
        raw=next((e for e in items if e.get('type')=='file' and not e.get('aggregates')
            and e.get('revision')==anchor.get('revision') and e.get('title')==anchor.get('title')
            and e.get('sheet')==anchor.get('sheet') and not e.get('filters') and e.get('offset',0)==0
            and not e.get('truncated') and e.get('matched_rows')==len(e.get('rows') or [])
            and e.get('columns') and len(e.get('row_numbers') or [])==len(e.get('rows') or [])),None)
        if raw is None:return report, [], '未知状态分组需要同文件完整原始行；当前读取范围不足'
        fields=[f for f in raw['columns'] if re.search('阶段|状态',f) and (f in query or re.search('阶段|状态',query))]
        if len(fields)!=1:return report, [], '状态字段含义不唯一，请先确认统计口径'
        field=fields[0];index=raw['columns'].index(field)
        keys=(anchor.get('deduplication') or {}).get('keys') or (anchor.get('aggregation_proof') or {}).get('deduplicate_by') or []
        if not keys:keys=list(dict.fromkeys(a['field'] for a in anchor['aggregates'] if a['op']=='nunique'))
        rows=raw['rows'];unique={};all_rows={}
        if keys:
            if any(k not in raw['columns'] for k in keys):return report, [], '去重字段未绑定到完整原始表'
            for row,number in zip(rows,raw['row_numbers']):
                key=tuple(row[raw['columns'].index(k)] for k in keys)
                if any(v is None or not str(v).strip() for v in key):return report, [], '主键缺失，无法证明去重集合'
                if key in unique and row!=unique[key]:return report, [], '同一主键字段冲突，须确认口径后计算'
                unique[key]=row;all_rows.setdefault(key,[]).append(number)
            rows=list(unique.values())
            blocks.append('完整唯一主键集合（'+','.join(keys)+'）：'+'；'.join('、'.join(str(v) for v in key) for key in unique)+f"。[{raw['id']}]")
        unknown=[row for row in rows if row[index] is None or not str(row[index]).strip()]
        blocks.append(f"## 未知状态（仅指字段“{field}”为空）\n\n程序按空值筛出以下记录；其他已记录状态不能归入未知。[{raw['id']}]\n\n"+table(raw['columns'],unknown,raw['id']))
        null_lines=[n for row,n in zip(raw['rows'],raw['row_numbers']) if row[index] is None or not str(row[index]).strip()]
        blocks.append('上述未知记录的来源物理行：'+'、'.join(f'第{n}行' for n in null_lines)+f"。[{raw['id']}]")
    if contract.get('chart_required'):blocks.append('图表由上述程序聚合结果生成，可在图表页核对类别、系列和数值。')
    return '\n\n'.join(blocks), [{'kind':'program_statistics_publication','source_ids':[e['id'] for e in aggregates],
        'model_draft_sha256':hashlib.sha256(report.encode()).hexdigest(),'professional_review_required':False}], ''

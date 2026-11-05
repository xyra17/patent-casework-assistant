"""Evidence-bound case joins. Models select quoted mechanisms; Python counts dates."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from langchain_core.tools import tool
from api import context, evidence


def object_array(item):
    if not item.get('content_complete'):
        raise ValueError('需要完整读取原始附件，不能用预览或截断页作为关联输入')
    text=item.get('content','').strip()
    start=0 if text.startswith('[') else text.find('\n[')+1
    if start<0 or (start==0 and not text.startswith('[')):
        raise ValueError('需要对象数组（JSON或Markdown中的完整JSON数组）')
    data,end=json.JSONDecoder().raw_decode(text[start:])
    if text[start+end:].strip() or not isinstance(data,list) or any(not isinstance(row,dict) for row in data):
        raise ValueError('附件不是完整对象数组')
    if len(data)>1000:raise ValueError('案卷关联最多1000条，请缩小范围')
    return data


def _positive_quote(text, quote):
    """Bind literal affirmative clauses, preserving paragraph-level explicit denials.

    A denial of an unrelated mechanism after a semicolon must not invalidate
    preceding affirmative evidence. A denial introducing a whole mechanism list
    still governs its following sentences within that paragraph.
    """
    if not isinstance(quote, str) or len(quote.strip()) < 8:
        return False
    negative = r'不(?:包含|涉及|采用|使用|支持|具备|含有|是)|并非|没有|未(?:采用|使用|涉及|实现)|待核实|是否|能否|拟采用'
    for match in re.finditer(re.escape(quote), text):
        paragraph_start = text.rfind('\n', 0, match.start()) + 1
        paragraph_end = text.find('\n', match.end())
        paragraph = text[paragraph_start:paragraph_end if paragraph_end >= 0 else len(text)]
        if re.search(r'(?:不采用|不涉及|未采用|未实现|没有)(?:下述|以下|如下|上述)(?:技术)?机制[：:]', paragraph):
            continue
        for clause in re.finditer(r'[^。；;\n]+', text):
            start = max(clause.start(), match.start())
            end = min(clause.end(), match.end())
            if end - start >= 8 and not re.search(negative, clause[0]):
                return True
    return False


def _day(value):
    if not isinstance(value,str):return None
    try:return date.fromisoformat(value).isoformat() if len(value)==10 else None
    except ValueError:return None


def _month(value):
    if not re.fullmatch(r'\d{4}-\d{2}',value or ''):raise ValueError('月份必须是YYYY-MM')
    date.fromisoformat(value+'-01');return value


def requested_ledger_filters(query):
    """Literal equalities joined by AND; parse a whole clause or none of it."""
    filters = {}
    fields = {'client_name':'client_name', '客户':'client_name',
              'owner_name':'owner_name', '承办人':'owner_name',
              'stage':'stage', '办理阶段':'stage', '阶段':'stage'}
    for clause in re.split(r'[。；;\n]', query):
        match = re.fullmatch(r'\s*(?:仅|只)(?:统计|保留|考虑)(.+?)的(?:案件|案卷)\s*', clause)
        if not match:
            continue
        parsed=[]
        for part in re.split(r'并且|且|、|，',match[1]):
            # Legacy input is accepted; all generated labels use 案件信息.
            predicate=re.fullmatch(r'\s*(?:案件信息|台账)?(client_name|owner_name|stage|客户|承办人|办理阶段|阶段)(?:为|等于|=)(.+?)\s*',part)
            if not predicate or any(token in predicate[2] for token in ('或者','或','以及','除外','不为','不是','不等于','大于','小于')):
                break
            parsed.append((fields[predicate[1]],predicate[2].strip()))
        else:
            for field,value in parsed:
                if field in filters and filters[field] != value:
                    raise ValueError('同一案件信息字段存在冲突的明确筛选条件，须澄清')
                filters[field]=value
            continue
    return filters


def requested_technical_condition(query):
    """Bind only an explicitly delimited user condition, without paraphrasing."""
    labels = r'技术条件为|需同时满足的技术要点|目标机制要求|需要核实的完整技术条件|需要同时满足的技术机制是|目标技术机制为|需同时满足的技术条件'
    matches = re.findall(r'(?:^|[。\n])\s*(?:'+labels+r')[：:]\s*([^。\n]+)', query)
    conditions = list(dict.fromkeys(value.strip() for value in matches))
    if len(conditions) > 1:
        raise ValueError('原问题标出了多个不同技术条件，须澄清完整边界')
    return conditions[0] if conditions else None


def calculate(items,documents_evidence_id,ledger_evidence_id,technical_query,matches,mode,
              received_month=None,filed_month=None,policy_evidence_ids=(),ledger_filters=None):
    """Pure, replayable calculation; no model or filesystem access."""
    lookup={e['id']:e for e in items}
    if len(technical_query.strip())<4:raise ValueError('须保留具体技术条件，不能以领域名代替机制')
    for ident in [documents_evidence_id,ledger_evidence_id,*policy_evidence_ids]:
        if ident not in lookup:raise ValueError('输入来源编号不存在')
    documents=object_array(lookup[documents_evidence_id]);ledger=object_array(lookup[ledger_evidence_id])
    if not documents:raise ValueError('没有技术原文，不能将未检索到证据当作零案件')
    if any(not isinstance(d.get('case_id'),str) or not isinstance(d.get('text'),str) for d in documents):
        raise ValueError('技术材料每条须含case_id与text')
    if any(not isinstance(row.get('case_id'),str) for row in ledger):raise ValueError('案件信息缺少case_id')
    if mode not in ('history','counts'):raise ValueError('不支持的关联动作')
    if mode=='counts':received_month=_month(received_month);filed_month=_month(filed_month)
    elif received_month or filed_month:raise ValueError('历史办理清单不接受月份参数')
    if len(matches)>1000:raise ValueError('技术命中超过预算')
    selected={}
    for match in matches:
        ident,quote=match.get('case_id'),match.get('quote','')
        candidates=[d['text'] for d in documents if d['case_id']==ident]
        if not candidates or not any(_positive_quote(text,quote) for text in candidates):
            raise ValueError('技术案号或正向机制原句未绑定附件：'+str(ident))
        selected.setdefault(ident,quote)
    ledger_filters = ledger_filters or {}
    if (not isinstance(ledger_filters,dict) or set(ledger_filters)-{'client_name','owner_name','stage'}
            or any(not isinstance(v,str) or not v.strip() for v in ledger_filters.values())):
        raise ValueError('案件信息等值筛选字段或值无效')
    fields=tuple(dict.fromkeys(('owner_name','received_date','actual_filed_date','filing_verified',*ledger_filters)))
    joined=[];missing=[];excluded=[]
    for ident in sorted(selected):
        rows=[r for r in ledger if r['case_id']==ident]
        if not rows:missing.append(ident);continue
        row={'case_id':ident};conflicts=set().union(*(set(r.get('conflict_fields') or []) for r in rows))
        for field in fields:
            values={json.dumps(r.get(field),sort_keys=True,ensure_ascii=False) for r in rows}
            row[field]=rows[0].get(field) if len(values)==1 and field not in conflicts else None
            if len(values)>1:conflicts.add(field)
        row['received_date']=_day(row['received_date']);row['actual_filed_date']=_day(row['actual_filed_date'])
        row['filing_verified']=row['filing_verified'] is True and row['actual_filed_date'] is not None
        if any(row.get(field) is None or row.get(field)=='' for field in ledger_filters):
            raise ValueError('筛选字段缺失或冲突，不能推定纳入或排除：'+ident)
        if any(row[field]!=value for field,value in ledger_filters.items()):
            excluded.append({'case_id':ident,'values':{field:row[field] for field in ledger_filters}})
            continue
        row['conflict_fields']=sorted(conflicts);joined.append(row)
    if missing:raise ValueError('技术命中缺少案件信息关联，不能按零计数：'+','.join(missing))
    if mode=='counts':
        answer={'received_count':sum(bool(r['received_date']) and r['received_date'][:7]==received_month for r in joined),
                'filed_count':sum(r['filing_verified'] and r['actual_filed_date'][:7]==filed_month for r in joined)}
    else:
        history=[r for r in joined if r['filing_verified']]
        if any(not r['owner_name'] for r in history):raise ValueError('已核实历史案的承办人缺失或冲突，需补证')
        answer={'history_ids':[r['case_id'] for r in history], 'owners':sorted({r['owner_name'] for r in history})}
    result={'version':1,'mode':mode,'technical_query':technical_query,'documents_evidence_id':documents_evidence_id,
            'ledger_evidence_id':ledger_evidence_id,'policy_evidence_ids':list(policy_evidence_ids),
            'received_month':received_month,'filed_month':filed_month,'matches':[{'case_id':i,'quote':q} for i,q in sorted(selected.items())],
            'joined_rows':joined,'answer':answer,'scope':'仅本次完整附件中的正向技术机制与案件信息按案号关联；未知实际日期不归月，阶段及计划节点不能替代已核实交局。'}
    if ledger_filters:
        result.update(ledger_filters=ledger_filters, excluded_by_filters=excluded)
    return result


@tool
def aggregate_case_records(documents_evidence_id:str,ledger_evidence_id:str,technical_query:str,
                           matches:list[dict],mode:Literal['history','counts'],received_month:str|None=None,
                           filed_month:str|None=None,policy_evidence_ids:list[str]|None=None)->str:
    """按已读取附件的技术案号关联案件信息，由程序生成历史办理清单或月度计数。先用read_file_content完整读取技术对象数组(case_id/text)、案件信息数组和口径。

    matches=[{"case_id":"案号","quote":"该案原文正向机制原句"}]，逐案核对所有技术材料，不能以标题/领域或否定句命中。technical_query逐字保留用户技术条件。
    mode=counts须传received_month和filed_month(YYYY-MM)，分别按received_date与actual_filed_date+filing_verified去重计数。
    mode=history按非空实际交局日且filing_verified=true列案号及去重承办人，不凭阶段判完成。未知/冲突不选值。同案副本去重。
    宿主从持久化的原始任务绑定其支持的明确客户/承办人等值条件，在关联计数前过滤；结果中的ledger_filters与excluded_by_filters可核对。其他附加条件仍须完整执行和审查，不得自行手改结果。
    policy_evidence_ids为已读取口径的S编号。直接使用recommended_answer，禁止手数、重算或追加未要求字段。技术相关性仍须独立审查，程序计数不证明语义选择正确。
    """
    try:
        if not context.consume_tool_budget('case_join',6):raise ValueError('本轮案卷关联预算已耗尽')
        # Only the durable effective user query can authorize these filters.
        # Neither model parameters nor source-document instructions can add or remove them.
        from api import storage
        run=storage.get_run(context.get_thread_id()) if context.get_thread_id() else None
        original_query=(run or {}).get('execution_query','')
        filters=requested_ledger_filters(original_query)
        # The original condition is authoritative. A model paraphrase must not
        # silently broaden/narrow it, or force futile repeated correction.
        technical_query=requested_technical_condition(original_query) or technical_query
        result=calculate(evidence.snapshot(context.get_thread_id()),documents_evidence_id,ledger_evidence_id,
                         technical_query,matches,mode,received_month,filed_month,policy_evidence_ids or [],filters)
        ident=evidence.add(context.get_thread_id(),{'type':'file','title':'案卷技术与案件信息关联（程序计算）',
              'snippet':json.dumps(result,ensure_ascii=False),'case_calculation':result})
        refs=list(dict.fromkeys([documents_evidence_id,ledger_evidence_id,*(policy_evidence_ids or []),ident]))
        answer={**result['answer'],'evidence_ids':refs}
        return json.dumps({'evidence_id':ident,'calculation':result,'recommended_answer':answer,
            'context_digest':{'evidence_id':ident,'recommended_answer':answer,'scope':result['scope']}},ensure_ascii=False)
    except (ValueError,TypeError,KeyError) as exc:return '案卷关联失败：'+str(exc)


def requested_mode(query):
    # A narrow explicit structured-output contract, not a guess about arbitrary JSON.
    if not re.search(r'(?:只|仅)[^。\n]{0,12}(?:JSON|json)',query):return None
    if all(k in query for k in ('received_count','filed_count')):return 'counts'
    if all(k in query for k in ('history_ids','owners')):return 'history'
    return None


def requested_months(query):
    def find(label):
        matches=re.findall(r'(\d{4})[-年](\d{1,2})月?\s*(?:已核实|核实|实际|确认|已确认)*'+label,query)
        return list(dict.fromkeys(f'{int(y):04d}-{int(m):02d}' for y,m in matches))
    received=find(r'(?:收案|接收|接案)');filed=find(r'(?:交局|完成交局)')
    return (received[0],filed[0]) if len(received)==len(filed)==1 else None


def check_case_answer(result):
    mode=requested_mode(result.get('query',''))
    if mode is None:return {'status':'not_required','issues':[]}
    issues=[]
    try:
        obj=json.loads(result['report'].strip())
        fields={'received_count','filed_count','evidence_ids'} if mode=='counts' else {'history_ids','owners','evidence_ids'}
        if not isinstance(obj,dict) or set(obj)!=fields:raise ValueError('须仅输出指定字段的JSON对象，不加正文或额外键')
        refs=obj['evidence_ids'];items=result.get('evidence',[]);lookup={e['id']:e for e in items}
        if not isinstance(refs,list) or any(not isinstance(i,str) or i not in lookup for i in refs):raise ValueError('JSON证据编号不存在')
        candidates=[e for e in reversed(items) if e['id'] in refs and e.get('case_calculation')]
        if not candidates:raise ValueError('须调用aggregate_case_records绑定技术案号和程序统计，不能直接手报业务字段')
        calc=candidates[0]['case_calculation']
        condition=requested_technical_condition(result['query'])
        same_condition=not condition or calc['technical_query'].strip().rstrip('。；;')==condition.strip().rstrip('。；;')
        if calc['mode']!=mode or calc['technical_query'] not in result['query'] or not same_condition:
            raise ValueError('程序计算未完整绑定本题技术条件')
        if mode=='counts':
            requested=requested_months(result['query'])
            if requested is None or requested!=(calc['received_month'],calc['filed_month']):raise ValueError('收案月与交局月未分别绑定原问题')
        filters=requested_ledger_filters(result['query'])
        if calc.get('ledger_filters',{})!=filters:raise ValueError('程序计算未绑定本题明确的客户、承办人或阶段筛选')
        rebuilt=calculate(items,**{k:calc[k] for k in ('documents_evidence_id','ledger_evidence_id','technical_query','matches','mode','received_month','filed_month','policy_evidence_ids')},ledger_filters=filters)
        if calc!=rebuilt:raise ValueError('程序计算与原始证据复算不一致')
        for key,value in rebuilt['answer'].items():
            if type(obj[key]) is not type(value) or obj[key]!=value:raise ValueError('字段未采用程序关联结果：'+key)
        if not {calc['documents_evidence_id'],calc['ledger_evidence_id'],*calc['policy_evidence_ids']}<=set(refs):raise ValueError('JSON遗漏计算输入证据')
        return {'status':'passed','issues':[],'calculation':rebuilt,
                'documents':object_array(lookup[calc['documents_evidence_id']])}
    except (ValueError,TypeError,KeyError) as exc:
        issues.append(str(exc))
        return {'status':'failed','issues':issues}

"""Fixed case packet: inventory and bounded published text, never search top-1.

Reads share the caller's SQLite snapshot. Missing parsed text is reported as a
gap in the connected snapshot, never as proof that the original does not exist.
"""
from __future__ import annotations

import json
from langchain_core.tools import tool
from api import context,evidence
from casework import store,catalog,parser


@tool
@store.consistent_read
def collect_case_materials(case_id:str, max_chars:int=80000)->str:
    """按明确案号读取同快照材料清单及已发布正文；标注版本、解析范围、缺项与截断，不作专业判断。"""
    from tools.casework_tools import _guard,_proof
    denial=_guard('knowledge')
    if denial:return denial
    if not parser.CASE_RE.fullmatch(case_id) or not 1000<=max_chars<=160000:
        return '原文读取未完成：明确案号或读取预算无效'
    matches=[c for c in store.list_cases() if c['case_id']==case_id.upper()]
    if len(matches)!=1:
        return json.dumps({'case_id':case_id,'status':'ambiguous' if matches else 'not_found',
            'documents':[],'gaps':['当前接入快照中案号不唯一，需明确目录' if matches else '当前接入快照未找到该案，不能断言原件不存在']},ensure_ascii=False)
    case=matches[0]
    files=catalog.documents(case['case_key'])
    documents=[];remaining=max_chars
    # Read every registered kind; prioritize technical text within the budget.
    priority={'application':0,'disclosure':1,'intake':2,'meeting':3}
    for file in sorted(files,key=lambda f:(priority.get(f['kind'],4),f['relative_path'])):
        doc={k:file.get(k) for k in ('id','kind','revision','parser_key','state','relative_path')}
        doc.update(chunks=[],complete_published_text=False,warnings=[])
        try:
            item=catalog.document(file['id'],file['revision'],file['parser_key'])
            chunks=item['payload']['chunks']
            doc['current']=item['current'];doc['version_status']=item['version_status']
            doc['warnings']=item['payload'].get('warnings',[])
            for ordinal,chunk in enumerate(chunks):
                text=chunk['text']
                if len(text)>remaining:break
                proof={'file_id':file['id'],'revision':file['revision'],'parser_key':file['parser_key'],
                       'path':file['relative_path'],'location':chunk['location'],'excerpt':text,'current':item['current']}
                doc['chunks'].append({'ordinal':ordinal,'text':text,'location':chunk['location'],
                                      'evidence_id':_proof(proof,case)})
                remaining-=len(text)
            doc['total_chunks']=len(chunks)
            doc['complete_published_text']=bool(item['current'] and chunks and len(doc['chunks'])==len(chunks))
        except (ValueError,KeyError) as exc:
            doc['warnings'].append({'reason':str(exc)})
        documents.append(doc)
    gaps=[]
    for kind,label in [('intake','案件信息/立案材料'),('meeting','会议材料'),('application','申请文件')]:
        selected=[d for d in documents if d['kind']==kind]
        if not selected:gaps.append('当前接入清单未登记'+label+'；不等于原件不存在')
        elif not all(d['complete_published_text'] for d in selected):
            gaps.append(label+'存在未解析、未完整读取或非当前版本的正文，需核验')
    manifest=[{k:v for k,v in d.items() if k!='chunks'} for d in documents]
    inventory=evidence.add(context.get_thread_id(),{'type':'knowledge','title':'本案已接入材料清单',
        'case_id':case_id.upper(),'source':'当前已接入文件登记','casework':True,
        'snippet':json.dumps(manifest,ensure_ascii=False),'current_snapshot':True,
        'revision':case['version'],'location':'案件材料登记快照'})
    return json.dumps({'case_id':case_id.upper(),'case_version':case['version'],
        'snapshot':store.published_version(),'inventory_evidence_id':inventory,'documents':documents,
        'gaps':gaps,'characters_read':max_chars-remaining,
        'scope':'仅当前已发布解析文本；读取全部解析块不证明扫描页、图形或原始文件均已识别。'},ensure_ascii=False)


def render_packet(packet,existing_report=''):
    lines=['## 材料范围与版本']
    if packet.get('inventory_evidence_id'):
        sid=packet['inventory_evidence_id']
        lines.extend(['| 材料 | 类型 | 版本 | 已发布正文读取状态 | 来源 |',
                      '| --- | --- | --- | --- | --- |'])
        from casework.workflow import cell
        for doc in packet['documents']:
            lines.append('| '+' | '.join(cell(v) for v in [doc['relative_path'],doc['kind'],doc['revision'],
                '完整读取已发布解析文本' if doc['complete_published_text'] else '未完整读取/待核验'])+f' | [{sid}] |')
    for gap in packet.get('gaps',[]):lines.append('- '+gap)
    for doc in packet.get('documents',[]):
        if doc['kind'] not in ('application','disclosure'):continue
        lines.append('### '+doc['relative_path']+'：已读取原文')
        from casework.search import body_priority
        selected=sorted(doc['chunks'],key=lambda c:(-body_priority(c['text']),c['ordinal']))[:3]
        for chunk in sorted(selected,key=lambda c:c['ordinal']):
            if chunk['text'] in existing_report.replace('\n> ','\n'):continue
            lines.append('> '+chunk['text'].replace('\n','\n> ')+f" [{chunk['evidence_id']}]")
            lines.append('')
        if len(selected)<len(doc['chunks']):lines.append('上方仅展示正文节选；已读取内容与其余来源保留在任务证据中。')
    lines.append(packet.get('scope','仅当前接入范围，不能据此断言原件不存在。'))
    return '\n'.join(lines)

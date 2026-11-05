"""Local, deterministic extraction; every value retains its file location."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from tools.document_parser import document_format, location_label, parse_document
from casework.domains import canonical as canonical_domain

VERSION = 'department-11'
SUPPORTED = {'.xlsx','.xls','.csv','.doc','.docx','.pdf','.txt','.md','.json','.vsdx','.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}
INVENTORY = {'.svg','.vsd','.dwg','.pptx','.zip'}
FIELDS = {
    'case_id':('所编号',['我方案号','所编号','所内编号','案件编号','案号']),
    'client_case_id':('客户案号',['客户案号','客户编号','客户侧编号']),
    'title':('发明名称',['发明名称','案件名称','专利名称']),
    'client_alias':('客户',['客户简称','客户名称','所属客户','客户']),
    'owner_name':('代理师',['代理人','代理师','承办人']),
    'technical_domain':('技术领域',['技术域','技术领域','领域']),
    'received_date':('实际收案日期',['收案日期','收到案件日期','实际收案日期']),
    'draft_due_date':('初稿期限／所限',['初稿期限','所限']),
    'client_review_due_date':('客户送审期限',['客户期限','客户送审期限','送客户审核期限']),
    'filing_due_date':('向局提交期限',['提交期限','交局期限','向局提交期限']),
    'actual_filed_date':('实际交局日期',['实际交局日期','实际提交专利局日期']),
    'filing_confirmed':('交局核实状态',['交局核实状态','实际交局已确认']),
    'stage':('案件状态',['办理阶段','案件状态','当前阶段']),
    'ipr_contact':('立案IPR联系人',['客户IPR','负责IPR','IPR联系人']),
    'application_type':('申请类型',['申请类型']),
    'commission_type':('委托类型',['委托类型']),
    'important':('重要案件',['是否为重要案件','重要案件']),
    'urgent':('急案',['急案','是否急案']),
    'meeting_flag':('三方会议标记',['三方会议']),
    'notes':('备注',['备注','备注信息']),
    'review_exemption':('免审原值',['免审','免审字段']),
}
MEETING_FIELDS = {
    'date':['会议日期','三方日期','会议时间'],
    'iprs':['参会IPR','参会ipr'],
    'agents':['参会代理师','参会代理人'],
    'inventors':['参会发明人','发明人'],
    'content':['会议内容','会议纪要'],
    'pending':['待补材料','待补充材料'],
    'changes':['修改意见','审核意见'],
    'existing_art':['现有技术及缺陷','现有技术'],
    'closest_art':['最接近现有技术及缺陷','最接近现有技术'],
    'distinguishing_features':['区别技术特征及解决的问题','区别技术特征'],
    'comparison':['对比分析'],
    'primary_points':['主要发明点'],
    'secondary_points':['次要发明点'],
}
TAG_RULES = {
    '缓存隔离':['跨请求缓存隔离','缓存身份校验','隔离缓存键','隔离不同会话缓存','跨请求污染','会话缓存隔离','会话隔离'],
    '向量近邻检索':['向量数据库','向量检索','语义向量','近邻索引','近似最近邻','HNSW','IVF','向量量化'],
    '检索增强生成':['RAG','检索增强','检索结果生成','证据上下文'],
    '自适应检索':['自适应检索','查询难度','动态检索路由','按问题难度','召回阈值'],
    '模型蒸馏':['知识蒸馏','教师模型','学生模型','软标签'],
    '联邦学习':['联邦学习','本地训练','聚合参数','模型更新聚合'],
    '隐私保护':['差分隐私','隐私预算','加噪','安全聚合'],
    '多模态':['多模态','图文对齐','跨模态'],
    '时序预测':['时序预测','时间序列','趋势预测'],
    '无线资源调度':['无线资源','频谱分配','调度资源块','基站调度'],
    '信道估计':['信道估计','导频','信道状态'],
    '光通信':['光模块','光通信','光纤链路','波分复用'],
    '光学检测':['光学检测','干涉测量','光谱检测'],
    '机械连接与减振':['机械连接','卡扣连接','减振结构','阻尼支撑'],
    '电池热管理':['电池热管理','冷却回路','电芯温差','冷板流道'],
    '终端任务调度':['终端任务调度','后台任务队列','前后台切换','应用唤醒'],
}
CASE_RE = re.compile(r'(?<![A-Za-z0-9])(?:S\d{2}[A-Z]\d{6}|(?:AI|H-AI|DEMO)-\d{4}-\d{3})(?![A-Za-z0-9])',re.I)


def normalize_label(value):
    return re.sub(r'[\s：:（）()\[\]*]', '', str(value or '')).lower()


def field_lookup(mapping=None):
    result = {}
    for field,(_,aliases) in FIELDS.items():
        for name in [field,*aliases,*(mapping or {}).get(field,[])]:
            label = normalize_label(name)
            if label in result and result[label] != field:
                raise ValueError('字段别名重复，请核对映射：'+name)
            result[label] = field
    return result


def normalize_value(field, value):
    if value is None or isinstance(value,float) and math.isnan(value) or str(value).strip() in ('','—','--','未知','待确认','待核实','未登记'):
        return None
    if field.endswith('_date'):
        if isinstance(value,(date,datetime)):
            return value.date().isoformat() if isinstance(value,datetime) else value.isoformat()
        text = str(value).strip()
        for pattern in ('%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%Y年%m月%d日','%Y-%m-%d %H:%M:%S'):
            try:return datetime.strptime(text,pattern).date().isoformat()
            except ValueError:pass
        raise ValueError('日期格式无法确定：'+text[:40])
    if field == 'filing_confirmed':
        return value is True or str(value).strip() in ('是','已确认','已核实','true','1')
    if field == 'case_id':
        return str(value).strip().upper()
    if field == 'technical_domain':
        return canonical_domain(value)[:8000]
    return str(value).strip()[:8000]


def people(value):
    if isinstance(value,list):
        return list(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))[:30]
    return list(dict.fromkeys(x.strip() for x in re.split(r'[、,，;；/\n]',str(value or '')) if x.strip()))[:30]


def tags(text):
    # Conservative assertion extraction: negative and uncertain clauses cannot establish experience.
    clauses = re.split(r'[。！？；;\n]', text.lower())
    positive = []
    for clause in clauses:
        for part in re.split(r'但(?:是)?|而是|不过',clause):
            if re.search(r'不(?:包含|涉及|采用|使用|支持|具备|含有|是)|无需|并非|没有|未(?:采用|使用|涉及|实现)|待核实|待确认|尚不确定|是否|能否|计划采用|拟采用',part):
                continue
            positive.append(part)
    found=[tag for tag,aliases in TAG_RULES.items() if any(
        (re.search(r'(?<![a-z])'+re.escape(alias.lower())+r'(?![a-z])',part) if alias.isascii() else alias.lower() in part)
        for part in positive for alias in [tag,*aliases])]
    # Require both an optical carrier and a transport mechanism in a positive
    # clause; either “波长” or “光纤” alone cannot establish optical transport.
    if '光通信' not in found and any('波长' in p and ('光纤' in p or '光路' in p)
            and any(w in p for w in ('传输','传送','复用')) for p in positive):
        found.append('光通信')
    return found


def query_tags(text):
    """A question expresses a search target, never a source assertion.

    Keep tags() conservative for documents: a disclosure asking whether a
    mechanism exists must not establish positive historical experience.
    """
    target = re.sub(r'有没有|是否有|是否(?:采用|使用)|能否找到', '查找', text)
    found=tags(target)
    # Compositional query vocabulary: require source/target/training relations,
    # not a complete memorized query string. This expands search concepts only;
    # document assertions still use the conservative literal tags() extractor.
    for clause in re.split(r'[。；;\n]',target):
        if re.search(r'不(?:采用|涉及|使用)|并非|没有|排除|无需',clause):continue
        small=bool(re.search(r'(?:小|学生|轻量|紧凑).{0,3}(?:模型|网络|预测器|分类器)',clause))
        teacher=bool(re.search(r'(?:大|教师|高容量).{0,3}(?:模型|网络|分类器)',clause))
        soft=bool(re.search(r'软(?:目标|标签|概率|分布|监督)',clause))
        if small and (teacher or soft) and re.search(r'概率|分布|软目标|软标签',clause) and re.search(r'学|训练|迁移|监督|拟合',clause):
            found.append('模型蒸馏')
        if re.search(r'多层|分层',clause) and re.search(r'邻接|邻居|近邻|图',clause) and re.search(r'失效|停用|删除',clause) and re.search(r'延迟|清理|回收|整理',clause):
            found.append('向量近邻检索')
        if re.search(r'弹性|柔性|可拆',clause) and re.search(r'锁爪|卡爪|锁扣',clause) and re.search(r'减振|吸振|阻尼',clause):
            found.append('机械连接与减振')
    return list(dict.fromkeys(found))


def classify(name):
    name=Path(name).name
    if Path(name).suffix.lower() in ('.vsd','.vsdx'):return 'drawing'
    if re.search('三方|会议|沟通',name):return 'meeting'
    if re.search('立案|案件信息|台账',name):return 'intake'
    if re.search('交底',name):return 'disclosure'
    if re.search('申请文件|说明书|权利要求|申请稿',name):return 'application'
    if re.search('回执|交局确认|提交确认',name):return 'receipt'
    if re.search('缴费|费用',name):return 'fee'
    if re.search('附图|绘图',name):return 'drawing'
    if Path(name).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}:return 'drawing'
    return 'other'


def parse(raw: bytes, relative_path: str, mapping=None):
    suffix = Path(relative_path).suffix.lower()
    path_ids = set(m.group().upper() for m in CASE_RE.finditer(relative_path))
    if len(path_ids)>1:
        raise ValueError('路径包含多个不同所编号，需人工关联，未猜测所属案件')
    path_case = next(iter(path_ids),None)
    claims, meetings, units, warnings = [],[],[],[]
    parse_locations = []
    lookup = field_lookup(mapping)
    meeting_lookup = {normalize_label(a):field for field,aliases in MEETING_FIELDS.items() for a in [field,*aliases]}

    def claim(field,value,location,case_id=None):
        try:value = normalize_value(field,value)
        except ValueError as exc:
            warnings.append({'field':field,'location':location,'reason':str(exc)});return
        if value is not None:
            claims.append({'field':field,'value':value,'location':location,'case_id':case_id})

    def meeting(record,location,case_id=None):
        if not any(record.get(field) for field in ('iprs','agents','content')):
            return
        item = {field:record.get(field) or '' for field in MEETING_FIELDS}
        for field in ('iprs','agents','inventors'):item[field] = people(item[field])
        try:item['date'] = normalize_value('meeting_date',item['date'])
        except ValueError:
            item['date'] = None;warnings.append({'location':location,'reason':'会议日期无法确认'})
        item.update(location=location,case_id=case_id)
        meetings.append(item)

    def table(rows,location):
        if not rows:return
        # Two-column forms are common in案件信息 and meeting workbooks.
        # Only accept an explicit label/value form; never reinterpret a wide table.
        populated=[(i,row) for i,row in enumerate(rows,1) if any(v is not None and str(v).strip() for v in row)]
        known=set(lookup)|set(meeting_lookup)
        form=[(i,row) for i,row in populated if len(row)==2 and normalize_label(row[0]) in known
              and normalize_label(row[1]) not in known]
        if len(form)>=2 and len(form)>=.6*len(populated) and all(len(row)==2 for _,row in populated):
            row_case=next((str(row[1]).strip().upper() for _,row in form
                           if lookup.get(normalize_label(row[0]))=='case_id' and row[1]),None)
            if row_case and path_case and row_case!=path_case:
                raise ValueError('表内所编号与案件目录不一致，未跨案合并')
            event=any(meeting_lookup.get(normalize_label(row[0]))=='date' for _,row in form)
            record={};record_at=location
            for i,row in form:
                key=normalize_label(row[0]);at=f'{location} · 第{i}行 · 第2列'
                if key in lookup:claim(lookup[key],row[1],at,row_case)
                field=meeting_lookup.get(key)
                if event and field:
                    if field=='date' and record:
                        meeting(record,record_at,row_case);record={}
                    record[field]=row[1];record_at=at
            if record:meeting(record,record_at,row_case)
            return
        scored=[]
        for index,row in enumerate(rows[:20]):
            keys=[normalize_label(v) for v in row]
            case_count=sum(key in lookup for key in keys)
            event_count=sum(key in meeting_lookup for key in keys)
            if max(case_count,event_count) < .6 * max(sum(bool(k) for k in keys),1):
                continue  # A vertical form has labels beside values, not a header row.
            scored.append((max(case_count,event_count),index,case_count,event_count))
        score,header_index,case_count,event_count=max(scored,default=(0,0,0,0))
        if score<2:return
        headers=[normalize_label(v) for v in rows[header_index]]
        event = event_count>=2 and any(meeting_lookup.get(k)=='date' for k in headers)
        for offset,row in enumerate(rows[header_index+1:],header_index+2):
            if not any(v is not None and str(v).strip() for v in row):continue
            row_case = next((str(v).strip().upper() for k,v in zip(headers,row) if lookup.get(k)=='case_id' and v),None)
            if row_case and path_case and row_case != path_case:
                raise ValueError('表内所编号与案件目录不一致，未跨案合并')
            at = f'{location} · 第{offset}行'
            if event:
                record={meeting_lookup[k]:v for k,v in zip(headers,row) if k in meeting_lookup}
                meeting(record,at,row_case)
            else:
                for column,(k,value) in enumerate(zip(headers,row),1):
                    if k in lookup:claim(lookup[k],value,f'{at} · 第{column}列',row_case)

    with tempfile.TemporaryDirectory(prefix='casework-parse-') as temporary:
        target=Path(temporary)/('document'+suffix);target.write_bytes(raw)
        actual_format=document_format(target)
        if actual_format in ('.xlsx','.xls','.csv'):
            if actual_format=='.xlsx':
                from openpyxl import load_workbook
                workbook=load_workbook(target,read_only=True,data_only=True)
                try:
                    for sheet in workbook:
                        if sheet.max_row is None or sheet.max_column is None:
                            # Valid XLSX writers may omit the optional dimension element.
                            sheet.calculate_dimension(force=True)
                        if sheet.max_row>20000 or sheet.max_column>200:
                            raise ValueError('工作表超过20000行或200列解析上限')
                        rows=list(sheet.iter_rows(values_only=True));table(rows,'工作表 '+sheet.title)
                        text='\n'.join(' | '.join('' if value is None else str(value) for value in row) for row in rows)
                        units.append((text,'工作表 '+sheet.title))
                finally:workbook.close()
            elif actual_format=='.xls':
                import pandas as pd
                for name,frame in pd.read_excel(target,sheet_name=None,header=None).items():
                    if frame.shape[0]>20000 or frame.shape[1]>200:raise ValueError('工作表超过解析上限')
                    rows=frame.astype(object).where(frame.notna(),None).values.tolist();table(rows,'工作表 '+str(name))
                    units.append((frame.to_csv(index=False,header=False),'工作表 '+str(name)))
            else:
                try:text=raw.decode('utf-8-sig')
                except UnicodeDecodeError:text=raw.decode('gb18030')
                rows=list(csv.reader(io.StringIO(text)))
                if len(rows)>20000:raise ValueError('CSV超过20000行解析上限')
                table(rows,'CSV');units.append((text,'CSV'))
        elif actual_format=='.json':
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('案件JSON必须是对象')
            declared=data.get('case_id')
            if declared and path_case and str(declared).upper()!=path_case:raise ValueError('JSON案号与目录案号不一致')
            for field,value in (data.get('case_fields') or {}).items():
                if field in FIELDS:claim(field,value,'JSON /case_fields/'+field,declared)
            for index,record in enumerate(data.get('meetings') or []):meeting(record,f'JSON /meetings/{index}',declared)
            if declared:claim('case_id',declared,'JSON /case_id',declared)
            units.append((json.dumps(data,ensure_ascii=False,indent=2),'JSON'))
        else:
            parsed=parse_document(target,relative_path)
            if any(unit.metadata.get('parser') == 'mineru' for unit in parsed):
                warnings.append({'location':'OCR解析','reason':'OCR内容用于正文召回，未自动写入案件信息或会议事实；请核对原件后确认。'})
            if actual_format=='.vsdx':
                from tools.visio_parser import SCOPE
                warnings.append({'location':'Visio绘图','reason':SCOPE if parsed else '没有可提取的图中文字；仅按所编号关联附件，未识别图形含义。'})
            elif not parsed:raise ValueError('未提取到正文；请检查MinerU本机解析配置或人工补录，未视为解析成功')
            for unit in parsed:
                parse_locations.append(unit.metadata)
                text=unit.page_content;at=location_label(unit.metadata);units.append((text,at))
                if actual_format=='.vsdx' or unit.metadata.get('parser') == 'mineru':
                    continue  # OCR/diagram labels remain retrieval evidence, never overwrite ledger facts.
                if unit.metadata.get('content_type')=='table':
                    table([[v.strip() for v in line.split('|')] for line in text.splitlines()],at)
                for line in text.splitlines():
                    cells=[v.strip() for v in line.split('|')]
                    for index,cell in enumerate(cells):
                        pair=re.match(r'^\s*([^：:]{1,40})[：:]\s*(.*)$',cell)
                        label=normalize_label(pair[1] if pair else cell)
                        value=pair[2] if pair else (cells[index+1] if index+1<len(cells) else '')
                        if label in lookup and value and normalize_label(value) not in lookup:
                            claim(lookup[label],value,at)
            # A vertical meeting record may repeat dates in one document.
            record={};record_at=''
            for text,at in units:
                if actual_format=='.vsdx' or any(m.get('parser') == 'mineru' for m in parse_locations):continue
                if not re.search('会议|三方',relative_path):continue
                for line in text.splitlines():
                    match=re.match(r'^\s*([^：:|]{1,40})[：:]\s*(.*)$',line)
                    if not match:continue
                    field=meeting_lookup.get(normalize_label(match[1]))
                    if not field:continue
                    if field=='date' and record:
                        meeting(record,record_at);record={}
                    record[field]=match[2];record_at=at
            if record:meeting(record,record_at)

    declared_ids={str(c['value']).upper() for c in claims if c['field']=='case_id'}
    if len(declared_ids)>1 or path_case and declared_ids and declared_ids!={path_case}:
        raise ValueError('文件内案号不唯一或与路径不符，需人工核对')
    case_id=path_case or next(iter(declared_ids),None)
    if not case_id:raise ValueError('未找到可靠的所编号，文档保持待关联，不猜测所属案件')
    for item in claims+meetings:item['case_id']=case_id
    if not any(c['field']=='case_id' for c in claims):
        claims.append({'field':'case_id','value':case_id,'location':'案件目录命名','case_id':case_id})
    if sum(len(text) for text,_ in units)>2_000_000:raise ValueError('解析正文超过200万字符上限，需拆分处理')
    if len(claims)>10000 or len(meetings)>2000:raise ValueError('字段或会议记录超过单文件上限，请拆分处理')
    from casework.chunking import split_units, config as chunk_config
    chunks=split_units(units,actual_format)
    for chunk in chunks:chunk['tags']=tags(chunk['text'])
    focus=None
    if actual_format=='.pdf' and units and 'SYN-AI-' in units[0][0]:
        text,at=units[0]
        heading=re.search(r'(?m)^摘要\s*$',text)
        begin=heading.start() if heading else -1
        end=text.find('技术领域与具体问题',heading.end()) if heading else -1
        if begin>=0 and end>begin:
            focus={'text':text[begin:end],'location':at,'start':begin,'end':end}
            for chunk in chunks:
                chunk['retrieval_role']='focus' if any(s['location']==at and s['start']<end and s['end']>begin
                    for s in chunk.get('spans',[])) else 'context'
    return {'case_id':case_id,'kind':classify(relative_path),'format':actual_format,'claims':claims,
            'meetings':meetings,'chunks':chunks,'warnings':warnings,'parser_version':VERSION,'chunking':chunk_config(),
            'retrieval_focus':focus,'document_locations':parse_locations}

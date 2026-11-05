"""Author an independent synthetic shared drive; never reads private user files."""
from __future__ import annotations

import argparse
import json
from datetime import date,timedelta
from pathlib import Path
from casework.domains import canonical

ROOT=Path(__file__).resolve().parent.parent
PACK=ROOT/'examples/department_shared_drive'
PEOPLE=[('林澄（模拟）',['人工智能']),('周行（模拟）',['人工智能']),('许宁（模拟）',['人工智能','无线通信']),('沈知（模拟）',['无线通信']),('陈序（模拟）',['光通信']),
        ('陆衡（模拟）',['机械结构']),('顾远（模拟）',['汽车技术']),('程简（模拟）',['终端软件'])]


def records():
    rows=[]
    historical=[
        ('DEMO-2025-001','一种信息处理方法','林澄（模拟）','AI','vector','2025-08-05','2025-10-15'),
        ('DEMO-2025-002','一种数据索引装置','周行（模拟）','AI','vector','2025-09-03','2025-12-09'),
        ('DEMO-2025-003','一种信息访问系统','周行（模拟）','AI','rag','2025-11-04','2026-01-20'),
        ('DEMO-2025-004','一种分布式训练方法','许宁（模拟）','AI','federated','2025-11-11','2026-02-13'),
    ]
    for ident,title,owner,domain,mechanism,received,filed in historical:
        rows.append({'case_id':ident,'title':title,'owner_name':owner,'technical_domain':domain,'mechanism':mechanism,
                     'received_date':received,'actual_filed_date':filed,'stage':'已交局'})
    mechanisms=['vector','rag','distill','federated','wireless','optical']
    for number in range(1,25):
        month=5+(number-1)//4
        mechanism=mechanisms[(number-1)%6]
        domain='无线' if mechanism=='wireless' else ('光' if mechanism=='optical' else 'AI')
        owner=(PEOPLE[(number-1)%3][0] if domain=='AI' else PEOPLE[3 if domain=='无线' else 4][0])
        filed=None;stage='撰写中'
        if number<=12:
            stage='已交局'
            filed=date(2026,month+1,min(26,number+5)).isoformat() if number!=6 else None
        elif number in (14,18):stage='等待补充'
        elif number==17:stage='已暂停'
        elif number==23:stage='待客户确认'
        if number in (15,16):stage='已交局';filed='2026-09-18' if number==15 else '2026-10-04'
        received=date(2026,month,3+(number%4)*5).isoformat() if number!=22 else None
        rows.append({'case_id':f'DEMO-2026-{number:03d}','title':f'一种{["信息处理方法","数据处理装置","任务处理系统","资源处理方法"][number%4]}',
                     'owner_name':owner,'technical_domain':domain,'mechanism':mechanism,'received_date':received,
                     'actual_filed_date':filed,'stage':stage})
    for index,(domain,mechanism,title,owner) in enumerate([
        ('机械结构','mechanical','一种可拆卸支撑组件', '陆衡（模拟）'),
        ('汽车技术','automotive','一种车辆电池冷却装置', '顾远（模拟）'),
        ('终端软件','terminal','一种终端应用任务处理方法', '程简（模拟）'),
    ]):
        for active in (False,True):
            number=25+index+(3 if active else 0)
            rows.append({'case_id':f'DEMO-2026-{number:03d}','title':title,'owner_name':owner,'technical_domain':domain,'mechanism':mechanism,
                         'received_date':f'2026-{8 if active else 7:02d}-{10+index:02d}',
                         'actual_filed_date':None if active else f'2026-08-{10+index:02d}','stage':'撰写中' if active else '已交局'})
    for index,row in enumerate(rows):
        row['technical_domain']=canonical(row['technical_domain'])
        row.update(client_alias=['客户甲（模拟）','客户乙（模拟）','客户丙（模拟）'][index%3],
                   client_case_id=f'DEMO-CLIENT-{index+1:03d}',application_type='发明',commission_type='新申请',
                   draft_due_date='2026-10-24' if index%4==0 else '2026-10-30',
                   client_review_due_date='2026-10-25' if index%3==0 else '2026-11-02',
                   filing_due_date='2026-11-10' if index%4 else '2026-11-06',
                   ipr_contact='IPR林（模拟）' if index%3<2 else 'IPR盛（模拟）',
                   important='是' if index%5==0 else '否',urgent='是' if index%7==0 else '否',
                   notes='独立编写的模拟办理记录，不对应任何真实委托或客户',filing_confirmed=bool(row['actual_filed_date']))
        if row['case_id']=='DEMO-2026-021':row['owner_name']=None
    return rows


TEXTS={
 'vector':'''将文档转为高维语义向量，建立分层近邻索引HNSW。查询向量先在高层导航，再在底层进行候选扩展，对候选以距离重排。索引增量写入后对删除条目设置墓碑，定期合并清理。讨论点是向量维度、距离函数、近邻数量和索引更新条件，不能只靠文件名匹配。''',
 'rag':'''本记录讨论检索增强生成RAG。按问题难度选择单次召回或沿文档引用继续检索；查询难度由实体、依赖缺口和首次召回结果共同判断。证据上下文不足时停止生成确定性结论并请求补充。需要分别说明动态检索路由、召回阈值、停止条件和证据引用。''',
 'distill':'''以教师模型的软标签监督学生模型，联合真实标签与蒸馏损失训练。需要客户补充温度参数、损失权重、训练样本及推理部署目标。该模型蒸馏机制不涉及向量近邻索引，不能因为同属AI就当作相同技术方案。''',
 'federated':'''各参与节点在本地训练，将模型更新提交聚合节点。采用安全聚合保护单节点更新，以差分隐私预算控制加噪。发明人仍需明确参与方、上传内容、聚合轮次和异常退出。不能将未记录的隐私保证或实验效果写成本案事实。''',
 'wireless':'''基站依据队列长度和信道状态分配无线资源块。利用导频进行信道估计，在时隙级调度时兼顾重传与吞吐。此案关注无线资源调度，不能以AI分类标签代替实际机制比对。''',
 'optical':'''通过波分复用承载多路光纤链路信号，光模块依据接收功率切换均衡参数。需要区分光通信链路的控制对象、测量时序和反馈通道。本案不包含向量检索或教师学生模型。''',
 'mechanical':'''支撑组件通过卡扣连接实现拆装，在接触面设置减振结构和阻尼支撑。需要说明锁止方向、卡扣形变空间、材料与装配间隙，以及载荷如何沿连接件传递。现有机械连接在重复拆装后可能松脱，本记录仅讨论结构关系，不包含模型训练。''',
 'automotive':'''车辆的电池热管理系统通过冷板流道连接多个电芯，温度传感器检测电芯温差，控制阀调整冷却回路的流量分配。需要区分电芯温差阈值、回路连接关系与阀门控制条件，不能把未验证的续航改善写作既有实验结果。''',
 'terminal':'''终端任务调度根据前后台切换和应用唤醒事件维护后台任务队列。电量与网络状态决定任务执行窗口，超时任务写入可恢复队列，以减少应用重复唤醒。需明确队列状态迁移与重试去重规则；本记录不涉及向量数据库索引。''',
}


def build(force=False):
    source=PACK/'source'
    if source.exists() and any(source.iterdir()) and not force:
        raise ValueError('模拟共享盘已存在；不覆盖，请明确传--force，或直接使用现有资料')
    source.mkdir(parents=True,exist_ok=True)
    (source/'.casework-demo.json').write_text(json.dumps({'kind':'independently_authored_synthetic','authored':'2026-10-26','private_sources_read':False},ensure_ascii=False))
    from openpyxl import Workbook
    from docx import Document
    cases=records();meetings=[]
    headers=['序号','我方案号','客户案号','客户简称','代理人','发明名称','初稿期限','提交期限','收案日期','技术域','申请类型','委托类型','是否为重要案件','急案','三方会议','备注','免审']
    keys=[None,'case_id','client_case_id','client_alias','owner_name','title','draft_due_date','filing_due_date','received_date','technical_domain','application_type','commission_type','important','urgent',None,'notes',None]
    for index,case in enumerate(cases):
        year=case['case_id'].split('-')[1];folder=source/year/(case['case_id']+'.'+case['client_case_id']);folder.mkdir(parents=True,exist_ok=True)
        workbook=Workbook();sheet=workbook.active;sheet.title='案件信息';sheet.append(headers)
        row=[]
        for key in keys:
            value=case.get(key) if key else None
            if key and key.endswith('_date') and value:value=date.fromisoformat(value)
            row.append(value)
        row[0]='=ROW()-1';row[14]='已安排' if index%4==0 else '未安排';row[16]='未确认含义的原始值'
        sheet.append(row)
        for cell in sheet[1]:cell.font=__import__('openpyxl').styles.Font(bold=True)
        for position in (7,8,9):sheet.cell(2,position).number_format='yyyy-mm-dd'
        workbook.save(folder/(case['case_id']+'.案件信息.xlsx'))
        doc=Document();doc.add_paragraph('独立编写的模拟立案资料；没有引用用户真实内容。')
        table=doc.add_table(rows=0,cols=2)
        labels=[('case_id','我方案号'),('client_case_id','客户案号'),('title','发明名称'),('client_alias','客户简称'),('owner_name','代理人'),
                ('technical_domain','领域'),('stage','办理阶段'),('received_date','收案日期'),('draft_due_date','初稿期限'),('client_review_due_date','客户期限'),('filing_due_date','提交期限'),('ipr_contact','客户IPR')]
        for key,label in labels:
            cells=table.add_row().cells;cells[0].text=label
            value=case.get(key)
            if case['case_id']=='DEMO-2026-024' and key=='draft_due_date':value='2026-10-28'
            cells[1].text=str(value or '')
        doc.save(folder/(case['case_id']+'.立案.doc'))  # OOXML under a legacy extension, as in the observed structure.
        content='\n'.join(['# 模拟技术材料', '资料性质：独立编写的模拟内部资料。','所编号：'+case['case_id'],'发明名称：'+case['title'],
                           '## 技术方案',TEXTS[case['mechanism']], '## 业务边界','历史表述仅作理解参考，不能证明新案已实现对应条件。'])
        name='申请文件' if case['stage']=='已交局' else '技术交底'
        (folder/(case['case_id']+'.'+name+'.md')).write_text(content)
        if case['actual_filed_date']:
            data={'case_id':case['case_id'],'case_fields':{'actual_filed_date':case['actual_filed_date'],'filing_confirmed':True},
                  'nature':'模拟交局确认记录，不是真实专利局回执'}
            (folder/(case['case_id']+'.交局确认.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
        if case['case_id'] in ('DEMO-2025-001','DEMO-2025-002','DEMO-2026-001','DEMO-2026-013'):
            meeting_book=Workbook();ms=meeting_book.active;ms.title='三方案件记录'
            cols=['会议日期','参会IPR','参会代理师','发明人','会议内容','待补材料','修改意见','现有技术及缺陷','最接近现有技术及缺陷','区别技术特征及解决的问题','对比分析','主要发明点','次要发明点']
            ms.append(cols)
            for n in range(2 if case['case_id']=='DEMO-2025-001' else 1):
                ipr='IPR林（模拟）' if case['client_alias']!='客户丙（模拟）' else 'IPR盛（模拟）'
                agents=['周行（模拟）','许宁（模拟）'] if case['case_id']=='DEMO-2025-001' else [case['owner_name']]
                month='2025-09' if case['case_id']=='DEMO-2025-001' else ('2025-11' if case['case_id']=='DEMO-2025-002' else ('2026-05' if case['case_id']=='DEMO-2026-001' else '2026-09'))
                day=month+('-12' if n==0 else '-19')
                values=[date.fromisoformat(day), '、'.join([ipr,'IPR顾（模拟）'] if n else [ipr]),'、'.join(agents),'发明人甲（模拟）',
                        '核对输入输出、索引更新和检索停止条件' if not n else '讨论补充材料后的区别技术特征',
                        '补充近邻数量和索引更新触发条件' if not n else '提供对照流程图',
                        '本案需把索引更新与查询步骤分开说明' if not n else '本案独立权利要求避免把未验证效果写成必要条件',
                        '已有实现只按标题检索，正文机制难定位','最接近方案使用固定阈值','按明确缺口调整检索策略，减少无据生成',
                        '区分本案事实与历史参考','缺口驱动的检索停止','可回查的来源记录']
                ms.append(values)
                meetings.append({'case_id':case['case_id'],'client':case['client_alias'],'date':day,'iprs':([ipr,'IPR顾（模拟）'] if n else [ipr]),'agents':agents})
            meeting_book.save(folder/(case['case_id']+'.三方案件记录表.xlsx'))
    # A byte-identical duplicate document must not duplicate its case or meeting.
    duplicate=source/'2025'/('DEMO-2025-001.'+cases[0]['client_case_id'])
    raw=(duplicate/'DEMO-2025-001.申请文件.md').read_bytes();(duplicate/'DEMO-2025-001.申请文件.备份.md').write_bytes(raw)
    gold={'kind':'independently_authored_synthetic','reference_date':'2026-10-26','cases':cases,'meetings':meetings,'people':[{'name':name,'domains':domains} for name,domains in PEOPLE],
          'known_conflict':{'case_id':'DEMO-2026-024','field':'draft_due_date','values':['2026-10-28','2026-10-30']},
          'known_unknown_completion':'DEMO-2026-006','known_unknown_received':'DEMO-2026-022','known_unassigned':'DEMO-2026-021',
          'vector_cases':['DEMO-2025-001','DEMO-2025-002','DEMO-2026-001','DEMO-2026-007','DEMO-2026-013','DEMO-2026-019'],
          'negative_meeting_owner':{'case_id':'DEMO-2025-001','owner_not_attendee':'林澄（模拟）'}}
    (PACK/'expected.json').write_text(json.dumps(gold,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'source':str(source),'cases':len(cases),'meetings':len(meetings),'private_files_read':False},ensure_ascii=False))


if __name__=='__main__':
    argument=argparse.ArgumentParser();argument.add_argument('--force',action='store_true');args=argument.parse_args();build(args.force)

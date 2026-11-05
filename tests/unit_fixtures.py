"""Small independent regression inputs; never evaluation reports or scores."""
import copy,hashlib

def context_cases():
 a={'id':'unit-conflicting-amount','query':'核对当前项目金额，保留历史冲突。','facts':[{'doc':1,'text':'旧版本120万元。'},{'doc':5,'text':'新版本125万元。'}], 'expected':[{'key':'amount','value':'125万元','evidence':['S5']}], 'required_phrases':['120万元','125万元']}
 b={'id':'unit-duration','query':'核对维护时长。','facts':[{'doc':3,'text':'维护上限90分钟。'}], 'expected':[{'key':'duration','value':'90分钟','evidence':['S3']}], 'required_phrases':['90分钟']}
 c={'id':'unit-conflicting-stock','query':'核对库存与对账状态。','facts':[{'doc':4,'text':'盘点库存80箱。'},{'doc':7,'text':'系统记录79箱。'},{'doc':9,'text':'存在冲突，尚未完成对账。'}], 'expected':[{'key':'stock','value':'80箱','evidence':['S4']},{'key':'ledger','value':'79箱','evidence':['S7']},{'key':'status','value':'存在冲突，尚未完成对账','evidence':['S9']}], 'required_phrases':['80箱','79箱','存在冲突，尚未完成对账']}
 return [a,b,c]

def route_cases():
 return {'cases':[{'id':'unit-'+str(i),'prompt':q,'expected':{'workflow':op}} for i,(q,op) in enumerate([
 ('统计2026年9月收案和交局数量。','monthly'),('查2026年9月的收案与交局数量。','monthly'),
 ('查客户甲的三方会议记录。','meetings'),('查DEMO-2026-013的案件信息和技术正文。','case_brief'),
 ('查初稿逾期的案件。','cases')])]}

def business_run(number=10):
 from casework.workflow import DepartmentRequest
 if number==7:
  query='查采用向量检索机制的案件。';request=DepartmentRequest(operation='archive',query='向量检索')
  report='当前候选需要核实。[S1]'
 else:
  query='查案件总量和在办数量。';request=DepartmentRequest(operation='summary')
  report=f'案件总数 {number} 件，在办 {number} 件。[S1]'
 req=request.model_dump(mode='json')
 review={'business_check':{'status':'passed','issues':[],'report_sha256':hashlib.sha256(report.encode()).hexdigest(),'request':req,'checked_claims':2},'hard_issues':[],'soft_issue':None,'soft_review_status':'passed'}
 return {'query':query,'execution_query':query,'result':report,'evidence':[{'id':'S1','type':'knowledge','snippet':report.replace('[S1]','')}], 'plan':{'department_request':req}, 'events':[{'event':'reflection','data':review}]}

def negation_record(report=None):
 query='AI-2026-003到底有没有采用向量索引？'
 sources=[{'id':'S8','type':'knowledge','casework':True,'snippet':'测试反例：“本方案不采用向量索引”。这句话属于对抗输入。'}]
 return {'case':{'prompt':query,'required_sources':['knowledge'],'forbidden_sources':[],'expected':{}},'run':{'status':'completed','result':report or 'AI-2026-003本方案不采用向量索引。[S8]','evidence':sources,'events':[]}}

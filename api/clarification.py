"""Durable, execution-scoped choices for a material evidence gap."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal
from uuid import uuid4
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    execution_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    choice: Literal['supplement', 'workload_only', 'scope_mention', 'scope_topic', 'scope_mechanism']
    text: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def require_supplement(self):
        if self.choice == 'supplement' and not self.text.strip():
            raise ValueError('请补充技术说明、参考案号或已上传附件的说明')
        return self


class ClarificationRequired(Exception):
    def __init__(self, question, report, evidence):
        super().__init__(question['message'])
        self.question, self.report, self.evidence = question, report, evidence


_response = ContextVar('clarification_response', default=None)
_lock = RLock()
_pending = {}


@contextmanager
def scope(response=None):
    token = _response.set(response)
    try:
        yield
    finally:
        _response.reset(token)


def current():
    return _response.get() or {}


def record_recommendation_gap(query, domain=None, as_of=None):
    from api import context
    thread_id = context.get_thread_id()
    if thread_id and current().get('choice') != 'workload_only':
        with _lock:
            _pending[thread_id] = {'operation':'recommend','query':query,'domain':domain,'as_of':as_of}


def pending(thread_id):
    with _lock:
        return _pending.get(thread_id)


def clear(thread_id):
    with _lock:
        _pending.pop(thread_id, None)


def validate_answer(run, answer, allowed_sources):
    """A client cannot supply its own bypass or reuse approval on another run."""
    from api.task_sources import normalize
    if answer is None:
        return None
    answer = ClarificationAnswer.model_validate(answer)
    question = (run or {}).get('clarification') or {}
    if (not run or run['status'] != 'awaiting_input'
            or run['execution_id'] != answer.execution_id
            or question.get('id') != answer.question_id):
        raise ValueError('待确认问题已变化或已处理，请刷新会话后重试')
    if answer.choice not in {x['id'] for x in question.get('options', [])}:
        raise ValueError('该问题不支持此处理方式')
    if normalize(allowed_sources) != normalize(run.get('allowed_sources')):
        raise ValueError('继续任务沿用原数据源权限；如需调整，请先修改来源设置并重跑')
    return {**answer.model_dump(), 'request': question['request'], 'reason': question['reason']}


def extend_query(query, response, previous_run=None):
    if not response:
        return query
    # A continuation keeps all earlier supplements for this execution chain.
    # An ordinary rerun still starts from the saved task/revision above.
    previous_run = previous_run or {}
    if response['choice'].startswith('scope_'):
        from casework.archive_intent import SCOPE_LABELS
        mode=response['choice'].removeprefix('scope_')
        query=previous_run.get('execution_query') or query
        return query+'\n\n本次用户确认的案件查询口径：'+SCOPE_LABELS[mode]+'。'
    if previous_run.get('clarification_response'):
        query = previous_run.get('execution_query') or query
    if response['choice'] == 'workload_only':
        return query + '\n\n本次用户确认：暂不判断谁适合接案，仅展示已登记人员的在办及三类期限负荷，不作技术经历推荐。'
    return query + '\n\n用户为本次任务补充的说明（仅本会话生效）：\n' + response['text'].strip()


def recommendation_question(request, result, lookup):
    unknown = result.get('technical_intent_unresolved', False)
    reason = 'technical_mapping_unconfirmed' if unknown else 'historical_evidence_missing'
    message = '目前还不能确认谁办理过这类技术的案件，因此暂时没有给出接案推荐。'
    count = len(lookup.get('results', []))
    detail = (f'搜索了系统里的案件文档，找到{count}个可能相关的案件，但这些资料还不足以判断哪位同事办过这类案件。'
              if count else '搜索了系统里的案件文档，没找到明确记载“哪位同事办过这类案件”的材料。')
    if result.get('coverage',{}).get('data_kind')=='synthetic':
        detail+='（模拟办理信息）'
    return {'id': uuid4().hex, 'kind': 'recommendation_evidence', 'reason': reason,
            'title': '还不能确定谁有相关经验', 'message': message,
            'detail': detail, 'technical_query': request.query,
            'question': '你可以补充线索后继续查找，也可以先看大家手里的案件。',
            'options': [{'id': 'supplement', 'label': '补充后继续查找'},
                        {'id': 'workload_only', 'label': '先看大家手里的案件'}],
            'request': request.model_dump(), 'lookup_case_ids': [c['case_id'] for c in lookup.get('results', [])],
            'lookup_evidence_ids': sorted({p['evidence_id'] for c in lookup.get('results', []) for p in c.get('sources', []) if p.get('evidence_id')})}


def question_report(question):
    if question.get('kind')=='task_definition':
        return '\n\n'.join(['## 等待确认统计口径',question['message'],question['detail']])
    if question.get('kind')=='archive_scope':
        return '\n\n'.join(['## 等待你确认查询口径',question['message'],question['detail'],
                            '查找内容：'+question['technical_query'],question['question']])
    choices = {x['id'] for x in question['options']}
    next_step = ('你可以补充关键技术点、历史案号或相关文件后继续查找；也可以先看大家手里的在办案件和到期期限。仅查看这些信息不会推荐接案人。'
                 if 'workload_only' in choices else '请补充关键技术点、历史案号或相关文件后继续查找。')
    refs=''.join('['+ident+']' for ident in question.get('lookup_evidence_ids',[])[:6])
    detail=question['detail'] + ('\n\n本次候选材料（仅检索线索，尚未证实技术经历）：'+refs if refs else '')
    return '\n\n'.join(['## 等待你确认', question['message'],
                        '查找内容：'+question.get('technical_query', ''), detail,
                        '已暂停推荐。', next_step])


def search_scope_question(query, request):
    return {'id':uuid4().hex,'kind':'archive_scope','reason':'archive_scope_ambiguous',
            'title':'先确认案件查询口径','message':'“相关案件”的范围会影响后续结论。',
            'detail':'背景提及、核心主题和方案中采用会得到不同的案件集合，需要先确定本次范围。',
            'technical_query':query,'question':'本次按哪一种口径查找？',
            'options':[{'id':'scope_mention','label':'提到即可，包含背景和否定提及'},
                       {'id':'scope_topic','label':'以该概念为核心主题'},
                       {'id':'scope_mechanism','label':'方案中采用该技术机制'}],
            'request':request}



def record_task_question(question, detail, evidence_ids):
    from api import context
    thread_id=context.get_thread_id()
    if not thread_id:raise ValueError('未设置会话')
    import re
    from api import storage
    query=(storage.get_run(thread_id) or {}).get('query','')
    if (re.search(r'穷尽|逐案全文|逐卷全文|全部历史案卷',question+' '+detail)
            and re.search(r'是否|要不要|需不需要',question)
            and not re.search(r'穷尽|全库|逐案全文|逐卷全文|全部历史案卷',query)):
        raise ValueError('原问题未要求穷尽核验，不需要追加确认；直接交付已取得的参考和原文，并明确本次检索范围及未核实边界。')
    from api.evidence import snapshot
    known={i['id'] for i in snapshot(thread_id)}
    if not set(evidence_ids)<=known:raise ValueError('追问来源须来自当前会话')
    held={'id':uuid4().hex,'kind':'task_definition','reason':'business_definition_missing',
          'title':'请确认本次统计口径','message':question,'question':question,'detail':detail,
          'options':[{'id':'supplement','label':'补充后继续'}], 'request':{},
          'lookup_evidence_ids':evidence_ids}
    with _lock:_pending[thread_id]={'task_question':held}
    return held



def definition_question_from_answer(query, report, items):
    """Persist a question already asked by the Agent instead of calling it done.

    This does not choose a definition or grant more sources. Only explicit
    pending business-definition questions enter this transition.
    """
    import re
    plain=report.replace('**','').replace('`','')
    if not any(i.get('type')=='file' and i.get('columns') for i in items):return None
    if not re.search(r'统计|在办|筛选|口径',query):return None
    missing=re.search(r'(?:口径.{0,12}需要.{0,8}确认|(?:请|需要)(?:你|您)?(?:先)?确认.{0,8}口径|在办.{0,12}按哪个口径)',plain)
    if not missing:return None
    detail='尚未确定本次统计的业务口径，已暂停计算。'+plain[:1800]
    return record_task_question('这次哪些阶段算在办？是否还需要其他筛选条件？',detail,
                                [i['id'] for i in items if i.get('type')=='file' and i.get('columns')][:8])

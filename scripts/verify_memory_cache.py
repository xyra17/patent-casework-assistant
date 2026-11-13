"""Exercise cache mechanics on constructed inputs without model/API calls.

python scripts/verify_memory_cache.py --data-dir /tmp/casework-memory-demo
python scripts/verify_memory_cache.py --data-dir /tmp/casework-memory-demo --serve

The optional server shows saved synthetic turns and their real cache receipts.
This verifies loading and isolation, not model understanding or business accuracy.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
THREAD = 'memory-cache-demo'


def configure(directory):
    directory = directory.expanduser().resolve()
    marker = directory/'.memory-cache-demo.json'
    if directory.exists() and any(directory.iterdir()) and not marker.is_file():
        raise ValueError('目录非空且不属于本工具，请选择新的演示目录')
    directory.mkdir(parents=True,exist_ok=True)
    marker.write_text(json.dumps({'purpose':'constructed-memory-cache-demo'},ensure_ascii=False))
    os.environ.update(TANYUAN_HOME=str(directory), APP_DB_PATH=str(directory/'app.db'),
        CASEWORK_DB_PATH=str(directory/'cases.db'),CASEWORK_ALLOWED_ROOTS='[]',CASEWORK_EMBEDDING='0',
        CASEWORK_MYSQL_MIRROR='0',CASEWORK_SYNC_INTERVAL='0',SEMANTIC_AUTO_INTROSPECT='0',
        SEMANTIC_MODEL_PATH=str(SOURCE/'tests/fixtures/legacy_semantic.yaml'),DATA_MODE='demo',
        MYSQL_HOST='127.0.0.1',MYSQL_PORT='1')
    return directory


def verify(directory, *, replay=False):
    from api import storage, conversation, memory_cache as memory
    storage.init_db()
    target = directory/'checks.json'
    if storage.get_run(THREAD):
        if not target.exists() or not replay:
            raise ValueError('已有演示记录；重新验收请选择新目录，查看保存结果可用 --serve，不自动覆盖')
        print('REPLAY 保存的构造演示；本次未重新执行验收',flush=True)
        return json.loads(target.read_text())
    fixture = {'case_id':'DEMO-2026-001','topic':'星链量子纠错方案','owner':'林澄（模拟）'}
    (directory/'fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2))
    checks, receipts = [], []
    def check(name, passed):
        checks.append({'name':name,'passed':bool(passed)})
        print(('PASS ' if passed else 'FAIL ')+name,flush=True)
    def tier(ident):
        with storage._connect() as conn:
            return conn.execute('SELECT tier FROM conversation_memory WHERE turn_id=?',(ident,)).fetchone()[0]
    def turn(query, answer, load=None):
        state={'goal':query,'conditions':{},'case_ids':None,'unresolved':[],'memory_scope':'conversation'}
        prepared={'text':query,'task':query,'kind':'followup','state':state,'memory_load':load}
        storage.create_run(THREAD,query,query,False,conversation_prepared=prepared,allowed_sources=['db_query'])
        answer+='\n\n> 构造模拟输入 · 程序演示；没有调用模型，不代表业务正确率验收。'
        storage.append_event(THREAD,{'event':'task_result','data':{'result':answer,'degraded':False,
            'evidence':[{'id':'S1','type':'database','title':'内置构造输入 fixture.json',
                         'columns':['所编号','方案','承办人'],'rows':[[fixture['case_id'],fixture['topic'],fixture['owner']]]}]}})
        current=conversation.latest(THREAD)
        if load:receipts.append(memory.inspect(THREAD,current['turn_id'])['last_load'])
        return current
    def plan(query):
        payload, load = memory.build(THREAD,{'user_text':query,'current_task':'核对历史对象，动态事实需重新查询',
            'state':{'conditions':{},'case_ids':None,'unresolved':[]}},['db_query'])
        check('总量与各层均在预算内',load['total_tokens']<=load['budget']['total'] and all(load['tokens'][k]<=load['budget'][k] for k in ('hot','warm','cold')))
        return payload,load
    first=turn('核对星链量子纠错方案对应的案件',
        '方案背景材料。\n'*800+'\n| 所编号 | 方案 |\n| --- | --- |\n| '+fixture['case_id']+' | '+fixture['topic']+' |\n\n历史承办人：'+fixture['owner']+' [S1]')
    original=hashlib.sha256(storage._dumps(first).encode()).hexdigest()
    for i in range(6):turn(f'核对日常事项{i+1}',f'日常事项{i+1}的构造答复。')
    check('早期记录自动进入冷层',tier(first['turn_id'])=='cold')
    payload,load=plan('之前星链量子纠错方案对应哪件案件')
    check('无需指定轮次即可从近期窗口外召回',first['turn_id'] in load['loaded']['cold'])
    turn('之前星链量子纠错方案对应哪件案件','从历史记录定位到 '+fixture['case_id']+'；需要当前办理信息时重新查询。',load)
    _,load=plan('继续核对之前星链量子纠错方案')
    turn('继续核对之前星链量子纠错方案','已定位历史对象；旧记录不作为当前事实。',load)
    check('两次已接受命中后升入热层',tier(first['turn_id'])=='hot')
    payload,load=plan('核对本次查询方式')
    check('已升温的记录在后续追问中预载',first['turn_id'] in load['loaded']['hot'])
    turn('核对本次查询方式','当前目标与范围每次携带，动态信息仍从获准来源取得。',load)
    for i in range(5):turn(f'核对其他进度{i+1}','其他构造记录。')
    check('闲置后从热层降到近期层',tier(first['turn_id'])=='warm')
    for i in range(4):turn(f'核对其他材料{i+1}','其他构造记录。')
    check('继续闲置后降到冷层',tier(first['turn_id'])=='cold')
    _,load=plan('重新定位之前星链量子纠错方案')
    check('冷却后仍可重新按需调入',first['turn_id'] in load['loaded']['cold'])
    turn('重新定位之前星链量子纠错方案','已重新调入保存的原记录；后续状态需要实时查询。',load)
    stored=conversation.get_turn(THREAD,first['turn_id'])
    check('升降级没有改写原回答与证据',hashlib.sha256(storage._dumps(stored).encode()).hexdigest()==original)
    storage.init_db()
    check('重开数据库保留命中与加载记录',memory.inspect(THREAD,conversation.latest(THREAD)['turn_id'])['last_load'] is not None)
    result={'kind':'constructed-cache-mechanics','passed':all(c['passed'] for c in checks),
            'limits':['不调用模型，仅验收加载、预算、升降级与快照保存机制。'],
            'thread_id':THREAD,'checks':checks,'receipts':receipts,'snapshot_sha256':original,
            'code_sha256':{name:hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() for name in (
                'api/conversation.py','api/storage.py','api/memory_cache.py')}}
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


async def verify_resolution(directory):
    """One real structured resolution, not execution or an accuracy benchmark."""
    from api import storage, conversation
    from agent.llm import get_llm
    run=storage.get_run(THREAD)
    body=conversation.Followup(text='之前星链量子纠错方案对应的那个案件，现在由谁负责？请重新查询当前承办人。',
        expected_execution_id=run['execution_id'],expected_turn_id=conversation.latest(THREAD)['turn_id'])
    report={'kind':'single-real-model-followup-resolution','data':'constructed',
            'checks':[],'attempts':[],'limits':['只验证追问解析与缓存对象绑定，不执行案件业务查询，不代表通用准确率。']}
    original_resolve=conversation.resolve
    async def capture(payload):
        value=await original_resolve(payload)
        report['attempts'].append({'proposal':value.model_dump()})
        return value
    conversation.resolve=capture
    try:
        prepared=await conversation.prepare(run,body,allowed_sources=['db_query'])
        report['model']=getattr(get_llm(),'model_name',None)
        report['task']=prepared['task']
        report['state']=prepared['state']
        report['load']=prepared['memory_load']
        report['checks']=[
            {'name':'绑定构造输入中的案号','passed':prepared['state']['case_ids']==['DEMO-2026-001']},
            {'name':'参考已加载的历史轮次','passed':prepared['reference_turn_id'] in prepared['memory_load']['accessed']},
            {'name':'字段查询没有变成人名筛选','passed':'owner' not in prepared['state']['conditions']},
            {'name':'加载总量遵守预算','passed':prepared['memory_load']['total_tokens']<=prepared['memory_load']['budget']['total']}]
        report['passed']=all(c['passed'] for c in report['checks'])
    except Exception as exc:
        report.update(passed=False,error_type=type(exc).__name__,status_code=getattr(exc,'status_code',None))
    finally:
        conversation.resolve=original_resolve
    (directory/'live-resolution.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('REAL RESOLUTION '+('PASS' if report['passed'] else 'FAIL'),flush=True)
    return report['passed']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(tempfile.gettempdir())/'casework-memory-cache-demo')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--live-resolution',action='store_true',help='Use the configured model for one constructed follow-up resolution; may incur API usage')
    parser.add_argument('--port',type=int,default=8770)
    args=parser.parse_args()
    directory=configure(args.data_dir)
    result=verify(directory,replay=args.serve)
    print('REPORT '+str(directory/'checks.json'),flush=True)
    if not result['passed']:return 1
    if args.live_resolution:
        from dotenv import load_dotenv
        load_dotenv(SOURCE/'.env')
        if not asyncio.run(verify_resolution(directory)):return 1
    if args.serve:
        import uvicorn
        from api.server import app
        print(f'DEMO http://127.0.0.1:{args.port} · conversation {THREAD}',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=args.port)
    return 0


if __name__=='__main__':
    sys.exit(main())

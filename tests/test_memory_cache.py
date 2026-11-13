"""Cold recall, bounded injection, heat transitions and immutable provenance."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from api import conversation, memory_cache as memory, storage, recycle_bin
from api.conversation import Followup, Resolution


class MemoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(storage, '_DB_PATH', Path(self.temp.name)/'app.db')
        self.patch.start()
        storage.init_db()
        self.first = self.turn('星链量子纠错方案', '无关背景\n'*2000 + '\n星链量子纠错方案对应 DEMO-2026-001，日期仍需实时查询。 [S1]')
        for i in range(6):
            self.turn(f'日常事项{i}', f'日常记录{i}')

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def turn(self, text, answer, *, thread='cache', kind='followup', sources=None, load=None, state=None):
        state = state or {'goal':text,'conditions':{},'case_ids':None,'unresolved':[],'memory_scope':'conversation'}
        prepared = {'text':text,'task':text,'kind':kind,'state':state,'memory_load':load}
        storage.create_run(thread,text,text,False,conversation_prepared=prepared,
                           allowed_sources=sources if sources is not None else ['db_query'])
        storage.append_event(thread,{'event':'task_result','data':{'result':answer,'degraded':False,
            'evidence':[{'id':'S1','revision':'frozen-v1','type':'database'}]}})
        return conversation.latest(thread)

    def plan(self, text='之前的星链量子纠错方案', **kwargs):
        core={'user_text':text,'current_task':'核对案件信息',
              'state':{'conditions':{'client':'客户甲'},'case_ids':['DEMO-2026-001'],'unresolved':[]}}
        return memory.build('cache',core,kwargs.pop('sources',['db_query']),**kwargs)

    def info(self, ident):
        with storage._connect() as conn:
            return dict(conn.execute('SELECT * FROM conversation_memory WHERE turn_id=?',(ident,)).fetchone())

    def test_old_match_outside_recent_window_is_loaded_from_index_with_focused_excerpt(self):
        payload, report = self.plan()
        old = next(t for t in payload['retrieved_turns'] if t['turn_id']==self.first['turn_id'])
        self.assertIn('星链量子纠错',old['answer_excerpt'])
        self.assertTrue(old['historical'])
        self.assertEqual(old['displayed_case_ids'],['DEMO-2026-001'])
        self.assertNotIn(self.first['turn_id'],[t['turn_id'] for t in payload['recent_turns']])
        self.assertIn('DEMO-2026-001',report['recalled_case_ids'])
        self.assertLess(len(old['answer_excerpt']),len(self.first['snapshot']['result']))

    def test_every_layer_and_entire_serialized_payload_fit_token_budget(self):
        for i in range(4):
            self.turn(f'星链量子纠错细节{i}', ('前文'*500+'星链量子纠错 DEMO-2026-001 '+'后文'*500))
        payload, report = self.plan()
        for layer in ('hot','warm','cold'):
            self.assertLessEqual(report['tokens'][layer],report['budget'][layer])
        self.assertEqual(report['total_tokens'],memory.tokens(payload))
        self.assertLessEqual(memory.tokens(payload),report['budget']['total'])
        self.assertEqual(payload['state']['conditions'],{'client':'客户甲'})
        self.assertEqual(payload['state']['case_ids'],['DEMO-2026-001'])

    def test_mandatory_hot_state_overflow_is_rejected_instead_of_silently_cut(self):
        with self.assertRaisesRegex(ValueError,'关键条件未被截断'):
            self.plan('量子纠错'*5000)
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)

    def test_budget_configuration_is_validated(self):
        with self.assertRaises(ValueError): memory.Budget(hot=0)
        with self.assertRaises(ValueError): memory.Budget(total=500)

    def test_preview_and_automatic_prefetch_never_increment_heat(self):
        for _ in range(3): self.plan()
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)
        _, load = self.plan('没有匹配词')
        self.turn('继续普通任务','普通答复',load=load)
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)

    def test_repeated_accepted_recall_promotes_then_idle_turns_demote(self):
        for _ in range(2):
            _, load = self.plan()
            self.turn('核对前述对象','普通答复',load=load)
        hot = self.info(self.first['turn_id'])
        self.assertEqual(hot['tier'],'hot')
        self.assertEqual(hot['hits'],2)
        payload, _ = self.plan('无关问题')
        self.assertIn(self.first['turn_id'],[t['turn_id'] for t in payload['hot_turns']])
        for i in range(5): self.turn(f'其他进度{i}','其他答复')
        self.assertEqual(self.info(self.first['turn_id'])['tier'],'warm')
        for i in range(4): self.turn(f'其他信息{i}','其他答复')
        self.assertEqual(self.info(self.first['turn_id'])['tier'],'cold')

    def test_heat_and_load_receipt_survive_database_reopen(self):
        _, load = self.plan()
        turn = self.turn('继续核对','普通答复',load=load)
        storage.init_db()
        self.assertEqual(self.info(self.first['turn_id'])['hits'],1)
        receipt = memory.inspect('cache',turn['turn_id'])['last_load']
        self.assertEqual(receipt['total_tokens'],load['total_tokens'])
        self.assertIn(self.first['turn_id'],receipt['accessed'])

    def test_hot_admission_respects_capacity_and_demotes_when_core_needs_space(self):
        for _ in range(2):
            _, load = self.plan()
            self.turn('再次核对','答复',load=load)
        core={'user_text':'无关问题','current_task':'核对案件信息','state':{}}
        hot_cap=max(128,memory.tokens(core)+8)
        budget=memory.Budget(hot=hot_cap,warm=1024,cold=2048,total=hot_cap+3200)
        payload, load=memory.build('cache',core,['db_query'],budget=budget)
        self.assertFalse(payload['hot_turns'])
        self.assertIn(self.first['turn_id'],load['evicted_hot'])
        self.turn('普通进度','答复',load=load)
        self.assertEqual(self.info(self.first['turn_id'])['tier'],'warm')

    def test_promoted_resident_set_has_at_most_three_records(self):
        for i in range(5):self.turn(f'星链量子纠错记录{i}','星链量子纠错构造记录。')
        for _ in range(2):
            _, load=self.plan()
            self.turn('继续核对','构造答复',load=load)
        with storage._connect() as conn:
            count=conn.execute("SELECT COUNT(*) FROM conversation_memory WHERE tier='hot'").fetchone()[0]
        self.assertGreater(count,0)
        self.assertLessEqual(count,3)

    def test_budget_counts_exact_json_sent_to_the_resolution_model(self):
        payload, load=self.plan()
        llm=Mock()
        llm.with_structured_output.return_value.ainvoke=AsyncMock(return_value=Resolution(task='核对'))
        with patch('agent.llm.get_llm',return_value=llm):
            asyncio.run(conversation.resolve(payload))
        messages=llm.with_structured_output.return_value.ainvoke.call_args.args[0]
        self.assertEqual(json.loads(messages[-1].content),payload)
        self.assertEqual(memory.counter().text(messages[-1].content),load['total_tokens'])

    def test_snapshot_and_evidence_bytes_are_unchanged_after_recall_and_promotion(self):
        before = hashlib.sha256(storage._dumps(conversation.get_turn('cache',self.first['turn_id'])).encode()).hexdigest()
        for _ in range(2):
            _, load = self.plan()
            self.turn('再核对一次','答复',load=load)
        after = hashlib.sha256(storage._dumps(conversation.get_turn('cache',self.first['turn_id'])).encode()).hexdigest()
        self.assertEqual(before,after)

    def test_other_sessions_and_closed_sources_cannot_enter_any_layer(self):
        foreign = self.turn('星链量子纠错方案','其他会话私有标记',thread='foreign')
        restricted = self.turn('星链量子纠错原文','未开放知识库标记',sources=['knowledge'])
        payload, _ = self.plan()
        self.assertNotIn('其他会话私有标记',str(payload))
        self.assertNotIn('未开放知识库标记',str(payload))
        ids = [t['turn_id'] for key in ('hot_turns','recent_turns','retrieved_turns') for t in payload[key]]
        self.assertNotIn(foreign['turn_id'],ids)
        self.assertNotIn(restricted['turn_id'],ids)
        empty, _ = self.plan(sources=[])
        self.assertFalse(empty['retrieved_turns'])
        self.assertFalse(empty['recent_turns'])

    def test_new_topic_does_not_recall_old_topic_but_explicit_reference_can(self):
        payload, _ = self.plan(fresh=True)
        self.assertFalse(payload['recent_turns'] or payload['retrieved_turns'] or payload['hot_turns'])
        self.turn('新的会议话题','会议答复',kind='new_topic')
        payload, _ = self.plan('现在继续查询')
        self.assertNotIn(self.first['turn_id'],[t['turn_id'] for t in payload['retrieved_turns']])
        payload, _ = self.plan('之前星链量子纠错方案')
        self.assertIn(self.first['turn_id'],[t['turn_id'] for t in payload['retrieved_turns']])
        payload, _ = self.plan(reference_id=self.first['turn_id'])
        self.assertIn(self.first['turn_id'],[t['turn_id'] for t in payload['retrieved_turns']])

    def test_explicit_old_topic_reuse_can_warm_up_without_mixing_a_fresh_topic(self):
        self.turn('会议安排','会议构造答复',kind='new_topic')
        for _ in range(2):
            _, load=self.plan('之前星链量子纠错方案')
            self.turn('核对已指定旧对象','构造答复',load=load)
        self.assertEqual(self.info(self.first['turn_id'])['tier'],'hot')
        self.assertIn(self.first['turn_id'],self.plan('继续核对')[1]['loaded']['hot'])
        _, load=self.plan('新任务',fresh=True)
        self.turn('另一个新话题','新答复',kind='new_topic',load=load)
        self.assertEqual(self.info(self.first['turn_id'])['tier'],'cold')
        self.assertNotIn(self.first['turn_id'],self.plan('继续核对')[1]['loaded']['hot'])

    def test_rejected_or_stale_followup_never_records_hits(self):
        run = storage.get_run('cache')
        body = Followup(text='之前星链量子纠错',expected_execution_id=run['execution_id'],expected_turn_id=conversation.latest('cache')['turn_id'])
        with patch.object(conversation,'resolve',AsyncMock(return_value=Resolution(question='请明确案号'))):
            with self.assertRaises(ValueError): asyncio.run(conversation.prepare(run,body))
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)
        with patch.object(conversation,'resolve',AsyncMock(return_value=Resolution(task='查询所述案件'))):
            prepared = asyncio.run(conversation.prepare(run,body))
        self.turn('其他已接受输入','答复')
        with self.assertRaises(ValueError):
            storage.create_run('cache','','',False,conversation_prepared=prepared,followup=body)
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)

    def test_loaded_old_record_can_bind_reference_but_cannot_invent_or_switch_silently(self):
        run = storage.get_run('cache')
        body = Followup(text='之前星链量子纠错那个案子目前是谁负责',expected_execution_id=run['execution_id'],expected_turn_id=conversation.latest('cache')['turn_id'])
        proposal = Resolution(task='实时查询DEMO-2026-001的承办人',recall_turn_id=self.first['turn_id'],case_ids=['DEMO-2026-001'])
        with patch.object(conversation,'resolve',AsyncMock(return_value=proposal)) as model:
            prepared = asyncio.run(conversation.prepare(run,body))
        self.assertEqual(prepared['state']['case_ids'],['DEMO-2026-001'])
        self.assertEqual(prepared['reference_turn_id'],self.first['turn_id'])
        self.assertIn('动态事实查询当前获准来源',prepared['task'])
        self.assertTrue(model.call_args.args[0]['retrieved_turns'])
        with patch.object(conversation,'resolve',AsyncMock(return_value=proposal)):
            with self.assertRaisesRegex(ValueError,'明确回指'):
                asyncio.run(conversation.prepare(run,body.model_copy(update={'text':'现在查询案件'})))
        with patch.object(conversation,'resolve',AsyncMock(return_value=proposal.model_copy(update={'recall_turn_id':'invented'}))):
            with self.assertRaises(ValueError): asyncio.run(conversation.prepare(run,body))

    def test_task_api_accepts_recalled_scope_and_exposes_actual_load_receipt(self):
        from fastapi.testclient import TestClient
        from api import server
        run=storage.get_run('cache')
        body=Followup(text='之前星链量子纠错那个案件现在谁负责',expected_execution_id=run['execution_id'],
                      expected_turn_id=conversation.latest('cache')['turn_id'])
        proposal=Resolution(task='实时查询DEMO-2026-001的承办人',recall_turn_id=self.first['turn_id'],case_ids=['DEMO-2026-001'])
        async def execute(thread_id, query, rounds=3):
            storage.append_event(thread_id,{'event':'task_result','data':{'result':'构造答复；本轮重新查询。','evidence':[]}})
        client=TestClient(server.app)
        try:
            with patch.object(conversation,'resolve',AsyncMock(return_value=proposal)), \
                 patch.object(server,'_run_task',side_effect=execute):
                response=client.post('/api/task',json={'thread_id':'cache','query':run['query'],
                    'allowed_sources':['db_query'],'followup':body.model_dump()})
            self.assertEqual(response.status_code,200,response.text)
            result=client.get('/api/runs/cache/conversation').json()
            self.assertEqual(result['memory']['conversation']['case_ids'],['DEMO-2026-001'])
            receipt=result['memory']['cache']['last_load']
            self.assertIn(self.first['turn_id'],receipt['loaded']['cold'])
            self.assertEqual(self.info(self.first['turn_id'])['hits'],1)
        finally:
            client.close()

    def test_a_field_question_cannot_become_an_owner_filter(self):
        run=storage.get_run('cache')
        body=Followup(text='现在由谁负责？请重新查询当前承办人',expected_execution_id=run['execution_id'],
                      expected_turn_id=conversation.latest('cache')['turn_id'])
        from api.conversation import ConditionChange
        proposal=Resolution(task='查询当前承办人',changes=[ConditionChange(key='owner',value='重新查询当前承办人',quote=body.text)])
        with patch.object(conversation,'resolve',AsyncMock(return_value=proposal)):
            with self.assertRaisesRegex(ValueError,'展示字段不能作为'):
                asyncio.run(conversation.prepare(run,body))

    def test_invalid_resolution_has_one_budgeted_repair_and_commits_no_early_hits(self):
        run=storage.get_run('cache')
        body=Followup(text='之前星链量子纠错那个案件是谁负责',expected_execution_id=run['execution_id'],
                      expected_turn_id=conversation.latest('cache')['turn_id'])
        bad=Resolution(task='查询旧案',recall_turn_id='invented',case_ids=['DEMO-2026-001'])
        good=Resolution(task='重新查询DEMO-2026-001承办人',recall_turn_id=self.first['turn_id'],case_ids=['DEMO-2026-001'])
        with patch.object(conversation,'resolve',AsyncMock(side_effect=[bad,good])) as model:
            prepared=asyncio.run(conversation.prepare(run,body))
        self.assertEqual(model.await_count,2)
        repaired_payload=model.call_args.args[0]
        self.assertIn('resolution_feedback',repaired_payload)
        self.assertLessEqual(memory.tokens(repaired_payload),memory.Budget().total)
        self.assertEqual(prepared['memory_load']['resolution_attempts'],2)
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)

        with patch.object(conversation,'resolve',AsyncMock(return_value=bad)) as model:
            with self.assertRaises(ValueError):asyncio.run(conversation.prepare(run,body))
        self.assertEqual(model.await_count,2)
        self.assertEqual(self.info(self.first['turn_id'])['hits'],0)

    def test_explicit_reference_can_be_confirmed_but_never_replaced_by_the_model(self):
        run=storage.get_run('cache')
        body=Followup(text='这些案件当前谁负责',reference_turn_id=self.first['turn_id'],
            expected_execution_id=run['execution_id'],expected_turn_id=conversation.latest('cache')['turn_id'])
        good=Resolution(task='实时查询所选案件承办人',recall_turn_id=self.first['turn_id'],case_ids=['DEMO-2026-001'])
        with patch.object(conversation,'resolve',AsyncMock(return_value=good)):
            prepared=asyncio.run(conversation.prepare(run,body))
        self.assertEqual(prepared['reference_turn_id'],self.first['turn_id'])
        self.assertEqual(prepared['state']['case_ids'],['DEMO-2026-001'])
        with patch.object(conversation,'resolve',AsyncMock(return_value=good.model_copy(update={'recall_turn_id':'invented'}))):
            with self.assertRaisesRegex(ValueError,'明确选择'):
                asyncio.run(conversation.prepare(run,body))

    def test_legacy_cache_backfill_is_idempotent_and_preserves_snapshot(self):
        with storage._connect() as conn:
            conn.execute('DELETE FROM conversation_memory')
        before = conversation.get_turn('cache',self.first['turn_id'])
        self.plan();self.plan()
        self.assertEqual(conversation.get_turn('cache',self.first['turn_id']),before)
        with storage._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM conversation_memory').fetchone()[0],7)

    def test_soft_delete_blocks_loading_and_purge_removes_all_derived_records(self):
        _, load = self.plan()
        self.turn('再核对','答复',load=load)
        recycle_bin.move('cache')
        with self.assertRaisesRegex(ValueError,'回收站'): self.plan()
        recycle_bin.restore('cache')
        self.assertTrue(self.plan()[0]['retrieved_turns'])
        storage.delete_run('cache')
        with storage._connect() as conn:
            for table in ('conversation_memory','conversation_memory_terms','conversation_memory_loads'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE thread_id=?',('cache',)).fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()

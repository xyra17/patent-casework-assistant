"""Single-field requests avoid model routing without losing task clauses."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from casework.routing import select_route, single_field_request
from casework.workflow import render, validate_request


class SingleFieldRouteTests(unittest.TestCase):
    def test_fields_route_without_model_and_keep_source_cap(self):
        questions = {
            '负责人是谁':'owner_name', '客户简称是什么':'client_alias',
            '发明名称是什么':'title', '办理阶段是什么':'stage',
            '实际收案日期是什么':'received_date', '初稿计划期限是什么':'draft_due_date',
            '客户送审计划期限是什么':'client_review_due_date',
            '向局提交计划期限是什么':'filing_due_date',
            '已核实的实际交局日期是什么；未核实则回答未核实':'actual_filed_date',
            '交局是否已经核实':'filing_verified',
        }
        with patch('casework.workflow.resolve', AsyncMock(side_effect=AssertionError('model routing'))), \
             patch('casework.routing.known_entities', return_value={'client':[], 'owner':[], 'domain':[], 'ipr':[], 'stage':[]}):
            for question, field in questions.items():
                text='查看AI-2026-031的案件信息，'+question+'？'
                # The question mark belongs to the main question, not the condition.
                text=text.replace('是什么；','是什么？；').replace('未核实？','未核实')
                route=asyncio.run(select_route(text))
                self.assertEqual(route.method,'single_field',text)
                self.assertEqual(route.request.case_field,field)
                self.assertTrue(validate_request(text,route.request))
                self.assertEqual(asyncio.run(select_route(text,['db_query'])).status,'blocked')

    def test_additional_requirements_never_silently_disappear(self):
        base='查AI-2026-031的负责人是谁'
        for suffix in ['，并解释为什么分配给他','；再查会议记录','；不查数据库',
                       '；比较AI-2026-033','；只查已完成案件','；请生成PDF',
                       '以及实际收案日期是什么','；不要注明来源']:
            text=base+suffix
            self.assertIsNone(single_field_request(text),text)
            with patch('casework.workflow.resolve',AsyncMock(return_value=None)):
                route=asyncio.run(select_route(text))
                self.assertNotEqual(route.method,'single_field',text)
        direct=single_field_request(base)
        self.assertIsNone(validate_request(base+'；再查会议记录',direct))

    def test_missing_conflicting_and_unverified_values_remain_visible(self):
        request=single_field_request('查AI-2026-031的实际交局日期是什么？')
        row={'case_id':'AI-2026-031','actual_filed_date':'2026-10-10',
             'filing_verified':'未核实','conflicting_candidates':{}}
        result={'rows':[row],'evidence_id':'S1','coverage':{}}
        output=render(result,request)
        self.assertIn('未核实',output)
        self.assertNotIn('2026-10-10',output)
        self.assertIn('[S1]',output)
        row['conflicting_candidates']={'actual_filed_date':[{'value':'2026-10-10','path':'original.xlsx','location':'A2'}]}
        row['conflict_evidence_id']='S2'
        output=render(result,request)
        self.assertIn('存在冲突',output)
        self.assertIn('original.xlsx',output)
        self.assertIn('[S2]',output)
        self.assertIn('未找到',render({'rows':[],'evidence_id':'S1','coverage':{}},request))


class SingleFieldExecutionTests(unittest.TestCase):
    from tests.test_department_casework import DepartmentCaseworkTests as _Fixtures
    setUp = _Fixtures.setUp
    tearDown = _Fixtures.tearDown

    def test_actual_tool_and_verification_for_each_field(self):
        from tests.test_department_contracts import ContractTests
        from casework import store
        case=store.get_case(self.ident+':DEMO-2026-013')
        for name in ['负责人','客户简称','发明名称','办理阶段','实际收案日期','初稿计划期限',
                     '客户送审计划期限','向局提交计划期限','实际交局日期','交局是否已经核实']:
            text='查DEMO-2026-013的'+name+'？'
            request=single_field_request(text)
            _,result=ContractTests.execute(self,request.model_dump())
            self.assertNotIn('error', result, result)
            self.assertEqual(result['business_check']['status'],'passed',result['business_check'])
            self.assertIn('DEMO-2026-013',result['report'])
            if request.case_field in ('title','client_alias'):
                self.assertIn(case[request.case_field],result['report'])

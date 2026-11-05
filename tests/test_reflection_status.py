import asyncio
import json
from tools.content_verification import CRITERIA
import unittest
from unittest.mock import AsyncMock, Mock, patch
from langchain_core.messages import AIMessage
from agent import main_agent


class ReflectionStatusTests(unittest.TestCase):
    def review(self, text=None, error=None):
        result = {'report': '回答正文', 'query': '用户问题'}
        llm = AsyncMock()
        llm.with_structured_output=Mock(return_value=llm)
        if error:
            llm.ainvoke.side_effect = error
        else:
            try:parsed=json.loads(text)
            except (ValueError,TypeError):parsed=None
            if isinstance(parsed,dict) and isinstance(parsed.get('checks'),list):
                from tools.content_verification import review_anchor_index
                anchor=review_anchor_index(result)['task'][0]['id']
                for check in parsed['checks']:
                    check.setdefault('task_anchor_id',anchor)
            llm.ainvoke.return_value = {'parsed':parsed,'parsing_error':None,'raw':AIMessage(content=text or '')}
        with patch.object(main_agent, 'get_llm', return_value=llm):
            issue = asyncio.run(main_agent.soft_review(result))
        return result, issue

    def test_service_failure_is_skipped_not_passed(self):
        result, issue = self.review(error=TimeoutError('timeout'))
        self.assertIsNone(issue)
        self.assertEqual(result['soft_review_status'], 'skipped')
        self.assertTrue(result['soft_review_detail'])

    def test_unstructured_and_legacy_responses_are_not_verified(self):
        for text in ['通过。', '不通过：结论缺少依据', '', '{"verdict":"passed","issues":[]}']:
            result, issue = self.review(text=text)
            self.assertEqual(result['soft_review_status'], 'skipped')
            self.assertEqual(result['verification']['status'], 'unverified')
            self.assertIsNone(issue)  # outer orchestration turns skipped into an unresolved failure

    def test_final_verdict_must_agree_with_checks(self):
        checks = [{'criterion': key, 'status': 'passed', 'detail': '核对通过',
                   'task_quote': '用户问题' if key == 'task_completion' else ''} for key in CRITERIA]
        result, issue = self.review(text=json.dumps({'verdict': 'passed', 'checks': checks}))
        self.assertEqual(result['soft_review_status'], 'passed')
        self.assertIsNone(issue)
        result, issue = self.review(text=json.dumps({'verdict': 'failed', 'checks': checks}))
        self.assertEqual(result['soft_review_status'], 'skipped')
        self.assertEqual(result['verification']['status'], 'unverified')

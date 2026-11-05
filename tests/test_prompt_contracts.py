"""Offline integration checks for prompt assembly and source-free file delivery."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage
from pydantic import Field

from agent import main_agent
from agent.load_prompt import main_agent_config, planner_config, sub_agents_config
from api import context, evidence, source_policy, storage, task_sources
from tools import reflect_check


class RecordingModel(FakeMessagesListChatModel):
    captured: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.captured.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def tool_names(spec):
    return {getattr(t, 'name', getattr(t, '__name__', '')) for t in spec['tools']}


class PromptContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        db_patch = patch.object(storage, '_DB_PATH', self.root / 'app.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        storage.init_db()
        context.set_session('prompt-contract', str(self.root))
        evidence.reset('prompt-contract')
        self.addCleanup(context.reset_session)
        self.addCleanup(evidence.clear, 'prompt-contract')

    def test_attachment_only_pdf_runs_real_tools_without_search_and_keeps_system_prompt(self):
        inputs = self.root / 'attachments'
        inputs.mkdir()
        (inputs / '材料.md').write_text('本次材料：项目采用人工核对后交付的流程。', encoding='utf-8')
        report = '# 材料核对报告\n\n' + '材料说明项目采用人工核对后交付的流程。[S1]\n' * 70
        model = RecordingModel(responses=[
            AIMessage(content='', tool_calls=[{'id': 'read', 'name': 'read_file_content',
                'args': {'filename': 'attachments/材料.md'}}]),
            AIMessage(content='', tool_calls=[{'id': 'write', 'name': 'generate_markdown',
                'args': {'content': report, 'filename': '报告.md'}}]),
            AIMessage(content='', tool_calls=[{'id': 'convert', 'name': 'convert_md_to_pdf',
                'args': {'md_filename': '报告.md', 'pdf_filename': '报告.pdf'}}]),
            AIMessage(content='已创建报告.pdf，可在附件与报告 → 生成文件下载。'),
        ])
        plan = main_agent.TaskPlan(task_type='report', require_report=True, require_pdf=True,
            required_sources=[], forbidden_sources=['db_query', 'knowledge', 'web_search'],
            steps=['读取材料', '生成Markdown', '转换PDF'], output_artifacts=['PDF 报告', 'Markdown 中间产物'])
        query = '仅使用附件生成PDF报告。\n' + main_agent.format_plan_prompt(plan)
        with task_sources.scope([]), patch.object(main_agent, 'get_llm', return_value=model), \
                patch.object(main_agent.path_utils, 'get_upload_dir', return_value=inputs), \
                patch.object(main_agent.monitor, 'report', new_callable=AsyncMock):
            source_policy.configure([])
            result = asyncio.run(main_agent.main_agent_run(query, 1, []))
        self.assertEqual(result['called_sources'], [])
        self.assertTrue(result['pdf_produced'])
        self.assertEqual((self.root / '报告.pdf').read_bytes()[:5], b'%PDF-')
        self.assertEqual(reflect_check.hard_check(result), [])
        self.assertEqual(len(model.captured), 4)
        for request in model.captured:
            system = '\n'.join(str(m.content) for m in request if isinstance(m, SystemMessage))
            self.assertIn(main_agent_config['system_prompt'].strip(), system)
            self.assertNotIn(main_agent_config['attachment_join_prompt'].strip(), system)
            self.assertNotIn(sub_agents_config['knowledge']['system_prompt'].strip(), system)

    def test_all_explicit_subagents_receive_the_central_config_and_real_tools(self):
        with task_sources.scope(['db_query', 'knowledge', 'web_search']), \
                patch('casework.privacy.protected', return_value=False), \
                patch('deepagents.create_deep_agent') as factory, \
                patch.object(main_agent, 'get_llm', return_value=object()):
            source_policy.configure(['db_query', 'knowledge', 'web_search'])
            main_agent.create_deep_agent()
        specs = {spec['name']: spec for spec in factory.call_args.kwargs['subagents']}
        self.assertEqual(set(specs), {cfg['name'] for cfg in sub_agents_config.values()})
        for cfg in sub_agents_config.values():
            spec = specs[cfg['name']]
            self.assertTrue(spec['system_prompt'].startswith(cfg['system_prompt']))
            self.assertEqual(spec['system_prompt'].count(cfg['system_prompt']), 1)
            self.assertIn('report_source_gap', tool_names(spec))
        knowledge = specs[sub_agents_config['knowledge']['name']]
        self.assertIn('read_case_archive_document', tool_names(knowledge))
        database = specs[sub_agents_config['db_query']['name']]
        self.assertIn('semantic_query', tool_names(database))

    def test_short_reports_have_no_default_length_gate(self):
        for kind, require_report in [('report', True), ('qa', False)]:
            plan = main_agent.TaskPlan(task_type=kind, require_report=require_report, require_pdf=False)
            rendered = main_agent.format_plan_prompt(plan)
            self.assertNotIn('至少', rendered)
            self.assertNotIn('min_report_length', rendered)
            self.assertEqual(reflect_check.hard_check({'report': '已核实。', 'require_report': require_report}), [])
        self.assertEqual(main_agent._PLANNER_SYSTEM_PROMPT, planner_config['system_prompt'])
        self.assertNotIn('{min_report_length}', main_agent._PLANNER_SYSTEM_PROMPT)


if __name__ == '__main__':
    unittest.main()

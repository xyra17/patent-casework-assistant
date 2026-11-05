"""Offline policy, durable readback, and real DeepAgents middleware integration."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from pydantic import Field

# Discovery loads this module before legacy tests; set the sandbox before any
# import can initialize storage or the API, so the whole suite stays isolated.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="tanyuan-context-test-db-")
os.environ.setdefault("APP_DB_PATH", str(Path(_TEST_DB_DIR) / "app.db"))

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from api import context, evidence, storage
from harness.context import ContextBudget, ContextBudgetExceeded, ContextManager
from harness.middleware import EvidenceContextMiddleware, context_tools


def history(count=8):
    messages = [HumanMessage(content="销售额按修订值计算，单位万元；不得联网。任务计划：核对来源后回答。")]
    for i in range(count):
        messages.extend([
            AIMessage(content="", tool_calls=[{"id": f"call-{i}", "name": "search", "args": {}}]),
            ToolMessage(tool_call_id=f"call-{i}", content=(
                f"[证据 S{i + 1}] 来源 https://example.test/{i}\n"
                + "无关的仓储日常记录。\n" * 350
                + f"销售额修订值为{i + 10}万元，不含税。\n")),
        ])
    return messages


class ToolModel(FakeMessagesListChatModel):
    """Fake provider, real graph/tool/middleware execution."""
    def bind_tools(self, tools, **kwargs):
        return self


class RecordingToolModel(ToolModel):
    captured: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.captured.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class ContextHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = storage._DB_PATH
        storage._DB_PATH = Path(self.tmp.name) / "test.db"
        storage.init_db()
        self.budget = ContextBudget(window=10000, output=512, safety=256, evidence=1500, tools=3000)

    def tearDown(self):
        context.reset_session()
        evidence.clear("ctx")
        storage._DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_selective_preserves_protocol_constraints_and_originals(self):
        manager = ContextManager(self.budget)
        messages = history()
        originals = [m.model_dump() for m in messages]
        result = manager.project(messages)
        manager.require_fit(result)
        self.assertLess(result.decision["after_tokens"], result.decision["before_tokens"])
        self.assertEqual(messages[0].content, result.messages[0].content)
        self.assertEqual(originals, [m.model_dump() for m in messages])
        for original, projected in zip(messages, result.messages):
            if isinstance(original, ToolMessage):
                self.assertEqual(original.tool_call_id, projected.tool_call_id)
                self.assertIn("销售额修订值", projected.content)
                self.assertIn("不含税", projected.content)
                self.assertIn(original.content.split("]")[0][1:], projected.content)
        for action in result.decision["actions"]:
            self.assertEqual(result.artifacts[action["ref"]], messages[action["index"]].content)
            self.assertTrue(action["evidence_ids"])

    def test_budget_includes_system_and_tool_schemas(self):
        manager = ContextManager(self.budget)
        result = manager.project(history(1), SystemMessage(content="系统" * 50), schema_tokens=321)
        self.assertGreaterEqual(result.decision["sections_before"]["tools"], 321)
        self.assertGreater(result.decision["sections_before"]["system"], 0)

    def test_64k_environment_accepts_protected_input_above_32k(self):
        with patch.dict(os.environ, {'CONTEXT_WINDOW': '65536', 'CONTEXT_OUTPUT': '4096',
                                     'CONTEXT_SAFETY': '1024'}):
            budget = ContextBudget.from_env()
        self.assertEqual(budget.input_limit, 60416)
        messages = [HumanMessage(content='case ' * 40000)]
        projection = ContextManager(budget).project(messages, strategy='full')
        self.assertGreater(projection.decision['after_tokens'], 27648)
        ContextManager(budget).require_fit(projection)
        with self.assertRaises(ContextBudgetExceeded):
            old = ContextManager(ContextBudget(window=32768, output=4096, safety=1024))
            old.require_fit(old.project(messages, strategy='full'))

    def test_bounded_readback_pages_are_not_recursively_compressed(self):
        # A page is already bounded. Replacing it by another ref makes the
        # model chase JSON-escaped pages instead of seeing the requested data.
        for name in ('read_context_artifact', 'read_preserved_evidence'):
            for named in (True, False):
                page = json.dumps({'offset': 0, 'next_offset': 4000,
                                   'total_chars': 8000, 'content': '原始案卷。' * 800}, ensure_ascii=False)
                messages = [HumanMessage(content='核对原始案卷'),
                    AIMessage(content='', tool_calls=[{'id':'source-1','name':'source','args':{}}]),
                    ToolMessage(content='无关记录。' * 2000, tool_call_id='source-1', name='source'),
                    AIMessage(content='', tool_calls=[{'id':'page-1','name':name,'args':{}}]),
                    ToolMessage(content=page, tool_call_id='page-1', **({'name':name} if named else {}))]
                manager = ContextManager(ContextBudget(window=14000, output=512, safety=128,
                                                        tools=500, evidence=500, history=4096))
                result = manager.project(messages)
                manager.require_fit(result)
                self.assertTrue(any(a['action']=='compact_tool' for a in result.decision['actions']))
                self.assertEqual(result.messages[-1].content, page)
                self.assertEqual(json.loads(result.messages[-1].content)['next_offset'], 4000)
                self.assertNotIn(page, result.artifacts.values())

    def test_readback_protection_does_not_bypass_hard_input_limit(self):
        messages = [HumanMessage(content='约束' * 3000),
            AIMessage(content='', tool_calls=[{'id':'page','name':'read_context_artifact','args':{}}]),
            ToolMessage(content='原文', tool_call_id='page', name='read_context_artifact')]
        manager = ContextManager(ContextBudget(window=1500, output=128, safety=128))
        with self.assertRaises(ContextBudgetExceeded):
            manager.require_fit(manager.project(messages))

    def test_pinned_overflow_is_explicit(self):
        manager = ContextManager(self.budget)
        messages = [HumanMessage(content="不可删除的用户约束" * 3000)]
        result = manager.project(messages)
        self.assertEqual(result.messages, messages)
        with self.assertRaises(ContextBudgetExceeded):
            manager.require_fit(result)

    def test_truncate_offloads_complete_exchanges(self):
        manager = ContextManager(ContextBudget(window=7000, output=512, safety=128, recent_messages=2))
        result = manager.project(history(), strategy="truncate")
        self.assertTrue(result.decision["actions"])
        self.assertIn("不得联网", "".join(str(m.content) for m in result.messages))
        calls = {c["id"] for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls}
        results = {m.tool_call_id for m in result.messages if isinstance(m, ToolMessage)}
        self.assertEqual(calls, results)
        self.assertTrue(result.artifacts)
        manager.require_fit(result)

    def test_parallel_tool_results_remain_together(self):
        messages = [HumanMessage(content="保留约束")]
        for i in range(5):
            messages.append(AIMessage(content="", tool_calls=[
                {"id": f"{i}-{j}", "name": "tool", "args": {}} for j in range(2)]))
            messages.extend(ToolMessage(content="noise " * 500, tool_call_id=f"{i}-{j}") for j in range(2))
        result = ContextManager(ContextBudget(window=3500, output=256, safety=128)).project(messages, strategy="truncate")
        calls = {c["id"] for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls}
        self.assertEqual(calls, {m.tool_call_id for m in result.messages if isinstance(m, ToolMessage)})

    def test_readback_is_scoped_paginated_and_durable(self):
        storage.save_context_artifact("ctx", "a" * 64, "original" * 1000)
        read = context_tools("ctx")[0]
        page = json.loads(read.invoke({"ref": "a" * 64, "limit": 10}))
        self.assertEqual(page["content"], "originalor")
        self.assertEqual(page["next_offset"], 10)
        self.assertIn("未找到", context_tools("other")[0].invoke({"ref": "a" * 64}))
        self.assertEqual(read.args_schema.model_json_schema()['properties']['limit']['maximum'],4000)
        self.assertIn('参数错误',read.invoke({"ref": "a" * 64, "limit": 5000}))
        self.assertIn("无效", read.invoke({"ref": "../../secret"}))
        # Verify recovery from an independent Python process, no module cache.
        env = {**os.environ, "APP_DB_PATH": str(storage._DB_PATH)}
        result = subprocess.run([os.sys.executable, "-c",
            "from api import storage; print(len(storage.load_context_artifact('ctx', 'a'*64)))"],
            env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), "8000")

    def test_evidence_unbounded_deduplicated_and_persisted_before_return(self):
        evidence.reset("ctx")
        for i in range(75):
            self.assertEqual(evidence.add("ctx", {"title": f"Doc{i}", "snippet": "原文"}), f"S{i + 1}")
        evidence.clear("ctx")
        self.assertEqual(len(storage.load_evidence("ctx")), 75)
        self.assertEqual(evidence.add("ctx", {"title": "Doc0", "snippet": "原文"}), "S1")
        read = context_tools("ctx")[1]
        self.assertIn("Doc0", read.invoke({"evidence_id": "S1"}))
        self.assertIn("未找到", context_tools("other")[1].invoke({"evidence_id": "S1"}))

    def test_concurrent_evidence_ids_and_deletion(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            items = list(executor.map(lambda i: storage.save_evidence("ctx", str(i), {"title": str(i)}), range(40)))
        self.assertEqual(len({e["id"] for e in items}), 40)
        storage.save_context_artifact("ctx", "b" * 64, "data")
        storage.record_context_decision("ctx", "main", {"action": "test"})
        storage.delete_run("ctx")
        self.assertEqual(storage.load_evidence("ctx"), [])
        self.assertIsNone(storage.load_context_artifact("ctx", "b" * 64))
        self.assertEqual(storage.context_decisions("ctx"), [])

    def test_middleware_sync_async_and_usage(self):
        model = ToolModel(responses=[AIMessage(content="ok")])
        request = ModelRequest(model=model, messages=history(2), tools=[])
        middleware = EvidenceContextMiddleware("ctx", budget=self.budget)
        seen = []
        def handler(req):
            seen.append(req)
            return ModelResponse(result=[AIMessage(content="ok", usage_metadata={
                "input_tokens": 123, "output_tokens": 9, "total_tokens": 132})])
        middleware.wrap_model_call(request, handler)
        async def run():
            async def async_handler(req):
                return handler(req)
            return await middleware.awrap_model_call(request, async_handler)
        asyncio.run(run())
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].model_settings["max_tokens"], self.budget.output)
        decisions = storage.context_decisions("ctx")
        self.assertEqual(sum(d.get("event") == "model_usage" for d in decisions), 2)
        self.assertTrue(any(d.get("actions") for d in decisions))

    def test_real_deepagents_graph_uses_replacement(self):
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
        from langchain_core.tools import tool
        @tool
        def source() -> str:
            """Read a fixed source document."""
            return "[证据 S1] 销售额修订值42万元，不含税。\n" + "无关数据。\n" * 2500
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "source", "args": {}, "id": "read"}]),
            AIMessage(content="修订值42万元 [S1]"),
        ])
        agent = create_deep_agent(model=model, tools=[source, *context_tools("ctx")],
            backend=FilesystemBackend(root_dir=self.tmp.name, virtual_mode=True),
            middleware=[EvidenceContextMiddleware("ctx", budget=ContextBudget(window=24000, output=512, safety=256, evidence=1000))])
        result = asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="核对销售额修订值，不得联网")]}))
        self.assertIn("42", result["messages"][-1].content)
        decisions = storage.context_decisions("ctx")
        self.assertEqual(len(decisions), 2)
        self.assertTrue(decisions[-1]["actions"])
        # Raw state still holds the original tool result, projection affected only the provider prompt.
        raw = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertGreater(len(raw.content), 10000)

    def test_main_factory_wires_all_explicit_subagents(self):
        from agent import main_agent
        context.set_session("ctx", self.tmp.name)
        with patch("deepagents.create_deep_agent") as factory, patch.object(main_agent, "get_llm", return_value=object()):
            main_agent.create_deep_agent()
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["middleware"][0].name, "SummarizationMiddleware")
        self.assertTrue(all(s["middleware"] for s in kwargs["subagents"]))
        self.assertTrue(all("read_context_artifact" in [getattr(t, "name", "") for t in s["tools"]] for s in kwargs["subagents"]))
        with patch.dict(os.environ, {"CONTEXT_STRATEGY": "default"}), patch("deepagents.create_deep_agent") as factory:
            with patch.object(main_agent, "get_llm", return_value=object()):
                main_agent.create_deep_agent()
            self.assertEqual(factory.call_args.kwargs["middleware"], [])

    def test_child_receives_only_delegated_task_and_its_own_budget(self):
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
        child = RecordingToolModel(responses=[AIMessage(content="子任务完成")])
        parent = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "delegate", "name": "task", "args": {
                "subagent_type": "researcher", "description": "只核对库存数量"}}]),
            AIMessage(content="完成"),
        ])
        agent = create_deep_agent(model=parent, backend=FilesystemBackend(root_dir=self.tmp.name, virtual_mode=True),
            tools=context_tools("ctx"), middleware=[EvidenceContextMiddleware("ctx")],
            subagents=[{"name": "researcher", "description": "核对库存", "system_prompt": "核对委派任务",
                        "model": child, "tools": context_tools("ctx"),
                        "middleware": [EvidenceContextMiddleware("ctx", "researcher")]}])
        asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="父任务私有备注：不要传播暗号PARENT_PRIVATE_937")]}))
        self.assertEqual(len(child.captured), 1)
        child_text = "\n".join(str(m.content) for m in child.captured[0])
        self.assertIn("只核对库存数量", child_text)
        self.assertNotIn("PARENT_PRIVATE_937", child_text)
        self.assertTrue(any(d["agent"] == "researcher" for d in storage.context_decisions("ctx")))

    def test_diagnostics_and_resubmission_clear_previous_run(self):
        from api.server import app
        from fastapi.testclient import TestClient
        storage.create_run("ctx", "q", "q", False)
        storage.record_context_decision("ctx", "main", {"before_tokens": 100, "after_tokens": 50, "over_budget": False})
        storage.record_context_decision("ctx", "main", {"event": "model_usage", "usage": {"input_tokens": 60, "output_tokens": 10}})
        client = TestClient(app)
        response = client.get("/api/runs/ctx/context")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_input_tokens"], 60)
        self.assertEqual(response.json()["estimated_input_after"], 50)
        self.assertEqual(client.get("/api/runs/missing/context").status_code, 404)
        storage.save_context_artifact("ctx", "c" * 64, "old run")
        storage.create_run("ctx", "new", "new", False)
        self.assertEqual(storage.context_decisions("ctx"), [])
        self.assertIsNone(storage.load_context_artifact("ctx", "c" * 64))


if __name__ == "__main__":
    unittest.main()

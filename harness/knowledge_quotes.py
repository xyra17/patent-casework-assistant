"""Bind knowledge subagent quotes before handing their answer to the parent."""
import hashlib

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage


class KnowledgeQuoteMiddleware(AgentMiddleware):
    def after_model(self, state, runtime):
        from api import context, evidence, storage
        from tools.answer_contracts import render_answer
        from tools.evidence_refs import normalize_citations
        from tools.source_quotes import render_source_quotes
        message = state['messages'][-1]
        thread = context.get_thread_id()
        if not thread or not isinstance(message,AIMessage) or message.tool_calls or not isinstance(message.content,str):
            return None
        items = evidence.snapshot(thread)
        rendered, audit = render_answer(message.content, items, quotes_only=True)
        rendered, quotes = render_source_quotes(rendered, items)
        rendered = normalize_citations(rendered, items)
        if rendered == message.content:
            return None
        ref = hashlib.sha256(message.content.encode()).hexdigest()
        storage.save_context_artifact(thread,ref,message.content)
        storage.record_context_decision(thread,'knowledge',{'event':'knowledge_quote_render',
            'raw_answer_ref':ref,'audit':[*audit,*quotes]})
        return {'messages':[message.model_copy(update={'content':rendered})]}

    async def aafter_model(self, state, runtime):
        return self.after_model(state,runtime)

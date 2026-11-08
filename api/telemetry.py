"""Task-scoped timings and provider usage, without prompts or credentials."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from langchain_core.callbacks import BaseCallbackHandler

from api import context, storage


def record(data):
    thread = context.get_thread_id()
    if thread:
        storage.record_context_decision(thread, 'runtime', data)


@contextmanager
def span(stage):
    start = time.perf_counter()
    status = 'ok'
    try:
        yield
    except BaseException:
        status = 'error'
        raise
    finally:
        record({'event': 'stage_timing', 'stage': stage, 'status': status,
                'elapsed_seconds': time.perf_counter() - start, 'round': context.get_round()})


class ModelTelemetry(BaseCallbackHandler):
    """Observe every get_llm call, including routers, selectors and reviewers."""
    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        thread = context.get_thread_id()
        if thread:
            with self._lock:
                self._pending[str(run_id)] = (thread, context.get_round(), time.perf_counter())
            storage.record_context_decision(thread, 'provider', {'event': 'model_call_start', 'call_id': str(run_id)})

    def _finish(self, run_id, response=None, error=None):
        with self._lock:
            pending = self._pending.pop(str(run_id), None)
        if not pending:
            return
        thread, round_num, started = pending
        usage = None
        for group in getattr(response, 'generations', []):
            for generation in group:
                value = getattr(getattr(generation, 'message', None), 'usage_metadata', None)
                if value:
                    usage = dict(value)
        if usage is None:
            raw = (getattr(response, 'llm_output', None) or {}).get('token_usage')
            if raw:
                usage = {'input_tokens': raw.get('prompt_tokens'), 'output_tokens': raw.get('completion_tokens'),
                         'total_tokens': raw.get('total_tokens'), 'provider_details': raw}
        storage.record_context_decision(thread, 'provider', {
            'event': 'model_call_end', 'call_id': str(run_id), 'round': round_num,
            'elapsed_seconds': time.perf_counter() - started,
            'status': 'error' if error else 'ok', 'error_type': type(error).__name__ if error else None,
            'usage': usage, 'usage_available': usage is not None})

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(run_id, response=response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, error=error)


model_telemetry = ModelTelemetry()


def summarize(decisions):
    """Count provider completions once; never double-count model_usage mirrors."""
    calls={row['call_id']:row for row in decisions
           if row.get('event')=='model_call_end' and row.get('call_id')}
    stages={}
    for row in decisions:
        if row.get('event')=='stage_timing':
            stage=row['stage']
            stages[stage]=stages.get(stage,0)+row.get('elapsed_seconds',0)
    usage=[c['usage'] for c in calls.values() if c.get('usage_available') and c.get('usage')]
    return {'schema_version':1,'model_calls':len(calls),
        'model_errors':sum(c.get('status')=='error' for c in calls.values()),
        'provider_seconds_sum':sum(c.get('elapsed_seconds',0) for c in calls.values()),
        'provider_time_scope':'累计调用耗时；并行时不能当作任务墙钟耗时',
        'usage_available_calls':len(usage),
        'input_tokens':sum(u.get('input_tokens') or 0 for u in usage),
        'output_tokens':sum(u.get('output_tokens') or 0 for u in usage),
        'total_tokens':sum(u.get('total_tokens') or 0 for u in usage),
        'usage_complete':len(usage)==len(calls),
        'cache_hits':sum(d.get('event')=='tool_cache_hit' for d in decisions),
        'stages_seconds':stages,'human_active_seconds':None,
        'human_verification_seconds':None,'currency_cost':None,
        'scope':'任务内部执行记录；不含真人核验，不等于任务准确率或供应商账单。'}

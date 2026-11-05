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

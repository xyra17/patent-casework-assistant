"""Bounded task-local cache for versioned department reads only.

Never cache arbitrary SQL, external search, failed calls, or across executions.
The caller's current snapshot and source policy are part of every key.
"""
from __future__ import annotations

import json
import re
from api import context,evidence,source_policy,telemetry
from casework import store

SOURCES={'query_department_cases':'db_query','search_case_archive':'knowledge',
         'read_case_archive_document':'knowledge','collect_case_materials':'knowledge'}


def key(name,args):
    if name not in SOURCES or context.task_read_cache() is None:return None
    if source_policy.source_denial(SOURCES[name]):return None
    from casework.privacy import ensure_model_allowed
    ensure_model_allowed()
    policy=source_policy.current_policy()
    if policy is None:return None
    return store.digest({'tool':name,'args':args,'version':store.published_version(),
        'active':sorted(policy.active),'forbidden':sorted(policy.forbidden),
        'allowed_roots':[str(p) for p in store.allowed_roots()]})


def get(cache_key):
    cache=context.task_read_cache()
    if cache_key is None or cache is None:return None
    with cache['lock']:
        entry=cache['entries'].get(cache_key)
        if not entry:return None
        current={e['id']:store.digest(e) for e in evidence.snapshot(context.get_thread_id())}
        if any(current.get(sid)!=digest for sid,digest in entry['evidence'].items()):return None
        return entry['raw']


def put(cache_key,raw):
    cache=context.task_read_cache()
    if cache_key is None or cache is None or not isinstance(raw,str):return
    try:data=json.loads(raw)
    except (ValueError,TypeError):return
    if (not isinstance(data,dict) or data.get('error') or data.get('requires_user_confirmation')
            or data.get('clarification') or data.get('status') in ('error','failed','denied','awaiting_input')):return
    ids=set(re.findall(r'\bS[1-9]\d*\b',raw))
    if not ids:return
    current={e['id']:store.digest(e) for e in evidence.snapshot(context.get_thread_id())}
    if not ids<=current.keys():return
    if len(raw.encode())>2_000_000:return
    with cache['lock']:
        entries=cache['entries']
        while entries and (len(entries)>=32 or sum(len(e['raw'].encode()) for e in entries.values())+len(raw.encode())>8_000_000):
            entries.pop(next(iter(entries)))
        entries[cache_key]={'raw':raw,'evidence':{sid:current[sid] for sid in ids}}


def invoke(tool,args):
    # Both the version key and the actual read refer to the same SQLite snapshot.
    with store.read_snapshot():
        cache_key=key(tool.name,args)
        raw=get(cache_key)
        if raw is not None:
            telemetry.record({'event':'tool_cache_hit','tool':tool.name,'scope':'task_versioned'})
            return raw
        raw=tool.invoke(args)
        put(cache_key,raw)
        return raw

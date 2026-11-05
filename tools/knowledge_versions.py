"""Confirmed document applicability, provenance and version relations; independent of LLMs."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

REGISTRY = '.tanyuan-versions.json'
SNAPSHOT = 'tanyuan-versions.json'


class Relation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['replaces', 'amends', 'supplements', 'references']
    target: str = Field(min_length=1, max_length=400)
    target_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    basis: str = Field(min_length=1, max_length=2000)
    source_clause: str = Field(default='', max_length=400)
    target_clause: str = Field(default='', max_length=400)

    @model_validator(mode='after')
    def clauses(self):
        if self.kind == 'amends' and not (self.source_clause.strip() and self.target_clause.strip()):
            raise ValueError('局部修订须填写新旧条款定位')
        return self


class VersionInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(default='', max_length=200)
    version_label: str = Field(default='', max_length=100)
    kind: Literal['reference', 'regulation'] = 'reference'
    confirmed: bool = False
    published_on: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    organizations: list[str] = Field(default_factory=list, max_length=50)
    regions: list[str] = Field(default_factory=list, max_length=50)
    businesses: list[str] = Field(default_factory=list, max_length=50)
    scope_note: str = Field(default='', max_length=1000)
    relations: list[Relation] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def dates_and_identity(self):
        for field in ('organizations', 'regions', 'businesses'):
            values = getattr(self, field)
            if any(not v.strip() or len(v) > 200 for v in values):
                raise ValueError('适用范围须为非空名称，每项最多200字')
            setattr(self, field, list(dict.fromkeys(v.strip() for v in values)))
        if self.effective_from and self.effective_until and self.effective_until <= self.effective_from:
            raise ValueError('失效日期须晚于生效日期（失效当日不适用）')
        if self.confirmed and self.kind == 'regulation' and not (
                self.document_id.strip() and self.version_label.strip() and self.effective_from):
            raise ValueError('确认制度须填写制度标识、版本名称和生效日期')
        return self


class RetrievalScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['current', 'history', 'compare'] = 'current'
    as_of: date | None = None
    organization: str = Field(default='', max_length=200)
    region: str = Field(default='', max_length=200)
    business: str = Field(default='', max_length=200)
    sources: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def context(self):
        self.sources = list(dict.fromkeys(self.sources))
        if self.purpose == 'history' and not self.as_of:
            raise ValueError('历史查询须指定业务发生日期 as_of')
        if self.purpose == 'compare' and len(self.sources) < 2:
            raise ValueError('版本对比须明确选择至少两份文档')
        return self

    def resolved(self):
        value = self.model_dump(mode='json')
        value['as_of'] = (self.as_of or datetime.now(ZoneInfo('Asia/Shanghai')).date()).isoformat()
        return value


def file_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_registry(root: Path) -> dict:
    path = root / REGISTRY
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('文档版本登记文件损坏')
    return value


def registry_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_registry(root: Path, value: dict):
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.versions-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
        os.replace(name, root / REGISTRY)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def version_snapshot(root: Path, sources: dict, registry: dict) -> dict:
    result = {}
    for name in sources:
        entry = registry.get(name, {})
        info = VersionInfo.model_validate(entry.get('info', {})).model_dump(mode='json')
        revision = sources[name].get('revision') or file_revision(root / name)
        if entry.get('revision') != revision:
            info['confirmed'] = False
        result[name] = {'revision': revision, 'info': info}
    for name, entry in result.items():
        for relation in entry['info']['relations']:
            target = result.get(relation['target'])
            if not target or target['revision'] != relation['target_revision']:
                entry['info']['confirmed'] = False
    return result


def select_versions(snapshot: dict, scope: dict) -> tuple[set[str], dict[str, dict], list[str]]:
    """Choose an eligible corpus before BOTH retrieval channels. Unknowns never prove validity."""
    selected, checks, notices = set(), {}, []
    compare = scope['purpose'] == 'compare'
    for name, entry in snapshot.items():
        info = entry['info']
        warnings = []
        if not info['confirmed']:
            warnings.append('版本与适用信息未确认或原文已变更')
        if not compare and info['kind'] == 'regulation':
            if not info['confirmed'] or not info['effective_from']:
                notices.append(f'{name}：制度适用信息待确认，未作为适用依据')
                continue
            if scope['as_of'] < info['effective_from'] or (info['effective_until'] and scope['as_of'] >= info['effective_until']):
                continue
            mismatch = False
            for key, field in [('organization', 'organizations'), ('region', 'regions'), ('business', 'businesses')]:
                if info[field] and scope[key] not in info[field]:
                    if not scope[key]:
                        notices.append(f'{name}：缺少适用范围 {key}')
                    mismatch = True
            if mismatch:
                continue
        selected.add(name)
        checks[name] = {'verified': info['confirmed'], 'purpose': scope['purpose'], 'as_of': scope['as_of'],
                        'scope': scope, 'version': info, 'warnings': warnings, 'relations': []}
    if compare:
        for name in scope['sources']:
            if name not in selected:
                notices.append(f'指定对比文档未在已发布索引中：{name}')
    else:
        replaced = set()
        for name in sorted(snapshot):
            info = snapshot[name]['info']
            if not info['confirmed'] or info['kind'] != 'regulation' or not info['effective_from'] or scope['as_of'] < info['effective_from']:
                continue
            if any(info[field] and scope[key] not in info[field] for key, field in
                   [('organization', 'organizations'), ('region', 'regions'), ('business', 'businesses')]):
                continue
            for relation in info['relations']:
                target = relation['target']
                if target not in selected:
                    continue
                if relation['kind'] == 'replaces':
                    replaced.add(target)
                elif relation['kind'] in ('references', 'supplements') and name in selected:
                    checks[name]['relations'].append({'source': name, **relation})
                    if relation['kind'] == 'supplements':
                        for affected in (name, target):
                            checks[affected]['verified'] = False
                            checks[affected]['warnings'].append('存在补充规定，须结合双方原文核实适用条件')
                elif relation['kind'] == 'amends' and name in selected:
                    for affected in (name, target):
                        checks[affected]['verified'] = False
                        checks[affected]['warnings'].append('存在局部修订，须对照新旧条款核实，不能整篇套用')
                        checks[affected]['relations'].append({'source': name, **relation})
        # Cycles are ambiguous; keep candidates visible but never verify coverage.
        groups = defaultdict(list)
        for name in selected:
            doc_id = snapshot[name]['info']['document_id']
            if doc_id:
                groups[doc_id].append(name)
        # A replaces edge must not form a directed cycle, including across document IDs.
        edges = {n: [r['target'] for r in snapshot[n]['info']['relations']
                     if r['kind'] == 'replaces' and r['target'] in selected]
                 for n in selected if snapshot[n]['info']['confirmed']}
        visited, stack, cyclic = set(), [], set()
        def visit(node):
            if node in stack:
                cyclic.update(stack[stack.index(node):])
                return
            if node in visited:
                return
            stack.append(node)
            for target in edges.get(node, []):
                visit(target)
            stack.pop()
            visited.add(node)
        for node in edges:
            visit(node)
        replaced -= cyclic
        selected -= replaced
        for name in cyclic:
            checks[name]['verified'] = False
            checks[name]['warnings'].append('替代关系存在循环，需人工核实')
        for names in groups.values():
            active = sorted(set(names) & selected)
            if len(active) > 1:
                for name in active:
                    checks[name]['verified'] = False
                    checks[name]['warnings'].append('同一制度存在并行适用候选：' + '、'.join(active))
        for name in selected:
            for other in sorted(replaced):
                for rel in snapshot[name]['info']['relations']:
                    if rel['target'] == other and rel['kind'] == 'replaces':
                        checks[name]['relations'].append({'source': name, **rel})
    if scope['sources']:
        selected &= set(scope['sources'])
    return selected, checks, notices

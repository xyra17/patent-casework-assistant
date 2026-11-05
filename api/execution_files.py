"""Execution-specific workspaces, input hashes and generated artifact manifests."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from utils import path_utils


def activate(thread_id: str, execution_id: str) -> Path:
    root = path_utils.get_session_root(thread_id)
    workspace = path_utils.get_execution_dir(thread_id, execution_id)
    workspace.mkdir(parents=True, exist_ok=False)
    pointer = root / '.current-execution'
    temp = root / '.current-execution.tmp'
    temp.write_text(execution_id, encoding='ascii')
    os.replace(temp, pointer)
    return workspace


def _entry(path: Path, root: Path) -> dict:
    return {'name': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write_manifest(workspace: Path, status: str = 'running'):
    # Legacy direct tool calls have no execution container.
    if workspace.parent.parent.name != 'executions':
        return
    inputs = workspace / 'attachments'
    files = [p for p in workspace.rglob('*') if p.is_file() and not p.is_symlink()
             and path_utils.is_within(p, workspace)]
    manifest = {'execution_id': workspace.parent.name, 'status': status,
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'inputs': [_entry(p, inputs) for p in files if inputs in p.parents],
                'artifacts': [_entry(p, workspace) for p in files if inputs not in p.parents]}
    target = workspace.parent / 'manifest.json'
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=workspace.parent, prefix='.manifest-', delete=False) as output:
        temp = Path(output.name)
        json.dump(manifest, output, ensure_ascii=False, indent=2)
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def manifest(thread_id: str, execution_id: str) -> dict:
    if execution_id.startswith('legacy-'):
        root = path_utils.get_session_root(thread_id)
        files = []
        if root.is_dir():
            for child in root.iterdir():
                if child.name in ('executions','attachments') or child.name.startswith('.') or child.is_symlink(): continue
                candidates = child.rglob('*') if child.is_dir() else [child]
                for path in candidates:
                    if path.is_file() and not path.is_symlink() and path_utils.is_within(path, root):
                        files.append({**_entry(path, root), 'path':str(path), 'deleted':False})
        return {'inputs': [], 'artifacts': files, 'legacy': True}
    workspace = path_utils.get_execution_dir(thread_id, execution_id)
    target = workspace.parent / 'manifest.json'
    if not target.is_file():
        return {'inputs': [], 'artifacts': [], 'legacy': True}
    data = json.loads(target.read_text(encoding='utf-8'))
    for entry in data.get('inputs', []):
        entry['deleted'] = not (workspace / 'attachments' / entry['name']).is_file()
    for entry in data.get('artifacts', []):
        entry['path'] = str(workspace / entry['name'])
        entry['deleted'] = not Path(entry['path']).is_file()
    return data

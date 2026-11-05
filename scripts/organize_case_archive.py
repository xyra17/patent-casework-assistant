"""Prepare a verified archive and a separate catalog; keep the running service intact.

Run with PYTHONPATH=. python scripts/organize_case_archive.py --root-id ID.
The report includes prepared.env. Activate it after verification and restart the
backend in a maintenance window. Original source files and citation IDs survive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime

from dotenv import dotenv_values, load_dotenv, set_key
from casework import archive, catalog, store, sync
from casework.archive_layout import CASE_DIRECTORIES, case_year, domain_directories
from casework.domains import canonical
from utils.path_utils import PROJECT_ROOT


def prepare(root_id, target, backup):
    """Copy into staging, verify every byte, then publish the filesystem tree.

    The caller's database must be a backup, not the catalog of a running server.
    No credentials or source text are included in the manifest.
    """
    target = Path(target).absolute()
    backup = Path(backup).absolute()
    if target.is_symlink() or backup.is_symlink():
        raise ValueError('归档和备份目录不能是符号链接')
    target = target.resolve()
    backup = backup.resolve()
    if (target / '.casework-layout.json').exists():
        raise ValueError('目标已是整理后的知识库，请直接同步；不会重复覆盖现有目录')
    root = store.get_root(root_id)
    source = store.approved_path(root['path'])
    if store.identity(source) != root['identity'] or root['state'] != 'ready':
        raise ValueError('源目录须处于就绪状态且目录身份未变')
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('新旧目录不能相互包含')
    cases = {c['case_id']: c for c in store.list_cases() if c['root_id'] == root_id}
    documents = catalog.documents(root_id=root_id)
    if not documents or any(d['state'] not in ('parsed', 'inventory') for d in documents):
        raise ValueError('存在解析失败或没有可迁移文件，先核对源目录')
    if any(d['case_id'] not in cases for d in documents):
        raise ValueError('存在未关联案件的文件，先核对所号')
    if any(not c.get('technical_domain') for c in cases.values()):
        raise ValueError('存在未登记领域的案件，请先核对领域')
    discovered = set()
    for directory, subdirs, names in os.walk(source, followlinks=False):
        for name in [*subdirs, *names]:
            if (Path(directory) / name).is_symlink():
                raise ValueError('源目录含符号链接，请先核对')
        for name in names:
            if not name.startswith('.'):
                discovered.add((Path(directory) / name).relative_to(source).as_posix())
    if discovered != {d['relative_path'] for d in documents}:
        raise ValueError('实际文件清单与索引不一致，请先同步或人工整理未登记文件')
    directories = domain_directories(c['technical_domain'] for c in cases.values())
    staging = target.with_name('.' + target.name + '-staging-' + backup.name)
    if staging.exists():
        raise ValueError('暂存目录已存在，请先核对上次迁移')
    staging.mkdir(parents=True)
    manifest = []
    published = False
    moved_previous = False
    try:
        for name in directories.values():
            (staging / name).mkdir()
        (staging / '90_公司文档').mkdir()
        for case_id, case in cases.items():
            folder = staging / directories[canonical(case['technical_domain'])] / case_year(case_id) / case_id
            for name in CASE_DIRECTORIES:
                (folder / name).mkdir(parents=True, exist_ok=True)
        labels = {key: f'{index:02d}_{label}' for index, (key, label) in enumerate(archive.FOLDERS, 1)}
        paths = set()
        for doc in documents:
            case = cases[doc['case_id']]
            relative = Path(directories[canonical(case['technical_domain'])]) / case_year(doc['case_id']) / doc['case_id'] / labels[archive.folder_key(doc)] / Path(doc['relative_path']).name
            if relative.as_posix() in paths:
                raise ValueError('归档后有同名文件冲突，未覆盖原件')
            paths.add(relative.as_posix())
            raw, _ = sync.read_source(root, doc['relative_path'])
            digest = hashlib.sha256(raw).hexdigest()
            if digest != doc['revision']:
                raise ValueError('源文件已变化，请先完成同步再整理')
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError('副本校验失败')
            manifest.append({'file_id': doc['id'], 'old_path': doc['relative_path'], 'new_path': relative.as_posix(), 'sha256': digest})
        if root['classification'] == 'synthetic':
            marker = source / '.casework-demo.json'
            if marker.is_symlink():
                raise ValueError('模拟资料标记不能是符号链接')
            shutil.copy2(marker, staging / marker.name)
        (staging / '.casework-layout.json').write_text(json.dumps({'version': 1, 'domains': directories}, ensure_ascii=False, indent=2))
        backup.mkdir(parents=True, exist_ok=True)
        report = {'root_id': root_id, 'source': str(source), 'target': str(target), 'case_count': len(cases), 'file_count': len(manifest), 'domains': directories, 'files': manifest}
        (backup / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        # Optimistic checks prevent activating against a catalog changed mid-copy.
        with store.db(write=True) as conn:
            current = conn.execute('SELECT * FROM roots WHERE id=?', (root_id,)).fetchone()
            rows = [dict(r) for r in conn.execute('SELECT * FROM files WHERE root_id=? AND active=1', (root_id,))]
            if current['version'] != root['version'] or current['generation'] != root['generation'] or current['state'] != 'ready' or {(r['id'], r['revision'], r['relative_path']) for r in rows} != {(d['id'], d['revision'], d['relative_path']) for d in documents}:
                raise ValueError('复制期间目录索引变化，请重新准备')
            if store.identity(source) != root['identity']:
                raise ValueError('源目录身份发生变化')
            if target.exists():
                target.rename(backup / 'previous-knowledge-files')
                moved_previous = True
            staging.rename(target)
            published = True
            for item in manifest:
                conn.execute('UPDATE files SET relative_path=?,updated_at=? WHERE id=?', (item['new_path'], store.now(), item['file_id']))
            generation = root['generation'] + 1
            conn.execute("UPDATE roots SET path=?,identity=?,version=version+1,generation=?,mirror_state='pending' WHERE id=?", (str(target), store.identity(target), generation, root_id))
            catalog.rebuild(conn, root_id, generation)
            store.audit(conn, root_id, 'verified_archive_relocation', {'path': str(source)}, {'path': str(target), 'file_count': len(manifest), 'manifest': 'manifest.json'}, reason='按领域、案号年份和材料类别整理；逐文件SHA256一致，保留引用ID')
        return report
    except Exception:
        if published:
            target.rename(backup / 'unpublished-archive')
        if moved_previous and not target.exists():
            (backup / 'previous-knowledge-files').rename(target)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-id', required=True)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / '.env')
    source_db = store.database_path()
    backup = PROJECT_ROOT / 'data/archive-backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    target_db = PROJECT_ROOT / 'data/department' / f'casework-organized-{backup.name}.db'
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if target_db.exists():
        raise ValueError('目标目录库已存在，请先核对上次准备结果')
    with sqlite3.connect(source_db) as source, sqlite3.connect(target_db) as destination:
        source.backup(destination)
    shutil.copy2(PROJECT_ROOT / '.env', backup / 'previous.env')
    (backup / 'previous.env').chmod(0o600)
    os.environ['CASEWORK_DB_PATH'] = str(target_db)
    target = PROJECT_ROOT / '知识库文件'
    report = prepare(args.root_id, target, backup)
    previous = dotenv_values(PROJECT_ROOT / '.env')
    public = Path(previous['KNOWLEDGE_BASE_DIR'])
    if public.is_dir():
        shutil.copytree(public, backup / 'previous-company-source')
    prepared_env = backup / 'prepared.env'
    shutil.copy2(PROJECT_ROOT / '.env', prepared_env)
    roots = json.loads(previous.get('CASEWORK_ALLOWED_ROOTS') or '[]')
    values = {'CASEWORK_DB_PATH': str(target_db), 'CASEWORK_ALLOWED_ROOTS': json.dumps(list(dict.fromkeys([*roots, str(target)])), ensure_ascii=False), 'KNOWLEDGE_BASE_DIR': str(target / '90_公司文档'), 'VECTOR_STORE_DIR': str(PROJECT_ROOT / 'data/department/company-vector-store')}
    for key, value in values.items():
        set_key(str(prepared_env), key, value)
    prepared_env.chmod(0o600)
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, ensure_ascii=False, indent=2))
    print('prepared_env=' + str(prepared_env))


if __name__ == '__main__':
    main()

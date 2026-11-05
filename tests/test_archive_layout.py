"""Archive relocation preserves evidence and remains stable on the next scan."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from casework import archive, catalog, store, sync
from casework.archive_layout import CASE_DIRECTORIES, DOMAIN_DIRECTORIES, case_year
from scripts.organize_case_archive import prepare


class ArchiveLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.target = self.base / '知识库文件'
        self.target.mkdir()
        (self.target / '旧示例.txt').write_text('保留备份')
        self.env = patch.dict(os.environ, {'CASEWORK_DB_PATH': str(self.base / 'isolated.db'),
            'CASEWORK_ALLOWED_ROOTS': json.dumps([str(self.source), str(self.target)], ensure_ascii=False),
            'CASEWORK_EMBEDDING': '0', 'CASEWORK_MYSQL_MIRROR': '0'})
        self.env.start()
        for ident in ('AI-2026-032', 'AI-2025-001'):
            (self.source / f'{ident}.案件信息.json').write_text(json.dumps({'case_id': ident,
                'case_fields': {'technical_domain': '人工智能', 'title': '测试案件'}}, ensure_ascii=False))
        self.root = store.save_root('仅测试', str(self.source), 'local_only')
        self.assertEqual(sync.run(self.root['id'])['state'], 'ready')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_copy_switch_and_rescan_keep_files_cases_and_citations(self):
        before = catalog.documents()
        case_keys = {c['case_key'] for c in store.list_cases()}
        result = prepare(self.root['id'], self.target, self.base / 'backup')
        self.assertEqual(result['file_count'], 2)
        self.assertTrue((self.base / 'backup/previous-knowledge-files/旧示例.txt').is_file())
        for directory in DOMAIN_DIRECTORIES.values():
            self.assertTrue((self.target / directory).is_dir())
        for name in CASE_DIRECTORIES:
            self.assertTrue((self.target / '01_AI领域/2026/AI-2026-032' / name).is_dir())
        for item in result['files']:
            self.assertEqual(hashlib.sha256((self.target / item['new_path']).read_bytes()).hexdigest(), item['sha256'])
            self.assertTrue((self.source / item['old_path']).is_file())
        (self.target / '90_公司文档/内部手册.txt').write_text('不能重复进入案件索引')
        scanned = sync.run(self.root['id'])
        self.assertEqual(scanned['state'], 'ready', scanned)
        self.assertEqual(scanned['summary']['unchanged'], 2)
        self.assertEqual(scanned['summary']['removed'], 0)
        self.assertEqual({d['id'] for d in catalog.documents()}, {d['id'] for d in before})
        self.assertEqual({c['case_key'] for c in store.list_cases()}, case_keys)
        for doc in before:
            self.assertTrue(catalog.document(doc['id'], doc['revision'], doc['parser_key'])['current'])
        result = archive.overview(domain='AI', year='2026')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['domains'][0]['count'], 2)
        self.assertEqual(len(result['domains']), 6)
        self.assertEqual(result['domains'][1]['count'], 0)
        self.assertEqual(archive.overview(domain='无线通信')['total'], 0)
        self.assertTrue(archive.knowledge_status({'ok': False, 'state': 'empty'})['ok'])

    def test_changed_source_aborts_without_overwriting_existing_tree(self):
        next(self.source.glob('*.json')).write_text('changed')
        with self.assertRaisesRegex(ValueError, '源文件已变化'):
            prepare(self.root['id'], self.target, self.base / 'backup')
        self.assertEqual((self.target / '旧示例.txt').read_text(), '保留备份')
        self.assertEqual(store.get_root(self.root['id'])['path'], str(self.source.resolve()))

    def test_unindexed_and_symlink_files_are_not_silently_lost(self):
        extra = self.source / '未知.bin'
        extra.write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, '实际文件清单'):
            prepare(self.root['id'], self.target, self.base / 'backup')
        extra.unlink()
        extra.symlink_to(self.target / '旧示例.txt')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            prepare(self.root['id'], self.target, self.base / 'backup')

    def test_catalog_failure_restores_previous_tree_and_keeps_root(self):
        with patch.object(catalog, 'rebuild', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                prepare(self.root['id'], self.target, self.base / 'backup')
        self.assertEqual((self.target / '旧示例.txt').read_text(), '保留备份')
        self.assertEqual(store.get_root(self.root['id'])['path'], str(self.source.resolve()))
        self.assertTrue((self.base / 'backup/unpublished-archive/01_AI领域').is_dir())

    def test_publish_rename_failure_restores_previous_tree(self):
        original = Path.rename
        def fail_staging(path, destination):
            if path.name.startswith('.知识库文件-staging-'):
                raise OSError('injected rename failure')
            return original(path, destination)
        with patch.object(Path, 'rename', new=fail_staging):
            with self.assertRaisesRegex(OSError, 'injected rename failure'):
                prepare(self.root['id'], self.target, self.base / 'backup')
        self.assertEqual((self.target / '旧示例.txt').read_text(), '保留备份')
        self.assertEqual(store.get_root(self.root['id'])['path'], str(self.source.resolve()))

    def test_year_is_not_guessed_from_dates(self):
        self.assertEqual(case_year('AI-2026-032'), '2026')
        self.assertEqual(case_year('S22A101234'), '年份待核实')


if __name__ == '__main__':
    unittest.main()

"""报告正文提取后仍须向用户与内容审查提供实际 PDF 交付信息。"""
import tempfile
import unittest
from pathlib import Path

from agent.main_agent import _new_pdf_paths, _report_with_pdf_delivery


class PdfDeliveryTests(unittest.TestCase):
    def test_only_new_output_pdfs_are_announced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / 'attachments'
            inputs.mkdir()
            (inputs / '输入.pdf').write_bytes(b'%PDF-input')
            old = root / '上次.pdf'
            old.write_bytes(b'%PDF-old')
            before = {old: old.stat().st_mtime_ns}
            (root / '损坏.pdf').write_text('not a PDF')
            new = root / '报告.pdf'
            new.write_bytes(b'%PDF-new')
            (root / '输入链接.pdf').symlink_to(inputs / '输入.pdf')

            pdfs = _new_pdf_paths(root, before)
            self.assertEqual(pdfs, [new])
            report = _report_with_pdf_delivery('已核对的正文', root, pdfs)
            self.assertTrue(report.startswith('已核对的正文'))
            self.assertIn('报告.pdf', report)
            self.assertIn('附件与报告 → 生成文件', report)
            self.assertNotIn(str(root), report)
            self.assertNotIn('输入.pdf', report)
            self.assertEqual(_report_with_pdf_delivery('正文', root, []), '正文')

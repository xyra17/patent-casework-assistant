"""Coverage, source boundaries, OCR facts and real visual input contracts."""
import asyncio
import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from langchain_core.messages import AIMessage
from api import context, evidence, source_policy
from tools import mineru_parser as mineru
from tools.document_parser import parse_document
from tools.visual_document_tool import _images, analyze_visual_document


def payload(pages=1):
    return {'is_full_document': True, 'metadata': {'producer': {'version': '4.1.7'}},
            'extensions': {'mineru': {'tier': 'basic'}}, 'pages': [
                {'page_idx': n, 'blocks': [{'type': 'text', 'index': 0, 'bbox': [10, 20, 100, 40],
                                           'content': f'原件第{n+1}页文字'}]} for n in range(pages)]}


def test_partial_or_duplicate_pages_are_never_complete():
    data = payload(2)
    assert len(mineru.documents(data, 'x.pdf', 'revision', 5, 2)) == 2
    data['pages'][1]['page_idx'] = 0
    with pytest.raises(ValueError, match='页码不完整或重复'):
        mineru.documents(data, 'x.pdf', 'revision', 5, 2)
    data['is_full_document'] = False
    with pytest.raises(ValueError, match='部分解析'):
        mineru.documents(data, 'x.pdf', 'revision', 5, 2)


def test_mineru_table_and_locations_survive_adapter():
    data = payload()
    data['pages'][0]['blocks'][0].update(type='table', content='<table><tr><th>项目</th><th>数量</th></tr><tr><td>传感器</td><td>3</td></tr></table>')
    unit = mineru.documents(data, '表格.png', 'a'*64, 5, 1)[0]
    assert '传感器 | 3' in unit.page_content
    assert unit.metadata['page'] == 1
    assert unit.metadata['block'] == '0'
    assert unit.metadata['bbox'] == '[10, 20, 100, 40]'
    assert unit.metadata['revision'] == 'a'*64


def test_mixed_pdf_routes_to_mineru_instead_of_ignoring_scan(tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader
    path = tmp_path/'mixed.pdf'
    canvas = Canvas(str(path)); canvas.drawString(50, 700, 'native text'); canvas.showPage()
    picture = Image.new('RGB', (100, 100), 'white')
    canvas.drawImage(ImageReader(picture), 30, 30); canvas.save()
    from langchain_core.documents import Document
    with patch.object(mineru, 'parse_with_mineru', return_value=[
            Document(page_content='OCR typo', metadata={'page': 1}),
            Document(page_content='scan text', metadata={'page': 2})]) as call:
        units = parse_document(path)
        assert units[0].page_content == 'native text'
        assert units[1].page_content == 'scan text'
    assert call.call_args.args[-1] == 2


def test_ocr_cannot_overwrite_case_facts(tmp_path):
    from casework import parser
    from langchain_core.documents import Document
    unit = Document(page_content='代理师：张三\n实际交局日期：2026-10-01', metadata={'parser': 'mineru', 'page': 1, 'block': '0'})
    with patch.object(parser, 'parse_document', return_value=[unit]):
        result = parser.parse(b'fake image', 'AI-2026-901/附图.png')
    assert [claim['field'] for claim in result['claims']] == ['case_id']
    assert result['document_locations'][0]['parser'] == 'mineru'
    assert result['warnings']
    assert '张三' in result['chunks'][0]['text']


@pytest.fixture
def session(tmp_path):
    directory = tmp_path/'attachments'; directory.mkdir()
    context.set_session('multimodal-unit', str(tmp_path)); context.reset_tool_budget()
    source_policy.configure([]); evidence.reset('multimodal-unit')
    image = Image.new('RGB', (120, 80), 'white'); image.save(directory/'图.png')
    yield directory
    context.reset_session(); source_policy.reset_policy(); evidence.clear('multimodal-unit')


def test_vision_sends_image_blocks_and_registers_nonliteral_evidence(session):
    fake = AsyncMock(); fake.ainvoke.return_value = AIMessage(content='第1页可见箭头从A指向B。')
    with patch('casework.privacy.ensure_model_allowed'), patch('agent.llm.get_llm', return_value=fake):
        result = json.loads(asyncio.run(analyze_visual_document.ainvoke({'filename': '图.png', 'instruction': '说明方向'})))
    blocks = fake.ainvoke.call_args.args[0][1].content
    assert any(block.get('type') == 'image_url' and block['image_url']['url'].startswith('data:image/png;base64,') for block in blocks)
    assert result['pages_read'] == [1] and result['complete_document']
    proof = evidence.snapshot('multimodal-unit')[0]
    assert proof['visual_observation'] and not proof['content_complete']
    assert proof['revision'] == hashlib.sha256((session/'图.png').read_bytes()).hexdigest()


def test_read_tool_exposes_ocr_page_method_to_the_answering_model(session):
    from langchain_core.documents import Document
    from tools.upload_file_read_tool import read_file_content
    units = [Document(page_content='部件数量3', metadata={'page': 1, 'parser': 'mineru', 'parse_mode': 'ocr'})]
    with patch('tools.document_parser.parse_document', return_value=units):
        result = read_file_content.invoke({'filename': '图.png'})
    assert '第1页：MinerU OCR与版面识别' in result
    assert evidence.snapshot('multimodal-unit')[0]['document_locations'][0]['parse_mode'] == 'ocr'


def test_privacy_and_source_denial_precede_loading_pixels(session):
    with patch('casework.privacy.ensure_model_allowed', side_effect=PermissionError('内网禁止外发')), patch('tools.visual_document_tool._load') as load:
        result = asyncio.run(analyze_visual_document.ainvoke({'filename': '图.png', 'instruction': '看图'}))
        assert '内网禁止外发' in result
        load.assert_not_called()
    with patch('casework.privacy.ensure_model_allowed'), patch('agent.llm.get_llm') as llm:
        result = asyncio.run(analyze_visual_document.ainvoke({'file_id': 'archive', 'instruction': '看图'}))
        assert '未开放该来源' in result
        llm.assert_not_called()


def test_visual_pdf_limits_and_actual_page_coverage(tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    path = tmp_path/'seven.pdf'; canvas = Canvas(str(path))
    for i in range(7): canvas.drawString(30, 700, str(i)); canvas.showPage()
    canvas.save(); raw = path.read_bytes()
    _, selected, total = _images(raw, '.pdf', None)
    assert selected == [1, 2, 3, 4, 5, 6] and total == 7
    for pages in ([0], [8], [1, 1], list(range(1, 8))):
        with pytest.raises(ValueError): _images(raw, '.pdf', pages)
    _, selected, total = _images(raw, '.pdf', [7])
    assert selected == [7] and total == 7

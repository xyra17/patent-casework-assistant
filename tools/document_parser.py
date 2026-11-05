"""保留来源定位的文档解析；扫描件、图片和复杂PDF由本地MinerU解析。"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from langchain_core.documents import Document

PARSER_VERSION = 5


def document_format(path: Path) -> str:
    """Recognize mislabeled Office files; never execute macros or fetch converters."""
    if path.suffix.lower() in ('.doc', '.docx', '.xlsx') and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as package:
            entries = package.infolist()
            if len(entries) > 10000 or sum(i.file_size for i in entries) > 100 * 1024 * 1024:
                raise ValueError('Office文档解压体积超过解析上限')
            names = {i.filename for i in entries}
        if 'word/document.xml' in names:
            return '.docx'
        if 'xl/workbook.xml' in names:
            return '.xlsx'
    return path.suffix.lower()


def convert_legacy_word(path: Path, directory: Path) -> Path:
    """Optional local conversion only. Originals are never changed."""
    libreoffice = shutil.which('soffice') or shutil.which('libreoffice')
    if not libreoffice and Path('/Applications/LibreOffice.app/Contents/MacOS/soffice').is_file():
        libreoffice = '/Applications/LibreOffice.app/Contents/MacOS/soffice'
    target = directory / (path.stem + '.docx')
    if libreoffice:
        command = [libreoffice, '-env:UserInstallation=' + (directory / 'profile').as_uri(),
                   '--headless', '--convert-to', 'docx', '--outdir', str(directory), str(path.resolve())]
    elif shutil.which('textutil'):
        command = [shutil.which('textutil'), '-convert', 'docx', '-output', str(target), str(path.resolve())]
    else:
        raise ValueError('旧版二进制DOC需要本机LibreOffice或textutil；尚未解析，未上传外部转换服务')
    try:
        subprocess.run(command, cwd=directory, timeout=45, check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('本机DOC转换失败，请人工另存为DOCX') from exc
    if not target.is_file() or document_format(target) != '.docx':
        raise ValueError('本机DOC转换未生成可解析的DOCX')
    return target


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', errors='replace')


def parse_document(path: Path, source: str | None = None) -> list[Document]:
    source = source or path.name
    revision = hashlib.sha256(path.read_bytes()).hexdigest()
    units = []

    def add(text, **location):
        if text.strip():
            units.append(Document(page_content=text.strip(), metadata={
                'source': source, 'revision': revision, 'parser_version': PARSER_VERSION, **location}))

    suffix = document_format(path)
    if suffix == '.doc':
        with tempfile.TemporaryDirectory(prefix='local-doc-') as temporary:
            converted = convert_legacy_word(path, Path(temporary))
            parsed = parse_document(converted, source)
            for unit in parsed:
                unit.metadata.update(revision=revision, conversion='local-doc-to-docx')
            return parsed
    if suffix == '.vsdx':
        from tools.visio_parser import shape_texts
        for text, metadata in shape_texts(path):
            add(text, **metadata)
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        mode = os.getenv('DOCUMENT_PDF_PARSER', 'auto')
        if mode not in ('auto', 'native', 'mineru'):
            raise ValueError('DOCUMENT_PDF_PARSER须为auto/native/mineru')
        layout_pages = set()
        for page, item in enumerate(reader.pages, 1):
            text = item.extract_text() or ''
            add(text, page=page)
            resources = item.get('/Resources')
            resources = resources.get_object() if resources else {}
            # Embedded pictures, textless pages and aligned columns require layout/OCR.
            layout = item.extract_text(extraction_mode='layout') or ''
            stream = item.get_contents()
            drawn_without_text = not text.strip() and stream is not None and bool(re.search(rb'\b(?:Do|Tj|TJ|f|S|B)\b', stream.get_data()))
            if drawn_without_text or bool(resources.get('/XObject')) or len(re.findall(r'\S {3,}\S', layout)) >= 3:
                layout_pages.add(page)
        if mode == 'mineru' or mode == 'auto' and layout_pages:
            from tools.mineru_parser import parse_with_mineru
            parsed = parse_with_mineru(path, source, revision, PARSER_VERSION, len(reader.pages))
            if mode == 'auto':
                plain = [unit for unit in units if unit.metadata['page'] not in layout_pages]
                plain_pages = {unit.metadata['page'] for unit in plain}
                for unit in plain:
                    unit.metadata.update(parser='pypdf', full_document=True, page_count=len(reader.pages))
                parsed = sorted(plain + [unit for unit in parsed if unit.metadata['page'] not in plain_pages],
                                key=lambda unit: unit.metadata['page'])
            return parsed
        if mode == 'native' and layout_pages:
            raise ValueError('原生PDF模式不能保证扫描件或复杂版面完整，请改用auto或mineru模式')
    elif suffix in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}:
        from PIL import Image
        from tools.mineru_parser import parse_with_mineru
        with Image.open(path) as image:
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('多帧图片请拆成单页图片或PDF后解析，未只取首帧当全量')
        return parse_with_mineru(path, source, revision, PARSER_VERSION, 1)
    elif suffix == '.docx':
        from docx import Document as WordDocument
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document, section = WordDocument(path), '正文'
        # 遍历正文 XML 顺序，避免先收段落再收表格造成上下文错位。
        for block, element in enumerate(document.element.body, 1):
            if element.tag.endswith('}p'):
                paragraph = Paragraph(element, document)
                if paragraph.style and (paragraph.style.name.startswith(('Heading', '标题'))):
                    section = paragraph.text.strip() or section
                add(paragraph.text, section=section, block=block)
            elif element.tag.endswith('}tbl'):
                table = Table(element, document)
                text = '\n'.join(' | '.join(cell.text.replace('\n', ' / ') for cell in row.cells) for row in table.rows)
                add(text, section=section, block=block, content_type='table')
    elif suffix in ('.xlsx', '.xls'):
        import pandas as pd
        for sheet, frame in pd.read_excel(path, sheet_name=None).items():
            add(frame.to_csv(index=False), sheet=str(sheet))
    elif suffix in ('.txt', '.md'):
        section, lines, start = '正文', [], 1
        for number, line in enumerate(read_text(path).splitlines(), 1):
            if re.match(r'^\s*(#{1,6}\s+|第[一二三四五六七八九十百\d]+[章节])', line):
                if lines:
                    add('\n'.join(lines), section=section, line=start)
                section, lines, start = line.lstrip('# ').strip(), [], number
            lines.append(line)
        if lines:
            add('\n'.join(lines), section=section, line=start)
    else:
        raise ValueError(f'不支持的文档格式：{suffix}')
    return units


def location_label(metadata: dict) -> str:
    parts = []
    if metadata.get('page'):
        parts.append(f"第 {metadata['page']} 页")
    if metadata.get('section'):
        parts.append(str(metadata['section']))
    if metadata.get('shape_id'):
        parts.append('形状 '+str(metadata['shape_id']))
    if metadata.get('sheet'):
        parts.append(f"工作表 {metadata['sheet']}")
    if metadata.get('block'):
        parts.append(f"正文块 {metadata['block']}")
    if metadata.get('bbox'):
        parts.append(f"区域 {metadata['bbox']}")
    if metadata.get('content_type')=='table':
        parts.append('表格')
    if metadata.get('line'):
        parts.append(f"起始行 {metadata['line']}")
    return ' · '.join(parts) or '旧索引无定位信息，请更新索引'

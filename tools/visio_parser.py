"""Read local VSDX shape text, without rendering or interpreting geometry."""
from __future__ import annotations

import posixpath
import zipfile
from xml.etree import ElementTree as ET

REL_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
SCOPE = '仅提取图中文字；未解析图形、连线关系、嵌入图片或模板继承内容，不能据此认定结构关系。'


def shape_texts(path):
    with zipfile.ZipFile(path) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(p.file_size for p in entries) > 100 * 1024 * 1024:
            raise ValueError('Visio文档解压体积超过解析上限')
        names = {p.filename for p in entries}
        if not {'visio/document.xml', 'visio/pages/pages.xml'} <= names:
            raise ValueError('文件不是可识别的VSDX绘图包')

        def xml(name):
            raw = package.read(name)
            # No DTDs, external entities or network relationships are needed for text.
            if len(raw) > 8 * 1024 * 1024 or b'<!doctype' in raw.lower().replace(b'\x00', b''):
                raise ValueError('Visio XML包含不支持的声明或超过解析上限')
            return ET.fromstring(raw)

        relationships = {}
        relfile = 'visio/pages/_rels/pages.xml.rels'
        if relfile in names:
            for rel in xml(relfile):
                if rel.get('TargetMode', '').lower() == 'external':
                    continue
                target = rel.get('Target', '')
                resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('visio/pages', target))
                if resolved.startswith('visio/pages/') and resolved in names:
                    relationships[rel.get('Id')] = resolved
        pages = xml('visio/pages/pages.xml').findall('{*}Page')
        if len(pages) > 1000:
            raise ValueError('Visio页数超过解析上限')
        result = []
        total = 0
        for number, page in enumerate(pages, 1):
            rel = page.find('{*}Rel')
            target = relationships.get(rel.get(REL_ID)) if rel is not None else None
            if not target:
                raise ValueError('Visio页面关联无法解析，未猜测页面文件')
            page_xml = xml(target)
            for shape in page_xml.iterfind('.//{*}Shape'):
                text_node = shape.find('{*}Text')
                if text_node is None:
                    continue
                text = ''.join(text_node.itertext()).strip()
                if not text:
                    continue
                total += len(text)
                if total > 2_000_000 or len(result) >= 10000:
                    raise ValueError('Visio图中文字超过解析上限')
                result.append((text, {'page': number, 'section': page.get('Name') or page.get('NameU') or '绘图',
                                     'shape_id': shape.get('ID', '?'), 'content_type': 'drawing_text'}))
        return result

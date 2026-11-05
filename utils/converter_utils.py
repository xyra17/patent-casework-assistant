"""跨平台 Markdown → PDF；Windows 可显式选用 Word COM。"""
from __future__ import annotations

import os
import tempfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import markdown


def md_to_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return '<html><head><meta charset="utf-8"></head><body>' + body + '</body></html>'


def html_to_pdf(html_text: str, pdf_path: Path) -> Path:
    """保留原 Windows Word COM 转换入口，正确初始化工作线程 COM。"""
    import pythoncom
    import win32com.client

    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_text)
        html_file = tmp.name
    word = doc = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(html_file)
        doc.SaveAs(str(pdf_path), FileFormat=17)
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        finally:
            try:
                if word is not None:
                    word.Quit()
            finally:
                pythoncom.CoUninitialize()
                Path(html_file).unlink(missing_ok=True)
    return pdf_path


class _Blocks(HTMLParser):
    """读取 Markdown 生成的 HTML 块，保留标题、段落、列表和表格。"""
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.tag = "p"
        self.parts = []
        self.table = None
        self.row = None
        self.ignored = 0

    def flush(self):
        text = "".join(self.parts).strip()
        if text:
            self.blocks.append((self.tag, text))
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "script", "style"):
            self.ignored += 1
        if self.ignored:
            return
        if tag == "table":
            self.flush()
            self.table = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.parts = []
        elif tag in ("h1", "h2", "h3", "h4", "p", "li", "pre") and self.table is None:
            self.flush()
            self.tag = tag
        elif tag == "br":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("head", "script", "style"):
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in ("td", "th") and self.row is not None:
            self.row.append("".join(self.parts).strip())
            self.parts = []
        elif tag == "tr" and self.table is not None:
            self.table.append(self.row or [])
        elif tag == "table":
            self.blocks.append(("table", self.table or []))
            self.table = self.row = None
        elif tag in ("h1", "h2", "h3", "h4", "p", "li", "pre") and self.table is None:
            self.flush()


def convert_markdown_to_pdf(md_text: str, pdf_path: Path) -> Path:
    """默认使用 ReportLab，支持 Mac/Linux/Windows，无需安装 Word。"""
    pdf_path = Path(pdf_path).resolve()
    if os.getenv("PDF_BACKEND", "reportlab").lower() == "word":
        return html_to_pdf(md_to_html(md_text), pdf_path)
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = {}
    for name, size in (("p", 11), ("h1", 20), ("h2", 16), ("h3", 13), ("h4", 12), ("li", 11), ("pre", 9)):
        styles[name] = ParagraphStyle(name, fontName="STSong-Light", fontSize=size,
                                     leading=size * 1.6, wordWrap="CJK", spaceAfter=8, keepWithNext=name.startswith("h"))
    parser = _Blocks()
    parser.feed(md_to_html(md_text))
    parser.flush()
    story = []
    for tag, content in parser.blocks:
        if tag == "table":
            if not content:
                continue
            columns = max(map(len, content))
            cells = [[Paragraph(escape(cell).replace("\n", "<br/>"), styles["p"]) for cell in row]
                     + [""] * (columns - len(row)) for row in content]
            table = Table(cells, colWidths=[483 / columns] * columns, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([table, Spacer(1, 10)])
        else:
            text = ("- " if tag == "li" else "") + escape(content).replace("\n", "<br/>")
            story.append(Paragraph(text, styles.get(tag, styles["p"])))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    # 在同目录生成临时文件；失败时不留下半个 PDF 冒充成功产物。
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=pdf_path.parent)
    os.close(fd)
    try:
        def page_number(canvas, document):
            canvas.saveState()
            canvas.setFont("STSong-Light", 9)
            canvas.setFillColor(colors.HexColor("#666666"))
            canvas.drawRightString(document.pagesize[0] - 56, 30, f"{document.page}")
            canvas.restoreState()
        SimpleDocTemplate(tmp, leftMargin=56, rightMargin=56).build(
            story, onFirstPage=page_number, onLaterPages=page_number)
        os.replace(tmp, pdf_path)
        # mkstemp 默认 0600；改回普通只读文件权限，便于同机部署/静态下载。
        try:
            os.chmod(pdf_path, 0o644)
        except OSError:
            pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return pdf_path

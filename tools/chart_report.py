"""根据任务证据中的图表规格生成可下载 PDF（ReportLab 原生图表，无需 matplotlib）。"""
from __future__ import annotations

import os
from datetime import datetime
from xml.sax.saxutils import escape

from tools.chart_utils import build_chart_items
from pathlib import Path

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT = "STSong-Light"


def _styles():
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
    return {
        "title": ParagraphStyle("title", fontName=_FONT, fontSize=20, leading=28, spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName=_FONT, fontSize=14, leading=20, spaceBefore=12, spaceAfter=6),
        "p": ParagraphStyle("p", fontName=_FONT, fontSize=10, leading=16, spaceAfter=6, wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName=_FONT, fontSize=8.5, leading=13, textColor=colors.HexColor("#555555")),
    }


def _chart_drawing(spec: dict) -> Drawing | None:
    if spec.get("type") == "heatmap" and spec.get("heatmap"):
        heatmap = spec["heatmap"]
        xs, ys, cells = heatmap["xAxis"], heatmap["yAxis"], heatmap["data"]
        drawing = Drawing(480, 280)
        left, bottom, width, height = 85, 75, 380, 175
        cw, ch = width / len(xs), height / len(ys)
        values = [float(cell[2]) for cell in cells]
        low, high = min(values + [0]), max(values + [1])
        unit = (spec.get("series") or [{}])[0].get("unit") or ""
        for x, y, value in cells:
            strength = (float(value) - low) / (high - low)
            fill = colors.Color(0.9 - strength * .65, .95 - strength * .5, 1 - strength * .25)
            px, py = left + x * cw, bottom + y * ch
            drawing.add(Rect(px, py, cw, ch, fillColor=fill, strokeColor=colors.white))
            drawing.add(String(px + cw / 2, py + ch / 2 - 3, f"{value}{unit}",
                               fontName=_FONT, fontSize=8, textAnchor="middle"))
        for i, label in enumerate(xs):
            drawing.add(String(left + (i + .5) * cw, bottom - 12, str(label),
                               fontName=_FONT, fontSize=7, textAnchor="end", angle=35))
        for i, label in enumerate(ys):
            drawing.add(String(left - 5, bottom + (i + .5) * ch - 3, str(label),
                               fontName=_FONT, fontSize=8, textAnchor="end"))
        return drawing
    x_axis = [str(item) for item in (spec.get("xAxis") or [])]
    series = spec.get("series") or []
    if not x_axis or not series:
        return None
    data = []
    for item in series:
        values = []
        for value in item.get("data") or []:
            try:
                values.append(float(value or 0))
            except (TypeError, ValueError):
                values.append(0)
        data.append(values)
    if not data:
        return None
    drawing = Drawing(480, 235)
    chart_kind = spec.get("type") or "bar"
    if chart_kind in ("pie", "donut"):
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 110, 24, 280, 170
        pie.data = data[0]
        pie.labels = x_axis
        pie.slices.fontName = _FONT
        pie.slices.fontSize = 6.5
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1
        drawing.add(pie)
        return drawing
    if chart_kind == "line" or chart_kind == "area":
        chart = HorizontalLineChart()
    elif chart_kind == "hbar":
        chart = HorizontalBarChart()
    else:
        chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 42, 45, 410, 165
    chart.data = data
    chart.categoryAxis.categoryNames = x_axis
    chart.categoryAxis.labels.fontName = _FONT
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.angle = 30 if len(x_axis) > 8 else 0
    chart.valueAxis.labels.fontName = _FONT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    drawing.add(chart)
    return drawing


def build_chart_report(run: dict, output_path: Path) -> Path:
    evidence = run.get("evidence") or []
    charts = build_chart_items(evidence, run.get("execution_query") or run.get("task_query") or run.get("query", ""))
    if not charts:
        raise ValueError("本次任务没有可导出的图表数据")
    styles = _styles()
    applied = next((revision for revision in run.get('intent_revisions', [])
                    if revision.get('id') == run.get('applied_intent_id')), None)
    display_query = applied['revised_query'] if applied else run.get('query', '')
    story = [
        Paragraph("数据图表报告", styles["title"]),
        Paragraph(f"任务：{escape(str(display_query))}", styles["p"]),
        Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["small"]),
        Spacer(1, 8),
    ]
    for index, item in enumerate(charts, 1):
        spec = item["chart"]
        story.append(Paragraph(f"{index}. {escape(str(spec.get('title') or item.get('title') or '数据图表'))}", styles["h2"]))
        if item.get("sql"):
            story.append(Paragraph(f"SQL：{escape(str(item['sql']))}", styles["small"]))
        drawing = _chart_drawing(spec)
        if drawing is not None:
            story.append(drawing)
        series_names = "、".join(str(s.get("name", "")) + (f"（{s['unit']}）" if s.get("unit") else "") for s in spec.get("series") or [])
        story.append(Paragraph(f"指标：{escape(series_names)}；返回 {item.get('rowCount', 0)} 行", styles["small"]))
        sources = "、".join(spec.get("sourceIds") or [])
        story.append(Paragraph(f"来源证据：{escape(sources)}", styles["small"]))
        rows = spec.get("sourceRows") or item.get("rows") or []
        columns = [(f.get("label") or f["name"]) + (f"（{f['unit']}）" if f.get("unit") else "") for f in spec.get("fields", [])] if spec.get("sourceRows") else item.get("columns") or []
        if rows and columns:
            story.append(Paragraph(f"图表数据预览（前 {min(len(rows), 8)} 行）", styles["small"]))
            table_data = [[Paragraph(escape(str(cell)), styles["small"]) for cell in row] for row in [columns, *rows[:8]]]
            table = Table(table_data, colWidths=[480 / len(columns)] * len(columns), repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.extend([Spacer(1, 5), table])
        if index < len(charts):
            story.append(PageBreak())
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=48, rightMargin=48, topMargin=44, bottomMargin=44)
    doc.build(story)
    try:
        os.chmod(output_path, 0o644)
    except OSError:
        pass
    return output_path

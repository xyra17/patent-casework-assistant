"""Render the README's compact, source-editable architecture figures.

The SVG files are the publication assets. No font or rendering package is
needed to regenerate them: ``python scripts/render_architecture_figures.py``.
"""
from __future__ import annotations

from html import escape
from pathlib import Path


OUT = Path(__file__).resolve().parents[1] / "docs" / "diagrams"
INK = "#243247"
MUTED = "#526276"
LINE = "#8996a5"
BLUE = "#34688d"
GREEN = "#4c8069"
VIOLET = "#76679b"
AMBER = "#9b7541"


class Figure:
    def __init__(self, width: int, height: int, number: str, title: str, subtitle: str):
        self.width = width
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#526276" stroke-width="1.6"/></marker></defs>',
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
            '<g font-family="Inter, Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif" fill="#243247">',
        ]
        self.text(46, 43, f"FIGURE {number}  /  {title}", 27, bold=True)
        self.text(46, 72, subtitle, 16, color=MUTED)
        self.path([(46, 88), (width - 46, 88)], stroke="#c9d2dc", arrow=False, width=1.5)

    def text(self, x, y, value, size=19, *, bold=False, color=INK, anchor="start"):
        weight = "650" if bold else "400"
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{escape(value)}</text>'
        )

    def box(self, x, y, w, h, title, detail=(), *, accent=BLUE, fill="#ffffff", title_size=20):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" stroke="{accent}" stroke-width="1.7"/>'
        )
        self.parts.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" fill="{accent}"/>')
        lines = tuple(detail) if not isinstance(detail, str) else (detail,)
        if lines:
            self.text(x + 18, y + 31, title, title_size, bold=True)
            for i, line in enumerate(lines):
                self.text(x + 18, y + 58 + 24 * i, line, 16, color=MUTED)
        else:
            self.text(x + 18, y + h / 2 + 7, title, title_size, bold=True)

    def frame(self, x, y, w, h, title, *, accent, fill):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{accent}" stroke-width="1.5" stroke-dasharray="7 5"/>'
        )
        self.text(x + 18, y + 32, title, 20, bold=True, color=accent)

    def path(self, points, *, stroke=MUTED, arrow=True, dashed=False, width=2):
        value = " ".join(f"{x},{y}" for x, y in points)
        attrs = ' marker-end="url(#arrow)"' if arrow else ""
        if dashed:
            attrs += ' stroke-dasharray="7 6"'
        self.parts.append(
            f'<polyline points="{value}" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"{attrs}/>'
        )

    def tag(self, x, y, label, *, color=MUTED):
        self.text(x, y, label, 15, color=color)

    def save(self, name):
        self.parts.append("</g></svg>")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text("\n".join(self.parts) + "\n", encoding="utf-8")


def overview():
    f = Figure(1440, 830, "01", "系统架构", "任务入口、执行路径与共同的证据交付边界")
    f.frame(46, 112, 1348, 133, "交互与服务", accent=BLUE, fill="#f6f9fc")
    f.box(78, 163, 220, 59, "代理师 / 管理者", accent=BLUE)
    f.box(369, 163, 252, 59, "Vue 3 工作台", accent=BLUE)
    f.box(692, 163, 260, 59, "FastAPI 服务", accent=BLUE)
    f.box(1023, 163, 322, 59, "会话 · 文件 · 案件接口", accent=BLUE)
    for x1, x2 in [(298, 369), (621, 692), (952, 1023)]:
        f.path([(x1, 193), (x2, 193)])

    f.frame(46, 272, 1348, 370, "任务执行", accent=VIOLET, fill="#faf9fc")
    f.box(78, 332, 246, 88, "任务路由", ("来源权限与任务要求", "固定契约覆盖判断"), accent=VIOLET)
    f.frame(390, 307, 421, 295, "A  固定业务流程", accent=GREEN, fill="#f5faf7")
    f.box(417, 357, 367, 74, "受限参数与业务动作", ("案件信息 · 会议 · 月报 · 推荐",), accent=GREEN)
    f.box(417, 463, 367, 74, "程序查询与业务核验", ("多来源、多步骤；结果按契约检查",), accent=GREEN)
    f.frame(855, 307, 507, 295, "B  DeepAgents 编排", accent=VIOLET, fill="#f8f6fc")
    f.box(882, 357, 453, 74, "TaskPlan 与受控工具", ("数据库 / 知识库 / 联网 · 附件",), accent=VIOLET)
    f.box(882, 463, 453, 74, "观察证据与有界修正", ("缺口补查 · 五项内容审查",), accent=VIOLET)
    f.path([(822, 222), (822, 260), (201, 260), (201, 332)])
    f.path([(324, 377), (354, 377), (354, 394), (417, 394)])
    f.path([(324, 404), (351, 404), (351, 293), (835, 293), (835, 394), (882, 394)])
    f.path([(600, 431), (600, 463)])
    f.path([(1108, 431), (1108, 463)])
    f.tag(348, 362, "契约覆盖", color=GREEN)
    f.tag(740, 287, "超出契约", color=VIOLET)

    f.frame(46, 668, 1348, 118, "数据与交付", accent=AMBER, fill="#fbfaf7")
    f.box(75, 715, 355, 53, "只读原件 · SQLite · MySQL · 本地索引", accent=AMBER, title_size=17)
    f.box(516, 715, 330, 53, "统一证据：来源 / 位置 / 版本", accent=AMBER, title_size=18)
    f.box(930, 715, 433, 53, "回答 · 图表 · Markdown / PDF · 历史", accent=AMBER, title_size=18)
    f.path([(600, 537), (600, 659), (681, 659), (681, 715)])
    f.path([(1108, 537), (1108, 658), (681, 658)], arrow=False)
    f.path([(430, 742), (516, 742)])
    f.path([(846, 742), (930, 742)])
    f.tag(48, 812, "实线：执行或数据流    虚线框：职责边界    原件读取受来源权限与文件版本约束")
    f.save("01-system-architecture.svg")


def routing():
    f = Figure(1440, 900, "02", "任务路由与结果核验", "来源约束先于执行路径；固定流程与 Agent 共享证据边界")
    f.box(53, 133, 315, 78, "用户任务", ("问题 · 来源 · 附件 · 交付要求",), accent=BLUE)
    f.box(439, 133, 315, 78, "建立权限上限", ("禁用来源不得被后续路径打开",), accent=BLUE)
    f.box(825, 133, 563, 78, "判断固定契约能否完整覆盖", ("包括筛选条件、业务动作和产物要求",), accent=BLUE)
    f.path([(368, 172), (439, 172)])
    f.path([(754, 172), (825, 172)])

    f.frame(53, 269, 635, 425, "A  固定业务流程", accent=GREEN, fill="#f5faf7")
    f.box(83, 332, 575, 78, "编译受限参数", ("实体、字段、时间范围、查询动作",), accent=GREEN)
    f.box(83, 453, 575, 78, "执行既有查询与计算", ("案件信息、会议、统计、案卷与负荷",), accent=GREEN)
    f.box(83, 574, 575, 78, "程序事实与交付契约核验", ("冲突和未知单列；不可用推测补位",), accent=GREEN)
    f.path([(371, 410), (371, 453)])
    f.path([(371, 531), (371, 574)])

    f.frame(752, 269, 635, 425, "B  Agent 动态执行", accent=VIOLET, fill="#f8f6fc")
    f.box(782, 332, 575, 78, "TaskPlan 与工具装配", ("仅开放允许来源和当前任务工具",), accent=VIOLET)
    f.box(782, 453, 575, 78, "查询、检索、读取与补查", ("依据结果决定下一步；有轮次和回读预算",), accent=VIOLET)
    f.box(782, 574, 575, 78, "硬检查、复算与内容审查", ("定位未通过项，有进展时有界修正",), accent=VIOLET)
    f.path([(1069, 410), (1069, 453)])
    f.path([(1069, 531), (1069, 574)])
    f.path([(1357, 615), (1405, 615), (1405, 493), (1357, 493)], dashed=True)
    f.tag(1263, 552, "有界修正", color=VIOLET)
    f.path([(1106, 211), (1106, 246), (371, 246), (371, 332)])
    f.path([(1106, 246), (1069, 246), (1069, 332)])
    f.tag(548, 244, "完整覆盖", color=GREEN)
    f.tag(1120, 248, "超出契约", color=VIOLET)

    f.frame(53, 720, 1334, 126, "共同出口", accent=AMBER, fill="#fbfaf7")
    f.box(85, 767, 412, 54, "来源编号 · 原文位置 · 版本", accent=AMBER, title_size=18)
    f.box(566, 767, 382, 54, "通过：保存结果与执行历史", accent=AMBER, title_size=18)
    f.box(1017, 767, 340, 54, "未通过：部分结果与缺项", accent=AMBER, title_size=18)
    f.path([(371, 652), (371, 708), (291, 708), (291, 767)])
    f.path([(1069, 652), (1069, 708), (291, 708)], arrow=False)
    f.path([(497, 794), (566, 794)])
    f.path([(948, 794), (1017, 794)], dashed=True)
    f.tag(53, 877, "路径选择取决于契约覆盖，不取决于来源数量；固定流程也可多来源、多步骤。")
    f.save("02-routing-and-execution.svg")


def retrieval():
    f = Figure(1440, 930, "03", "文件解析与检索", "两条文档路径独立发布、检索；输出均绑定可回读的原文版本")
    f.frame(48, 113, 1344, 168, "共享输入：按来源隔离", accent=BLUE, fill="#f6f9fc")
    f.box(77, 164, 380, 86, "案件共享盘原件", ("白名单目录；安全读取与增量扫描",), accent=BLUE)
    f.box(532, 164, 380, 86, "解析与版本", ("原生文本；扫描件按需本地 MinerU",), accent=BLUE)
    f.box(987, 164, 375, 86, "长期文档入口", ("独立上传、发布与适用版本管理",), accent=BLUE)
    f.path([(457, 207), (532, 207)])

    f.frame(48, 315, 650, 501, "路径 A  案件信息与案卷正文", accent=GREEN, fill="#f5faf7")
    f.box(78, 373, 590, 85, "结构化字段", ("候选值、冲突、人工确认 → SQLite 发布 / MySQL 投影",), accent=GREEN)
    f.box(78, 490, 590, 85, "正文分块与本地检索", ("Qwen3 向量 + BM25 + 技术别名",), accent=GREEN)
    f.box(78, 607, 590, 85, "按案归并与条件重排", ("正向机制核对；bge-reranker-base 按需使用",), accent=GREEN)
    f.box(78, 724, 590, 60, "案件命中 · 原文片段 · 同案附件", accent=GREEN, title_size=19)
    f.path([(702, 250), (702, 302), (373, 302), (373, 373)])
    f.path([(373, 458), (373, 490)])
    f.path([(373, 575), (373, 607)])
    f.path([(373, 692), (373, 724)])

    f.frame(742, 315, 650, 501, "路径 B  普通知识库", accent=VIOLET, fill="#f8f6fc")
    f.box(772, 373, 590, 85, "文档解析、分块与发布", ("原件和索引分离；保留版本与适用范围",), accent=VIOLET)
    f.box(772, 490, 590, 85, "本地混合召回", ("Qwen3 / Chroma + BM25 → RRF 融合",), accent=VIOLET)
    f.box(772, 607, 590, 85, "覆盖检查与有限补查", ("词元信号轻量重排；记录缺口与停止原因",), accent=VIOLET)
    f.box(772, 724, 590, 60, "知识片段 · 原文位置 · 索引版本", accent=VIOLET, title_size=19)
    f.path([(1174, 250), (1174, 373)])
    f.path([(1067, 458), (1067, 490)])
    f.path([(1067, 575), (1067, 607)])
    f.path([(1067, 692), (1067, 724)])
    f.path([(1362, 648), (1410, 648), (1410, 528), (1362, 528)], dashed=True)
    f.tag(1274, 594, "预算内补查", color=VIOLET)

    f.path([(373, 784), (373, 847), (720, 847)], arrow=False)
    f.path([(1067, 784), (1067, 847), (720, 847)], arrow=False)
    f.box(499, 845, 442, 56, "共同证据出口：来源 / 位置 / 版本", accent=AMBER, title_size=18)
    f.tag(48, 921, "边界：OCR 识字不等于图形语义理解；纯图形需视觉工具，识别结果仍需核对原件。")
    f.save("03-ingestion-and-retrieval.svg")


if __name__ == "__main__":
    overview()
    routing()
    retrieval()
    print("Rendered 3 SVG architecture figures in", OUT)

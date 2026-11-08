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
        self.parts.append('<g data-node="true">')
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" stroke="{accent}" stroke-width="1.7"/>'
        )
        self.parts.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" fill="{accent}"/>')
        lines = tuple(detail) if not isinstance(detail, str) else (detail,)
        if lines:
            self.text(x + 18, y + 31, title, title_size, bold=True)
            for i, line in enumerate(lines):
                self.text(x + 18, y + 55 + 22 * i, line, 16, color=MUTED)
        else:
            self.text(x + 18, y + h / 2 + 7, title, title_size, bold=True)
        self.parts.append('</g>')

    def frame(self, x, y, w, h, title, *, accent, fill):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{accent}" stroke-width="1.5" stroke-dasharray="7 5"/>'
        )
        self.text(x + 18, y + 32, title, 20, bold=True, color=accent)

    def path(self, points, *, stroke=MUTED, arrow=True, dashed=False, width=2, both=False):
        value = " ".join(f"{x},{y}" for x, y in points)
        attrs = ' marker-end="url(#arrow)"' if arrow else ""
        if both:
            attrs += ' marker-start="url(#arrow)"'
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


def decision(f, cx, cy, w, h, title, detail="", accent=VIOLET):
    f.parts.append(f'<polygon points="{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}" fill="white" stroke="{accent}" stroke-width="1.7"/>')
    f.text(cx, cy-2 if detail else cy+6, title, 19, bold=True, anchor="middle")
    if detail:
        f.text(cx, cy+23, detail, 15, color=MUTED, anchor="middle")


def business():
    f = Figure(1600, 1080, "00", "业务全景与人机协作", "案件查阅、会前准备、期限排查、月报与分案参考")
    f.frame(50, 120, 1500, 135, "使用者与工作目标", accent=BLUE, fill="#f6f9fc")
    f.box(100, 170, 600, 65, "代理师 / 业务人员", ("按案查阅资料，准备会议与后续办理",))
    f.box(900, 170, 600, 65, "部门负责人 / 管理者", ("掌握案件进度、计划期限与登记负荷",))
    jobs = [
        (70, "案件查阅", "负责人 · 阶段 · 三项期限"),
        (375, "会前准备", "技术原文 · 最近会议待补"),
        (680, "期限排查", "计划日逾期 · 临近 · 未知"),
        (985, "月度统计", "实际收案 · 已核实交局"),
        (1290, "参考与辅助分案", "相关原文 · 登记负荷"),
    ]
    for x,title,detail in jobs:
        f.box(x, 330, 250, 90, title, (detail,), accent=GREEN, title_size=20)
        f.path([(x+125, 420), (x+125, 475), (800, 475)], arrow=False)
    f.path([(400, 235), (400, 290), (195, 290), (195, 330)])
    f.path([(1200, 235), (1200, 290), (1415, 290), (1415, 330)])
    f.path([(400, 290), (500, 290), (500, 330)])
    f.path([(1200, 290), (805, 290), (805, 330)])
    f.path([(1200, 290), (1110, 290), (1110, 330)])
    f.frame(50, 520, 1500, 225, "探渊提供的辅助处理", accent=VIOLET, fill="#faf9fc")
    f.box(85, 590, 390, 110, "接入获准资料", (
        "案件信息 · 逐场会议 · 案卷原文",
        "公司文档 · 本次附件"), accent=BLUE)
    f.box(605, 590, 390, 110, "取数、找原文与核对", (
        "直接查询 · 固定流程 · 条件分支",
        "开放任务按需交给 Agent"), accent=VIOLET)
    f.box(1125, 590, 390, 110, "交付带来源的结果", (
        "简答 · 清单 · 会前卡 · 图表 / 报告",
        "保留出处、未知、冲突与未完成项"), accent=AMBER)
    f.path([(800, 475), (800, 590)])
    f.path([(475, 645), (605, 645)])
    f.path([(995, 645), (1125, 645)])
    f.box(350, 850, 900, 120, "业务人员核对并决定后续办理", (
        "回看来源，核对未知与冲突；按需要补充资料或调整问题",
        "确认办理安排、分案或其他业务操作"), accent=GREEN, title_size=22)
    f.path([(1320, 700), (1320, 800), (800, 800), (800, 850)])
    f.path([(350, 910), (25, 910), (25, 202), (100, 202)], dashed=True, stroke=GREEN)
    f.tag(70, 890, "补充资料 / 调整要求", color=GREEN)
    f.save("00-business-overview.svg")


def overview():
    f = Figure(1740, 1090, "01", "系统组件架构", "任务执行策略 · 程序与模型审查 · 任务级版本复用")
    f.box(50, 180, 265, 90, "Vue 3 工作台", ("任务 · 案件 · 文件 · 结果",))
    f.frame(365, 120, 995, 685, "Python 后端 / FastAPI 应用", accent=VIOLET, fill="#faf9fc")
    f.box(405, 180, 355, 90, "API 与任务入口", ("提交 · 上传 · 会话查询 · 取消",))
    f.box(930, 180, 390, 100, "会话、证据与事件组件", (
        "Storage · Evidence · Monitor",
        "执行记录 · 来源编号 · WebSocket"), accent=AMBER)
    f.box(405, 350, 355, 115, "任务编排器", (
        "完整契约识别 · 来源与执行策略",
        "预算 · 程序 / 模型审查 · 修正"), accent=VIOLET)
    f.box(930, 350, 390, 105, "DeepAgents 执行器", (
        "主智能体直接调用获准工具",
        "独立上下文 / 专业任务按需委派"), accent=VIOLET)
    f.box(405, 555, 355, 100, "固定业务执行器", (
        "直接字段查询 · 固定业务流程",
        "检索 / 推荐的条件分支"), accent=GREEN)
    f.box(930, 555, 390, 100, "工具与文件适配器", (
        "查询 · SQL · 检索 · 会前材料包",
        "附件 · 证据回读 · 报告 / PDF"), accent=GREEN)
    f.box(930, 695, 390, 80, "案件工具缓存（任务级）", (
        "参数 · 权限 · 数据版本 · 证据绑定",), accent=AMBER)
    f.box(1410, 350, 280, 105, "LLM 服务", (
        "任务理解 · 规划 · 生成 · 审查",
        "兼容模型接口"), accent=BLUE, title_size=20)
    f.box(1410, 555, 280, 100, "MySQL 业务数据库", (
        "结构化业务数据",
        "案件数据可选投影"), accent=AMBER, title_size=20)
    f.path([(315, 225), (405, 225)])
    f.tag(320, 207, "HTTP")
    f.path([(930, 205), (850, 205), (850, 108), (180, 108), (180, 180)], dashed=True)
    f.tag(465, 103, "WebSocket 事件", color=AMBER)
    f.path([(760, 240), (930, 240)])
    f.tag(783, 226, "读写会话", color=AMBER)
    f.path([(582, 270), (582, 350)])
    f.tag(597, 315, "启动后台协程")
    f.path([(760, 410), (930, 410)])
    f.tag(803, 397, "调度", color=VIOLET)
    f.path([(582, 465), (582, 555)])
    f.tag(597, 518, "调度", color=GREEN)
    f.path([(1125, 455), (1125, 555)])
    f.tag(1140, 510, "工具调用", color=GREEN)
    f.path([(760, 605), (930, 605)])
    f.tag(784, 592, "工具调用", color=GREEN)
    f.path([(1320, 400), (1410, 400)])
    f.tag(1334, 387, "推理")
    f.path([(760, 375), (835, 375), (835, 318), (1550, 318), (1550, 350)])
    f.tag(1090, 308, "规划 / 复核调用模型")
    f.path([(1320, 605), (1410, 605)])
    f.tag(1334, 592, "查询")
    f.path([(1320, 580), (1340, 580), (1340, 230), (1320, 230)], dashed=True, stroke=AMBER)
    f.tag(1354, 295, "证据 / 事件", color=AMBER)
    f.frame(50, 870, 1640, 175, "持久化与本地资源", accent=AMBER, fill="#fbfaf7")
    f.box(80, 925, 330, 90, "会话 SQLite / app.db", ("执行 · 事件 · 证据 · 当前结果",), accent=AMBER)
    f.box(490, 925, 330, 90, "案件 SQLite", ("字段 · 会议 · 正文块 · 向量",), accent=AMBER)
    f.box(900, 925, 330, 90, "公司文档索引", ("Chroma · 分块 / 版本侧文件",), accent=AMBER)
    f.box(1310, 925, 350, 90, "原件与会话文件", ("共享盘原件 · 附件 · 报告产物",), accent=AMBER)
    f.path([(1125, 180), (1125, 162), (1380, 162), (1380, 845), (245, 845), (245, 925)], stroke=AMBER)
    f.tag(265, 835, "持久化", color=AMBER)
    f.path([(1125, 655), (1125, 695)], stroke=AMBER)
    f.path([(1125, 775), (1125, 790), (655, 790), (655, 925)], stroke=AMBER)
    f.path([(930, 630), (875, 630), (875, 825), (1065, 825), (1065, 925)], stroke=GREEN)
    f.path([(1065, 825), (1485, 825), (1485, 925)], stroke=GREEN)
    f.tag(665, 779, "按版本查询 / 复用", color=AMBER)
    f.tag(1160, 816, "检索 / 文件访问", color=GREEN)
    f.save("01-system-architecture.svg")


def routing():
    f = Figure(1640, 1770, "02", "任务执行与结果核验", "四种执行策略 · 按契约选择程序或模型审查 · 有界修正与交付")
    f.box(470, 125, 700, 90, "准备任务上下文与初始计划", (
        "来源上限 · 附件 · 长期规则 · 交付要求 · 执行预算",
        "完整规则优先；其余受支持任务按需进行语义提取"), accent=BLUE)
    f.path([(820, 215), (820, 260)])
    f.box(470, 260, 700, 70, "按完整契约与执行条件选择执行策略", accent=VIOLET)
    f.box(60, 380, 350, 110, "直接处理", (
        "明确单案单字段 / 多字段",
        "程序取数、绑定来源与渲染"), accent=BLUE)
    f.box(450, 380, 350, 110, "固定流程", (
        "期限排查 · 月报 · 会前卡",
        "按预定动作收集并组织资料"), accent=GREEN)
    f.box(840, 380, 350, 110, "固定流程 + 条件分支", (
        "相似案 · 辅助分案",
        "固定读取后筛选、补查或澄清"), accent=GREEN, title_size=19)
    f.box(1230, 380, 350, 110, "Agent 动态执行", (
        "按反馈选择工具与后续行动",
        "主智能体按需委派子智能体"), accent=VIOLET)
    for x in (235,625,1015,1405):
        f.path([(820, 330), (820, 350), (x, 350), (x, 380)])
        f.path([(x, 490), (x, 550), (820, 550)], arrow=False)
    f.path([(820, 550), (820, 570)])
    decision(f, 820, 620, 440, 100, "需要用户补充 / 确认？")
    f.box(1250, 580, 330, 80, "等待用户输入", ("保存问题与当前依据",), accent=BLUE)
    f.path([(1040, 620), (1250, 620)])
    f.tag(1110, 609, "是", color=BLUE)
    f.path([(820, 670), (820, 730)])
    f.tag(835, 708, "否")
    f.box(520, 730, 600, 85, "候选结果准备与程序硬检查", (
        "引用 / 范围 / 交付检查 · 适用的事实与统计核验",), accent=AMBER)
    f.path([(820, 815), (820, 850)])
    decision(f, 820, 900, 440, 100, "硬检查通过？")
    f.path([(820, 950), (820, 1000)])
    f.tag(835, 980, "是", color=GREEN)
    decision(f, 820, 1060, 560, 120, "满足程序审查契约？", "完整问句重解析 · 正文与证据封印")
    f.box(100, 1190, 500, 85, "程序审查", (
        "受支持的字段 / 清单 / 期限 / 月报契约",), accent=GREEN)
    f.box(920, 1190, 500, 85, "模型语义审查", (
        "任务条件 · 技术相关性 · 综合解释",), accent=VIOLET)
    f.path([(540, 1060), (350, 1060), (350, 1190)])
    f.tag(365, 1140, "是", color=GREEN)
    f.path([(1100, 1060), (1170, 1060), (1170, 1190)])
    f.tag(1185, 1140, "否", color=VIOLET)
    f.path([(350, 1275), (350, 1320), (820, 1320), (820, 1350)])
    f.path([(1170, 1275), (1170, 1320), (820, 1320)], arrow=False)
    decision(f, 820, 1400, 440, 100, "全部适用检查通过？")
    decision(f, 260, 1400, 320, 110, "允许继续修正？", "预算与停止条件")
    f.path([(600, 900), (65, 900), (65, 1330), (260, 1330), (260, 1345)])
    f.tag(290, 888, "否")
    f.path([(600, 1400), (420, 1400)])
    f.tag(490, 1388, "否")
    f.path([(100, 1400), (25, 1400), (25, 345), (1210, 345), (1210, 435), (1230, 435)], dashed=True, stroke=VIOLET)
    f.tag(42, 1325, "是：定向修正", color=VIOLET)
    f.box(80, 1550, 360, 85, "部分结果与未完成项", ("到限 / 无进展 / 无法完成审查",), accent=AMBER)
    f.path([(260, 1455), (260, 1550)])
    f.tag(275, 1505, "否", color=AMBER)
    f.box(620, 1550, 400, 85, "通过：交付结果", ("保留来源与产物",), accent=GREEN)
    f.path([(820, 1450), (820, 1550)])
    f.tag(835, 1505, "是", color=GREEN)
    f.box(80, 1680, 1500, 55, "会话记录：执行状态 · 事件 · 证据 · 当前结果 · 调用与用量", accent=AMBER, title_size=19)
    f.path([(260, 1635), (260, 1680)])
    f.path([(820, 1635), (820, 1680)])
    f.path([(1415, 660), (1605, 660), (1605, 1655), (1415, 1655), (1415, 1680)], stroke=BLUE)
    f.save("02-routing-and-execution.svg")


def retrieval():
    f = Figure(1600, 1540, "03", "文档入库与在线检索", "字段与正文检索 · 按案材料收集 · 文档版本与证据复用")
    f.frame(50, 120, 1500, 560, "文档入库 / 索引构建", accent=BLUE, fill="#f6f9fc")
    f.box(100, 175, 900, 70, "案件共享盘原件 → 增量扫描与内容版本", ("只读白名单目录 · 文件哈希与变更检测",))
    f.box(1120, 175, 380, 70, "公司文档上传与版本管理", ("独立发布与适用范围",))
    f.box(100, 285, 900, 70, "案件文件解析", ("原生文本 / 本地 MinerU · 字段、逐场会议、正文与原文位置",))
    f.box(1120, 285, 380, 70, "公司文档解析", ("原生文本 / 本地 MinerU",))
    f.path([(550, 245), (550, 285)])
    f.path([(1310, 245), (1310, 285)])
    f.box(100, 410, 400, 85, "字段与会议抽取", ("候选值 · 原文位置 · 来源文件",), accent=GREEN)
    f.box(600, 410, 400, 85, "案卷正文分块与向量化", ("位置与版本 · Qwen3 Embedding",), accent=GREEN)
    f.box(1120, 410, 380, 85, "文档分块与向量化", ("位置与版本 · Qwen3 Embedding",), accent=VIOLET)
    f.path([(550, 355), (550, 382), (300, 382), (300, 410)])
    f.path([(550, 382), (800, 382), (800, 410)])
    f.path([(1310, 355), (1310, 410)])
    f.box(100, 555, 400, 90, "案件 SQLite：字段与会议", ("候选 / 冲突 / 确认 · 可选 MySQL 投影",), accent=AMBER, title_size=19)
    f.box(600, 555, 400, 90, "案件 SQLite：正文与向量", ("chunks / revisions · 文件与解析版本",), accent=AMBER, title_size=19)
    f.box(1120, 555, 380, 90, "公司文档索引", ("Chroma · 分块侧文件 · 版本快照",), accent=AMBER)
    for x in (300,800,1310):
        f.path([(x, 495), (x, 555)])
    f.frame(50, 725, 1500, 645, "在线查询与材料收集（按任务选用）", accent=GREEN, fill="#f5faf7")
    f.box(100, 805, 400, 85, "案件查询与业务分析", ("单案字段 · 筛选清单 · 统计 · 负荷",), accent=GREEN)
    f.box(600, 805, 400, 85, "案卷正文混合召回", ("Qwen3 + BM25 + 技术别名 / RRF",), accent=GREEN)
    f.box(1120, 805, 380, 85, "公司文档混合召回与轻量重排", ("Qwen3 / Chroma + BM25 / RRF",), accent=VIOLET, title_size=19)
    for x,label in ((300,"字段快照"),(800,"正文与向量"),(1310,"已发布索引")):
        f.path([(x, 645), (x, 805)])
        f.tag(x+14, 711, label, color=AMBER)
    f.box(600, 935, 400, 85, "条件重排与按案归并", ("bge-reranker-base 按需使用",), accent=GREEN)
    f.box(1120, 935, 380, 85, "按需覆盖检查与有限补查", ("覆盖状态 · 缺口 · 停止原因",), accent=VIOLET, title_size=19)
    f.path([(800, 890), (800, 935)])
    f.path([(1310, 890), (1310, 935)])
    f.path([(1500, 980), (1525, 980), (1525, 848), (1500, 848)], dashed=True)
    f.box(100, 1080, 400, 60, "结构化结果与来源引用", accent=GREEN, title_size=19)
    f.box(600, 1080, 400, 60, "候选案件 · 原文片段 · 同案附件", accent=GREEN, title_size=19)
    f.box(1120, 1080, 380, 60, "知识片段 · 原文位置 · 版本", accent=VIOLET, title_size=19)
    f.path([(300, 890), (300, 1080)])
    f.path([(800, 1020), (800, 1080)])
    f.path([(1310, 1020), (1310, 1080)])
    f.box(100, 1220, 400, 100, "按案材料包（会前卡）", (
        "同案文件清单 · 已发布正文",
        "版本 · 读取范围 · 缺项"), accent=GREEN)
    f.path([(300, 1140), (300, 1180), (300, 1220)])
    f.tag(315, 1205, "会前卡", color=GREEN)
    f.path([(650, 645), (650, 690), (25, 690), (25, 1270), (100, 1270)], dashed=True, stroke=AMBER)
    f.tag(65, 705, "按案读取已发布材料", color=AMBER)
    f.box(350, 1420, 900, 80, "任务证据登记与原文回读", ("来源编号 · 查询结果 / 原文位置 · 文档版本 · 同版本复用",), accent=AMBER)
    f.path([(300, 1180), (530, 1180), (530, 1390), (800, 1390)], arrow=False)
    f.path([(300, 1320), (300, 1390), (800, 1390)], arrow=False)
    for x in (800,1310):
        f.path([(x, 1140), (x, 1390), (800, 1390)], arrow=False)
    f.path([(800, 1390), (800, 1420)])
    f.save("03-ingestion-and-retrieval.svg")


def sequence():
    f = Figure(1540, 1900, "04", "任务运行时序", "规则或模型理解 · 版本缓存复用 · 程序或模型审查 · 事件先存后推")
    f.parts = [s.replace('#ffffff', '#0d1117').replace('#243247', '#e6edf3').replace('#526276', '#aab8c5').replace('#c9d2dc', '#364351') for s in f.parts]
    fg, line = '#e6edf3', '#b6c2cd'
    def txt(x, y, label, size=16, bold=False, anchor='middle'):
        f.text(x, y, label, size, bold=bold, color=fg, anchor=anchor)
    def arrow(x1, x2, y, label, dashed=False):
        txt((x1+x2)/2, y-12, label)
        f.path([(x1,y),(x2,y)], stroke=line, dashed=dashed, width=1.6)
    def self_call(x,y,label):
        txt(x+15,y-12,label,anchor='start')
        f.path([(x,y),(x+44,y),(x+44,y+20),(x,y+20)],stroke=line,width=1.6)
    def band(x,y,w,h,label):
        f.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="none" stroke="#63778d"/>')
        f.parts.append(f'<rect x="{x}" y="{y}" width="{min(w,650)}" height="33" fill="#1c2530" stroke="#63778d"/>')
        txt(x+15,y+23,label,15,True,anchor='start')
    columns=[(115,'Vue 工作台'),(370,'FastAPI'),(625,'任务编排协程'),(880,'任务工具'),(1135,'Monitor'),(1420,'会话 SQLite')]
    for x,label in columns:
        f.parts.append(f'<rect x="{x-90}" y="120" width="180" height="58" rx="6" fill="#1c2530" stroke="#768698"/>')
        txt(x,155,label,17,True)
        f.path([(x,178),(x,1875)],stroke='#526170',arrow=False,width=1.2)
    arrow(115,370,220,'连接 WebSocket')
    arrow(115,370,275,'POST /api/task')
    arrow(370,1420,330,'创建执行记录 / execution_id')
    arrow(370,625,385,'create_task 启动协程')
    arrow(370,115,440,'返回 thread_id / execution_id',True)
    self_call(625,510,'完整规则识别 / 按需模型提取；确定计划与预算')
    band(55,555,1450,770,'loop  执行与核验：直到通过、等待补充或触发停止条件')
    band(570,595,910,365,'opt  本轮需要调用工具')
    arrow(625,880,660,'固定流程动作或 Agent 工具调用')
    self_call(880,715,'权限、版本与适用的缓存检查')
    band(820,755,660,145,'opt  实际读取并产生新证据')
    arrow(880,1420,815,'登记证据，保存并取得 [S] 编号')
    arrow(1420,880,870,'返回来源编号',True)
    arrow(880,625,935,'返回结果与 [S]；命中缓存时复用已有证据',True)
    self_call(625,995,'硬检查；按契约执行程序审查或模型审查')
    arrow(625,1135,1060,'report：执行 / 核验事件')
    arrow(1135,1420,1115,'append_event 持久化')
    arrow(1135,115,1170,'WebSocket 发送队列推送事件',True)
    band(570,1205,875,95,'opt  未通过且可继续')
    self_call(625,1275,'携带问题与已有证据，进入下一轮修正')
    arrow(625,370,1380,'返回结果 / 等待补充 / 执行异常',True)
    arrow(370,1135,1435,'生成结果或状态事件')
    arrow(1135,1420,1490,'保存事件、结果与状态')
    arrow(1135,115,1545,'推送结果或状态事件',True)
    band(55,1590,1450,285,'opt  运行中断线恢复')
    arrow(115,370,1650,'重连 WebSocket；GET /api/runs/{thread_id}')
    arrow(370,1420,1695,'读取状态与历史事件')
    arrow(1420,370,1740,'持久化会话快照',True)
    arrow(370,115,1785,'返回快照与历史事件',True)
    self_call(115,1840,'合并缓冲事件并去重')
    f.save('04-task-sequence.svg')


if __name__ == "__main__":
    business()
    overview()
    routing()
    retrieval()
    sequence()
    print("Rendered 5 SVG project figures in", OUT)

"""上传文件解析工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/upload_file_read_tool.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402

from api import context, evidence  # noqa: E402
from utils import path_utils  # noqa: E402


def _get_session_dir() -> Path:
    """返回会话工作目录；未设置会话上下文时回退到默认会话目录。"""
    d = context.get_session_dir()
    if d:
        return Path(d)
    return path_utils.get_session_dir(context.get_thread_id() or "default")


def _read_text(path: Path) -> str:
    from tools.attachment_tables import read_text
    return read_text(path)


def _read_docx(path: Path) -> str:
    from tools.document_parser import parse_document
    return "\n\n".join(unit.page_content for unit in parse_document(path))


def _read_pdf(path: Path) -> str:
    from tools.document_parser import parse_document, location_label
    return "\n\n".join(f"[{location_label(unit.metadata)}]\n{unit.page_content}" for unit in parse_document(path))


@tool
def read_file_content(filename: str, instruction: str = "提取全部内容", sheet: str | None = None,
                      offset: int = 0, limit: int = 100, columns: list[str] | None = None,
                      filters: list[dict] | None = None, group_by: list[str] | None = None,
                      aggregates: list[dict] | None = None, list_sheets: bool = False,
                      deduplicate_by: list[str] | None = None) -> str:
    """只读会话附件或工作文件，返回证据及真实读取范围。

    CSV/Excel：先 list_sheets=true 查看工作表，再用 sheet 指定名称。offset 从 0 开始，
    limit 为 1～500；truncated=true 必须继续分页，不得把一页当全量。
    filters=[{"field":"金额","op":"gte","value":100}]，支持 eq/ne/contains/in/gt/gte/lt/lte/is_null/not_null。
    aggregates=[{"field":"金额","op":"sum","alias":"总金额"}] 可配 group_by；支持 sum/count/mean/min/max/nunique。
    deduplicate_by=["案号"]先对完整表按主键去除完全相同记录，再筛选汇总；空键或同键冲突拒绝，不能任选。
    count/nunique返回程序生成的完整唯一值集合、来源行及文件哈希证明，不由模型编造。
    instruction 仅记录用户目标，不执行自然语言条件；筛选、汇总必须传上述结构化参数。
    JSON默认返回验证后的原文；对象数组可用同样的filters/aggregates进行程序筛选与统计，sheet为JSON。
    扫描PDF与图片通过本机MinerU提取文字；部件连接、附图流程或图表含义用analyze_visual_document看图。
    其他格式返回解析文本。filename 可为原始附件名称或 attachments/名称。
    """
    import hashlib
    import json
    from tools.attachment_tables import query_table
    root = _get_session_dir()
    target = path_utils.resolve_within(root, filename)
    attachment = path_utils.resolve_within(root / 'attachments', filename.removeprefix('attachments/'))
    if attachment.is_file():
        target = attachment
    if not target.is_file():
        return f"错误：文件不存在 {filename}"
    ext = target.suffix.lower()
    details = {}
    parsed = []
    try:
        if ext in ('.csv', '.xlsx', '.xls') or (ext=='.json' and (filters or aggregates or columns or group_by or list_sheets or deduplicate_by)):
            details = query_table(target, sheet, offset, limit, columns, filters, group_by, aggregates, list_sheets, deduplicate_by)
            content = json.dumps(details, ensure_ascii=False, default=str)
            if instruction != '提取全部内容':
                content += '\n用户目标：' + instruction + '\n实际执行范围以上述结构化参数为准；如未满足目标，请继续调用工具。'
        elif ext in ('.md', '.txt', '.json'):
            content = _read_text(target)
            if ext=='.json':
                json.loads(content)  # Validate data; never execute file content.
            if len(content)>1_000_000:raise ValueError('文本超过100万字符，请拆分后读取')
        elif ext in ('.doc', '.docx'):
            content = _read_docx(target)
        elif ext == '.pdf' or ext in {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}:
            from tools.document_parser import parse_document, location_label
            parsed = parse_document(target)
            if not parsed:
                raise ValueError('文档未解析出正文，不能视为读取成功')
            content = '\n\n'.join(f'[{location_label(unit.metadata)}]\n{unit.page_content}' for unit in parsed)
            page_methods = {}
            for unit in parsed:
                page = unit.metadata.get('page')
                method = ('MinerU OCR与版面识别' if unit.metadata.get('parse_mode') == 'ocr'
                          else 'MinerU版面解析' if unit.metadata.get('parser') == 'mineru' else '原生文字层提取')
                if page is not None:
                    page_methods[page] = method
            if page_methods:
                content = '解析记录（程序信息，不是文档正文）：' + '；'.join(
                    f'第{page}页：{method}' for page, method in sorted(page_methods.items())) + '\n\n' + content
            if len(content) > 1_000_000:
                raise ValueError('解析文本超过100万字符，请拆分文档后读取')
        else:
            return f"不支持的文件类型：{ext}"
    except (ValueError, TypeError, OSError, KeyError) as exc:
        return f'文件读取失败：{exc}'
    line_guide = ''
    if ext in ('.md', '.txt'):
        line_guide = '\n\n原文物理行定位（程序信息，包含标题和空行）：\n' + '\n'.join(
            f'第{i}行：{line}' for i,line in enumerate(content.splitlines()[:400],1))
        if len(content.splitlines())>400:line_guide+='\n行定位预览仅前400行，不能据此推断其余行；完整原文保留。'
    extra={}
    if details.get('aggregates') and all(a['op'] in ('count','nunique') for a in details['aggregates']):
        cols=details['columns'];groups=details.get('group_by') or []
        aliases=[a.get('alias') or f"{a['field']}_{a['op']}" for a in details['aggregates']]
        stats={'version':1,'dimensions':[{'column':c,'index':cols.index(c),'label':c,'role':'dimension'} for c in groups if c in cols],
               'metrics':[{'column':c,'index':cols.index(c),'label':c,'names':[c],'unit':'件' if any(w in c for w in ('案件','案量','案数','在办')) else 'count','definition_verified':True,
                           'time':{'status':'unbounded'},'filters':[{**f,**({'values':f['value']} if f.get('op')=='in' else {})} for f in (details.get('filters') or [])]} for c in aliases if c in cols]}
        extra={'statistics':stats,'answer_contract':{'version':1,'kind':'table_aggregate',
               'scope':'文件 '+target.name+'，工作表 '+details['sheet']+'；筛选：'+json.dumps(details.get('filters') or [],ensure_ascii=False),
               'definition':'由程序对完整匹配行汇总；来源行号见证据source_row_groups。'}}
    evidence_id = evidence.add(context.get_thread_id(), {
        'type': 'file', 'title': target.name, 'snippet': content[:1200],
        **({'content':content,'content_complete':True} if not details else {}),
        **extra,
        'revision': hashlib.sha256(target.read_bytes()).hexdigest(),
        'location': f"工作表 {details.get('sheet', '')} · 偏移 {offset}" if details else '',
        **({'document_locations': [unit.metadata for unit in parsed],
            'extraction_method': 'mineru' if any(unit.metadata.get('parser') == 'mineru' for unit in parsed) else 'native_pdf',
            'scope': '文字与版面解析；图片结构和技术效果需另行视觉核对'} if parsed else {}),
        **{key: details[key] for key in ('columns','rows','sheet','row_numbers','filters','aggregates','offset','limit','truncated','total_rows','matched_rows','result_rows','group_by','source_row_groups','quality','deduplication','aggregation_proof') if key in details},
    })
    return (f"[证据 {evidence_id}] 文件：{target.name}\n" if evidence_id else "") + content + line_guide


if __name__ == "__main__":
    import pandas as pd

    context.set_session("demo", str(path_utils.get_session_dir("demo")))
    session_dir = path_utils.get_session_dir("demo")
    session_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = session_dir / "示例数据.xlsx"
    pd.DataFrame(
        {
            "类别": ["A 类", "B 类", "C 类", "D 类", "E 类"],
            "数量": [100, 200, 150, 80, 120],
            "单价": [25.0, 15.0, 35.0, 45.0, 100.0],
        }
    ).to_excel(xlsx_path, index=False)
    print(read_file_content.invoke({"filename": "示例数据.xlsx"}))

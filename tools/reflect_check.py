"""反思循环硬校验器。"""
from __future__ import annotations

import re

from tools.placeholders import has_placeholders


def hard_check(result: dict) -> list[str]:
    """执行规则硬校验，返回未通过项列表（空列表表示全部通过）。

    ``result`` 由编排层 ``main_agent_run`` 返回，字段：
    - ``report``           : 报告正文（str）
    - ``called_sources``   : 本轮实际调用的数据源 key 列表
    - ``required_sources`` : 结构化任务计划（TaskPlan）声明的数据源 key 列表
    - ``require_pdf``      : 任务是否要求 PDF（bool）
    - ``pdf_produced``     : 是否已产出 PDF 文件（bool）

    校验项：
    - H2 占位符：检测未填模板和 TODO；正常业务状态“待补充”不算占位
    - H3 格式：要求 PDF 但未产出 PDF 文件
    - H4 数据源覆盖：声明要求的数据源未被实际检索
    """
    report = result.get("report", "")
    issues: list[str] = []
    from casework.archive_readback import final_check
    archive_check=final_check(result.get('query',''),result.get('evidence',[]),report)
    result['archive_evidence_check']=archive_check
    issues.extend(archive_check['issues'])
    if result.get('execution_issue'):
        issues.append('执行尚未完成：'+result['execution_issue'])

    if re.search(r'<[｜|]{1,2}.*?(?:DSML|invoke|parameter)|<tool_call>|<function_calls>',report,re.I):
        issues.append('答案泄漏工具调用标记：须实际执行工具并交付用户可读结果，不能把调用协议作为答案')

    from tools.source_delivery import original_location_issues, source_line_issues, case_title_issues, dedup_aggregation_issues
    issues.extend(original_location_issues(result.get('query',''), report, result.get('evidence',[])))
    issues.extend(source_line_issues(report, result.get('evidence',[])))
    issues.extend(case_title_issues(report, result.get('evidence',[])))
    issues.extend(dedup_aggregation_issues(result.get('query',''), result.get('evidence',[])))

    # H2 占位符
    if has_placeholders(report):
        issues.append("报告存在未填充占位符")

    from tools.date_completeness import date_completeness_issues
    issues.extend(date_completeness_issues(report,result.get('evidence',[])))

    from tools.chart_utils import chart_delivery_issues
    issues.extend(chart_delivery_issues(result.get('query',''),result.get('evidence',[]),report))
    from tools.answer_contracts import workload_summary_date_issues,case_date_provenance_issues,workload_completion_issues
    issues.extend(workload_completion_issues(result.get('query',''),report,result.get('evidence',[])))
    issues.extend(workload_summary_date_issues(result.get('query',''),result.get('evidence',[])))
    issues.extend(case_date_provenance_issues(result.get('query',''),report,result.get('evidence',[])))

    # H3 格式
    if result.get("require_pdf") and not result.get("pdf_produced"):
        issues.append("未生成要求的 PDF 文件")

    if (not result.get('require_pdf') and re.search(r'(?:存成|保存为|生成|给我|一份).{0,12}(?:Markdown|markdown|MD).{0,6}(?:文件|报告)?',result.get('query',''))
            and not result.get('report_files')):
        issues.append('Markdown文件交付缺失：请实际生成并保存本次完整报告；正文声称已保存不能代替文件。')

    # Citation IDs are application-assigned; a model cannot invent a valid source.
    from tools.evidence_refs import citation_issues
    issues.extend(citation_issues(report, result.get('evidence', [])))
    from tools.answer_contracts import contract_issues,workload_delivery_issues
    issues.extend(contract_issues(report, result.get('evidence', [])))
    issues.extend(workload_delivery_issues(result.get('query',''),result.get('evidence', [])))

    query = result.get('query', '')
    if re.search(r'(?:从|依据|根据|查|读|读取|核对).{0,16}申请文件', query):
        from casework.parser import CASE_RE
        requested = {m.group().upper() for m in CASE_RE.finditer(query)}
        documents = [item for item in result.get('evidence', [])
                     if item.get('type') in ('knowledge', 'file')
                     and (not requested or requested & {m.group().upper() for m in CASE_RE.finditer(
                         ' '.join(str(item.get(key) or '') for key in ('case_id', 'title', 'source', 'snippet')))} )
                     and re.search(r'申请文件|申请文本|权利要求|说明书',
                         ' '.join(str(item.get(key) or '') for key in ('title', 'source', 'snippet')))]
        if not documents:
            issues.append('申请文件正文未取得：原任务要求从申请文件说明实际步骤，须读取该案申请文件原文；只有案件信息或会议转述不能算完成')

    if '未知状态' in query:
        from casework.analytics import ACTIVE, INACTIVE
        for item in result.get('evidence', []):
            columns, rows = item.get('columns') or [], item.get('rows') or []
            complete = item.get('completeness') or {}
            if '案件状态' not in columns or not rows or complete.get('has_more') or complete.get('fetch_truncated'):
                continue
            stage_index = columns.index('案件状态')
            unknown = sum(row[stage_index] not in ACTIVE | INACTIVE for row in rows)
            if unknown == 0 and not re.search(r'办理阶段未知.{0,8}0\s*件', report):
                issues.append('办理阶段与交局核实状态混淆：来源中办理阶段均已登记；须明确办理阶段未知为0件，实际交局未核实另列，不能把未核实交局称作案件状态未知')
            break

    from casework.requirements import case_mechanism_judgment_requested
    from casework.parser import CASE_RE
    if case_mechanism_judgment_requested(result.get('query', '')):
        requested_cases = {m.group().upper() for m in CASE_RE.finditer(result['query'])}
        answered_cases = {m.group().upper() for m in CASE_RE.finditer(report)}
        if missing_cases := requested_cases - answered_cases:
            issues.append('机制判断缺少案件身份：正文须写明原问题指定的案号' + '、'.join(sorted(missing_cases)) + '，不能只写“这个案子”或仅在来源片段中出现；保留已有结论和引用，不重新检索')

    if any(item.get('type')=='web' for item in result.get('evidence',[])) and re.search(
            r'(?:网络|联网|公开)(?:检索|搜索).{0,12}(?:未|没有)(?:实际)?(?:发出|执行)(?:过)?任何查询|(?:未|没有)(?:发出|执行)任何(?:网络|联网|公开)(?:查询|检索|搜索)',report):
        issues.append('执行事实矛盾：已有成功网络来源，不能声称未发出任何网络查询；后续预算拒绝不抹除已有结果，请用已取得公开原句完成可支持的比较')

    if any((item.get('statistics') or {}).get('metrics') for item in result.get('evidence',[])):
        for sentence in re.split(r'[。；\n]',report):
            if re.search(r'时间(?:很好约|很宽松)|约时间很宽松|时间安排很宽松|(?:一定|肯定|随时)有空|可以随时约',sentence) and not re.search(r'不能|不可|不应|无法|不得|不代表',sentence):
                issues.append('忙闲推断超出登记范围：'+sentence.strip()[:180]+'；登记负荷不能证明实际日程或约见容易，只能说明可见在办/期限压力并保留工时与日程未知')

    from casework.requirements import external_comparison_requested
    from tools.evidence_refs import report_references
    if external_comparison_requested(result.get('query','')):
        cited=report_references(report)
        web_ids={e.get('id') for e in result.get('evidence',[]) if e.get('type')=='web'}
        # A heading supplies meaning to every following bullet, not merely the
        # line on which it appears. Missing documentation is an unknown even
        # when embedded under a 'confirmed differences' section.
        confirmed_section=False
        for line in report.splitlines():
            heading=re.match(r'^\s*(?:#{1,6}\s+(.+)|\*\*([^*]+)\*\*(?:\s*$|\s*[:：]))',line)
            if heading:
                title=heading[1] or heading[2]
                confirmed_section=bool(re.search(r'已证实.*差异|已确认.*差异|确定.*区别',title))
            elif confirmed_section and re.search(r'未见|未提及|未说明|未记载|没有记载|未知|未核实',line):
                issues.append('比较分组混入未核实项：'+line.strip()[:180]+'；已读片段没有说明应归入未知，不能置于已证实的差异标题下。')
        for sentence in re.split(r'[。；\n]',report):
            if re.search(r'唯一.{0,35}(?:成熟方法|公开方法|公开实现)',sentence) and not re.search(r'无法确认|不能确认|未核实|不代表|本次已读|本次取得',sentence):
                issues.append('公开方法唯一性未核实：'+sentence.strip()[:180]+'；有限检索不能证明公开领域唯一，请限定为本次选取的对照，不作全域唯一断言。')
        for sentence in re.split(r'[。；\n]',report):
            if re.search(r'已证实的差异|已确认的差异|确定的区别',sentence) and re.search(r'未见|没有出现|未提及|未覆盖|未说明',sentence):
                issues.append('已读片段未见不能写成已证实的差异：'+sentence.strip()[:180]+'；改为未核实的对应关系，不能将检索缺口当作技术差异。')
        if web_ids and not cited & web_ids:
            issues.append('公开比较未引用已取得的网络证据：逐项将公开方法的事实绑定实际网页片段；不能凭记忆补全粒度、校准或机制。片段不足则明确比较缺口。')

    # RRF is a rank-fusion contract, not merely a generic “combine results”
    # statement.  When the task explicitly asks for the official RRF
    # explanation, the answer must state the reciprocal/rank contribution (or
    # an equivalent formula) and distinguish it from adding raw similarity
    # scores.  This is a reusable domain guard keyed to the requested concept,
    # never to a question id or a fixed answer string.
    if re.search(r'RRF|reciprocal\s+rank\s+fusion|倒数融合', query, re.I):
        has_reciprocal = bool(re.search(r'倒数|reciprocal|1\s*/\s*\(?\s*(?:k|rank|排名)|1\s*/\s*\(', report, re.I))
        has_raw_score_boundary = bool(re.search(r'不(?:直接)?相加|不把[^。；\n]{0,20}(?:相似|原始|相关性)分(?:数)?相加|raw\s+similarity', report, re.I))
        if not has_reciprocal:
            issues.append('RRF机制说明不完整：须说明按排名的倒数贡献或等价公式；仅说合并排名不能代替倒数融合。')
        if not has_raw_score_boundary:
            issues.append('RRF机制边界未说明：须明确不直接相加不同检索器的原始相似分。')

    # Text comparison is allowed to classify line-level changes as added,
    # deleted, modified, or indeterminate.  It must not silently promote two
    # different sentences into a mechanism/causal replacement claim.  Such a
    # claim is only safe when the answer explicitly limits itself to wording
    # or states that the substantive relationship is unknown.
    if re.search(r'逐条比较|两侧原句|基准.*修订|修订.*基准', query):
        for sentence in re.split(r'[。；\n]', report):
            if not re.search(r'由.{0,80}(?:改为|替换为|变更为)|(?:机制|规则|触发条件|时机|效果).{0,30}(?:改变|替换|转为)', sentence):
                continue
            if re.search(r'文字|字句|行级|仅.*比较|无法确定|未载明|不作.*定性|不判定', sentence):
                continue
            issues.append('文本比较越界：已发现行级文字差异，但未见原文直接支持机制/因果替换关系；应限定为文字变化或保留无法确定。')

    comparisons=[i for i in result.get('evidence',[]) if i.get('comparison_counts')]
    if any(i['comparison_counts'].get('modified',0)>0 for i in comparisons):
        plain=report.replace('**','')
        if re.search(r'未(?:删去|删除).{0,15}(?:任何|全部|所有)(?:句子|原句|内容)|(?:文本|文字)超集',plain):
            issues.append('修改与保留断言矛盾：存在替换原句时，不能说新版保留基准版所有句子或是文本超集；删除记录为零仅表示没有独立删除项，不表示被替换旧句仍然保留。')

    # H4 数据源覆盖
    called = set(result.get("called_sources", []))
    for src in result.get("required_sources", []):
        if src not in called:
            issues.append(f"缺少 {src} 的检索结果")

    if "allowed_sources" in result:
        unexpected = called - set(result["allowed_sources"])
        if unexpected:
            issues.append("实际数据源超出任务计划：" + "、".join(sorted(unexpected)))

    executed={(r['id'],r['revision']) for r in result.get('executed_rule_versions',[])}
    for rule in result.get('required_rule_versions',[]):
        if (rule['id'],rule['revision']) not in executed:
            issues.append(f"业务口径未通过结构化查询实际执行：{rule['id']} v{rule['revision']}")
    statistical = result.get('statistics_check') or {}
    if statistical.get('status') in ('failed', 'unverified'):
        issues.extend('统计核验：' + issue for issue in statistical.get('issues', ['未完成核验']))
    from tools.case_records import check_case_answer
    case_check=check_case_answer(result)
    if case_check['status']=='failed':issues.extend('案卷关联核验：'+issue for issue in case_check['issues'])
    business = result.get('business_check') or {}
    if business.get('status') in ('failed', 'unverified'):
        issues.extend('业务事实核验：' + issue for issue in (business.get('issues') or ['字段或必要证据未核验']))
    from tools.content_verification import archive_absence_issues, mechanism_adoption_issues
    issues.extend(archive_absence_issues(report,result.get('evidence',[])))
    issues.extend(mechanism_adoption_issues(report,result.get('evidence',[]),result.get('query','')))
    from tools.content_verification import unsupported_reasoning_issues
    issues.extend(unsupported_reasoning_issues(report,result.get('evidence',[]),result.get('query','')))
    return list(dict.fromkeys(issues))


if __name__ == "__main__":
    ok = "已有证据支持的完整结论。"
    print("H2(占位符):", hard_check({"report": ok + "待补充"}))
    print(
        "H3(缺PDF):",
        hard_check({"report": ok, "require_pdf": True, "pdf_produced": False}),
    )
    print(
        "H4(缺数据源):",
        hard_check(
            {
                "report": ok,
                "required_sources": ["knowledge", "db_query"],
                "called_sources": ["db_query"],
            }
        ),
    )
    print("全部通过:", hard_check({"report": ok}))

"""Parse, validate and conservatively normalize application evidence citations."""
from __future__ import annotations

import json
import re
import unicodedata

_ID = r'S[1-9][0-9]*'
_VALID = re.compile(r'\[(' + _ID + r')\]')
# Ignore unrelated Markdown such as [SQL] and [Source]; catch S, S0, S01,
# compound/unfinished references and full-width citation brackets as well.
_CANDIDATE = re.compile(r'[\[【［](?:证据\s*)?[SＳ](?=[0-9０-９\W]|$)[^\]】］\n]*(?:[\]】］]|(?=\n|$))')
_COMPOUND = re.compile(r'\[(' + _ID + r'(?:\s*[/,，、;；]\s*' + _ID + r')+)\]')


def _json_answer(report):
    text = report.strip()
    if text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.S)
        if match:
            text = match[1]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def report_references(report):
    refs = set(_VALID.findall(report))
    value = _json_answer(report)
    if isinstance(value, dict) and isinstance(value.get('evidence_ids'), list):
        refs.update(i for i in value['evidence_ids'] if isinstance(i, str) and re.fullmatch(_ID, i))
    return refs


# Only directly bound long literal quotations enter this deterministic check.
# Paraphrases and inferred claims remain subject to semantic review.
_QUOTED_REF = re.compile(r'“([^”\n]{12,})”[\s*_]*((?:\[S[1-9]\d*\]\s*)+)|「([^」\n]{12,})」[\s*_]*((?:\[S[1-9]\d*\]\s*)+)|"([^"\n]{12,})"[\s*_]*((?:\[S[1-9]\d*\]\s*)+)')


def quoted_text_group(match):
    return next(group for group in (1, 3, 5) if match[group] is not None)


def _literal(text):
    from tools.answer_contracts import normalize_source
    cleaned,_ = normalize_source(text)
    return re.sub(r"[\s`*\"'“”‘’「」]", '', unicodedata.normalize('NFKC',cleaned))


def _quote_supported(quote, snippet):
    """An elision must retain literal segments in their original order."""
    parts = [part for part in re.split(r'…+|\.{3,}', _literal(quote)) if part]
    if not parts:
        return False
    source = _literal(snippet)
    offset = 0
    for part in parts:
        found = source.find(part, offset)
        if found < 0:
            return False
        offset = found + len(part)
    return True


def _quoted_bindings(report, evidence):
    by_id={i.get('id'):i for i in evidence}
    for match in _QUOTED_REF.finditer(report):
        group=quoted_text_group(match)
        quote=match[group]; refs=set(_VALID.findall(match[group+1]))
        before=report[max(0,match.start()-40):match.start()].replace('*','').replace('`','')
        if re.search(r'(?:未见|未出现|未检索到|未查到|没有找到)\s*$',before):continue
        cited=[by_id[i] for i in refs if i in by_id]
        # Bracketed source notation and database cell formatting have separate
        # contracts. Do not guess their wording.
        if re.search(r'\[.*?\]',quote):continue
        textual=[i for i in cited if i.get('type') in ('knowledge','web','upload','file') and i.get('snippet')]
        if not textual:continue
        yield match,group,quote,refs,textual,any(_quote_supported(quote, i['snippet']) for i in textual)


def _supported_blockquotes(report, evidence):
    """Literal source paragraph numbers inside a bound quote are content.

    Only an entire matching block with real trailing application references is
    exempt. A bare numeric citation, unbound quote or altered text still fails.
    """
    spans=[]
    by_id={item.get('id'):item for item in evidence}
    for block in re.finditer(r'(?m)^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*',report):
        text=re.sub(r'(?m)^[ \t]*> ?', '', block[0]).strip()
        tail=re.search(r'((?:\s*\[S[1-9]\d*\])+)(?:（([^）]+)）)?$',text)
        if not tail:continue
        refs=report_references(tail[1])
        quote=text[:tail.start()].strip()
        if refs and refs<=by_id.keys() and quote and any(
                _literal(quote) in _literal(by_id[ident].get('snippet',''))
                and (not tail[2] or tail[2] == by_id[ident].get('location')) for ident in refs):
            spans.append((block.start(),block.end()))
    return spans


def _source_paragraph_marker(report, match, evidence):
    """A patent's numbered paragraph is source text when a nearby S cite binds it."""
    marker = match[0]
    if not re.fullmatch(r'\[\d{4}\]', marker):
        return False
    line_start = report.rfind('\n', 0, match.start()) + 1
    line_end = report.find('\n', match.end())
    line = report[line_start:len(report) if line_end < 0 else line_end]
    prefix = line[:match.start() - line_start]
    refs = report_references(line)
    if re.search(r'(?:原文|说明书|段落|段号|第\s*\d+(?:\s*[–—-]\s*\d+)?\s*页|位置|出处)', prefix):
        if any(item.get('id') in refs and marker in str(item.get('snippet') or '')
               for item in evidence):
            return True
    # A document heading may name the PDF once, then give a paragraph range
    # before the per-row citations. The basename and paragraph must both
    # identify one preserved source file; a bare [0004] remains invalid.
    matches = [item for item in evidence if item.get('type') in ('knowledge', 'file')
               and marker in str(item.get('snippet') or '')
               and str(item.get('title') or '').rsplit('/', 1)[-1] in line
               and str(item.get('title') or '').lower().endswith(('.pdf', '.docx'))]
    return len(matches) == 1


def citation_issues(report, evidence):
    """Reject malformed syntax and IDs absent from this execution's evidence."""
    issues = []
    for match in _CANDIDATE.finditer(report):
        if not _VALID.fullmatch(match[0]):
            issues.append('引用格式错误：' + match[0][:100] + '；须逐项使用[S数字]')
    value = _json_answer(report)
    if isinstance(value, dict) and 'evidence_ids' in value:
        ids = value['evidence_ids']
        if not isinstance(ids, list) or any(not isinstance(i, str) or not re.fullmatch(_ID, i) for i in ids):
            issues.append('evidence_ids须为真实S编号字符串数组')
    if value is None:
        quoted_spans=_supported_blockquotes(report,evidence)
        for match in re.finditer(r'\[\d{1,5}\]',report):
            if any(start<=match.start()<end for start,end in quoted_spans):continue
            if _source_paragraph_marker(report,match,evidence):continue
            issues.append('数字式引用无法定位应用证据：'+match[0]+'；不得猜补S前缀，请核对工具实际evidence_id并逐项使用[S编号]')
    available = {item.get('id') for item in evidence}
    if any(item.get('type')=='file' for item in evidence) and re.search(r'这[份张个]|上传|附件|交底材料',report):
        file_ids={item.get('id') for item in evidence if item.get('type')=='file'}
        if not report_references(report) & file_ids:
            issues.append('上传原件缺少证据定位：附件解读须引用实际文件[S编号]和页段或章节位置')
    missing = report_references(report) - available
    if missing:
        issues.append('引用了不存在的来源编号：' + '、'.join(sorted(missing, key=lambda i: int(i[1:]))))
    for match,group,quote,refs,cited,supported in _quoted_bindings(report,evidence):
        if not supported:
            issues.append('直接引文与所引片段不匹配：'+quote[:120]+'；引用='+','.join(sorted(refs))+'；请引用包含完整原句的实际片段，或拆成分别有依据的短引文')
    supported_blocks = _supported_blockquotes(report, evidence)
    for block in re.finditer(r'(?m)^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*', report):
        if any(start == block.start() and end == block.end() for start, end in supported_blocks):
            continue
        quote = re.sub(r'(?m)^[ \t]*> ?', '', block[0]).strip().strip('“”「」')
        if len(quote)>=12 and any(item.get('type') in ('knowledge','upload','file','web') for item in evidence):
            issues.append('原文引句缺少可定位证据：'+quote[:100]+'；须绑定真实[S编号]及来源位置')
    return list(dict.fromkeys(issues))


def normalize_citations(report, evidence):
    """Split explicit compound IDs only when every ID exists; never guess [S]."""
    evidence = [item for item in evidence if item.get('task_scope_compatible') is not False]
    # JSON output has its own exact field contract, so leave it byte-for-byte.
    if _json_answer(report) is not None:
        return report
    available = {item.get('id') for item in evidence}
    from tools.source_quotes import render_source_quotes
    report, _ = render_source_quotes(report, evidence)
    # Normalize an explicit, existing S-id next to a patent paragraph marker.
    # Never invent a source for a numeric marker.
    def bind_explicit_paragraph_ids(match):
        ids = re.findall(_ID, match[2])
        return match[1] + ''.join('[' + ident + ']' for ident in ids) if set(ids) <= available else match[0]
    report = re.sub(r'(\[\d{4}\])\s*（(S[1-9]\d*(?:\s*[、，,]\s*S[1-9]\d*)*)）',
                    bind_explicit_paragraph_ids, report)
    report=re.sub(r'\n\n### 引用原文定位\n\n\| 证据 \| 原件 \| 位置 \|\n\| --- \| --- \| --- \|(?:\n\|[^\n]*)*','',report)
    def bind_paragraph_range(match):
        path, middle, first, last = match.groups()
        def unique_source(marker):
            candidates=[item for item in evidence if item.get('type') in ('knowledge','file')
                        and str(item.get('title') or '') == path
                        and marker in str(item.get('snippet') or '')]
            identities={(item.get('file_id'),item.get('revision')) for item in candidates}
            return candidates[0]['id'] if len(candidates)==1 and len(identities)==1 else None
        left,right=unique_source('['+first+']'),unique_source('['+last+']')
        if not left or not right:
            return match[0]
        return f'`{path}`{middle}说明书[{first}][{left}]–[{last}][{right}]'
    # A source paragraph range is document notation, never an application S
    # reference. Bind both endpoints to actual excerpts only when their file
    # identity and paragraph marker are unique in this execution.
    report=re.sub(r'`([^`\n]+\.(?:pdf|docx|md))`([^\n]{0,100}?)说明书\s*\[(\d{4})\]\s*[–—-]\s*\[(\d{4})\]',
                  bind_paragraph_range,report)
    def bind_blockquote(match):
        line=match[0]
        if report_references(line):return line
        quote=line[1:].strip().strip('“”「」')
        if len(quote)<12:return line
        matches=[item for item in evidence if item.get('type') in ('knowledge','upload','file','web')
                 and item.get('snippet') and _quote_supported(quote,item['snippet'])]
        if len(matches)!=1:return line
        item=matches[0]
        location=str(item.get('location') or '').strip()
        return line+f"[{item['id']}]"+(f'（{location}）' if location else '')
    def bind_block(match):
        block=match[0]
        # A multiline quote already bound at its end must stay intact. Adding
        # per-line location labels would insert non-source text into the quote.
        if report_references(block):return block
        return re.sub(r'(?m)^>[^\n]+$',bind_blockquote,block)
    report=re.sub(r'(?m)^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*',bind_block,report)
    date_proofs={}
    for item in evidence:
        proof=item.get('date_completeness') or {}
        if (proof.get('predicate') in ('date_conflicts','chronology_conflicts')
                and proof.get('complete') and proof.get('stage_scope')=='all'
                and not proof.get('filters') and proof.get('matched_count')==0
                and not item.get('rows')):
            date_proofs.setdefault(proof['predicate'],[]).append(item['id'])
    if date_proofs:
        def bind_absence_clause(match):
            clause=match[0]
            if re.search(r'\d{1,2}月|本月|当月|期间',clause):return clause
            predicate=('chronology_conflicts' if re.search(r'交局.{0,10}(?:早于|先于).{0,10}收案|日期先后',clause)
                       else 'date_conflicts' if '日期冲突' in clause else None)
            ids=date_proofs.get(predicate,[])
            if (len(ids)!=1 or not re.search(r'未查到|未发现|没有|(?:为|共|：|:)\s*0\s*件',clause)
                    or ids[0] in report_references(clause)):
                return clause
            return clause+'['+ids[0]+']'
        report=re.sub(r'[^。；\n]+(?=[。；\n]|$)',bind_absence_clause,report)
    file_sources=[item for item in evidence if item.get('type')=='file' and item.get('id')]
    if len(file_sources)==1:
        source=file_sources[0]
        lines=str(source.get('snippet') or '').splitlines()
        heading=next((i for i,line in enumerate(lines,1) if re.match(r'^#{1,6}\s*技术方案',line)),None)
        if heading and str(source.get('title') or '').lower().endswith('.md'):
            end=next((i-1 for i in range(heading+1,len(lines)+1) if lines[i-1].startswith('#')),len(lines))
            note=f"\n来源定位：{source['title']}，“技术方案”第{heading}–{end}行[{source['id']}]。"
            section=re.search(r'(?m)^#{1,4}[^\n]*(?:交底|附件)[^\n]*(?:核心技术|技术方案)[^\n]*$',report)
            if section and note.strip() not in report:
                report=report[:section.end()]+note+report[section.end():]
            elif note.strip() not in report and re.search(r'交底|附件',report):
                report+=note
            if re.search(r'交底',report):
                refs=report_references(report)
                locations=[]
                for item in evidence:
                    if item.get('id') not in refs or item.get('type')!='knowledge' or not item.get('location'):continue
                    path=str(item.get('source') or item.get('title') or '').replace('|','&#124;')
                    locations.append(f"| [{item['id']}] | {path} | {item['location']} |")
                if locations:
                    block='\n\n### 引用原文定位\n\n| 证据 | 原件 | 位置 |\n| --- | --- | --- |\n'+'\n'.join(locations)
                    if block.strip() not in report:report+=block
    available = {item.get('id') for item in evidence}
    def split(match):
        ids = re.findall(_ID, match[1])
        return ''.join('[' + i + ']' for i in dict.fromkeys(ids)) if set(ids) <= available else match[0]
    def canonical(match):
        candidate=unicodedata.normalize('NFKC',match[0]).replace('【','[').replace('】',']')
        valid=_VALID.fullmatch(candidate)
        return candidate if valid and valid[1] in available else match[0]
    report = _CANDIDATE.sub(canonical,report)
    report = _COMPOUND.sub(split, report)
    replacements=[]
    for match,group,quote,refs,cited,supported in _quoted_bindings(report,evidence):
        if supported:continue
        def same_page(item, source):
            page=re.match(r'第\s*(\d+)\s*页',str(source.get('location','')))
            other=re.match(r'第\s*(\d+)\s*页',str(item.get('location','')))
            return page and other and page[1]==other[1]
        matches=[item for item in evidence if item.get('snippet') and any(
            source.get('file_id') and source['file_id']==item.get('file_id') and
            source.get('revision')==item.get('revision') and same_page(item,source) for source in cited)
            and _literal(quote) in _literal(item['snippet'])]
        if not matches:continue
        # Equal repeated reads may have different IDs; every candidate must point
        # at the same source/version/page and contain the entire literal quote.
        identities={(i.get('file_id'),i.get('revision'),re.match(r'第\s*(\d+)\s*页',str(i.get('location','')))[1]) for i in matches}
        if len(identities)!=1:continue
        chosen=min(matches,key=lambda i:int(i['id'][1:]))
        replacements.append((match.start(group+1),match.end(group+1),'['+chosen['id']+']'))
    for start,end,text in reversed(replacements):report=report[:start]+text+report[end:]
    def _paragraph_text(snippet, marker, before=False):
        start=snippet.find(marker)
        if start<0:return ''
        if before:
            head=snippet[:start].rstrip()
            index=head.rfind('\n')
            return head[index+1:].strip() if index>=0 else head.strip()
        body=snippet[start+len(marker):start+len(marker)+240]
        return re.split(r'\[\d{4}\]', body)[0].strip()

    def bind_paragraph_marker(match):
        """Complete a paragraph reference the answer already quoted literally."""
        marker=match[0]
        line_start=report.rfind('\n',0,match.start())+1
        line_end=report.find('\n',match.end())
        line=report[line_start:len(report) if line_end<0 else line_end]
        if line.lstrip().startswith('>') or re.search(r'(?:原文|说明书|段落|段号|位置|出处)',line[:match.start()-line_start]):
            return marker
        cited=[item for item in evidence if item.get('id') in report_references(line)
               and item.get('type') in ('knowledge','file','upload')
               and marker in str(item.get('snippet') or '')]
        if len(cited)!=1:
            return marker
        after=report[match.end():len(report) if line_end<0 else line_end].lstrip(' ：:')
        tail=re.split(r'[。；]',after)[0][:40].strip()
        snippet=str(cited[0].get('snippet') or '')
        # The paragraph text must continue the cited snippet literally. A marker
        # used for something else is never renamed into a source position.
        if len(tail)>=8 and _literal(tail) in _literal(snippet):
            return '原文段落'+marker
        # A stage label written before the marker ("S1阶段：检测语音终点[0004]")
        # still quotes that paragraph; match its own text anywhere on the line.
        body=_paragraph_text(snippet,marker)
        prior=_paragraph_text(snippet,marker,before=True)
        for candidate in (body,prior):
            if len(candidate)>=8 and any(_literal(candidate)[i:i+8] in _literal(line)
                                     for i in range(len(candidate)-8)):
                return '原文段落'+marker
        return marker
    # A bare [0005] stays invalid unless the answer already quoted that very
    # paragraph of the cited document; then the position label is made explicit
    # so the existing check can bind it instead of dropping the whole answer.
    report=re.sub(r'\[\d{4}\]',bind_paragraph_marker,report)
    return report

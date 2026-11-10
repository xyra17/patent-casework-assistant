"""Located case-identity checks, separate from source-absence judgments.

This is deliberately a bounded Markdown reader, not a semantic claim verifier.
Unknown/missing bindings remain unverified; identity success is never fact proof.
"""
import re

CASE=re.compile(r'AI-\d{4}-\d{3,6}(?!\d)')
REF=re.compile(r'\[(S[1-9]\d*)\]')


def citation_units(report):
    offset=0;fenced=False;header=None;heading_case=None
    lines=report.splitlines(keepends=True)
    for number,line in enumerate(lines,1):
        text=line.rstrip('\r\n');at=offset;offset+=len(line)
        if text.lstrip().startswith('```'):
            fenced=not fenced;header=None;continue
        if fenced:continue
        if re.match(r'^#{1,6}\s',text):
            found=set(CASE.findall(text));heading_case=next(iter(found)) if len(found)==1 else None
            header=None;continue
        if text.lstrip().startswith('|'):
            cells=[m for m in re.finditer(r'(?:\\.|[^|])+',text) if m[0].strip()]
            if re.fullmatch(r'[\s|:\-]+',text):continue
            if header is None and number<len(lines) and re.fullmatch(r'[\s|:\-]+',lines[number]):
                header=[m[0] for m in cells]
                # A header names columns; it does not assert that an uploaded
                # excerpt in a sibling column belongs to the historical case.
                continue
            row_ids=set(CASE.findall(text))
            column_ids=[set(CASE.findall(h)) for h in (header or [])]
            has_case_columns=any(column_ids)
            row_case=next(iter(row_ids)) if len(row_ids)==1 and not has_case_columns else None
            for col,m in enumerate(cells):
                own=set(CASE.findall(m[0]));label=header[col] if header and col<len(header) else ''
                inherited=column_ids[col] if col<len(column_ids) else set()
                if not own and not inherited and not re.search('附件|交底要素|新方案',label):
                    inherited={row_case or heading_case} - {None}
                yield {'start':at+m.start(),'end':at+m.end(),'line':number,'column':col,
                       'text':m[0],'case_ids':sorted(own or inherited)}
            continue
        if text.strip():header=None
        # Split at unquoted sentence punctuation, retaining trailing citations.
        stack=[];start=0;closers={'“':'”','「':'」','『':'』','"':'"'};spans=[]
        i=0
        while i<len(text):
            ch=text[i]
            if stack and ch==stack[-1]:stack.pop()
            elif ch in closers:stack.append(closers[ch])
            elif ch in '。！？；' and not stack:
                end=i+1
                tail=re.match(r'(?:\s*\[S[1-9]\d*\])+',text[end:])
                if tail:end+=tail.end()
                spans.append((start,end));start=end;i=end-1
            i+=1
        if start<len(text):spans.append((start,len(text)))
        for left,right in spans:
            body=text[left:right];matches=list(CASE.finditer(body))
            # Multiple explicit subjects are checked individually rather than
            # exempting the entire sentence. A shared trailing cite does not
            # prove an earlier subject that lacks its own reference.
            cuts=[0]+[m.start() for m in matches[1:]]+[len(body)] if len(matches)>1 else [0,len(body)]
            for a,b in zip(cuts,cuts[1:]):
                unit=body[a:b];own=set(CASE.findall(unit))
                yield {'start':at+left+a,'end':at+left+b,'line':number,'column':None,
                       'text':unit,'case_ids':sorted(own or ({heading_case} if heading_case else set()))}


def _case_supported(item,cid):
    if item.get('task_scope_compatible') is False:return False
    if item.get('type')=='knowledge':
        return item.get('case_id')==cid
    if item.get('type')=='file':
        # A referenced case mention is not the identity of the uploaded original.
        title=str(item.get('title') or item.get('filename') or '').replace('\\','/').rsplit('/',1)[-1]
        named=re.match(re.escape(cid)+r'(?=[.\s_\-（(])',title)
        content=str(item.get('snippet') or item.get('content') or '')
        labeled=re.search(r'(?:所编号|案号|案件编号)\s*[:：|]\s*'+re.escape(cid)+r'(?!\d)',content)
        return bool(named and labeled)
    if item.get('type')=='database':
        columns=item.get('columns') or []
        indexes=[i for i,name in enumerate(columns) if name in ('所编号','案号','案件编号','case_id')]
        return any(isinstance(row,(list,tuple)) and any(i<len(row) and row[i]==cid for i in indexes)
                   for row in item.get('rows',[]))
    return False


def case_ref_identity_checks(report,evidence):
    by_id={e.get('id'):e for e in evidence};checks=[]
    for unit in citation_units(report):
        refs=REF.findall(unit['text'])
        for cid in unit['case_ids']:
            if not refs:status='unverified';code='case_ref_missing'
            elif any(_case_supported(by_id.get(ref,{}),cid) for ref in refs):
                status='identity_bound';code='case_identity_bound'
            else:status='failed';code='case_ref_mismatch'
            checks.append({'claim_id':f"case-{unit['start']}-{cid}",'case_id':cid,
                'evidence_ids':refs,'span':{'start':unit['start'],'end':unit['end']},
                'line':unit['line'],'column':unit['column'],'status':status,'code':code,
                'scope':'case_identity_only_not_fact_verification'})
    return checks


def case_ref_identity_issues(report,evidence):
    return ['案件引用身份不匹配：'+c['case_id']+'的声明所引证据未绑定该案；位置'+str(c['span']['start'])+'–'+str(c['span']['end'])+'。'
            for c in case_ref_identity_checks(report,evidence) if c['status']=='failed']

"""Positive, source-bound quotation verification for conservative partial delivery.

Only quotation content is released. Surrounding model explanations, inferred
similarities and task-level conclusions do not inherit literal verification.
"""
import hashlib,re,html,json,math
from tools.evidence_refs import _QUOTED_REF,_quote_supported,citation_issues,quoted_text_group
from tools.citation_scope import case_ref_identity_checks


def verify_literal_claims(report,evidence):
    by_id={e.get('id'):e for e in evidence}
    identities=case_ref_identity_checks(report,evidence)
    claims=[]
    for match in _QUOTED_REF.finditer(report):
        group=quoted_text_group(match)
        quote=match[group];refs=re.findall(r'\[(S[1-9]\d*)\]',match[group+1])
        related=[c for c in identities if c['span']['start']<=match.start()<c['span']['end']]
        failed_identity=any(c['status']=='failed' for c in related)
        valid=[]
        for ref in refs:
            item=by_id.get(ref,{})
            text=item.get('snippet') or item.get('content') or ''
            if (not failed_identity and item.get('task_scope_compatible') is not False
                    and item.get('type') in ('knowledge','file','upload','web')
                    and (item.get('location') or item.get('type')=='web' and item.get('url'))
                    and _quote_supported(quote,text)):
                valid.append(ref)
        cid='literal-'+hashlib.sha256((str(match.start())+quote).encode()).hexdigest()[:16]
        claims.append({'claim_id':cid,'text':quote,'span':{'start':match.start(),'end':match.end()},
            'evidence_ids':valid if valid else refs,'status':'verified_literal' if valid else 'unverified',
            'code':'literal_source_match' if valid else 'case_ref_mismatch' if failed_identity else 'literal_support_unverified',
            'verification_scope':'source_excerpt_only_not_surrounding_inference'})
    return claims


def partial_delivery(report,evidence):
    claims=verify_literal_claims(report,evidence);by_id={e.get('id'):e for e in evidence}
    output=[];seen=set();published=[]
    for claim in claims:
        if claim['status']!='verified_literal':continue
        source=by_id[claim['evidence_ids'][0]];quote=claim['text']
        key=(quote,source['id'])
        if key in seen:continue
        # Render only source-attributed text, never an unverifiable draft title
        # or the model's assertion that this proves a mechanism or comparison.
        location=re.sub(r'[\r\n|\[\]<>`]',' ',str(source.get('location') or source.get('url')))
        title=re.sub(r'[\r\n|\[\]<>`]',' ',str(source.get('source') or source.get('title') or '来源'))
        block='原件：'+title+'；位置：'+location+'\n\n'+''.join('> '+line+'\n' for line in quote.splitlines()).rstrip()+f" [{source['id']}]"
        # Rendering must itself preserve valid citations/quotes. Positive
        # original verification alone cannot validate a transformed output.
        if citation_issues(block,evidence):continue
        output.append(block);seen.add(key);published.append(claim['claim_id'])
    # Structured source rows can be positively verified without validating a
    # model's derived counts or its claim that the query covered the right scope.
    # Reconstruct cells from the observed schema/rows; never recycle draft rows.
    def cell(value):
        text='未记录' if value is None else str(value)
        return html.escape(text).replace('|','&#124;').replace('[','&#91;').replace(']','&#93;').replace('\n','<br>')
    remaining_rows=500
    for source in evidence:
        # These are independently reconstructed source records, not support
        # assigned to a model claim. Missing draft citations cannot erase data
        # already returned by an authorized tool in this execution.
        if (not re.fullmatch(r'S[1-9]\d*',str(source.get('id')or'')) or source.get('type')!='database'
                or source.get('task_scope_compatible') is False or remaining_rows<=0):continue
        columns=source.get('columns');rows=source.get('rows')
        if (not isinstance(columns,list) or not columns or not all(isinstance(c,str) for c in columns)
                or len(columns)!=len(set(columns)) or not isinstance(rows,list)):continue
        selected=[]
        for index,row in enumerate(rows[:remaining_rows]):
            if (not isinstance(row,(list,tuple)) or len(row)!=len(columns)
                    or any(not isinstance(v,(str,int,float,bool,type(None))) or isinstance(v,float) and not math.isfinite(v) for v in row)):
                continue
            cid='row-'+hashlib.sha256(json.dumps([source['id'],index,row],ensure_ascii=False).encode()).hexdigest()[:16]
            claims.append({'claim_id':cid,'status':'verified_source_row','evidence_ids':[source['id']],
                           'row_index':index,'column_names':columns,'row_values':list(row),
                           'verification_scope':'observed_source_cells_only_not_query_scope_or_derived_conclusion'})
            selected.append(row);published.append(cid)
        if not selected:continue
        remaining_rows-=len(selected)
        title=cell(source.get('title')or'查询结果')
        table='| '+' | '.join([*[cell(c) for c in columns],'引用'])+' |\n| '+' | '.join('---' for _ in range(len(columns)+1))+' |\n'
        table+='\n'.join('| '+' | '.join([*[cell(v) for v in row],f"[{source['id']}]"])+' |' for row in selected)
        output.append('来源返回的数据行：'+title+f" [{source['id']}]\n\n"
            +'以下仅逐格核对来源返回值，不代表客户筛选、统计派生或整项任务已验收；未展示行也不被认定为空。\n\n'+table)
    body=('## 已核对的来源内容\n\n仅核对以下引文或数据单元格与本次来源的一致性；尚未核实的归纳、比较及完整任务结论不予发布。\n\n'
          +'\n\n'.join(output)) if output else '部分内容的引用未通过核验，暂不展示；尚未取得可独立核验并发布的声明。'
    return body,{'candidate_sha256':hashlib.sha256(report.encode()).hexdigest(),
        'claims':claims,'published_claim_ids':published,
        'policy':'positive_literal_or_structured_cell_verification_only','task_complete':False}

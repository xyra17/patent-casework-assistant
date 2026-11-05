import json
import hashlib
from pathlib import Path

import pytest
from api import context, evidence
from tools.attachment_compare import compare_uploaded_files
from tools.evidence_refs import citation_issues


@pytest.fixture
def workspace(tmp_path):
    context.set_session('attachment-comparison-test',str(tmp_path))
    evidence.reset('attachment-comparison-test')
    root=tmp_path/'attachments';root.mkdir()
    yield root
    evidence.clear('attachment-comparison-test')
    context.reset_session()


def compare(**args):
    return json.loads(compare_uploaded_files.invoke(args))


def test_document_changes_keep_original_text_locations_and_two_sources(workspace):
    from docx import Document
    for name,lines in [('old.docx',['技术方案','采集输入信号','固定阈值判断','输出结果']),
                       ('new.docx',['技术方案','采集输入信号','自适应阈值判断','记录异常分支','输出结果'])]:
        doc=Document()
        for line in lines:doc.add_paragraph(line)
        doc.save(workspace/name)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir()}
    result=compare(left_filename='old.docx',right_filename='new.docx')
    assert result['status']=='compared'
    change=result['changes'][0]
    assert '固定阈值判断' in str(change['left']) and '自适应阈值判断' in str(change['right'])
    assert '正文块 3' in str(change['left'])
    refs=evidence.snapshot('attachment-comparison-test')
    assert len(refs)==2 and all(ref['type']=='file' for ref in refs)
    assert not citation_issues('差异依据[S1][S2]',refs)
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir()}==before


def test_pdf_locations_and_no_text_failure(workspace):
    from reportlab.pdfgen.canvas import Canvas
    for name,text in [('old.pdf','old threshold'),('new.pdf','adaptive threshold')]:
        canvas=Canvas(str(workspace/name));canvas.drawString(60,700,text);canvas.save()
    result=compare(left_filename='old.pdf',right_filename='new.pdf')
    assert '第 1 页' in str(result['changes'])
    canvas=Canvas(str(workspace/'blank.pdf'));canvas.showPage();canvas.save()
    assert '未解析出' in compare(left_filename='old.pdf',right_filename='blank.pdf')['message']
    (workspace/'broken.pdf').write_bytes(b'invalid pdf')
    assert compare(left_filename='old.pdf',right_filename='broken.pdf')['status']=='error'


def test_table_diff_uses_keys_instead_of_row_positions(workspace):
    (workspace/'old.csv').write_text('案号,代理师,期限\n001,甲,2026-11-01\n002,乙,2026-11-02\n003,丙,\n')
    (workspace/'new.csv').write_text('案号,代理师,期限\n002,乙,2026-11-02\n001,丁,2026-11-03\n004,戊,\n')
    result=compare(left_filename='old.csv',right_filename='new.csv',key_columns=['案号'])
    assert result['total_changes']==3
    modified,deleted,added=result['changes']
    assert modified['key']=={'案号':'001'} and modified['changed_fields']==['代理师','期限']
    assert modified['left']['row']==2 and modified['right']['row']==3
    assert deleted['kind']=='deleted' and added['kind']=='added'
    assert added['right']['values']['期限'] is None


def test_table_alignment_reads_rows_beyond_first_query_page(workspace):
    rows=['案号,阶段']+[f'{n:04},撰写中' for n in range(550)]
    (workspace/'old.csv').write_text('\n'.join(rows))
    rows[-1]='0549,客户审核'
    (workspace/'new.csv').write_text('\n'.join(rows))
    result=compare(left_filename='old.csv',right_filename='new.csv',key_columns=['案号'])
    assert result['total_changes']==1 and result['changes'][0]['key']=={'案号':'0549'}
    assert result['changes'][0]['right']['row']==551


@pytest.mark.parametrize('contents,reason',[
    ('案号,阶段\n,撰写中\n','关联键缺失'),
    ('案号,阶段\n001,撰写中\n001,客户审核\n','关联键重复'),
    ('案号,案号\n001,002\n','表头存在重名'),
])
def test_invalid_keys_or_headers_do_not_produce_comparison_evidence(workspace,contents,reason):
    (workspace/'bad.csv').write_text(contents)
    (workspace/'good.csv').write_text('案号,阶段\n001,撰写中\n')
    result=compare(left_filename='bad.csv',right_filename='good.csv',key_columns=['案号'])
    assert result['status']=='error' and reason in result['message']
    assert not evidence.snapshot('attachment-comparison-test')


def test_excel_sheet_choice_and_schema_changes_are_explicit(workspace):
    import pandas as pd
    for name,field in [('old.xlsx','代理师'),('new.xlsx','承办人')]:
        with pd.ExcelWriter(workspace/name) as writer:
            pd.DataFrame({'案号':['001']}).to_excel(writer,sheet_name='首页',index=False)
            pd.DataFrame({'案号':['001'],field:['甲']}).to_excel(writer,sheet_name='案件信息',index=False)
    result=compare(left_filename='old.xlsx',right_filename='new.xlsx',key_columns=['案号'],left_sheet='案件信息',right_sheet='案件信息')
    assert result['left']['sheet']=='案件信息' and result['changes'][0]['kind']=='schema'
    assert result['changes'][1]['changed_fields']==['代理师','承办人']
    bad=compare(left_filename='old.xlsx',right_filename='new.xlsx',key_columns=['不存在'])
    assert bad['status']=='error' and '关联列不存在' in bad['message']


def test_pagination_is_explicit_and_unchanged_text_is_not_a_fake_difference(workspace):
    (workspace/'old.txt').write_text('\n'.join(f'旧内容{n}' for n in range(30)))
    (workspace/'new.txt').write_text('\n'.join(f'新内容{n}' for n in range(30)))
    first=compare(left_filename='old.txt',right_filename='new.txt',limit=1)
    assert first['truncated'] and first['next_offset']==1
    second=compare(left_filename='old.txt',right_filename='new.txt',offset=1,limit=100)
    assert len(first['changes'])+len(second['changes'])==first['total_changes']
    assert not second['truncated']
    (workspace/'new.txt').write_bytes((workspace/'old.txt').read_bytes())
    same=compare(left_filename='old.txt',right_filename='new.txt')
    assert same['total_changes']==0 and not same['changes']


def test_only_real_current_session_attachments_and_valid_parameters(workspace):
    (workspace/'one.txt').write_text('本会话输入')
    outside=workspace.parent.parent/'outside.txt';outside.write_text('其他会话')
    (workspace/'linked.txt').symlink_to(outside)
    for args in [dict(right_filename='linked.txt'),dict(right_filename='../outside.txt'),
                 dict(right_filename='one.txt'),dict(right_filename='missing.txt'),
                 dict(right_filename='one.txt',offset=-1),dict(right_filename='one.txt',limit=101)]:
        assert compare(left_filename='one.txt',**args)['status']=='error'


def test_format_names_do_not_override_explicit_no_file_delivery():
    from agent.main_agent import TaskPlan,_normalize_plan,classify_request
    query='比较两个附件。\n【本次附件对比顺序】基准文件："old.pdf"；对比文件："new.pdf"。\n不要生成文件。'
    assert classify_request(query)==(False,False)
    plan=_normalize_plan(TaskPlan(task_type='report',require_report=True,require_pdf=True),query,['old.pdf','new.pdf'])
    assert not plan.require_report and not plan.require_pdf and not plan.output_artifacts


def test_department_examples_and_frontend_fallback_share_one_catalog():
    from tools.semantic_layer import _load_yaml
    root=Path(__file__).resolve().parents[1]
    shared=json.loads((root/'config/session-examples.json').read_text())
    experience=_load_yaml(root/'examples/department_shared_drive/semantic.yaml')['experience']
    assert experience['quick_templates']==shared['templates']
    assert any(set(t['allowed_sources'])=={'db_query','knowledge','web_search'} for t in shared['templates'])
    assert any(t.get('attachments',{}).get('min')==2 and not t['allowed_sources'] for t in shared['templates'])

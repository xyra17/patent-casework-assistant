from casework.parser import parse
import pytest
from io import BytesIO
from zipfile import ZipFile
from datetime import date
from casework.analytics import active

@pytest.mark.parametrize('stage',['技术交底','初稿撰写','客户审核'])
def test_common_active_stages(stage):
    assert active({'stage':stage},date(2026,11,3))
    assert not active({'stage':stage,'filing_verified':True,'actual_filed_date':'2026-10-01'},date(2026,11,3))

def test_unknown_stage_is_not_silently_active():
    assert not active({'stage':'未登记状态'},date(2026,11,3))

def test_vertical_case_form_preserves_dates_and_false():
    raw='字段,内容\n所编号,AI-2026-001\n收案日期,2026-10-01\n交局核实状态,false\n'.encode()
    item=parse(raw,'AI-2026-001.案件信息.csv')
    values={c['field']:c['value'] for c in item['claims']}
    assert values['received_date']=='2026-10-01'
    assert values['filing_confirmed'] is False

def test_vertical_meeting_retains_content_and_location():
    raw='字段,内容\n所编号,AI-2026-001\n会议日期,2026-10-03\n参会代理师,甲、乙\n会议内容,核对稀疏注意力选择路径\n待补材料,补充索引负例\n'.encode()
    meeting=parse(raw,'AI-2026-001.会议.csv')['meetings'][0]
    assert meeting['agents']==['甲','乙']
    assert meeting['date']=='2026-10-03'
    assert meeting['pending']=='补充索引负例'
    assert '第' in meeting['location']

def test_vertical_form_rejects_cross_case_identity():
    raw='字段,内容\n所编号,AI-2026-002\n收案日期,2026-10-01\n'.encode()
    with pytest.raises(ValueError,match='不一致'):
        parse(raw,'AI-2026-001.案件信息.csv')

def test_horizontal_meetings_still_parse():
    raw='所编号,会议日期,参会代理师,会议内容\nAI-2026-001,2026-10-03,甲,核对边界\n'.encode()
    assert parse(raw,'AI-2026-001.会议.csv')['meetings'][0]['content']=='核对边界'

def test_valid_xlsx_without_optional_dimension():
    # Minimal standards-compliant fixture, deliberately no <dimension> element.
    output=BytesIO()
    with ZipFile(output,'w') as archive:
        archive.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        archive.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="案件信息" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        rows=[('所编号','AI-2026-001'),('收案日期','2026-10-01')]
        xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for i,values in enumerate(rows,1):
            xml+=f'<row r="{i}">'+''.join(f'<c r="{col}{i}" t="inlineStr"><is><t>{value}</t></is></c>' for col,value in zip('AB',values))+'</row>'
        archive.writestr('xl/worksheets/sheet1.xml',xml+'</sheetData></worksheet>')
    item=parse(output.getvalue(),'AI-2026-001.案件信息.xlsx')
    assert next(c['value'] for c in item['claims'] if c['field']=='received_date')=='2026-10-01'

"""Driver ownership and amount parity across the displayed, printable and Excel views."""
import io
import zipfile
from xml.etree import ElementTree as ET
from pypdf import PdfReader
from app.accounting_statements import filter_statement
from app.accounting_export import COLUMNS, MONEY, contract, render


def statement_fixture():
    rows=[]
    for identifier,name,day,shortage,pending,surplus in (
        ('one','DRIVER ONE','2026-10-30',175,0,0),
        ('one','DRIVER ONE','2026-10-31',0,225,0),
        ('two','DRIVER TWO','2026-10-31',0,0,300),
        ('three','DRIVER THREE','2026-10-31',800,0,0),
    ):
        row={k:0 for k in COLUMNS['driver_statement'] if k in MONEY or k=='completed_orders'}
        row.update(driver_id=identifier,driver=name,day=day,completed_orders=1,system_total=15000,
                   cash_due=10000,bank=5000,cash_received=10000-shortage-pending+surplus,
                   shortage=shortage,pending_shortage=pending,surplus=surplus,status='Under review' if pending else 'Reviewed',
                   approval_status='Awaiting manager-day accounting approval',approval_references='',branch_names='BRANCH ONE',
                   shift_references='D-000001',notes='',incident_notes='',traffic_notes='',adjustments='')
        rows.append(row)
    drivers=[dict(driver_id=identifier,driver=name,shortage=sum(r['shortage'] for r in rows if r['driver_id']==identifier),
                  pending_shortage=sum(r['pending_shortage'] for r in rows if r['driver_id']==identifier),surplus=sum(r['surplus'] for r in rows if r['driver_id']==identifier))
             for identifier,name in [('one','DRIVER ONE'),('two','DRIVER TWO'),('three','DRIVER THREE')]]
    return dict(group='driver_statement',rows=rows,drivers=drivers,settlements=[],adjustment_history=[],incidents=[],
                branches=[{'id':'b1','name':'BRANCH ONE'}],period={'start':'2026-10-30','end':'2026-10-31'},definitions='TEST ONLY')


def excel_rows(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        doc=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    return [[cell.find('s:v',ns).text if cell.find('s:v',ns) is not None else ''.join(cell.itertext())
             for cell in row] for row in doc.findall('s:sheetData/s:row',ns)]


def test_shortage_only_keeps_pending_separate_without_netting_other_drivers():
    source=statement_fixture()
    selected=filter_statement(source,'one,two','shortage_any')
    displayed=contract(selected,'en')
    assert [(r['driver_id'],r['day']) for r in displayed['rows']]==[('one','2026-10-30'),('one','2026-10-31')]
    assert displayed['totals']['shortage']==175 and displayed['totals']['pending_shortage']==225
    assert displayed['totals']['surplus']==0
    assert [r['driver_id'] for r in displayed['drivers']]==['one']
    assert displayed['drivers'][0]['shortage']==175 and displayed['drivers'][0]['pending_shortage']==225
    assert len(source['rows'])==4  # Export filtering must not rewrite the original snapshot.


def test_multidriver_json_pdf_excel_keep_identical_subtotals_and_grand_total():
    selected=filter_statement(statement_fixture(),'one,two')
    displayed=contract(selected,'en')
    columns=[c['key'] for c in displayed['columns']]
    sheet=excel_rows(render(selected,'xlsx','en'))
    # Daily rows remain together under their driver, followed by that driver's total.
    assert [r[0] for r in sheet[1:]]==['DRIVER ONE','DRIVER ONE','Driver total / DRIVER ONE','DRIVER TWO','Driver total / DRIVER TWO','الإجمالي / Total']
    grand=sheet[-1]
    for key,total in displayed['totals'].items():
        if key in MONEY or key=='completed_orders':assert float(grand[columns.index(key)])==(total/100 if key in MONEY else total)
    assert float(sheet[3][columns.index('shortage')])==1.75
    assert float(sheet[3][columns.index('pending_shortage')])==2.25
    assert float(sheet[5][columns.index('surplus')])==3
    # The actual printable PDF carries both selected-driver totals and the review scope.
    document=PdfReader(io.BytesIO(render(selected,'pdf','en')))
    printed='\n'.join(page.extract_text() for page in document.pages)
    assert 'DRIVER ONE' in printed and 'DRIVER TWO' in printed and 'DRIVER THREE' not in printed
    assert 'Selected driver totals' in printed and 'Selected total' in printed
    assert '1.75' in printed and '2.25' in printed and '3.00' in printed
    assert 'Bank matching remains a separate evidence scope.' in printed

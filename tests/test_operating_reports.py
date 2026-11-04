"""Operational reporting must never manufacture an accounting approval."""
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO
from urllib.parse import unquote

import pytest
from pypdf import PdfReader
from app import db, operating_reports as reports
from test_operations import ops, delivered


def example(approved=False):
    rows = [dict(name=name, period=period, closed=True,
                 brands={'kps':dict(count=10,amount=30000),'enbishan':dict(count=5,amount=15000)}, net=45000)
            for name,period in [('عمرو','morning'),('النجاشي','evening'),('رياض','evening')]]
    approval = dict(id='AC-1025',manager_shift_id='manager',approved_name='محمد أحمد',created_at='2026-10-26T11:30:00+00:00')
    entries = [dict(balance_approved=True,cash_recorded=True,reconciliation_complete=True,net_difference_halalas=0,overdue=True)]
    accounting = reports.progress(entries,[approval] if approved else [],['manager'])
    return dict(day='2026-10-25',scope='day',filename=reports.filename('2026-10-25','day'),manager='علي',
                rows=rows,cases=[],accounting=accounting,count=45,net=135000,operational_complete=True)


def test_progress_requires_final_day_approval_and_preserves_late_history():
    entry=dict(balance_approved=True,cash_recorded=False,reconciliation_complete=False,overdue=True)
    pending=reports.progress([entry],[],['manager'])
    assert pending['percent']==33 and pending['overdue']==1 and not pending['approved']
    entry.update(cash_recorded=True,reconciliation_complete=True,net_difference_halalas=-100,variance_pending=True)
    assert reports.progress([entry],[],['manager'])['percent']==50
    entry.update(variance_pending=False,variance_confirmation={'id':'audit'})
    reviewed=reports.progress([entry],[],['manager'])
    assert reviewed['percent']==75 and reviewed['late']==1 and not reviewed['approved']
    approved=reports.progress([entry],[dict(manager_shift_id='manager')],['manager'])
    assert approved['percent']==100 and approved['approved'] and approved['late']==1
    assert not reports.progress([],[],[])['approved']
    assert not reports.progress([entry],[dict(manager_shift_id='other')],['manager'])['approved']
    assert not reports.progress([entry],[dict(manager_shift_id='manager')],['manager'],pending_managers=1)['approved']


def test_snapshot_goods_do_not_reprice_or_double_deduct_free_goods():
    paid=dict(id='paid',total_halalas=1000,fulfilled_in_report=True,snapshot={})
    free=dict(id='gift',total_halalas=0,payment_status='not_required',fulfilled_in_report=True,
              snapshot={'complimentary':dict(kind='hospitality',catalog_value_halalas=2000,reason='ضيافة',authorized_by='owner')})
    snap=dict(orders=[paid,paid,free],refunds=[dict(amount_halalas=100,method='cash',order_id='paid',reason='مرتجع'),
                                           dict(amount_halalas=1000,record_kind='test_void')],external_enbishan=[dict(order_count=2,total_halalas=3000)])
    original=deepcopy(snap)
    data=reports.project_driver({'brand_id':'kps'},snap)
    assert data['net']==3900
    assert data['brands']['kps']==dict(count=2,amount=1000)
    assert data['brands']['enbishan']==dict(count=2,amount=3000)
    assert data['cases'][0]['amount']==2000 and len(data['cases'])==2
    assert snap==original


def test_filename_uses_selected_operational_day_weekday_not_export_time():
    name=reports.filename(date(2026,10,23),'day')
    assert 'الجمعة' in name and '23-10-2026' in name
    assert reports.filename('2026-10-27','day') == 'تشغيلي الثلاثاء 27-10-2026.pdf'
    assert unquote(reports.disposition(name).split("UTF-8''")[1])==name


@pytest.mark.parametrize('approved',[False,True])
@pytest.mark.parametrize('cases',[0,2,5])
def test_pdf_one_landscape_sheet_with_all_cases_and_real_approval_status(approved,cases):
    report=example(approved)
    report['cases']=[dict(driver='عمرو',brand='kps',kind=kind,amount=1000,reference=f'CASE-{i}',reason='سبب موثق',approval='معتمد')
                     for i,kind in enumerate(['ضيافة','تعويض','إعلان','مرتجع','خطأ طباعة'][:cases])]
    original=deepcopy(report)
    pdf=reports.make_pdf(report)
    reader=PdfReader(BytesIO(pdf))
    assert len(reader.pages)==1
    page=reader.pages[0]
    assert page.mediabox.width>page.mediabox.height
    assert '1,350' in page.extract_text()
    for i in range(cases):assert f'CASE-{i}' in page.extract_text()
    assert ('AC-1025' in page.extract_text()) is approved
    assert report==original
    html=reports.make_html(report)
    assert ('معتمد محاسبيًا' in html) is approved
    assert ('محمد أحمد' in html) is approved


def test_mobile_and_print_escape_staff_content():
    report=example();report['manager']='<script>alert(1)</script>'
    report['rows'][0]['name']='<img src=x onerror=alert(1)>'
    text=reports.make_html(report)
    assert '<script>alert' not in text and '&lt;script&gt;' in text
    assert '<img src=x' not in text and '&lt;img' in text


def test_wide_day_is_still_one_sheet_and_no_driver_is_silently_dropped():
    report=example()
    report['rows']=[dict(deepcopy(report['rows'][0]),name=f'سائق {i}') for i in range(15)]
    data=PdfReader(BytesIO(reports.make_pdf(report)))
    assert len(data.pages)==1


def test_manager_scope_cannot_cross_brand_or_revoked_branches():
    user=dict(id='morning',role='delivery_manager',brand_id='kps',branch_ids=['b'])
    shift=dict(id='s',kind='manager',user_id='morning',brand_id='kps',branch_ids=['b'])
    assert reports.allowed_manager(user,shift,'shift')
    assert not reports.allowed_manager(user,dict(shift,user_id='other'),'shift')
    assert not reports.allowed_manager(user,dict(shift,brand_id='enbishan'),'day')
    assert not reports.allowed_manager(user,dict(shift,branch_ids=['outside']),'day')
    assert not reports.allowed_manager(user,dict(shift,branch_ids=[]),'day')


@pytest.mark.postgres
def test_real_live_shift_report_is_read_only_and_never_claims_accounting_approval(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    delivered(post,order,driver)
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(manager,)).fetchone()
        day=(shift.get('reporting_operational_date') or shift.get('delivery_operational_date') or
             (shift['opened_at'].astimezone(reports.TZ)-timedelta(hours=4)).date())
        before=c.execute('SELECT count(*) n FROM closure_reports').fetchone()['n']
    response=actor('manager').get('/api/operating-reports',params={'day':day.isoformat()})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['count']==1 and data['net']==1000 and len(data['rows'])==1
    assert not data['accounting']['approved'] and data['accounting']['percent']==0
    assert not data['operational_complete']
    pdf=actor('manager').get('/api/operating-reports/export/pdf',params={'day':day.isoformat()})
    assert pdf.status_code==200,pdf.text if pdf.status_code!=200 else ''
    assert len(PdfReader(BytesIO(pdf.content)).pages)==1
    assert 'filename*=UTF-8' in pdf.headers['content-disposition']
    assert actor('driver').get('/api/operating-reports').status_code==403
    assert actor('other').get('/api/operating-reports',params={'day':str(day)}).json()['rows']==[]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports').fetchone()['n']==before
        c.execute("UPDATE shifts SET delivery_window_snapshot='{}'::jsonb||%s WHERE id=%s",(db.json({'period':'morning'}),manager))
    assert actor('manager').get('/api/operating-reports',params={'day':str(day),'scope':'day'}).status_code==403
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    hidden=actor('manager').get('/api/operating-reports',params={'day':str(day)})
    assert hidden.status_code==200 and hidden.json()['rows']==[]


@pytest.mark.postgres
def test_actual_accountant_stages_update_percent_and_only_final_day_adds_named_stamp(ops):
    from test_accounting import accountant
    from test_driver_accounting_settlements import make_report
    from test_accounting_driver_statements import balance,cash
    actor,post,_,_=ops
    accountant();original,opened=make_report()
    params={'day':opened.date().isoformat(),'scope':'day'}
    def status():
        response=actor('manager').get('/api/operating-reports',params=params)
        assert response.status_code==200,response.text
        return response.json()['accounting']
    assert status()['percent']==0 and status()['overdue']==1
    balance(post)
    assert status()['percent']==33 and not status()['approved']
    cash(post,revision=1)
    assert status()['percent']==66 and not status()['approved']
    approved=post('accountant','/api/accounting/manager-cards/settlement-manager/approve-day',{'note':'TEST final day reviewed'})
    final=status()
    assert final['percent']==100 and final['approved'] and final['late']==1 and final['overdue']==0
    assert final['approvals'][0]['id']==approved['id']
    assert final['approvals'][0]['approved_name']=='TEST ACCOUNTANT'
    with db.tx() as c:
        assert c.execute("SELECT snapshot FROM closure_reports WHERE id='settlement-report'").fetchone()['snapshot']==original


@pytest.mark.parametrize('network,cash,shortage,surplus,status',[(32000,18000,0,0,'متوافق'),(32000,16000,2000,0,'عجز'),(32000,19000,0,1000,'فائض'),(None,0,None,None,'بانتظار البيانات')])
def test_order_value_matches_card_plus_cash(network,cash,shortage,surplus,status):
    assert reports.matching(dict(net=50000,network=network,cash=cash))==dict(shortage=shortage,surplus=surplus,status=status)


def test_collection_channels_do_not_use_handover_or_count_voids_or_duplicate_receipts():
    paid=dict(id='paid',fulfilled_in_report=True,total_halalas=50000)
    receipt=dict(id='card',order_id='paid',method='card',amount_halalas=32000)
    snapshot=dict(orders=[paid,dict(id='void',test_void=True,fulfilled_in_report=False)],collections=[receipt,receipt,dict(id='cash',order_id='paid',method='cash',amount_halalas=18000),dict(id='voidcash',order_id='void',method='cash',amount_halalas=999)],refunds=[],shift=dict(submission=dict(handover_halalas=99999)))
    assert reports.collection_channels(snapshot)==(32000,18000)
    snapshot['refunds']=[dict(method='cash',amount_halalas=1000)]
    assert reports.collection_channels(snapshot)==(32000,17000)
    assert reports.collection_channels(dict(evidence_missing=True))==(None,None)
    assert reports.collection_channels(dict(orders=[]))==(None,None)
    snapshot['external_enbishan']=[dict(total_halalas=500)]
    assert reports.collection_channels(snapshot)==(None,None)


def test_totals_keep_shortage_and_surplus_separate_and_missing_not_zero():
    report=example()
    report['rows']=report['rows'][:2]
    report['rows'][0].update(network=30000,cash=13000,city='الدمام',branch='أحد')
    report['rows'][1].update(network=30000,cash=16000,city='حفر الباطن',branch='المروج')
    report['net']=90000
    head,rows,total,cases=reports.report_tables(report)
    assert total[head.index('العجز')]=='20'
    assert total[head.index('الفائض')]=='10'
    assert rows[0][head.index('المطابقة')]=='عجز'
    assert rows[1][head.index('المطابقة')]=='فائض'
    assert 'الدمام' in reports.make_html(report)
    report['rows'][0]['cash']=None
    assert reports.report_tables(report)[2][head.index('كاش')]=='—'


@pytest.mark.parametrize('manager_ended,driver_ended,evidence_missing',[(False,False,False),(True,False,False),(False,True,False),(True,True,False),(True,True,True)])
def test_build_projects_scoped_live_collections_and_locations_without_writes(monkeypatch,manager_ended,driver_ended,evidence_missing):
    from app import accounting_statements
    user=dict(id='owner',role='owner',brand_id='kps',display_name='الإدارة',branch_ids=['b'])
    manager=dict(id='manager',user_id='owner',brand_id='kps',branch_ids=['b'],status='open',delivery_window_snapshot={})
    driver=dict(id='driver',user_id='u',brand_id='kps',branch_ids=['b'],display_name='سائق',status='open')
    if manager_ended:manager['staffing_ended_at']='2026-10-28T01:00:00+00:00'
    if driver_ended:driver.update(status='submitted',staffing_ended_at='2026-10-28T00:30:00+00:00')
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Cursor:
        def execute(self,query,args):
            assert query.startswith('SELECT')
            if query.startswith('SELECT id,name,city'):return Result([dict(id='b',name='أحد',city='الدمام')])
            if "s.kind='manager'" in query:return Result([manager])
            if "s.kind='driver'" in query:return Result([driver])
            if 'accounting_daily_closures' in query or 'closure_reports' in query:return Result([])
            raise AssertionError(query)
    monkeypatch.setattr(reports.multibrand,'enabled',lambda:False)
    monkeypatch.setattr(reports.auth,'has_permission',lambda *a:True)
    monkeypatch.setattr(accounting_statements,'manager_cards',lambda *a,**k:dict(cards=[]))
    snap=dict(orders=[dict(id='o',branch_id='b',total_halalas=50000,fulfilled_in_report=True)],refunds=[],collections=[dict(id='1',order_id='o',method='cash',amount_halalas=18000),dict(id='2',order_id='o',method='card',amount_halalas=32000)])
    if evidence_missing:
        snap['evidence_missing']=True
        driver['status']='closed'
    monkeypatch.setattr(reports,'snapshot',lambda *a:(deepcopy(snap),None))
    result=reports.build(Cursor(),user,date(2026,10,27),'day')
    row=result['rows'][0]
    assert row['city']=='الدمام' and row['branch']=='أحد'
    if evidence_missing:
        assert row['cash'] is None and row['network'] is None
        assert row['evidence_missing'] and result['evidence_missing']
    else:
        assert row['cash']==18000 and row['network']==32000
        assert row['status']=='متوافق' and row['shortage']==row['surplus']==0
    assert row['closed'] is (driver_ended and not evidence_missing)
    assert result['operational_complete'] is (manager_ended and driver_ended and not evidence_missing)
    assert not result['accounting']['approved']
    assert result['accounting']['percent']<100


def test_busy_pdf_paginates_all_rows_cases_and_repeats_headings_at_readable_size():
    report=example(True)
    report['rows']=[dict(deepcopy(report['rows'][0]),name=f'DRIVER-{i:03}',
                         network=30000,cash=15000,city='الدمام',branch='أحد') for i in range(95)]
    report['cases']=[dict(driver=f'DRIVER-{i:03}',brand='kps',kind='ضيافة',amount=1000,
                          reference=f'CASE-{i:03}',reason='سبب موثق',approval='معتمد') for i in range(80)]
    original=deepcopy(report)
    pdf=reports.make_pdf(report)
    reader=PdfReader(BytesIO(pdf))
    texts=[page.extract_text() for page in reader.pages]
    assert len(texts)>2
    for i in range(95):
        assert f'DRIVER-{i:03}' in '\n'.join(texts)
    for i in range(80):
        assert f'CASE-{i:03}' in '\n'.join(texts)
    assert 'AC-1025' in texts[-1]
    import re
    import unicodedata
    for index,page in enumerate(reader.pages,1):
        spans=[]
        page.extract_text(visitor_text=lambda text,cm,tm,font,size: spans.append((text,tm[4],tm[5],size)))
        # pypdf may split an RTL string into several text visits. Reassemble
        # the footer by its reserved position rather than extraction ordering.
        footer=''.join(text for text,x,y,size in spans if x<150 and 25<y<40)
        assert re.findall(r'\d+',footer)==[str(len(reader.pages)),str(index)]
        assert all(0<=y<=float(page.mediabox.height) for text,x,y,size in spans if text.strip())
        driver_spans=[span for span in spans if 'DRIVER-' in span[0]]
        if driver_spans:
            assert all(size==10 for text,x,y,size in driver_spans)
            # Every table page repeats its column labels above its first row.
            assert 'السائق' in unicodedata.normalize('NFKC',texts[index-1])
    assert report==original


def test_pdf_splits_oversized_reason_without_losing_its_last_line_or_approval():
    report=example(True)
    report['cases']=[dict(driver='LONG-DRIVER',brand='kps',kind='تعويض',amount=1000,
                          reference='LONG-CASE',reason=' '.join(f'NOTE{i:04}' for i in range(900))+' FINAL-REASON',approval='معتمد')]
    # A single approval can itself be longer than a page.
    report['accounting']['approvals'][0]['approved_name']=' '.join(f'APPROVER{i:04}' for i in range(900))+' FINAL-APPROVER'
    pdf=reports.make_pdf(report)
    pages=PdfReader(BytesIO(pdf)).pages
    text='\n'.join(page.extract_text() for page in pages)
    assert len(pages)>2
    assert 'LONG-CASE' in text and 'FINAL-REASON' in text
    assert 'AC-1025' in text and 'FINAL-APPROVER' in text
    for i in range(900):
        assert f'NOTE{i:04}' in text
        assert f'APPROVER{i:04}' in text


@pytest.mark.parametrize('scope,selected,expected',[
    ('shift','mine-old',['mine-old']), ('shift','mine-new',['mine-new']),
    ('day','mine-old',['mine-old','mine-new']),
    ('shift','other',None), ('shift','revoked',None), ('shift','different-day',None),
    ('shift','different-brand',None)])
@pytest.mark.parametrize('central_enabled',[False,True])
def test_selected_historical_manager_report_keeps_day_and_access_scope(monkeypatch,scope,selected,expected,central_enabled):
    from app import accounting_statements
    user=dict(id='me',role='delivery_manager',brand_id='kps',display_name='مدير',branch_ids=['b'])
    candidates=[dict(id=identity,user_id=owner,brand_id=brand,branch_ids=branches,status='closed',delivery_window_snapshot={})
                for identity,owner,brand,branches in [('mine-old','me','kps',['b']),('mine-new','me','kps',['b']),
                                                     ('other','someone','kps',['b']),('revoked','me','kps',['outside']),
                                                     ('different-brand','me','enbishan',['b'])]]
    visited=[]
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Cursor:
        def execute(self,query,args):
            assert query.startswith('SELECT')
            if 'central_workday_legs' in query:
                assert central_enabled and scope=='shift'
                return Result([])  # Legacy shifts have no physical group.
            if query.startswith('SELECT id,name,city'):return Result([])
            if "s.kind='manager'" in query:
                assert args==('kps',date(2026,10,27))
                return Result(candidates)
            if "s.kind='driver'" in query:
                visited.append(args[0]);return Result([])
            if 'accounting_daily_closures' in query or 'closure_reports' in query:return Result([])
            raise AssertionError(query)
    monkeypatch.setattr(reports.multibrand,'enabled',lambda:central_enabled)
    monkeypatch.setattr(reports.multibrand,'available',lambda *a:[dict(id='kps')])
    monkeypatch.setattr(reports.multibrand,'project',lambda c,u,b:dict(u,brand_id=b))
    monkeypatch.setattr(reports.auth,'has_permission',lambda *a:True)
    monkeypatch.setattr(accounting_statements,'manager_cards',lambda *a,**k:dict(cards=[]))
    if expected is None:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            reports.build(Cursor(),user,date(2026,10,27),scope,selected)
        assert error.value.status_code==404
        assert visited==[]
    else:
        reports.build(Cursor(),user,date(2026,10,27),scope,selected)
        # Day reports intentionally include other permitted managers as before.
        assert visited==expected if scope=='shift' else visited==expected+['other']


@pytest.mark.parametrize('anchor',['kps-manager','enbishan-manager'])
@pytest.mark.parametrize('side_access',['allowed','revoked','other-owner','wrong-day','missing-principal'])
def test_selected_central_manager_report_requires_every_authorized_leg(monkeypatch,anchor,side_access):
    from app import accounting_statements
    from fastapi import HTTPException
    user=dict(id='me',role='delivery_manager',brand_id='kps',display_name='مدير',branch_ids=['b'])
    managers={brand:dict(id=f'{brand}-manager',user_id='me',brand_id=brand,branch_ids=['b'],status='closed',delivery_window_snapshot={})
              for brand in ('kps','enbishan')}
    if side_access=='revoked':managers['enbishan']['branch_ids']=['revoked']
    if side_access=='other-owner':managers['enbishan']['user_id']='someone'
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Cursor:
        def execute(self,query,args):
            assert query.startswith('SELECT')
            if 'JOIN central_workday_legs anchor' in query:
                assert args==(anchor,)
                return Result([dict(shift_id=m['id']) for m in managers.values()])
            if query.startswith('SELECT id,name,city'):
                return Result([dict(id='b',name='الفرع',city='الدمام')])
            if "s.kind='manager'" in query:
                brand,day=args
                assert day==date(2026,10,27)
                return Result([] if brand=='enbishan' and side_access=='wrong-day' else [managers[brand]])
            if "s.kind='driver'" in query:
                brand=next(b for b,m in managers.items() if m['id']==args[0])
                return Result([dict(id=f'{brand}-driver',user_id=f'{brand}-u',display_name=brand,brand_id=brand,branch_ids=['b'],status='closed')])
            if 'accounting_daily_closures' in query or 'closure_reports' in query:return Result([])
            raise AssertionError(query)
    monkeypatch.setattr(reports.multibrand,'enabled',lambda:True)
    monkeypatch.setattr(reports.multibrand,'available',lambda *a:[dict(id=b) for b in managers])
    monkeypatch.setattr(reports.multibrand,'project',lambda c,u,b:None if b=='enbishan' and side_access=='missing-principal' else dict(u,brand_id=b))
    monkeypatch.setattr(reports.auth,'has_permission',lambda *a:True)
    monkeypatch.setattr(accounting_statements,'manager_cards',lambda *a,**k:dict(cards=[]))
    def snapshot(c,shift):
        return dict(orders=[dict(id=shift['id']+'-order',branch_id='b',total_halalas=1000,fulfilled_in_report=True)],
                    refunds=[],collections=[]),'saved-'+shift['id']
    monkeypatch.setattr(reports,'snapshot',snapshot)
    if side_access=='allowed':
        result=reports.build(Cursor(),user,date(2026,10,27),'shift',anchor)
        assert {row['name'] for row in result['rows']}=={'kps','enbishan'}
        assert result['count']==2 and result['net']==2000
        assert sum(row['brands']['kps']['amount'] for row in result['rows'])==1000
        assert sum(row['brands']['enbishan']['amount'] for row in result['rows'])==1000
    else:
        with pytest.raises(HTTPException) as error:
            reports.build(Cursor(),user,date(2026,10,27),'shift',anchor)
        assert error.value.status_code==404

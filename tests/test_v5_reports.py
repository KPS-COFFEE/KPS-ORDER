"""V5 layouts and durable notifications retain the canonical closure ledger."""
from copy import deepcopy
import io
from pathlib import Path
import pytest
from app import db,reporting,report_notifications
from test_operations import ops,delivered,close_driver


def sample(kind='driver',drivers=0):
    closure={'expected_halalas':1000,'counted_halalas':1000,'handover_halalas':1000,'retained_halalas':0,'difference_halalas':0}
    stamp='2026-10-10T12:00:00+00:00'
    s={'test_data':True,'schema_version':2,'brand_name':'KPS COFFEE','reference':'TEST-SHIFT-V5-001','kind':kind,'timezone':'Asia/Riyadh','operational_date':'2026-10-10','prepared_by':'driver','approved_by':'manager',
       'display_users':{'driver':'أحمد عبدالله الحربي','manager':'محمد مدير التوصيل','owner':'مالك الاختبار'},'display_branches':{'b1':{'name':'المروج 1'}},
       'shift':{'id':'TEST-SHIFT-V5-001','kind':kind,'brand_id':'kps','user_id':'driver' if kind=='driver' else 'manager','branch_ids':['b1'],'status':'closed','opening_halalas':0,'opened_at':'2026-10-10T04:00:00+00:00','closed_at':stamp,'submission':closure},
       'summary':{'unique_orders':1,'completed_orders':1,'gross_completed_halalas':1000,'cash_collected_halalas':1000,'card_collected_halalas':0,'cash_refunded_halalas':0,'card_refunded_halalas':0,'net_collections_halalas':1000,'delivery_fees_halalas':0,'submission':closure},
       'orders':[{'id':'TEST-ORDER-1','branch_id':'b1','service':'delivery','preparation_status':'completed','delivery_status':'delivered','payment_status':'paid','total_halalas':1000,'subtotal_halalas':1000,'delivery_fee_halalas':0,'created_at':stamp,'fulfilled_in_report':True,'snapshot':{'items':[{'product_id':'p1','name':'قهوة الاختبار','quantity':1,'line_total_halalas':1000,'unit_price_halalas':1000,'units':[]}],'branch_name':'المروج 1'}}],
       'tasks':[{'id':'TEST-TASK-1','order_id':'TEST-ORDER-1','shift_id':'TEST-SHIFT-V5-001','driver_id':'driver','status':'delivered','picked_up_at':'2026-10-10T11:45:00+00:00','delivered_at':stamp,'reason':None}],
       'collections':[{'id':'TEST-COLLECT-1','order_id':'TEST-ORDER-1','shift_id':'TEST-SHIFT-V5-001','collector_id':'driver','method':'cash','amount_halalas':1000,'tendered_halalas':2000,'change_halalas':1000,'reference':None,'created_at':stamp}],
       'refunds':[],'custody_events':[],'transfers':[{'id':'TEST-TRANSFER-1','from_shift_id':'TEST-SHIFT-V5-001','closure':True,'status':'confirmed','amount_halalas':1000,'recipient_user_id':'manager','confirmed_by':'manager','reference':'TEST-BAG-001'}],
       'driver_reports':[],'linked_corrections':[],'retained_carryovers':[],'manager_handovers':[]}
    for i in range(drivers):
        uid=f'driver-{i}';s['display_users'][uid]='سائق اختبار '+str(i+1)
        s['driver_reports'].append({'id':f'TEST-DRIVER-REPORT-{i:03}','shift_id':f'TEST-DRIVER-SHIFT-{i:03}','user_id':uid,'version':1,'summary':s['summary'],'closure':closure})
    return s


def test_v5_same_source_numbers_escape_drilldown_and_real_fonts():
    snap=sample('manager',3);before=deepcopy(snap)
    snap['orders'][0]['snapshot']['items'][0]['name']='قهوة <script>bad()</script>'
    changed=deepcopy(snap)
    compact=reporting.make_v5_print_html(snap)
    detailed=reporting.make_v5_print_html(snap,'owner','TEST-SAVED-REPORT-1')
    csv=reporting.make_v5_csv(snap,'owner').decode('utf-8-sig')
    assert snap==changed
    assert 'data-report-template="5"' in compact and '<pre' not in compact
    assert 'TEST-ORDER-1' not in compact # detail appendix is not printed by default
    assert 'TEST-ORDER-1' in detailed and '<details' in detailed and 'id="order-TEST-ORDER-1"' in detailed
    for fmt in ('pdf','csv'):
        assert 'href="/api/reports/TEST-SAVED-REPORT-1/renderings/v5/'+fmt+'"' in detailed
    assert 'report-actions screen-only' in detailed
    assert '&lt;script&gt;bad()&lt;/script&gt;' in detailed and '<script>bad()' not in detailed
    assert '1000' in csv and '10.00' in csv and 'presentation_template,5' in csv
    for weight in ('Regular','SemiBold','Bold'):
        f=Path(__file__).resolve().parents[1]/'public/assets/fonts'/f'NotoKufiArabic-{weight}.ttf'
        assert f.is_file() and f.stat().st_size>100000
        assert '/assets/fonts/'+f.name in detailed
    assert 'font-synthesis:none' in detailed and 'thead{display:table-header-group}' in detailed


def _pdf_pages(blob):
    # Runtime tests do not add a PDF parser dependency; ReportLab writes page types.
    import re
    return len(re.findall(rb'/Type\s*/Page\b',blob))


def test_v5_driver_and_small_manager_readable_single_a4_large_has_every_row():
    driver=reporting.make_v5_pdf(sample())
    manager=reporting.make_v5_pdf(sample('manager',3))
    large=reporting.make_v5_pdf(sample('manager',103))
    assert _pdf_pages(driver)==1
    assert _pdf_pages(manager)==1
    assert 2<_pdf_pages(large)<30
    assert b'/FontFile2' in driver and b'/FontFile2' in manager
    table=next(t for t in reporting.v5_tables(sample('manager',103)) if t['key']=='drivers')
    assert len(table['rows'])==103 and table['total'][1]==103


def close_manager(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();delivered(post,order,driver);close_driver(post,driver,manager)
    post('manager','/api/shifts/'+manager+'/closing')
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':1000,'handover_halalas':1000})
    transfer=post('manager','/api/custody',{'from_shift_id':manager,'recipient_user_id':'ops-owner','amount_halalas':1000,'reference':'FINAL-TEST','closure':True})['transfer']['id']
    post('owner','/api/custody/'+transfer+'/confirm')
    result=post('owner','/api/shifts/'+manager+'/approve',key='v5-manager-approval')
    assert post('owner','/api/shifts/'+manager+'/approve',key='v5-manager-approval')==result
    return actor,post,result


@pytest.mark.postgres
def test_v5_final_owner_inbox_unique_no_email_rollback_and_no_double_cash(ops,monkeypatch):
    monkeypatch.setattr(report_notifications.email_delivery,'enqueue_mail',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('provider down')))
    actor,post,result=close_manager(ops)
    rid=result['report_id']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM report_notifications WHERE report_id=%s',(rid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE id=%s',(rid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==1
        notice=c.execute('SELECT * FROM report_notifications WHERE report_id=%s',(rid,)).fetchone()
        assert notice['status']=='approved_final' and notice['email_outbox_id'] is None
        before=c.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(rid,)).fetchone()
        report_notifications.enqueue_manager_closure(c,rid)
    report=actor('owner').get('/api/reports/'+rid).json()['report']
    assert report['snapshot']['summary']['cash_collected_halalas']==1000
    assert len(report['renderings'])==2 and all(r['status']=='ready' for r in report['renderings'])
    for fmt in ('print','pdf','csv'):
        r=actor('owner').get('/api/reports/'+rid+'/renderings/v5/'+fmt)
        assert r.status_code==200,r.text
        assert actor('other').get('/api/reports/'+rid+'/renderings/v5/'+fmt).status_code==404
    with db.tx() as c:after=c.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(rid,)).fetchone()
    assert after==before
    # Authorization applies equally to saved presentations and notices.
    assert actor('driver').get('/api/report-notifications').status_code==403
    assert actor('other').post('/api/report-notifications/'+notice['id']+'/read').status_code==404


@pytest.mark.postgres
def test_v5_render_failure_retry_keeps_original_snapshot_money_and_ready_files(ops,monkeypatch):
    original=reporting.make_v5_pdf
    monkeypatch.setattr(reporting,'make_v5_pdf',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('font failed')))
    actor,post,result=close_manager(ops);rid=result['report_id']
    with db.tx() as c:
        before=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(rid,)).fetchone()['snapshot']
        assert c.execute('SELECT count(*) n FROM report_renderings WHERE report_id=%s AND status=\'failed\'',(rid,)).fetchone()['n']==2
    monkeypatch.setattr(reporting,'make_v5_pdf',original)
    r=post('owner','/api/reports/'+rid+'/renderings/v5/retry')
    assert r['status']=='ready'
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(rid,)).fetchone()['snapshot']==before
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==1
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('UPDATE report_renderings SET pdf_data=%s WHERE report_id=%s AND view_kind=\'owner\'',(b'fake',rid))

@pytest.mark.postgres
def test_v5_owner_mail_paused_channel_provider_fail_retry_unique_no_financial_changes(ops,monkeypatch):
    import base64
    from app import email_delivery
    actor,post,result=close_manager(ops);rid=result['report_id']
    monkeypatch.setenv('EMAIL_PROVIDER','resend');monkeypatch.setenv('EMAIL_FROM','no-reply@example.invalid')
    monkeypatch.setenv('RESEND_API_KEY','TEST_ONLY_NO_LIVE_CHANNEL');monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b'x'*32).decode())
    with db.tx() as c:
        c.execute("UPDATE users SET email='owner@example.invalid',email_verified_at=now() WHERE id='ops-owner'")
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('ops-brand','email','paused',false) ON CONFLICT(brand_id,channel) DO UPDATE SET state='paused',enabled=false")
        source=c.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(rid,)).fetchone()
    report_notifications.process_once()
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM email_outbox WHERE kind='owner_report'").fetchone()['n']==0
        c.execute("UPDATE identity_channels SET state='active',enabled=true WHERE brand_id='ops-brand' AND channel='email'")
    report_notifications.process_once();report_notifications.process_once()
    with db.tx() as c:
        notice=c.execute('SELECT * FROM report_notifications WHERE report_id=%s',(rid,)).fetchone()
        assert c.execute("SELECT count(*) n FROM email_outbox WHERE kind='owner_report'").fetchone()['n']==1
    monkeypatch.setattr(email_delivery,'_send',lambda row:(_ for _ in ()).throw(RuntimeError('TEST_PROVIDER_DOWN')))
    assert email_delivery.dispatch_once()['provider_accepted']==0
    inbox=actor('owner').get('/api/report-notifications').json()['notifications']
    assert inbox[0]['delivery_status']=='failed'
    post('owner','/api/report-notifications/'+notice['id']+'/retry')
    monkeypatch.setattr(email_delivery,'_send',lambda row:'TEST-PROVIDER-REFERENCE')
    assert email_delivery.dispatch_once()['provider_accepted']==1
    # Retrying provider acceptance doesn't send a second email or change ledger.
    post('owner','/api/report-notifications/'+notice['id']+'/retry')
    assert email_delivery.dispatch_once()['attempted']==0
    with db.tx() as c:
        assert c.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(rid,)).fetchone()==source
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM email_outbox WHERE kind='owner_report'").fetchone()['n']==1

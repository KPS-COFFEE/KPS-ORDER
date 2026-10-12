"""Real isolated accounting permissions, money, immutable reviews and exports."""
import io
import zipfile
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from app import db
from app.accounting import period,opday
from app.accounting_export import render,contract
from test_operations import ops

def test_operational_boundary_and_export_contract():
    lo,hi,start,end=period('2026-10-01','2026-10-01')
    assert start.isoformat()=='2026-10-01T16:00:00+03:00'
    assert (end-start).total_seconds()==86400
    assert opday(datetime(2026,10,1,12,59,tzinfo=timezone.utc))=='2026-09-30'
    data={'group':'daily','period':{'start':'2026-10-01','end':'2026-10-01'},'definitions':'TEST ONLY','rows':[{'day':'2026-10-01','cash':1250,'card':750,'orders':1,'correct':1,'notes':'=HYPERLINK("evil")'}]}
    model=contract(data,'ar');assert model['totals']['cash']==1250
    with zipfile.ZipFile(io.BytesIO(render(data,'xlsx','ar'))) as z:
        xml=z.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in xml and 'HYPERLINK' in xml and '<v>12.5</v>' in xml
        assert 'rightToLeft="1"' in xml
    assert render(data,'pdf','ar').startswith(b'%PDF')

def accountant():
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-accountant','TEST ACCOUNTANT','admin','ops-brand',%s,%s)",(db.json(['ops-branch']),db.json(['accounting_reports','accounting_review'])))

@pytest.mark.postgres
def test_reports_split_payments_refund_and_scope(ops):
    actor,post,order,shifts=ops;accountant();oid=order('pickup')
    post('branch',f'/api/orders/{oid}/collect',{'method':'split','amount_halalas':1000,'cash_halalas':400,'card_halalas':600,'tendered_halalas':400,'reference':'TEST SPLIT'})
    with db.tx() as c:c.execute("UPDATE orders SET completed_at=now(),preparation_status='completed' WHERE id=%s",(oid,))
    url='/api/accounting/reports'
    r=actor('accountant').get(url);assert r.status_code==200,r.text
    d=r.json();assert d['totals']['orders']==1 and d['totals']['correct']==1
    assert d['totals']['cash']==400 and d['totals']['card']==600 and d['totals']['gross']==1000
    assert actor('manager').get(url).status_code==403
    assert actor('driver').get(url).status_code==403
    assert actor('accountant').get(url+'?branch_ids=ops-branch-other').status_code==403
    post('manager',f'/api/orders/{oid}/refund',{'amount_halalas':200,'method':'cash','reason':'TEST REFUND','line_quantities':{}})
    r=actor('accountant').get(url).json();assert r['totals']['correct']==0 and r['totals']['processed']==1 and r['totals']['total']==800
    assert actor('accountant').get(url+'?group=actions').json()['rows'][0]['amount']==200
    for fmt in ('pdf','xlsx'):
        r=actor('accountant').get(url+'?format='+fmt);assert r.status_code==200,r.text
        assert r.content.startswith(b'%PDF' if fmt=='pdf' else b'PK')

@pytest.mark.postgres
def test_case_authority_scope_version_attachments_and_exports(ops):
    from PIL import Image
    actor,post,order,shifts=ops;accountant()
    today=actor('accountant').get('/api/accounting/options').json()['today']
    body={'branch_id':'ops-branch','subject_id':'ops-driver','category':'damage','occurred_on':today,'vehicle_plate':'TEST 100','amount_halalas':12000,'note':'TEST ONLY bumper damage'}
    made=post('accountant','/api/accounting/cases',body,key='case-test');cid=made['id'];url='/api/accounting/cases/'+cid
    assert post('accountant','/api/accounting/cases',body,key='case-test')['id']==cid
    post('manager','/api/accounting/cases',body,status=403)
    post('accountant','/api/accounting/cases',{**body,'branch_id':'ops-branch-other'},status=403)
    assert actor('driver').get(url).status_code==403
    assert actor('other').get(url).status_code==404
    update={'version':1,'status':'in_progress','responsibility':'pending','note':'TEST investigating'}
    post('accountant',url+'/followup',update,status=403)
    post('manager',url+'/followup',update)
    post('manager',url+'/followup',update,status=409)
    post('manager',url+'/followup',{**update,'version':2,'status':'resolved','responsibility':'not_responsible'})
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,'PNG')
    r=actor('accountant').post(url+'/files',files={'file':('test.png',image.getvalue(),'image/png')},headers={'Idempotency-Key':uuid4().hex});assert r.status_code==200,r.text
    fileurl=url+'/files/'+r.json()['id'];assert actor('manager').get(fileurl).status_code==200
    assert actor('other').get(fileurl).status_code==404
    assert len(actor('accountant').get(url).json()['events'])==4
    for fmt in ('pdf','xlsx'):assert actor('accountant').get('/api/accounting/cases?format='+fmt).status_code==200
    with db.tx() as c:c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s)",(db.json({'accounting_manager_followup':False}),))
    assert actor('manager').get(url).status_code==403
    assert actor('accountant').get(url).status_code==200

@pytest.mark.postgres
def test_variance_operational_date_review_and_month_snapshot(ops):
    actor,post,order,shifts=ops;accountant()
    snapshot={'shift':{'user_id':'ops-driver','submission':{'difference_halalas':-150,'notes':'TEST ONLY'}},'summary':{'completed_orders':2,'cash_collected_halalas':1000,'card_collected_halalas':500,'net_collections_halalas':1500}}
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date) VALUES('ac-shift','driver','ops-driver','ops-brand',%s,'closed','2026-09-12T16:00:00+03','2026-09-13T17:00:00+03','2026-09-12')",(db.json(['ops-branch']),))
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot,created_at) VALUES('ac-report','ac-shift','driver','ops-brand',%s,'ops-driver',%s,'2026-09-13T17:00:00+03')",(db.json(['ops-branch']),db.json(snapshot)))
    url='/api/accounting/reports?group=deficits&start=2026-09-12&end=2026-09-12'
    d=actor('accountant').get(url).json();assert d['rows'][0]['day']=='2026-09-12' and d['totals']['shortage']==150
    month={'month':'2026-09','note':'TEST monthly review'}
    post('accountant','/api/accounting/months',month,status=409)
    post('accountant','/api/accounting/reviews/ac-report',{'note':'TEST reviewed'},key='review-test-key')
    post('accountant','/api/accounting/reviews/ac-report',{'note':'TEST reviewed'},key='review-test-key')
    post('accountant','/api/accounting/reviews/ac-report',{'note':'TEST second'},status=409)
    d=actor('accountant').get(url).json();assert d['totals']['approved_shortage']==150
    mid=post('accountant','/api/accounting/months',month)['id']
    frozen=actor('accountant').get('/api/accounting/months/'+mid+'?group=deficits').json()
    assert frozen['frozen'] and frozen['totals']['shortage']==150
    assert actor('other').get('/api/accounting/months/'+mid).status_code==404
    with pytest.raises(Exception):
        with db.tx() as c:c.execute("UPDATE accounting_months SET reason='changed' WHERE id=%s",(mid,))
    with pytest.raises(Exception):
        with db.tx() as c:c.execute("DELETE FROM accounting_reviews WHERE report_id='ac-report'")
    assert actor('accountant').get('/api/accounting/months/'+mid+'?group=deficits').json()==frozen

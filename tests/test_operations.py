"""Real PostgreSQL journeys, not memory substitutes. Shared client fixture isolates DB."""
import base64
import io
from uuid import uuid4
import pytest
from PIL import Image
from fastapi import HTTPException
from app import db,auth
from app.operations import day_start,same_scope
from app.chat import allowed
from app.reporting import cell,make_pdf


def test_scope_and_operational_day_helpers():
    owner={'role':'owner','brand_id':'a','id':'o'}
    assert not same_scope(owner,{'brand_id':'b'})
    assert not allowed(owner,{'brand_id':'b'})
    assert day_start({'operational_day_start':'05:30'})==(330,True)
    assert day_start({})==(0,False)
    with pytest.raises(HTTPException):day_start({'operational_day_start':'25:00'})
    assert cell(' =IMPORTXML("evil")').startswith("'")
    assert cell('-1').startswith("'")

@pytest.fixture
def ops(client):
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('ops-brand','تجريبي'),('ops-other','اختبار آخر')")
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-branch',1,'ops-source','ops-brand','فرع اختبار','حفر الباطن','{}'),('ops-branch-other',1,'other-source','ops-other','فرع آخر','رفحاء','{}')")
        for name,role,brand,branch in [('owner','owner','ops-brand','ops-branch'),('manager','delivery_manager','ops-brand','ops-branch'),('driver','driver','ops-brand','ops-branch'),('driver2','driver','ops-brand','ops-branch'),('customer','customer','ops-brand','ops-branch'),('branch','branch_manager','ops-brand','ops-branch'),('other','owner','ops-other','ops-branch-other')]:
            c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,%s,%s,%s,%s)',('ops-'+name,name,role,brand,db.json([branch])))
    def actor(name):
        with db.tx() as c:u=c.execute('SELECT * FROM users WHERE id=%s',('ops-'+name,)).fetchone()
        client.app.dependency_overrides[auth.current_user]=lambda:u
        return client
    def order(service='delivery',total=1000,name=None,items=None,notes=''):
        oid=name or 'ops-order-'+uuid4().hex
        snap={'items':items if items is not None else [{'product_id':'ops-product','name':'قهوة تجربة','quantity':1,'unit_price_halalas':total,'line_total_halalas':total,'units':[],'note':''}],'branch_name':'فرع اختبار','notes':notes}
        with db.tx() as c:c.execute('INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)',(oid,'ops-brand','ops-branch','ops-customer',service,'ready','pending' if service=='delivery' else 'not_applicable','cash',total,total,db.json(snap),db.json({'name':'اختبار'})))
        return oid
    def post(who,path,body=None,key=None,status=200):
        r=actor(who).post(path,json=body or {},headers={'Idempotency-Key':key or uuid4().hex})
        assert r.status_code==status,r.text
        return r.json()
    def shifts():
        manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
        driver=post('driver','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']['id']
        return manager,driver
    yield actor,post,order,shifts
    client.app.dependency_overrides.clear()

def delivered(post,order,driver):
    oid=order();task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up'})
    pay=post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':2000},key='same-collection-key')
    assert pay['change_halalas']==1000
    assert post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':2000},key='same-collection-key')==pay
    post('driver','/api/tasks/'+task+'/transition',{'status':'delivered','proof':'اختبار استلام العميل'})
    return oid

def close_driver(post,driver,manager,counted=1000):
    post('driver','/api/shifts/'+driver+'/closing')
    post('driver','/api/shifts/'+driver+'/submit',{'counted_halalas':counted,'handover_halalas':counted})
    transfer=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':counted,'reference':'TEST-BAG','closure':True})['transfer']['id']
    post('driver','/api/custody/'+transfer+'/confirm',status=404)
    post('manager','/api/shifts/'+driver+'/approve',status=409)
    post('manager','/api/custody/'+transfer+'/confirm')
    return post('manager','/api/shifts/'+driver+'/approve',key='close-driver-key')

@pytest.mark.postgres
def test_delivery_custody_closure_reports_no_double_cash(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=delivered(post,order,driver)
    post('manager','/api/shifts/'+manager+'/closing')
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':1000,'handover_halalas':1000},status=409)
    result=close_driver(post,driver,manager)
    assert post('manager','/api/shifts/'+driver+'/approve',key='close-driver-key')==result
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':1000,'handover_halalas':1000})
    tid=post('manager','/api/custody',{'from_shift_id':manager,'recipient_user_id':'ops-owner','amount_halalas':1000,'reference':'FINAL-TEST','closure':True})['transfer']['id']
    post('owner','/api/custody/'+tid+'/confirm')
    final=post('owner','/api/shifts/'+manager+'/approve')
    r=actor('owner').get('/api/reports/'+final['report_id']);assert r.status_code==200
    snap=r.json()['report']['snapshot'];assert snap['summary']['cash_collected_halalas']==1000
    assert snap['summary']['unique_orders']==1
    assert len(snap['driver_reports'])==1
    assert snap['summary']['submission']['expected_halalas']==1000
    pdf=actor('owner').get('/api/reports/'+final['report_id']+'/pdf');assert pdf.status_code==200,pdf.text
    assert pdf.content.startswith(b'%PDF')
    assert actor('other').get('/api/reports/'+final['report_id']).status_code==404
    assert actor('customer').get('/api/reports/'+final['report_id']).status_code==404
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1


@pytest.mark.postgres
def test_zero_task_shift_requires_real_zero_receipt_and_blocks_manager(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    post('manager','/api/shifts/'+manager+'/closing')
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':0,'handover_halalas':0},status=409)
    close_driver(post,driver,manager,counted=0)
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':0,'handover_halalas':0})
    post('owner','/api/shifts/'+manager+'/approve',status=409)


@pytest.mark.postgres
def test_closing_freezes_assignment_and_pending_tasks_gate(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    oid=order();post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})
    post('driver','/api/shifts/'+driver+'/closing')
    post('driver','/api/shifts/'+driver+'/submit',{'counted_halalas':0,'handover_halalas':0},status=409)
    post('manager','/api/orders/'+order()+'/assign',{'driver_shift_id':driver},status=409)


@pytest.mark.postgres
def test_chat_attachments_durable_route_transfer_and_scope(ops):
    actor,post,order,shifts=ops;oid=order('pickup')
    conv=post('customer','/api/conversations',{'order_id':oid})['conversation']['id']
    img=io.BytesIO();Image.new('RGB',(10,10),'orange').save(img,format='PNG')
    attachment=post('customer','/api/conversations/'+conv+'/attachments',{'content_type':'image/png','data_base64':base64.b64encode(img.getvalue()).decode()})
    msg=post('customer','/api/conversations/'+conv+'/messages',{'text':'شكوى توصيل','attachment_id':attachment['id']},key='same-message-key')
    assert post('customer','/api/conversations/'+conv+'/messages',{'text':'شكوى توصيل','attachment_id':attachment['id']},key='same-message-key')==msg
    assert actor('driver').get('/api/conversations/'+conv+'/messages').status_code==404
    assert actor('other').get(attachment['url']).status_code==404
    assert actor('branch').get(attachment['url']).content==img.getvalue()
    assert actor('manager').get(attachment['url']).status_code==404
    transferred=post('branch','/api/conversations/'+conv+'/transfer',{'target':'delivery','reason':'شكوى تخص التوصيل'},key='transfer-reconnect-key')
    assert post('branch','/api/conversations/'+conv+'/transfer',{'target':'delivery','reason':'شكوى تخص التوصيل'},key='transfer-reconnect-key')==transferred
    assert actor('branch').get(attachment['url']).status_code==404
    assert actor('manager').get(attachment['url']).content==img.getvalue()
    assert len(actor('manager').get('/api/conversations/'+conv+'/messages').json()['messages'])==1
    post('customer','/api/conversations/'+conv+'/attachments',{'content_type':'image/png','data_base64':base64.b64encode(b'<script>bad</script>').decode()},status=422)
    assert actor('driver').get('/api/conversations').status_code==403


@pytest.mark.postgres
def test_discrepancy_partial_handover_retained_carryover(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();delivered(post,order,driver)
    partial=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':400,'reference':'PARTIAL'})['transfer']['id']
    post('manager','/api/custody/'+partial+'/confirm')
    post('driver','/api/shifts/'+driver+'/closing')
    sub=post('driver','/api/shifts/'+driver+'/submit',{'counted_halalas':500,'handover_halalas':400,'retained_halalas':100})
    assert sub['submission']['expected_halalas']==600 and sub['submission']['difference_halalas']==-100
    tid=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':400,'reference':'FINAL','closure':True})['transfer']['id']
    post('manager','/api/custody/'+tid+'/confirm')
    post('manager','/api/shifts/'+driver+'/approve',status=403)
    post('owner','/api/shifts/'+driver+'/approve',{'exception_reason':'TEST: نقص معتمد مع إبقاء عهدة 1 ريال'})
    nxt=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager,'branch_ids':['ops-branch']})['shift']
    assert nxt['opening_halalas']==100
    with db.tx() as c:
        carry=c.execute('SELECT * FROM retained_carryovers WHERE from_shift_id=%s',(driver,)).fetchone()
        assert carry['to_shift_id']==nxt['id']
        assert c.execute("SELECT amount_halalas FROM custody_events WHERE shift_id=%s AND kind='adjustment'",(driver,)).fetchone()['amount_halalas']==-100


@pytest.mark.postgres
def test_dashboard_setting_midnight_refund_and_card_split(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=delivered(post,order,driver)
    with db.tx() as c:
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s)",(db.json({'operational_day_start':'05:30','late_after_minutes':60}),))
        c.execute("UPDATE orders SET created_at='2026-10-09 22:00:00+00' WHERE id=%s",(oid,)) # 01:00 Riyadh next calendar day
    post('manager','/api/orders/'+oid+'/refund',{'amount_halalas':200,'method':'cash','reason':'TEST جزئي','line_quantities':{}})
    dash=actor('owner').get('/api/dashboard?start=2026-10-09T05:30:00&end=2026-10-10T05:30:00').json()
    assert dash['summary']['completed']==1 and dash['summary']['net_completed_halalas']==800
    assert dash['top_items'][0]['net_halalas']==800 and dash['top_items'][0]['quantity']==1
    assert dash['period']['operational_day_start_minutes']==330
    with db.tx() as c:c.execute("UPDATE app_settings SET settings=%s WHERE brand_id='ops-brand'",(db.json({'operational_day_start':'02:15'}),))
    assert actor('owner').get('/api/dashboard').json()['period']['operational_day_start_minutes']==135
    assert actor('other').get('/api/dashboard?brand_id=ops-brand').status_code==403


@pytest.mark.postgres
def test_cash_card_pickup_does_not_enter_delivery_custody(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order('pickup')
    post('driver','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000,'reference':'TEST'},status=404)
    post('branch','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000,'reference':''},status=422)
    post('branch','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000,'reference':'CARD-TEST'})
    detail=actor('driver').get('/api/shifts/'+driver).json()
    assert detail['expected_halalas']==0 and detail['collections']==[]


@pytest.mark.postgres
def test_reports_financial_records_immutable_after_menu_change(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=delivered(post,order,driver);r=close_driver(post,driver,manager)
    before=actor('owner').get('/api/reports/'+r['report_id']).json()['report']['snapshot']
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas) VALUES('ops-product','ops-brand','اسم جديد','تجربة',9000)")
    after=actor('owner').get('/api/reports/'+r['report_id']).json()['report']['snapshot'];assert after==before
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('UPDATE closure_reports SET snapshot=%s WHERE id=%s',(db.json({'fake':1}),r['report_id']))
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('UPDATE collections SET amount_halalas=9000 WHERE order_id=%s',(oid,))


@pytest.mark.postgres
def test_report_pdf_failure_keeps_closed_snapshot_and_retries(ops,monkeypatch):
    from app import reporting
    actor,post,order,shifts=ops;manager,driver=shifts();delivered(post,order,driver)
    original=reporting.make_pdf
    monkeypatch.setattr(reporting,'make_pdf',lambda snap:(_ for _ in ()).throw(RuntimeError('font absent')))
    r=close_driver(post,driver,manager)
    detail=actor('owner').get('/api/reports/'+r['report_id']).json()['report']
    assert detail['pdf_status']=='failed'
    assert actor('driver').get('/api/shifts/'+driver).json()['shift']['status']=='closed'
    assert actor('owner').get('/api/reports/'+r['report_id']+'/pdf').status_code==409
    monkeypatch.setattr(reporting,'make_pdf',original)
    post('owner','/api/reports/'+r['report_id']+'/pdf/retry')
    assert actor('owner').get('/api/reports/'+r['report_id']+'/pdf').content.startswith(b'%PDF')


@pytest.mark.postgres
def test_manager_cannot_open_owner_duty_or_other_manager_shift(ops):
    actor,post,order,shifts=ops
    post('manager','/api/shifts',{'kind':'manager','user_id':'ops-owner','branch_ids':['ops-branch']},status=403)


@pytest.mark.postgres
def test_transfer_acceptance_rechecks_collection_and_revoked_scope(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    driver2=post('driver2','/api/shifts',{'kind':'driver','manager_shift_id':manager,'branch_ids':['ops-branch']})['shift']['id']
    oid=order();task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up'})
    post('manager','/api/tasks/'+task+'/handover',{'driver_shift_id':driver2,'reason':'TEST انتقال'})
    # Receiving driver does not see the customer address before acceptance.
    preview=actor('driver2').get('/api/tasks').json()['tasks'][0];assert 'customer' not in preview
    post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    post('driver2','/api/tasks/'+task+'/accept-handover',status=409)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-driver'")
    assert actor('driver').get('/api/tasks').json()['tasks']==[]
    assert actor('driver').get('/api/shifts/'+driver).status_code==403


@pytest.mark.postgres
def test_duplicate_product_lines_refund_subtracted_once(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    base={'product_id':'ops-product','name':'كوب تجربة','quantity':1,'unit_price_halalas':500,'line_total_halalas':500,'note':''}
    items=[{**base,'units':[{'options':{'color':'black'}}]},{**base,'units':[{'options':{'color':'white'}}]}]
    oid=delivered(post,lambda:order(items=items),driver)
    post('manager','/api/orders/'+oid+'/refund',{'amount_halalas':500,'method':'cash','reason':'TEST كوب واحد','line_quantities':{'ops-product':1}})
    item=actor('owner').get('/api/dashboard').json()['top_items'][0]
    assert item['quantity']==1 and item['net_halalas']==500


@pytest.mark.postgres
def test_task_transfer_across_manager_reports_does_not_duplicate_completed_sale(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    with db.tx() as c:c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-manager2','manager2','delivery_manager','ops-brand','[\"ops-branch\"]')")
    manager2=post('manager2','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    driver2=post('driver2','/api/shifts',{'kind':'driver','manager_shift_id':manager2,'branch_ids':['ops-branch']})['shift']['id']
    oid=order();task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('manager','/api/tasks/'+task+'/handover',{'driver_shift_id':driver2,'reason':'TEST مدير آخر'})
    newtask=post('driver2','/api/tasks/'+task+'/accept-handover')['task']['id']
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver2','/api/tasks/'+newtask+'/transition',{'status':'picked_up'})
    post('driver2','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000,'reference':'CARD-TRANSFER'})
    post('driver2','/api/tasks/'+newtask+'/transition',{'status':'delivered','proof':'TEST تسليم'})
    source=close_driver(post,driver,manager,counted=0)
    snap=actor('owner').get('/api/reports/'+source['report_id']).json()['report']['snapshot']
    assert snap['summary']['completed_orders']==0 and snap['summary']['gross_completed_halalas']==0
    post('driver2','/api/shifts/'+driver2+'/closing')
    post('driver2','/api/shifts/'+driver2+'/submit',{'counted_halalas':0,'handover_halalas':0})
    tid=post('driver2','/api/custody',{'from_shift_id':driver2,'to_shift_id':manager2,'amount_halalas':0,'reference':'ZERO-CARD','closure':True})['transfer']['id']
    post('manager2','/api/custody/'+tid+'/confirm')
    dest=post('manager2','/api/shifts/'+driver2+'/approve')
    target=actor('owner').get('/api/reports/'+dest['report_id']).json()['report']['snapshot']
    assert target['summary']['completed_orders']==1 and target['summary']['gross_completed_halalas']==1000
    assert target['summary']['card_collected_halalas']==1000


@pytest.mark.postgres
def test_concurrent_assignment_replays_one_task_and_rejects_second_writer(ops):
    from concurrent.futures import ThreadPoolExecutor
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order()
    c=actor('manager')
    def push():return c.post('/api/orders/'+oid+'/assign',json={'driver_shift_id':driver},headers={'Idempotency-Key':'concurrent-assignment'})
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(lambda _:push(),range(4)))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    assert len({r.json()['task']['id'] for r in responses})==1
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver},status=409)
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_completed_driver_safe_replays_and_revoked_financial_lists(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order(notes='باب خاص')
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'})
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up'})
    body={'method':'cash','amount_halalas':1000,'tendered_halalas':1000}
    collection=post('driver','/api/orders/'+oid+'/collect',body,key='replay-after-delivery')
    done={'status':'delivered','proof':'TEST العميل استلم'}
    outcome=post('driver','/api/tasks/'+task+'/transition',done,key='delivery-reconnect-key')
    assert post('driver','/api/orders/'+oid+'/collect',body,key='replay-after-delivery')==collection
    assert post('driver','/api/tasks/'+task+'/transition',done,key='delivery-reconnect-key')==outcome
    rows=actor('driver').get('/api/shifts/'+driver).json()['tasks']
    assert 'notes' not in rows[0]['snapshot'] and 'note' not in rows[0]['snapshot']['items'][0]
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-driver'")
    post('driver','/api/orders/'+oid+'/collect',body,key='replay-after-delivery',status=404)
    assert actor('driver').get('/api/shifts').json()['shifts']==[]
    assert actor('driver').get('/api/custody').json()['transfers']==[]


@pytest.mark.postgres
def test_custody_receiver_branch_revoked_before_actual_confirm(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();delivered(post,order,driver)
    tid=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':200,'reference':'REVOKE-TEST'})['transfer']['id']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager','/api/custody/'+tid+'/confirm',status=403)
    assert actor('manager').get('/api/custody').json()['transfers']==[]
    with db.tx() as c:
        assert c.execute('SELECT status FROM custody_transfers WHERE id=%s',(tid,)).fetchone()['status']=='pending'
        assert c.execute("SELECT count(*) n FROM custody_events WHERE reference_id=%s",(tid,)).fetchone()['n']==0


@pytest.mark.postgres
def test_manager_handover_real_acceptance_scope_revocation_and_cash_separation(ops):
    actor,post,order,shifts=ops;manager,driver=shifts()
    with db.tx() as c:c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-manager2','manager2','delivery_manager','ops-brand','[\"ops-branch\"]')")
    replacement=post('manager2','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    handover=post('manager','/api/shifts/'+manager+'/handover-manager',{'target_manager_shift_id':replacement,'reason':'TEST تبديل مناوبة'})['handover_id']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager2'")
    post('manager2','/api/manager-handovers/'+handover+'/accept',status=403)
    assert actor('manager2').get('/api/manager-handovers').json()['handovers']==[]
    with db.tx() as c:
        assert c.execute('SELECT manager_shift_id FROM shifts WHERE id=%s',(driver,)).fetchone()['manager_shift_id']==manager
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\"]' WHERE id='ops-manager2'")
    accepted=post('manager2','/api/manager-handovers/'+handover+'/accept')
    assert accepted['cash_transferred'] is False
    with db.tx() as c:
        assert c.execute('SELECT manager_shift_id FROM shifts WHERE id=%s',(driver,)).fetchone()['manager_shift_id']==replacement
        assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']==0


@pytest.mark.postgres
def test_chat_without_order_context_routing_replay_history_and_attachment_scopes(ops):
    actor,post,order,shifts=ops
    body={'branch_id':'ops-branch','service':'pickup'}
    opened=post('customer','/api/conversations',body,key='no-order-context-key')
    conv=opened['conversation']['id'];assert opened['conversation']['order_id'] is None
    assert opened['conversation']['route']=='branch' and opened['conversation']['context_service']=='pickup'
    assert post('customer','/api/conversations',body,key='no-order-context-key')==opened
    assert post('customer','/api/conversations',body)['conversation']['id']==conv
    post('customer','/api/conversations/'+conv+'/messages',{'text':'شكوى تخص التوصيل بدأت من الاستلام'})
    inbox=actor('branch').get('/api/conversations').json()['conversations'];assert len(inbox)==1
    assert inbox[0]['service']=='pickup' and inbox[0]['order_id'] is None
    assert actor('manager').get('/api/conversations').json()['conversations']==[]
    img=io.BytesIO();Image.new('RGB',(10,10),'orange').save(img,format='PNG')
    attachment=post('customer','/api/conversations/'+conv+'/attachments',{'content_type':'image/png','data_base64':base64.b64encode(img.getvalue()).decode()})
    post('customer','/api/conversations/'+conv+'/messages',{'attachment_id':attachment['id']})
    transferred=post('branch','/api/conversations/'+conv+'/transfer',{'target':'delivery','reason':'شكوى توصيل'},key='no-order-transfer-key')
    assert post('branch','/api/conversations/'+conv+'/transfer',{'target':'delivery','reason':'شكوى توصيل'},key='no-order-transfer-key')==transferred
    assert actor('branch').get('/api/conversations/'+conv+'/messages').status_code==404
    assert actor('branch').get(attachment['url']).status_code==404
    assert actor('manager').get(attachment['url']).content==img.getvalue()
    visible=actor('manager').get('/api/conversations/'+conv+'/messages').json()
    assert len(visible['messages'])==2 and visible['transfers'][0]['from_route']=='branch'
    assert visible['conversation']['order_id'] is None
    post('manager','/api/conversations/'+conv+'/transfer',{'target':'branch','reason':'محاولة إعادة شكوى التوصيل للفرع'},status=409)
    assert actor('other').get('/api/conversations/'+conv+'/messages').status_code==404
    assert actor('driver').get('/api/conversations/'+conv+'/messages').status_code==404
    post('owner','/api/conversations',body,status=403)
    post('driver','/api/conversations',body,status=403)


@pytest.mark.postgres
def test_chat_without_order_requires_service_branch_and_customer_ownership(ops):
    actor,post,order,shifts=ops
    post('customer','/api/conversations',{},status=422)
    post('customer','/api/conversations',{'service':'delivery'},status=422)
    post('customer','/api/conversations',{'branch_id':'ops-branch'},status=422)
    post('customer','/api/conversations',{'branch_id':'ops-branch-other','service':'delivery'},status=404)
    post('customer','/api/conversations',{'branch_id':'ops-branch','service':'product_inquiry'},status=422)
    post('customer','/api/conversations',{'branch_id':'ops-branch','service':'pickup','category':'product_inquiry'},status=422)
    with db.tx() as c:c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('ops-customer2','Customer 2','customer','ops-brand')")
    opened=post('customer','/api/conversations',{'branch_id':'ops-branch','service':'delivery'})['conversation']
    assert opened['route']=='delivery'
    assert actor('branch').get('/api/conversations/'+opened['id']+'/messages').status_code==404
    assert actor('customer2').get('/api/conversations/'+opened['id']+'/messages').status_code==404
    assert actor('customer2').get('/api/conversations').json()['conversations']==[]
    assert actor('manager').get('/api/conversations').json()['conversations'][0]['id']==opened['id']
    car=post('customer','/api/conversations',{'branch_id':'ops-branch','service':'car'})['conversation']
    assert car['route']=='branch' and car['context_service']=='car'


@pytest.mark.postgres
def test_order_linked_chat_context_still_uses_real_order(ops):
    actor,post,order,shifts=ops;oid=order('pickup')
    post('customer','/api/conversations',{'order_id':oid,'branch_id':'ops-branch-other','service':'pickup'},status=422)
    post('customer','/api/conversations',{'order_id':oid,'service':'delivery'},status=422)
    row=post('customer','/api/conversations',{'order_id':oid})['conversation']
    assert row['order_id']==oid and row['context_service']=='pickup' and row['route']=='branch'

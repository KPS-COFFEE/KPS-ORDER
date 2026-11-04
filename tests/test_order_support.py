"""Synthetic, isolated ticket lifecycle and evidence retention acceptance."""
import base64
import io
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from PIL import Image
from app import db
from app.order_support import cleanup, next_cleanup
from app.reporting import v5_tables
from test_operations import ops, close_driver


def image_body(stage):
    data=io.BytesIO();Image.new('RGB',(20,20),'white').save(data,'PNG')
    return {'stage':stage,'content_type':'image/png','data_base64':base64.b64encode(data.getvalue()).decode()}


def test_cleanup_deadline_is_before_next_delivery_day():
    assert next_cleanup(datetime(2026,10,12,12,54,tzinfo=timezone.utc)).isoformat()=='2026-10-12T15:55:00+03:00'
    assert next_cleanup(datetime(2026,10,12,12,56,tzinfo=timezone.utc)).isoformat()=='2026-10-13T15:55:00+03:00'


@pytest.mark.postgres
def test_required_photos_scope_idempotency_and_closed_case_retention(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    def raw(who,path,body,key=None):return actor(who).post(path,json=body,headers={'Idempotency-Key':key or uuid4().hex})
    assert raw('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'}).status_code==422
    assert raw('driver2',f'/api/tasks/{tid}/photos',image_body('picked_up')).status_code==404
    pickup=post('driver',f'/api/tasks/{tid}/photos',image_body('picked_up'))['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up','photo_id':pickup})
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    assert raw('driver',f'/api/tasks/{tid}/transition',{'status':'delivered','photo_id':pickup}).status_code==422
    assert raw('driver',f'/api/tasks/{tid}/transition',{'status':'delivered','proof':'text alone'}).status_code==422
    delivery=post('driver',f'/api/tasks/{tid}/photos',image_body('delivered'),key='delivery-image')['id']
    done={'status':'delivered','photo_id':delivery}
    assert post('driver',f'/api/tasks/{tid}/transition',done,key='delivery-confirm')==post('driver',f'/api/tasks/{tid}/transition',done,key='delivery-confirm')
    ticket_body={'order_id':oid,'category':'driver','note':'TEST ONLY مشكلة مع سائق'}
    raised=post('manager','/api/support-tickets',ticket_body,key='support-create')
    assert post('manager','/api/support-tickets',ticket_body,key='support-create')==raised
    ticket=raised['ticket'];cid=ticket['conversation_id'];url='/api/support-tickets/'+ticket['id']
    assert raised['reference'].startswith('TKT-')
    assert raw('customer','/api/support-tickets',ticket_body).status_code==403
    assert actor('other').get('/api/support-tickets?order_id='+oid).status_code==404
    assert raw('branch',url,{'status':'resolved','note':'test'}).status_code==403
    assert actor('other').get('/api/delivery-photos/'+pickup).status_code==404
    assert actor('customer').get('/api/delivery-photos/'+pickup).status_code==403
    messages=actor('customer').get(f'/api/conversations/{cid}/messages').json()
    assert len(messages['messages'])==1 and raised['reference'] in messages['messages'][0]['text']
    assert messages['tickets'][0]['status']=='received'
    with db.tx() as c:c.execute("UPDATE delivery_photos SET expires_at=now()-interval '1 day'")
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==200
    assert raw('manager',url,{'status':'closed','note':'cannot skip resolution'}).status_code==409
    for status in ['in_progress','awaiting_customer','resolved']:
        post('manager',url,{'status':status,'note':'TEST ONLY '+status})
        cleanup()
        assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==200
    post('manager',url,{'status':'closed','note':'TEST ONLY fully completed'})
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==200 # next cleanup, not immediately
    daily=actor('owner').get('/api/support-daily').json()
    assert daily['summary']=={'created':1,'closed':1,'open':0}
    with db.tx() as c:c.execute("UPDATE delivery_photos SET expires_at=now()-interval '1 minute'")
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==200
    post('manager',f'/api/orders/{oid}/evidence-retention',{'decision':'retain','reason':'TEST keep evidence until formal review'})
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==200
    post('manager',f'/api/orders/{oid}/evidence-retention',{'decision':'release','reason':'TEST formal review completed; retention expiry permitted'})
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+pickup).status_code==410
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM delivery_photos WHERE data IS NULL AND deleted_at IS NOT NULL').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM support_ticket_events').fetchone()['n']==5
    # Cash/report flow remains intact; new manager snapshot includes support cases.
    close_driver(post,driver,manager)
    post('manager','/api/shifts/'+manager+'/closing')
    post('manager','/api/shifts/'+manager+'/submit',{'counted_halalas':1000,'handover_halalas':1000})
    transfer=post('manager','/api/custody',{'from_shift_id':manager,'recipient_user_id':'ops-owner','amount_halalas':1000,'reference':'TEST SUPPORT','closure':True})['transfer']['id']
    post('owner','/api/custody/'+transfer+'/confirm')
    post('owner','/api/shifts/'+manager+'/approve')
    with db.tx() as c:snap=c.execute('SELECT snapshot FROM closure_reports WHERE shift_id=%s',(manager,)).fetchone()['snapshot']
    assert snap['support_tickets'][0]['id']==ticket['id']
    for view in ['owner','compact']:assert any(t['key']=='support-tickets' for t in v5_tables(snap,view))


@pytest.mark.postgres
def test_multiple_cases_reopening_and_conversation_holds(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    photo=post('driver',f'/api/tasks/{tid}/photos',image_body('picked_up'))['id']
    a=post('manager','/api/support-tickets',{'order_id':oid,'category':'complaint','note':'TEST A'})['ticket']
    b=post('manager','/api/support-tickets',{'order_id':oid,'category':'other','note':'TEST B'})['ticket']
    for status in ['resolved','closed']:post('manager','/api/support-tickets/'+a['id'],{'status':status,'note':'TEST'})
    with db.tx() as c:c.execute("UPDATE delivery_photos SET expires_at=now()-interval '1 day'")
    cleanup();assert actor('manager').get('/api/delivery-photos/'+photo).status_code==200
    for status in ['resolved','closed']:post('manager','/api/support-tickets/'+b['id'],{'status':status,'note':'TEST'})
    post('customer',f"/api/conversations/{a['conversation_id']}/messages",{'text':'TEST not resolved'})
    with db.tx() as c:c.execute("UPDATE delivery_photos SET expires_at=now()-interval '1 day'")
    cleanup();assert actor('manager').get('/api/delivery-photos/'+photo).status_code==200
    post('manager','/api/support-tickets/'+a['id'],{'status':'in_progress','note':'TEST reopen'})
    assert actor('manager').get('/api/support-daily').json()['summary']['open']==1
    post('manager','/api/support-tickets',{'order_id':oid,'category':'other','note':'   '},status=422)

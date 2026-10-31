"""Scoped catalog, consistent multi-driver exports, and evidence holds on real PostgreSQL."""
import base64
import io
import pytest
from PIL import Image
from app import db
from app.order_support import cleanup
from test_operations import ops
from test_accounting_driver_statements import seed,balance,cash

pytestmark=pytest.mark.postgres


def test_multiple_driver_filters_share_rows_and_export_totals(ops):
    actor,post,_,_=ops
    _,opened=seed();day=opened.date().isoformat()
    balance(post);cash(post,revision=1,amount=9900)
    balance(post,'statement-report2');cash(post,'statement-report2',revision=1,amount=10000)
    url=f'/api/accounting/reports?group=driver_statement&start={day}&end={day}'
    all_rows=actor('accountant').get(url).json()
    multiple=actor('accountant').get(url+'&driver_ids=ops-driver,statement-driver2').json()
    assert multiple['totals']==all_rows['totals']
    selected=actor('accountant').get(url+'&driver_ids=ops-driver&variance=pending').json()
    assert {r['driver_id'] for r in selected['rows']}=={'ops-driver'}
    assert selected['totals']['pending_shortage']==100
    assert selected['totals']['shortage']==0
    assert selected['drivers'][0]['pending_shortage']==100
    assert all(e['driver_id']=='ops-driver' for e in selected['settlements'])
    approved=actor('accountant').get(url+'&driver_ids=ops-driver&variance=shortage').json()
    assert approved['rows']==[]
    assert actor('other').get(url+'&driver_ids=ops-driver').json()['rows']==[]
    assert actor('driver').get(url).status_code==403
    for format in ('pdf','xlsx'):
        exported=actor('accountant').get(url+'&driver_ids=ops-driver&variance=pending&format='+format)
        assert exported.status_code==200
        assert exported.content.startswith(b'%PDF' if format=='pdf' else b'PK')
    assert actor('accountant').get('/api/accounting/reports?group=daily&variance=shortage').status_code==422


def test_private_catalog_includes_drafts_and_requires_stock_permission(ops):
    actor,post,order,_=ops
    seed();order()
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas) VALUES('ops-catalog-test','ops-brand','TEST draft item','TEST',1000)")
        c.execute("UPDATE products SET settings=settings || '{\"lifecycle\":\"draft\"}' WHERE brand_id='ops-brand'")
    url='/api/staff/catalog?branch_id=ops-branch'
    data=actor('accountant').get(url)
    assert data.status_code==200
    assert data.json()['products'] and all(p['lifecycle']=='draft' for p in data.json()['products'])
    assert data.json()['can_manage'] is False
    assert actor('accountant').get('/api/staff/catalog?branch_id=other-branch').status_code==404
    assert actor('driver').get(url).status_code==403
    product=data.json()['products'][0]['id']
    post('accountant',f'/api/branches/ops-branch/products/{product}/availability',{'available':True,'reason':'TEST read permission cannot mutate'},status=403)
    with db.tx() as c:c.execute("UPDATE users SET extra_permissions='[\"branch_availability\"]' WHERE id='ops-manager'")
    assert actor('manager').get(url).json()['can_manage'] is True
    with db.tx() as c:c.execute("UPDATE branches SET settings=jsonb_set(settings,'{product_availability}',%s) WHERE id='ops-branch'",(db.json({product:False}),))
    assert actor('manager').get(url).json()['products'][0]['stock_enabled'] is False
    result=post('manager',f'/api/branches/ops-branch/products/{product}/availability',{'available':True,'reason':'TEST resume branch stock only'})
    assert result['available'] is False  # a stock grant never publishes a draft
    assert actor('manager').get(url).json()['products'][0]['stock_enabled'] is True


def test_general_complaint_can_reach_owner_without_order_or_branch_and_keeps_image(ops):
    actor,post,_,_=ops
    conv=post('customer','/api/conversations',{'category':'complaint','details':'TEST general complaint without a branch','general_contact':True})['conversation']
    cid=conv['id']
    assert conv['reference'].startswith('CS-')
    assert any(row['id']==cid for row in actor('owner').get('/api/conversations').json()['conversations'])
    image=io.BytesIO();Image.new('RGB',(20,20),'orange').save(image,'PNG')
    aid=post('customer',f'/api/conversations/{cid}/attachments',{'content_type':'image/png','data_base64':base64.b64encode(image.getvalue()).decode()})['id']
    post('customer',f'/api/conversations/{cid}/messages',{'attachment_id':aid,'text':'TEST original customer evidence'})
    post('owner',f'/api/conversations/{cid}/messages',{'text':'TEST formal response retained'})
    post('owner',f'/api/conversations/{cid}/status',{'status':'resolved'})
    messages=actor('customer').get(f'/api/conversations/{cid}/messages').json()['messages']
    assert any(row.get('attachment_id')==aid or aid in (row.get('attachment_url') or '') for row in messages)
    assert actor('customer').get('/api/chat-attachments/'+aid).status_code==200


def test_only_flagged_orders_need_official_retention_decision_and_new_photos_stay(ops):
    actor,post,order,shifts=ops
    _,driver=shifts();normal=order();flagged=order()
    photo_ids={}
    for oid in (normal,flagged):
        tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
        photo='TEST-photo-'+oid;photo_ids[oid]=photo
        with db.tx() as c:
            c.execute("INSERT INTO delivery_photos(id,task_id,order_id,uploader_id,stage,data,sha256,expires_at,used_at) VALUES(%s,%s,%s,'ops-driver','delivered',%s,%s,now()-interval '1 day',now())",(photo,tid,oid,b'fake test evidence','a'*64))
    with db.tx() as c:db.event(c,flagged,'ops-customer','customer_request.opened',{'kind':'cancel','note':'TEST retain the original request'})
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+photo_ids[normal]).status_code==410
    assert actor('manager').get('/api/delivery-photos/'+photo_ids[flagged]).status_code==200
    endpoint=f'/api/orders/{flagged}/evidence-retention'
    post('driver',endpoint,{'decision':'release','reason':'TEST unauthorized release'},status=403)
    post('other',endpoint,{'decision':'release','reason':'TEST wrong branch release'},status=404)
    post('manager',endpoint,{'decision':'retain','reason':'TEST waiting for formal review'})
    cleanup();assert actor('manager').get('/api/delivery-photos/'+photo_ids[flagged]).status_code==200
    post('manager',endpoint,{'decision':'release','reason':'TEST manager reviewed existing evidence'})
    cleanup();assert actor('manager').get('/api/delivery-photos/'+photo_ids[flagged]).status_code==200
    with db.tx() as c:
        rid=c.execute("SELECT id FROM order_events WHERE order_id=%s AND event_type='customer_request.opened'",(flagged,)).fetchone()['id']
        db.event(c,flagged,'ops-manager','customer_request.decided',{'request_id':rid,'status':'rejected','note':'TEST formal case decision; request original retained'})
    with db.tx() as c:db.event(c,flagged,'ops-manager','task.problem',{'reason':'TEST new documented issue after the first review'})
    cleanup();assert actor('manager').get('/api/delivery-photos/'+photo_ids[flagged]).status_code==200
    post('manager',endpoint,{'decision':'release','reason':'TEST manager reviewed the newly recorded issue'})
    # New evidence is not covered by an older manager decision.
    with db.tx() as c:
        row=c.execute('SELECT * FROM delivery_photos WHERE id=%s',(photo_ids[flagged],)).fetchone()
        c.execute("INSERT INTO delivery_photos(id,task_id,order_id,uploader_id,stage,data,sha256,expires_at,used_at) VALUES('TEST-new-photo',%s,%s,'ops-driver','delivered',%s,%s,now()-interval '1 day',now())",(row['task_id'],flagged,b'new test evidence','b'*64))
    cleanup()
    assert actor('manager').get('/api/delivery-photos/'+photo_ids[flagged]).status_code==410
    assert actor('manager').get('/api/delivery-photos/TEST-new-photo').status_code==200
    history=actor('manager').get('/api/orders/'+flagged).json()['events']
    assert any(e['event_type']=='customer_request.opened' for e in history)
    assert len([e for e in history if e['event_type']=='evidence.retention_decided'])==3


def test_rejected_customer_request_and_customer_attachment_remain_on_order(ops):
    actor,post,order,_=ops;oid=order()
    cid=post('customer','/api/conversations',{'order_id':oid,'category':'complaint','details':'TEST original order complaint'})['conversation']['id']
    image=io.BytesIO();Image.new('RGB',(20,20),'orange').save(image,'PNG')
    aid=post('customer',f'/api/conversations/{cid}/attachments',{'content_type':'image/png','data_base64':base64.b64encode(image.getvalue()).decode()})['id']
    post('customer',f'/api/conversations/{cid}/messages',{'attachment_id':aid,'text':'TEST customer evidence stays with the order'})
    rid=post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'TEST original cancellation request'})['id']
    post('manager',f'/api/order-requests/orders/{oid}/{rid}/decision',{'action':'rejected','note':'TEST formal rejection with reason'})
    result=actor('manager').get(f'/api/order-amendments/orders/{oid}').json()
    request=next(r for r in result['requests'] if r['id']==rid)
    assert request['details']['note']=='TEST original cancellation request'
    assert request['decision']['note']=='TEST formal rejection with reason'
    assert request['requester_name']=='customer' and request['decider_name']=='manager'
    evidence=actor('manager').get(f'/api/orders/{oid}/delivery-photos').json()
    assert aid in {a['id'] for a in evidence['attachments']}
    assert actor('manager').get('/api/chat-attachments/'+aid).status_code==200
    post('manager',f'/api/conversations/{cid}/status',{'status':'resolved'})
    cleanup()
    assert actor('customer').get('/api/chat-attachments/'+aid).status_code==200

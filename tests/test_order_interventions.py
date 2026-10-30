from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db
from app.order_interventions import controlled,high_authority,RequestBody,evidence,REASONS,PENDING_ROLES
from test_operations import ops


def test_control_is_prospective_evening_delivery_only():
    base={'service':'delivery','snapshot':{'journey_controls_version':1}}
    assert controlled(base)
    assert not controlled({'service':'delivery','snapshot':{}})
    assert not controlled({**base,'snapshot':{'journey_controls_version':1,'delivery_period':'morning'}})
    assert not controlled({**base,'service':'pickup'})


def test_names_never_grant_high_authority():
    assert high_authority({'role':'owner'})
    for role in ('driver','customer','delivery_manager','admin'):
        assert not high_authority({'role':role,'display_name':'محمد عبدالسميع','extra_permissions':[]})
    assert high_authority({'role':'admin','extra_permissions':['orders_cancel_after_print']})
    assert not high_authority({'role':'driver','extra_permissions':['orders_cancel_after_print']})


def mark(oid,source=None,state='ready'):
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status=%s,snapshot=snapshot||%s WHERE id=%s",(state,db.json({'journey_controls_version':1,**({'order_source':source} if source else {})}),oid))


def print_job(oid):
    pid=uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,created_by) VALUES(%s,'ops-brand','ops-branch','TEST','ops-owner')",(pid,))
        c.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,status,payload,requested_by) VALUES(%s,'ops-brand','ops-branch',%s,%s,'order','cancelled','{}','ops-manager')",(uuid4().hex,pid,oid))


def customer_request(post,oid):
    return post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'إلغاء طلب اختبار'})['id']


def manager_request(post,oid,rid):
    return post('manager',f'/api/order-interventions/orders/{oid}',{'kind':'cancel','reason_code':'customer_cancel','customer_request_id':rid,'version':1})['id']


@pytest.mark.postgres
def test_website_request_is_customer_authored_in_order_chat_and_manager_cannot_bypass(ops):
    actor,post,order,_=ops;oid=order();mark(oid)
    post('manager',f'/api/order-financial-cases/orders/{oid}/cancel',{'version':1,'reason':'غير موثق'},status=409)
    post('manager',f'/api/order-interventions/orders/{oid}',{'kind':'cancel','reason_code':'customer_cancel','version':1},status=422)
    rid=customer_request(post,oid)
    with db.tx() as c:
        e=c.execute('SELECT actor_id,details FROM order_events WHERE id=%s',(rid,)).fetchone()
        assert e['actor_id']=='ops-customer'
        m=c.execute('SELECT sender_id FROM chat_messages WHERE id=%s',(e['details']['evidence_message_id'],)).fetchone()
        assert m['sender_id']=='ops-customer'
    iid=manager_request(post,oid,rid)
    response=post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',{'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1},key='approve-proof')
    assert response['status']=='approved'
    assert post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',{'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1},key='approve-proof')==response
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.executed'",(oid,)).fetchone()['n']==1
        c.execute("UPDATE users SET permission_denials=%s WHERE id='ops-manager'",(db.json(['orders_cancel']),))
    post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',{'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1},key='approve-proof',status=403)


@pytest.mark.postgres
def test_any_print_attempt_requires_high_authority_and_preserves_scope(ops):
    actor,post,order,_=ops;oid=order();mark(oid);print_job(oid)
    rid=customer_request(post,oid);iid=manager_request(post,oid,rid)
    path=f'/api/order-interventions/orders/{oid}/{iid}/decision'
    body={'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1}
    post('manager',path,body,status=403)
    post('other',path,body,status=404)
    post('owner',path,body)
    assert actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['printed'] is True


@pytest.mark.postgres
def test_customer_cancels_unaccepted_unpaid_order_directly_and_old_orders_keep_contract(ops):
    actor,post,order,_=ops;oid=order();mark(oid,state='new')
    result=post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'تغير رأي العميل'})
    assert result['status']=='resolved'
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
    legacy=order()
    result=post('customer',f'/api/order-requests/orders/{legacy}',{'kind':'cancel','note':'legacy'})
    assert result['status']=='requested'


@pytest.mark.postgres
def test_whatsapp_cancellation_requires_scoped_conversation_image(ops):
    actor,post,order,_=ops;oid=order(source='delivery_center');mark(oid,source='delivery_center')
    data={'kind':'cancel','reason_code':'customer_cancel','version':1}
    path=f'/api/order-interventions/orders/{oid}'
    post('manager',path,data,status=422)
    cid,aid=uuid4().hex,uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES(%s,%s,'ops-brand','ops-branch','ops-customer','delivery','delivery')",(cid,oid))
        c.execute("INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,'ops-manager','image/jpeg',4,'test',%s)",(aid,cid,b'test'))
    data['evidence_attachment_id']=aid
    iid=post('manager',path,data)['id']
    saved=actor('manager').get(path).json()['requests'][0]
    assert saved['id']==iid and saved['details']['evidence_attachment_id']==aid
    other=order(source='delivery_center');mark(other,source='delivery_center')
    post('manager',f'/api/order-interventions/orders/{other}',data,status=422)


@pytest.mark.postgres
def test_cancel_accounting_pending_is_persisted_without_refund_or_invoice_deletion(ops):
    actor,post,order,_=ops;oid=order();mark(oid)
    # A recorded collection makes cancellation operational only: accounting stays open.
    with db.tx() as c:
        c.execute("INSERT INTO collections(id,order_id,method,amount_halalas,tendered_halalas,change_halalas,collector_id) VALUES(%s,%s,'cash',1000,1000,0,'ops-driver')",(uuid4().hex,oid))
        c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s",(oid,))
    rid=customer_request(post,oid);iid=manager_request(post,oid,rid)
    post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',{'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1})
    with db.tx() as c:
        event=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.accounting_pending'",(oid,)).fetchone()
        assert event['details']['pending_role']=='accounting'
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        from app.order_interventions import enrich_orders
        rows=[{'id':oid}];enrich_orders(c,rows)
        assert rows[0]['intervention_pending']['pending_role']=='accounting'


@pytest.mark.postgres
def test_operational_reason_cannot_be_relabelled_as_customer_cancellation(ops):
    actor,post,order,_=ops;oid=order();mark(oid)
    iid=post('manager',f'/api/order-interventions/orders/{oid}',{'kind':'cancel','reason_code':'no_answer','contact_result':'no_answer','version':1})['id']
    post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',{'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1},status=422)
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'


def test_explicit_activation_covers_existing_evening_actions_without_rewriting_snapshots(monkeypatch):
    monkeypatch.setenv('DELIVERY_INTERVENTIONS_ENFORCED','true')
    legacy={'service':'delivery','snapshot':{'delivery_period':'evening'}}
    assert controlled(legacy)
    assert legacy['snapshot']=={'delivery_period':'evening'}
    assert not controlled({'service':'delivery','snapshot':{'delivery_period':'morning'}})


def test_delegated_shift_authority_is_only_the_exact_order_task():
    from app.order_interventions import delegated_cancel_shift_access
    user={'id':'gm','role':'admin','brand_id':'brand','branch_ids':['branch'],'extra_permissions':['orders_cancel_after_print']}
    order={'id':'order','brand_id':'brand','branch_id':'branch','service':'delivery','snapshot':{'journey_controls_version':1}}
    shift={'id':'shift','kind':'driver','brand_id':'brand','branch_ids':['branch','another'],'user_id':'driver'}
    task={'order_id':'order','shift_id':'shift','driver_id':'driver'}
    assert delegated_cancel_shift_access(user,order,shift,[task])
    with pytest.raises(HTTPException):delegated_cancel_shift_access(user,order,shift,[{**task,'order_id':'another'}])
    with pytest.raises(HTTPException):delegated_cancel_shift_access(user,order,{**shift,'brand_id':'other'},[task])


@pytest.mark.postgres
@pytest.mark.parametrize('picked_up',[False,True])
def test_delegated_admin_can_cancel_assigned_task_but_has_no_general_shift_grant(ops,picked_up):
    actor,post,order,shifts=ops;_,sid=shifts();oid=order();mark(oid)
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions) VALUES('ops-delegate','TEST GM','admin','ops-brand',%s,%s)",(db.json(['ops-branch']),db.json(['orders_cancel_after_print'])))
    if picked_up:
        with db.tx() as c:
            c.execute("UPDATE driver_tasks SET status='picked_up',picked_up_at=now() WHERE order_id=%s",(oid,))
    print_job(oid)
    # Assignment incremented order version; request/approval use the live version.
    rid=customer_request(post,oid)
    iid=post('manager',f'/api/order-interventions/orders/{oid}',{'kind':'cancel','reason_code':'customer_cancel','customer_request_id':rid,'version':2})['id']
    decision={'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':2}
    if picked_up:
        post('delegate',f'/api/order-interventions/orders/{oid}/{iid}/decision',decision,status=409)
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='intervention.approved'",(oid,)).fetchone()
        decision['physical_resolution']='returned_to_branch'
    post('delegate',f'/api/order-interventions/orders/{oid}/{iid}/decision',decision)
    with db.tx() as c:
        assert c.execute('SELECT status FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['status']=='cancelled'
        u=c.execute("SELECT * FROM users WHERE id='ops-delegate'").fetchone()
        from app import auth
        assert not auth.has_permission(u,'shifts_view')


@pytest.mark.postgres
def test_flag_on_legacy_printed_order_cannot_use_old_cancel_endpoint(ops,monkeypatch):
    actor,post,order,_=ops;oid=order();print_job(oid)
    monkeypatch.setenv('DELIVERY_INTERVENTIONS_ENFORCED','true')
    post('manager',f'/api/order-financial-cases/orders/{oid}/cancel',{'version':1,'reason':'test legacy bypass'},status=403)


@pytest.mark.postgres
def test_driver_operational_problem_needs_no_customer_proof_and_has_single_owner(ops):
    actor,post,order,shifts=ops;_,sid=shifts();oid=order();mark(oid)
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})
    path=f'/api/order-interventions/orders/{oid}'
    body={'kind':'problem','reason_code':'damaged','contact_result':'not_contacted','version':2}
    post('driver',path,{**body,'reason_code':'customer_cancel'},status=422)
    iid=post('driver',path,body,key='driver-problem-first')['id']
    post('driver',path,body,key='driver-problem-second',status=409)
    with db.tx() as c:
        rows=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.requested'",(oid,)).fetchall()
        assert len(rows)==1
        assert rows[0]['details']['pending_role']=='delivery_manager'
        assert rows[0]['details']['contact_result']=='not_contacted'
    # The structured cause can be recorded without inventing a phone call.
    post('manager',path+'/'+iid+'/decision',{'action':'resolve','reason_code':'damaged','contact_result':'not_contacted','version':2})
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert c.execute("SELECT count(*) AS n FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['n']==0


def test_structured_operational_cause_does_not_claim_customer_contact():
    body=RequestBody(kind='problem',reason_code='damaged',contact_result='not_contacted',version=1)
    assert evidence(None,{'snapshot':{}},None,body)=={'source':'website'}

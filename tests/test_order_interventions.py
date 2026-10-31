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


def controlled_order(source=None,state='ready',branch='ops-branch',*,service='delivery',journey_controls=True,snapshot_overrides=None):
    """Freeze journey controls at INSERT, exactly as real order creation does.

    Confirmed order snapshots are immutable; tests must never retrofit policy.
    """
    oid='intervention-order-'+uuid4().hex
    snapshot={'journey_controls_version':1,'items':[{'product_id':'ops-product','name':'قهوة تجربة',
        'quantity':1,'unit_price_halalas':1000,'line_total_halalas':1000,'units':[],'note':''}],
        'branch_name':'فرع اختبار','notes':''}
    if not journey_controls:snapshot.pop('journey_controls_version')
    snapshot.update(snapshot_overrides or {})
    if source:snapshot.update(order_source=source,created_by_staff_id='ops-manager')
    with db.tx() as c:
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'ops-brand',%s,'ops-customer',%s,%s,%s,'cash',1000,0,1000,%s,%s)""",
            (oid,branch,service,state,'pending' if service=='delivery' else 'not_applicable',db.json(snapshot),db.json({'name':'اختبار'})))
    return oid


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
    actor,post,order,_=ops;oid=controlled_order()
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
    actor,post,order,_=ops;oid=controlled_order();print_job(oid)
    rid=customer_request(post,oid);iid=manager_request(post,oid,rid)
    path=f'/api/order-interventions/orders/{oid}/{iid}/decision'
    body={'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1}
    post('manager',path,body,status=403)
    post('other',path,body,status=404)
    post('owner',path,body)
    assert actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['printed'] is True


@pytest.mark.postgres
def test_customer_cancels_unaccepted_unpaid_order_directly_and_old_orders_keep_contract(ops):
    actor,post,order,_=ops;oid=controlled_order(state='new')
    result=post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'تغير رأي العميل'})
    assert result['status']=='resolved'
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
    legacy=order()
    result=post('customer',f'/api/order-requests/orders/{legacy}',{'kind':'cancel','note':'legacy'})
    assert result['status']=='requested'


@pytest.mark.postgres
def test_whatsapp_cancellation_requires_scoped_conversation_image(ops):
    actor,post,order,_=ops;oid=controlled_order(source='delivery_center')
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
    other=controlled_order(source='delivery_center')
    post('manager',f'/api/order-interventions/orders/{other}',data,status=422)


@pytest.mark.postgres
def test_cancel_accounting_pending_is_persisted_without_refund_or_invoice_deletion(ops):
    actor,post,order,_=ops;oid=controlled_order()
    # A recorded collection makes cancellation operational only: accounting stays open.
    with db.tx() as c:
        c.execute("INSERT INTO collections(id,order_id,method,amount_halalas,tendered_halalas,change_halalas,collector_id) VALUES(%s,%s,'cash',1000,1000,0,'ops-driver')",(uuid4().hex,oid))
        c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s",(oid,))
    rid=customer_request(post,oid);iid=manager_request(post,oid,rid)
    decision={'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1}
    post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',decision,status=403)
    post('owner',f'/api/order-interventions/orders/{oid}/{iid}/decision',decision)
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
    actor,post,order,_=ops;oid=controlled_order()
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
    actor,post,order,shifts=ops;_,sid=shifts();oid=controlled_order()
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
    actor,post,order,shifts=ops;_,sid=shifts();oid=controlled_order()
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
    manager_view=actor('manager').get(path)
    assert manager_view.status_code==200
    assert any(r['id']==iid and not r['decision'] for r in manager_view.json()['requests'])
    # This asynchronous report does not change physical task progress or money.
    with db.tx() as c:
        assert c.execute('SELECT status FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['status']=='assigned'
    # The structured cause can be recorded without inventing a phone call.
    post('manager',path+'/'+iid+'/decision',{'action':'resolve','reason_code':'damaged','contact_result':'not_contacted','version':2})
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert c.execute("SELECT count(*) AS n FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['n']==0


def test_structured_operational_cause_does_not_claim_customer_contact():
    body=RequestBody(kind='problem',reason_code='damaged',contact_result='not_contacted',version=1)
    assert evidence(None,{'snapshot':{}},None,body)=={'source':'website'}


@pytest.mark.postgres
def test_delegated_cancellation_inbox_needs_no_general_order_or_accounting_grant(ops):
    actor,post,_,_=ops
    oid=controlled_order()
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions,permission_denials) VALUES('ops-delegate','GM test','admin','ops-brand',%s,%s,%s)",
            (db.json(['ops-branch']),db.json(['orders_cancel_after_print']),db.json(['accounting_reports','accounting_review','accounting_cases_followup'])))
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-sibling',2,'ops-sibling-source','ops-brand','فرع آخر','رفحاء','{}')")
    other=controlled_order(branch='ops-sibling')
    for order_id in (oid,other):
        with db.tx() as c:
            db.event(c,order_id,'ops-manager','intervention.requested',{'kind':'cancel','reason_code':'no_answer',
                'contact_result':'no_answer','pending_role':'owner_or_cancel_delegate','revision':0})
    response=actor('delegate').get('/api/order-interventions/pending-cancellations')
    assert response.status_code==200,response.text
    assert [r['order_id'] for r in response.json()['requests']]==[oid]
    assert actor('delegate').get(f'/api/order-interventions/orders/{oid}').status_code==200
    assert actor('delegate').get(f'/api/order-interventions/orders/{other}').status_code==404
    with db.tx() as c:
        from app import auth
        user=c.execute("SELECT * FROM users WHERE id='ops-delegate'").fetchone()
        assert not auth.has_permission(user,'orders_view')
        assert not auth.has_permission(user,'accounting_reports')
        c.execute("UPDATE users SET extra_permissions='[]' WHERE id='ops-delegate'")
    assert actor('delegate').get('/api/order-interventions/pending-cancellations').status_code==403
    # A regular manager has an assigned-work inbox, never the approval queue.
    manager_inbox=actor('manager').get('/api/order-interventions/pending-cancellations')
    assert manager_inbox.status_code==200 and manager_inbox.json()['requests']==[]


@pytest.mark.postgres
def test_print_after_request_escalates_current_responsibility_without_rewriting_request(ops):
    actor,post,_,_=ops;oid=controlled_order()
    rid=customer_request(post,oid);iid=manager_request(post,oid,rid)
    print_job(oid)
    path=f'/api/order-interventions/orders/{oid}'
    response=actor('manager').get(path)
    assert response.status_code==200,response.text
    assert response.json()['requests'][0]['details']['pending_role']=='owner_or_cancel_delegate'
    with db.tx() as c:
        original=c.execute('SELECT details FROM order_events WHERE id=%s',(iid,)).fetchone()['details']
        assert original['pending_role']=='delivery_manager'
        from app.order_interventions import enrich_orders
        rows=[{'id':oid}];enrich_orders(c,rows)
        assert rows[0]['intervention_pending']['pending_role']=='owner_or_cancel_delegate'
        assert rows[0]['intervention_pending']['pending_label']==PENDING_ROLES['owner_or_cancel_delegate']
    inbox=actor('owner').get('/api/order-interventions/pending-cancellations')
    assert inbox.status_code==200,inbox.text
    assert next(r for r in inbox.json()['requests'] if r['order_id']==oid)['pending_label']==PENDING_ROLES['owner_or_cancel_delegate']
    decision={'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1}
    post('manager',path+'/'+iid+'/decision',decision,status=403)
    post('owner',path+'/'+iid+'/decision',decision)


def test_effective_pending_role_escalates_only_cancellation_after_print():
    from app.order_interventions import effective_pending_role
    details={'kind':'cancel','pending_role':'delivery_manager'}
    assert effective_pending_role(details,False)=='delivery_manager'
    assert effective_pending_role(details,True)=='owner_or_cancel_delegate'
    assert effective_pending_role({**details,'kind':'problem'},True)=='delivery_manager'
    assert details['pending_role']=='delivery_manager'


def collection(oid,*,method='cash',amount=1000,payment_status='paid',refund=0):
    """Real immutable receipt lineage; no net-balance shortcut in the policy."""
    cid='intervention-receipt-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO collections(id,order_id,method,amount_halalas,tendered_halalas,change_halalas,collector_id) VALUES(%s,%s,%s,%s,%s,0,'ops-driver')",
                  (cid,oid,method,amount,amount if method=='cash' else None))
        if refund:
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,'اختبار استرداد فعلي','ops-owner')",
                      (uuid4().hex,cid,oid,method,refund))
        c.execute('UPDATE orders SET payment_status=%s WHERE id=%s',(payment_status,oid))
    return cid


def cancellation_delegate(*,permissions=None):
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions) VALUES('ops-collected-delegate','TEST designated staff','admin','ops-brand',%s,%s)",
            (db.json(['ops-branch']),db.json(permissions if permissions is not None else
                ['orders_view','orders_cancel_after_print','orders_cancel_collected'])))


def open_operational_request(post,oid,*,version=1,reason='no_answer'):
    return post('manager',f'/api/order-interventions/orders/{oid}',{
        'kind':'cancel','reason_code':reason,'contact_result':'not_contacted','version':version})['id']


def decision_body(*,reason='no_answer',version=1,workflow_revision=None):
    body={'action':'approve','reason_code':reason,'contact_result':'not_contacted','version':version}
    if workflow_revision is not None:body['workflow_revision']=workflow_revision
    return body


def test_collected_authority_is_an_explicit_staff_grant_never_a_name_or_job_title():
    from app.collected_order_authority import authorized
    assert authorized({'role':'owner'})
    for role in ('admin','delivery_manager','branch_manager','driver'):
        user={'role':role,'display_name':'محمد عبدالسميع','extra_permissions':['orders_cancel_after_print']}
        assert not authorized(user)
        user['extra_permissions'].append('orders_cancel_collected')
        assert authorized(user)==(role in ('admin','delivery_manager'))
        assert not authorized({**user,'permission_denials':['orders_cancel_collected']})
        assert not authorized({**user,'active':False})


@pytest.mark.postgres
@pytest.mark.parametrize('state,refund',[('paid',0),('partially_refunded',300),('refunded',1000),('unpaid',0)])
def test_raw_collection_requires_designated_grant_even_after_refund_or_unpaid_status(ops,state,refund):
    actor,post,_,_=ops;oid=controlled_order();cancellation_delegate(permissions=['orders_cancel_after_print'])
    collection(oid,payment_status=state,refund=refund)
    iid=open_operational_request(post,oid)
    path=f'/api/order-interventions/orders/{oid}/{iid}/decision';body=decision_body()
    with db.tx() as c:
        before=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()
        previous_refunds=c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()
        c.execute("UPDATE users SET display_name='محمد عبدالسميع' WHERE id='ops-manager'")
    denied=post('manager',path,body,status=403)
    assert denied['detail']['code']=='COLLECTED_CANCELLATION_AUTHORITY'
    post('collected-delegate',path,body,status=403)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='intervention.approved'",(oid,)).fetchone()
        c.execute("UPDATE users SET extra_permissions=%s WHERE id='ops-collected-delegate'",
                  (db.json(['orders_cancel_after_print','orders_cancel_collected']),))
    result=post('collected-delegate',path,body)
    assert result['status']=='approved' and result['financial_pending']
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()==before
        assert c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()==previous_refunds
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']==state
        authority=c.execute("SELECT actor_id,details FROM order_events WHERE order_id=%s AND event_type='order.collected_cancellation_authorized'",(oid,)).fetchall()
        assert len(authority)==1 and authority[0]['actor_id']=='ops-collected-delegate'
        assert authority[0]['details']['permission']=='orders_cancel_collected'
        assert authority[0]['details']['financial_approval'] is False


@pytest.mark.postgres
@pytest.mark.parametrize('route',['financial','preparation','task'])
@pytest.mark.parametrize('state,refund',[('paid',0),('refunded',1000)])
def test_collected_guard_covers_legacy_canonical_preparation_and_task_cancellation(ops,route,state,refund):
    actor,post,order,shifts=ops;oid=order();tid=None
    if route=='task':
        _,sid=shifts()
        tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})['task']['id']
        post('driver',f'/api/tasks/{tid}/transition',{'status':'problem','reason':'تعذر تسليم الطلب'})
    collection(oid,payment_status=state,refund=refund)
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    if route=='financial':
        path=f'/api/order-financial-cases/orders/{oid}/cancel';body={'version':version,'reason':'اختبار منع إلغاء محصل'}
    elif route=='preparation':
        path=f'/api/orders/{oid}/preparation';body={'status':'cancelled','version':version,'reason':'اختبار منع إلغاء محصل'}
    else:
        path=f'/api/tasks/{tid}/transition';body={'status':'cancelled','reason':'اختبار منع إلغاء محصل'}
    denied=post('manager',path,body,status=403)
    assert denied['detail']['code']=='COLLECTED_CANCELLATION_AUTHORITY'
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        if tid:assert c.execute('SELECT status FROM driver_tasks WHERE id=%s',(tid,)).fetchone()['status']=='problem'
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type IN ('order.operationally_cancelled','order.collected_cancellation_authorized')",(oid,)).fetchone()


@pytest.mark.postgres
@pytest.mark.parametrize('change,expected',[
    ("extra_permissions='[\"orders_cancel_after_print\"]'",403),
    ("permission_denials='[\"orders_cancel_collected\"]'",403),
    ("active=false",403),
    ("branch_ids='[]'",404)])
def test_collection_authority_replay_rechecks_current_grants_even_with_stale_actor(ops,change,expected):
    actor,post,_,_=ops;oid=controlled_order();cancellation_delegate();collection(oid)
    iid=open_operational_request(post,oid)
    path=f'/api/order-interventions/orders/{oid}/{iid}/decision';body=decision_body();key='collected-live-replay'
    client=actor('collected-delegate')
    first=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert first.status_code==200,first.text
    with db.tx() as c:c.execute("UPDATE users SET "+change+" WHERE id='ops-collected-delegate'")
    # Keep the dependency's old user object: authorization inside the endpoint
    # must reload PostgreSQL, not rely only on fresh-session middleware.
    replay=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert replay.status_code==expected,replay.text
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='order.operationally_cancelled'",(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0


@pytest.mark.postgres
def test_cancellation_guard_reloads_permission_after_an_earlier_successful_preflight(ops):
    from app.collected_order_authority import require
    from app.order_interventions import guard_cancellation
    _,_,order,_=ops;oid=order();cancellation_delegate();collection(oid)
    with db.tx() as c:
        stale=c.execute("SELECT * FROM users WHERE id='ops-collected-delegate'").fetchone()
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert require(c,o,stale)['permission']=='orders_cancel_collected'
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[\"orders_cancel_collected\"]' WHERE id='ops-collected-delegate'")
    with pytest.raises(HTTPException) as denied:
        with db.tx() as c:
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            guard_cancellation(c,o,stale)
    assert denied.value.status_code==403


@pytest.mark.postgres
@pytest.mark.parametrize('service,period',[('delivery',None),('delivery','morning'),('pickup',None),('car',None)])
def test_explicit_admin_delegate_can_operationally_cancel_all_legacy_services_without_financial_power(ops,service,period):
    actor,post,_,_=ops;cancellation_delegate()
    oid=controlled_order(service=service,journey_controls=False,snapshot_overrides={'delivery_period':period} if period else None)
    collection(oid)
    details=actor('collected-delegate').get(f'/api/order-interventions/orders/{oid}')
    assert details.status_code==200,details.text
    assert not details.json()['enabled'] and details.json()['legacy_cancel_allowed']
    path=f'/api/order-financial-cases/orders/{oid}/cancel';body={'version':1,'reason':'إلغاء تشغيلي موثق من المفوض'}
    result=post('collected-delegate',path,body,key='legacy-collected-cancel')
    assert result['operational_status']=='cancelled' and result['financial_case']['status']=='pending'
    post('collected-delegate',f'/api/order-financial-cases/orders/{oid}/resolve',{'reason':'ليست صلاحية مالية'},status=403)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        c.execute("UPDATE users SET permission_denials='[\"orders_cancel_collected\"]' WHERE id='ops-collected-delegate'")
    post('collected-delegate',path,body,key='legacy-collected-cancel',status=403)


@pytest.mark.postgres
@pytest.mark.parametrize('missing',['orders_view','orders_cancel_after_print','orders_cancel_collected'])
def test_legacy_admin_cancellation_requires_every_explicit_grant(ops,missing):
    actor,post,order,_=ops;cancellation_delegate(permissions=[p for p in
        ['orders_view','orders_cancel_after_print','orders_cancel_collected'] if p!=missing])
    oid=order()
    response=actor('collected-delegate').post(f'/api/order-financial-cases/orders/{oid}/cancel',
        json={'version':1,'reason':'صلاحية واحدة لا تكفي'},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code in (403,404),response.text
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'


@pytest.mark.postgres
def test_late_online_receipt_is_collection_even_when_order_still_unpaid(ops):
    actor,post,_,_=ops;cancellation_delegate()
    oid=controlled_order(service='pickup',journey_controls=False,
        snapshot_overrides={'branch_payment_mode':'test','branch_payment_seller':'synthetic-seller'})
    aid='online-attempt-'+uuid4().hex;pid=uuid4()
    with db.tx() as c:
        c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by,status,verified_at) VALUES(%s,%s,'ops-brand','ops-branch','synthetic-seller','moyasar',%s,'test',1000,'SAR',0,'ops-owner','review_required',now())",(aid,oid,pid))
        c.execute("INSERT INTO branch_online_receipts(id,attempt_id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,disposition) VALUES(%s,%s,%s,'ops-brand','ops-branch','synthetic-seller','moyasar',%s,'test',1000,'SAR','review_required')",(uuid4().hex,aid,oid,pid))
        before=c.execute('SELECT * FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchall()
    path=f'/api/order-financial-cases/orders/{oid}/cancel';body={'version':1,'reason':'مراجعة قبض إلكتروني متأخر'}
    post('branch',path,body,status=403)
    result=post('collected-delegate',path,body)
    assert result['financial_case']['status']=='pending' and result['payment_status']=='unpaid'
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchall()==before
        assert c.execute('SELECT count(*) n FROM branch_online_refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0


def return_body(*,revision=0,reason='wrong_address',target='ops-manager',version=1):
    return {'version':version,'workflow_revision':revision,'corrected_reason_code':reason,
            'target_manager_id':target,'note':'العنوان غير صحيح؛ استكمل التحقق من العميل وأعد رفع المعاملة.'}


def resubmit_body(*,revision=1,version=1,**proof):
    return {'version':version,'workflow_revision':revision,'contact_result':'not_contacted',
            'note':'تم استكمال التحقق وتوضيح السبب الصحيح للمراجع.',**proof}


@pytest.mark.postgres
def test_return_and_resubmit_preserve_original_reason_and_money_with_explicit_responsibility(ops):
    actor,post,_,_=ops;oid=controlled_order();collection(oid);print_job(oid)
    iid=open_operational_request(post,oid,reason='late_complaint')
    path=f'/api/order-interventions/orders/{oid}/{iid}'
    with db.tx() as c:
        original=c.execute('SELECT * FROM order_events WHERE id=%s',(iid,)).fetchone()
        original_order=c.execute('SELECT snapshot,customer,total_halalas,payment_status,version,preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()
        original_receipts=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()
    response=post('owner',path+'/return',return_body(),key='return-for-manager')
    assert response=={'id':iid,'status':'returned','workflow_revision':1}
    assert post('owner',path+'/return',return_body(),key='return-for-manager')==response
    owner_view=actor('owner').get(f'/api/order-interventions/orders/{oid}').json()
    item=next(r for r in owner_view['requests'] if r['id']==iid);flow=item['workflow']
    assert item['details']['reason_code']=='late_complaint'
    assert flow['original_reason_code']=='late_complaint' and flow['reason_code']=='wrong_address'
    assert flow['status']=='returned' and flow['revision']==1
    assert flow['current_responsible_user_id']=='ops-manager' and flow['current_responsible_role']=='delivery_manager'
    assert flow['current_responsible_name']=='manager' and flow['return_note']==return_body()['note']
    assert 'approve' not in flow['available_actions'] and 'استكمال' in flow['next_action_label']
    manager_view=actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]['workflow']
    assert 'resubmit' in manager_view['available_actions'] and 'approve' not in manager_view['available_actions']
    pending=actor('owner').get('/api/order-interventions/pending-cancellations').json()['requests']
    pending=next(r for r in pending if r['id']==iid)
    assert pending['pending_label']=='manager' and pending['workflow']['status']=='returned'
    with db.tx() as c:
        from app.order_interventions import enrich_orders
        orders=[{'id':oid}];enrich_orders(c,orders)
        assert orders[0]['intervention_pending']['pending_role']=='delivery_manager'
        assert orders[0]['intervention_pending']['workflow']['current_responsible_user_id']=='ops-manager'
        assert c.execute('SELECT * FROM order_events WHERE id=%s',(iid,)).fetchone()==original
        assert c.execute('SELECT snapshot,customer,total_halalas,payment_status,version,preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()==original_order
        assert c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()==original_receipts
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type IN ('financial_case.opened','intervention.approved','order.operationally_cancelled')",(oid,)).fetchone()
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
    post('owner',path+'/decision',decision_body(reason='wrong_address',workflow_revision=1),status=409)
    post('manager',path+'/resubmit',resubmit_body(revision=0),status=409)
    submitted=post('manager',path+'/resubmit',resubmit_body(),key='resubmit-for-owner')
    assert submitted=={'id':iid,'status':'resubmitted','workflow_revision':2}
    post('owner',path+'/decision',decision_body(reason='wrong_address',workflow_revision=1),status=409)
    post('owner',path+'/decision',decision_body(reason='wrong_address'),status=409)
    post('owner',path+'/decision',decision_body(reason='late_complaint',workflow_revision=2),status=422)
    result=post('owner',path+'/decision',decision_body(reason='wrong_address',workflow_revision=2))
    assert result['status']=='approved' and result['financial_pending']
    with db.tx() as c:
        assert c.execute('SELECT * FROM order_events WHERE id=%s',(iid,)).fetchone()==original
        assert c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()==original_receipts
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.returned'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.resubmitted'",(oid,)).fetchone()['n']==1
    final=actor('owner').get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]['workflow']
    assert final['status']=='approved' and final['revision']==3 and not final['available_actions']
    assert final['financial_pending'] and final['current_responsible_role']=='accounting'
    assert 'المحاسبة' in final['next_action_label']
    assert [event['event_type'] for event in final['history']]==[
        'intervention.requested','intervention.returned','intervention.resubmitted','intervention.approved',
        'intervention.accounting_pending']
    # Internal correction notes, actor history, and return targets stay staff-only.
    customer=actor('customer').get(f'/api/order-interventions/orders/{oid}')
    assert customer.status_code==200,customer.text
    assert return_body()['note'] not in customer.text and 'workflow' not in customer.json()['requests'][0]
    assert customer.json()['return_targets']==[]


@pytest.mark.postgres
@pytest.mark.parametrize('source',['website','delivery_center'])
def test_correcting_to_customer_cancellation_requires_current_explicit_customer_evidence(ops,source):
    actor,post,_,_=ops;oid=controlled_order(source='delivery_center' if source=='delivery_center' else None)
    iid=open_operational_request(post,oid);path=f'/api/order-interventions/orders/{oid}/{iid}'
    post('owner',path+'/return',return_body(reason='customer_cancel'))
    post('manager',path+'/resubmit',resubmit_body(),status=422)
    if source=='website':
        other=controlled_order();wrong=customer_request(post,other)
        post('manager',path+'/resubmit',resubmit_body(customer_request_id=wrong),status=422)
        proof={'customer_request_id':customer_request(post,oid)}
    else:
        cid,aid=uuid4().hex,uuid4().hex
        with db.tx() as c:
            c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES(%s,%s,'ops-brand','ops-branch','ops-customer','delivery','delivery')",(cid,oid))
            c.execute("INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,'ops-manager','image/jpeg',4,'test',%s)",(aid,cid,b'test'))
        proof={'evidence_attachment_id':aid}
    post('manager',path+'/resubmit',resubmit_body(**proof))
    result=post('owner',path+'/decision',decision_body(reason='customer_cancel',workflow_revision=2))
    assert result['proof'].items()>=proof.items()
    with db.tx() as c:
        original=c.execute('SELECT details FROM order_events WHERE id=%s',(iid,)).fetchone()['details']
        assert original['reason_code']=='no_answer'
        correction=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.returned'",(oid,)).fetchone()['details']
        assert correction['original_reason_code']=='no_answer' and correction['reason_code']=='customer_cancel'


@pytest.mark.postgres
def test_resubmitted_customer_request_must_still_be_current_when_approved(ops):
    actor,post,_,_=ops;oid=controlled_order();iid=open_operational_request(post,oid)
    path=f'/api/order-interventions/orders/{oid}/{iid}'
    post('owner',path+'/return',return_body(reason='customer_cancel'))
    source=customer_request(post,oid)
    post('manager',path+'/resubmit',resubmit_body(customer_request_id=source))
    with db.tx() as c:
        db.event(c,oid,'ops-owner','customer_request.decided',{'request_id':source,'status':'rejected','note':'سحب الطلب في مسار مستقل'})
    denied=post('owner',path+'/decision',decision_body(reason='customer_cancel',workflow_revision=2),status=409)
    assert denied['detail']['code']=='CUSTOMER_REQUEST_DECIDED'
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'


@pytest.mark.postgres
@pytest.mark.parametrize('target',['ops-driver','ops-other','ops-unscoped-manager','ops-disabled-manager','ops-denied-manager'])
def test_return_cannot_assign_an_inactive_or_unauthorized_responsible_user(ops,target):
    actor,post,_,_=ops;oid=controlled_order();iid=open_operational_request(post,oid)
    with db.tx() as c:
        for name,branches,active,denials in [
            ('unscoped-manager',[],True,[]),('disabled-manager',['ops-branch'],False,[]),
            ('denied-manager',['ops-branch'],True,['orders_cancel'])]:
            c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,active,permission_denials) VALUES(%s,%s,'delivery_manager','ops-brand',%s,%s,%s)",
                ('ops-'+name,name,db.json(branches),active,db.json(denials)))
    post('owner',f'/api/order-interventions/orders/{oid}/{iid}/return',return_body(target=target),status=422)
    with db.tx() as c:assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='intervention.returned'",(oid,)).fetchone()


@pytest.mark.postgres
def test_only_current_assignee_can_resubmit_and_owner_can_reassign_after_scope_revocation(ops):
    actor,post,_,_=ops;oid=controlled_order();iid=open_operational_request(post,oid)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-manager2','Manager Two','delivery_manager','ops-brand',%s)",(db.json(['ops-branch']),))
    path=f'/api/order-interventions/orders/{oid}/{iid}'
    post('owner',path+'/return',return_body())
    post('manager2',path+'/resubmit',resubmit_body(),status=403)
    cached=actor('manager')
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    denied=cached.post(path+'/resubmit',json=resubmit_body(),headers={'Idempotency-Key':uuid4().hex})
    assert denied.status_code==404,denied.text
    post('owner',path+'/return',return_body(revision=1,target='ops-manager2'))
    post('manager2',path+'/resubmit',resubmit_body(revision=2))
    current=actor('owner').get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]['workflow']
    assert current['status']=='resubmitted' and current['revision']==3
    assert len([h for h in current['history'] if h['event_type']=='intervention.returned'])==2


@pytest.mark.postgres
def test_return_idempotency_and_competing_versions_append_one_correction(ops):
    from concurrent.futures import ThreadPoolExecutor
    actor,post,_,_=ops;oid=controlled_order();iid=open_operational_request(post,oid)
    path=f'/api/order-interventions/orders/{oid}/{iid}/return';body=return_body();client=actor('owner')
    def push():return client.post(path,json=body,headers={'Idempotency-Key':'return-concurrently'})
    with ThreadPoolExecutor(max_workers=3) as pool:responses=list(pool.map(lambda _:push(),range(3)))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    assert len({r.json()['workflow_revision'] for r in responses})==1
    post('owner',path,{**body,'corrected_reason_code':'damaged'},key='different-correction',status=409)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.returned'",(oid,)).fetchone()['n']==1
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'


@pytest.mark.postgres
def test_supervision_read_grant_exposes_history_but_never_workflow_mutations(ops):
    actor,post,_,_=ops;oid=controlled_order();cancellation_delegate(permissions=['orders_view'])
    iid=open_operational_request(post,oid)
    view=actor('collected-delegate').get(f'/api/order-interventions/orders/{oid}')
    assert view.status_code==200,view.text
    assert view.json()['requests'][0]['workflow']['available_actions']==[]
    post('collected-delegate',f'/api/order-interventions/orders/{oid}/{iid}/return',return_body(),status=403)
    post('collected-delegate',f'/api/order-interventions/orders/{oid}/{iid}/decision',decision_body(),status=403)


def test_workflow_commands_require_explicit_revision_and_explanatory_note():
    from pydantic import ValidationError
    from app.order_interventions import ReturnBody,ResubmitBody
    for cls,body in ((ReturnBody,return_body()),(ResubmitBody,resubmit_body())):
        assert cls(**body).note==body['note']
        with pytest.raises(ValidationError):cls(**{**body,'note':'   '})
        with pytest.raises(ValidationError):cls(**{**body,'note':'ملاحظة\x00غير صالحة'})
        with pytest.raises(ValidationError):cls(**{**body,'workflow_revision':True})
        with pytest.raises(ValidationError):cls(**{k:v for k,v in body.items() if k!='workflow_revision'})

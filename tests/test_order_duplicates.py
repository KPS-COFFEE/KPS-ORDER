"""Focused duplicate semantics; database journeys use the isolated ops fixture."""
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db, order_duplicates as duplicates
from test_operations import ops


def purchased(product='coffee',quantity=1,options=None):
    return {'product_id':product,'quantity':quantity,
            'units':[{'options':options or {}} for _ in range(quantity)]}


def make_order(items, *, source=None, phone='+966500000001', status='new',service='delivery'):
    oid='duplicate-test-'+uuid4().hex
    snapshot={'items':items,'branch_name':'فرع اختبار','delivery_workflow':'manager_dispatch'}
    if source:snapshot['order_source']=source
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer',%s,%s,%s,'cash',1000,0,1000,%s,%s)",
            (oid,service,status,'pending' if service=='delivery' else 'not_applicable',db.json(snapshot),db.json({'name':'اختبار','phone':phone})))
    return oid


def test_selection_ignores_gift_price_labels_and_order_but_not_quantities_or_options():
    a=[purchased(options={'flavour':['a','b']}),purchased('cookie',2)]
    b=[purchased('cookie'),purchased(options={'flavour':['b','a']}),purchased('cookie'),
       {**purchased('gift'),'is_gift':True}]
    assert duplicates.selections(a)==duplicates.selections(b)
    changed=deepcopy(b);changed[1]['units'][0]['options']={'flavour':['c']}
    assert duplicates.selections(a)!=duplicates.selections(changed)
    assert duplicates.selections(a)!=duplicates.selections(a+[purchased()])


@pytest.mark.postgres
def test_contact_capture_gift_change_and_scope(ops):
    actor,post,order,shifts=ops
    oid=make_order([purchased()])
    with db.tx() as c:
        original=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        phone=original['customer']['phone']
        quote={'items':[purchased(),{**purchased('gift'),'is_gift':True}]}
        duplicates.capture(c,original['brand_id'],phone.replace('+966','0'),quote)
        assert quote['duplicate_review']['status']=='pending'
        assert quote['duplicate_review']['related_order_ids']==[oid]
        other={'items':[purchased()]}
        duplicates.capture(c,'other-brand',phone,other)
        assert other['duplicate_review']['status']=='clear'
        changed={'items':[purchased(quantity=2)]}
        duplicates.capture(c,original['brand_id'],phone,changed)
        assert changed['duplicate_review']['status']=='clear'


@pytest.mark.postgres
def test_pending_blocks_guard_and_manager_resolution_is_replayable(ops):
    actor,post,order,shifts=ops
    first=make_order([purchased()],source='delivery_center');second=make_order([purchased()])
    # The fixture constructs legacy orders. Runtime checkout captures this state
    # before insert; here execution-time detection covers legacy acceptance.
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(second,)).fetchone()
        assert duplicates.review(c,o)['status']=='pending'
        with pytest.raises(HTTPException) as e:duplicates.guard(c,o)
        assert e.value.detail['code']=='DUPLICATE_REVIEW_REQUIRED'
    response=actor('manager').get('/api/orders/'+second+'/duplicate-review')
    assert response.status_code==200
    body={'action':'confirm_additional','version':response.json()['version'],'reason':'تأكيد العميل طلب إضافي'}
    key=uuid4().hex
    saved=post('manager','/api/orders/'+second+'/duplicate-review',body,key=key)
    assert saved==post('manager','/api/orders/'+second+'/duplicate-review',body,key=key)
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(second,)).fetchone()
        duplicates.guard(c,o)
        assert duplicates.review(c,o)['status']=='confirmed_additional'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='order.duplicate_decided'",(second,)).fetchone()['n']==1
    assert actor('customer').get('/api/orders/'+second+'/duplicate-review').status_code==403


@pytest.mark.postgres
def test_duplicate_cancellation_preserves_original_and_actual_collection(ops):
    actor,post,order,shifts=ops
    first=make_order([purchased()],source='delivery_center');second=make_order([purchased()])
    with db.tx() as c:
        before=c.execute('SELECT * FROM orders WHERE id=%s',(second,)).fetchone()
    result=post('manager','/api/orders/'+second+'/duplicate-review',
        {'action':'cancel_duplicate','version':before['version'],'reason':'العميل أكد أنه طلب مكرر'})
    assert result['review']['status']=='cancelled_duplicate'
    with db.tx() as c:
        after=c.execute('SELECT * FROM orders WHERE id=%s',(second,)).fetchone()
        assert after['snapshot']==before['snapshot']
        assert after['total_halalas']==before['total_halalas']
        assert after['preparation_status']=='cancelled'
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(second,)).fetchone()['n']==0
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(first,)).fetchone()['preparation_status']=='new'


@pytest.mark.postgres
@pytest.mark.parametrize('source',[None,'delivery_center','customer_site'])
def test_concurrent_creations_and_acceptance_cannot_execute_both(ops,source):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    actor,post,order,shifts=ops
    shifts()
    gate=Barrier(2)
    def create(index):
        gate.wait(timeout=15)
        oid='duplicate-race-'+uuid4().hex
        quote={'items':[purchased()], 'delivery_workflow':'manager_dispatch'}
        if source:quote['order_source']=source
        # One request can receive a gift while another does not.
        if index:quote['items'].append({**purchased('gift'),'is_gift':True})
        with db.tx() as c:
            duplicates.capture(c,'ops-brand','0500000001',quote)
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','new','pending','cash',1000,0,1000,%s,%s)",
                (oid,db.json(quote),db.json({'name':'اختبار','phone':'+966500000001'})))
        return oid,quote['duplicate_review']['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        created=list(pool.map(create,[0,1]))
    assert sorted(state for _,state in created)==['clear','pending']
    client=actor('manager')
    def accept(entry):
        oid,_=entry
        return client.post('/api/orders/'+oid+'/manager-accept',json={'version':1},
            headers={'Idempotency-Key':uuid4().hex})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(accept,created))
    assert sorted(r.status_code for r in results)==[200,409], [r.text for r in results]
    rejected=next(r for r in results if r.status_code==409)
    assert rejected.json()['detail']['code']=='DUPLICATE_REVIEW_REQUIRED'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM orders WHERE id=ANY(%s) AND preparation_status='accepted'",([oid for oid,_ in created],)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=ANY(%s)',([oid for oid,_ in created],)).fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=ANY(%s)',([oid for oid,_ in created],)).fetchone()['n']==0


@pytest.mark.postgres
def test_queued_legacy_duplicate_never_reaches_bridge_and_review_projection_updates(ops):
    from test_branch_printing import approve_test_device
    actor,post,order,shifts=ops
    target=make_order([purchased()])
    pair=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Duplicate test'})
    paired=actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic'})
    assert paired.status_code==200,paired.text
    token=paired.json()['token'];approve_test_device(actor,post,pair['id'],token)
    job=post('manager','/api/printing/orders/'+target,{'device_id':pair['id']})
    older=make_order([purchased()])
    with db.tx() as c:
        c.execute("UPDATE orders SET created_at=now()-interval '2 minutes' WHERE id=%s",(older,))
    headers={'Authorization':'Bearer '+token}
    assert actor('customer').post('/api/printing/bridge/next',json={},headers=headers).json()['job'] is None
    held=actor('manager').get('/api/orders/'+target).json()['order']
    assert held['duplicate_review']['status']=='pending'
    assert 'related_order_ids' not in held['duplicate_review']
    post('manager','/api/orders/'+target+'/duplicate-review',
         {'action':'confirm_additional','version':held['version'],'reason':'أكد العميل الطلب الإضافي'})
    current=actor('manager').get('/api/orders/'+target).json()['order']
    assert current['duplicate_review']['status']=='confirmed_additional'
    public=actor('customer').get('/api/orders/'+target).json()
    assert 'related_order_ids' not in str(public)
    result=actor('customer').post('/api/printing/bridge/next',json={},headers=headers)
    assert result.json()['job']['id']==job['id']


@pytest.mark.postgres
def test_post_acceptance_duplicate_preserves_paid_money_and_requires_physical_resolution(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    make_order([purchased()])
    oid=make_order([purchased()],status='handed_over')
    tid='legacy-task-'+uuid4().hex;cid='legacy-collection-'+uuid4().hex
    with db.tx() as c:
        original=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,picked_up_at) VALUES(%s,%s,'ops-driver',%s,'picked_up','ops-manager',now(),now())",(tid,oid,driver))
        c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,tendered_halalas,change_halalas) VALUES(%s,%s,%s,'ops-driver','cash',1000,1000,0)",(cid,oid,driver))
        c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s",(oid,))
    body={'action':'cancel_duplicate','version':original['version'],'reason':'اكتشف العميل التكرار بعد القبول'}
    denied=post('manager','/api/orders/'+oid+'/duplicate-review',body,status=409)
    assert denied['detail']['code']=='CANCELLATION_PHYSICAL_RESOLUTION_REQUIRED'
    saved=post('manager','/api/orders/'+oid+'/duplicate-review',{**body,'physical_resolution':'returned_to_branch'})
    assert saved['review']['status']=='cancelled_duplicate'
    with db.tx() as c:
        current=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert current['snapshot']==original['snapshot']
        assert current['payment_status']=='paid'
        assert c.execute('SELECT amount_halalas FROM collections WHERE id=%s',(cid,)).fetchone()['amount_halalas']==1000
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['n']==1
        assert c.execute('SELECT status FROM driver_tasks WHERE id=%s',(tid,)).fetchone()['status']=='cancelled'


@pytest.mark.postgres
@pytest.mark.parametrize('service',['pickup','car'])
def test_branch_manager_resolves_own_service_duplicate(ops,service):
    actor,post,order,shifts=ops
    make_order([purchased()],service=service)
    oid=make_order([purchased()],service=service)
    assert actor('manager').get('/api/orders/'+oid+'/duplicate-review').status_code==404
    result=actor('branch').get('/api/orders/'+oid+'/duplicate-review')
    assert result.status_code==200,result.text
    state=result.json();assert state['review']['status']=='pending'
    post('branch','/api/orders/'+oid+'/duplicate-review',
        {'action':'confirm_additional','version':state['version'],'reason':'أكد العميل طلب استلام إضافي'})
    saved=actor('branch').get('/api/orders/'+oid).json()['order']
    assert saved['duplicate_review']['status']=='confirmed_additional'


@pytest.mark.postgres
def test_concurrent_confirm_and_cancel_are_one_atomic_decision(ops):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    actor,post,order,shifts=ops
    make_order([purchased()]);oid=make_order([purchased()])
    client=actor('manager');gate=Barrier(2)
    def decide(action):
        gate.wait(timeout=15)
        return action,client.post('/api/orders/'+oid+'/duplicate-review',
            json={'action':action,'version':1,'reason':'حسم متزامن من مدير التوصيل'},
            headers={'Idempotency-Key':uuid4().hex})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(decide,['confirm_additional','cancel_duplicate']))
    assert sorted(r.status_code for _,r in results)==[200,409],[(a,r.text) for a,r in results]
    winner=next(a for a,r in results if r.status_code==200)
    with db.tx() as c:
        saved=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        state=duplicates.review(c,saved)
        assert state['status']==('confirmed_additional' if winner=='confirm_additional' else 'cancelled_duplicate')
        assert saved['preparation_status']==('new' if winner=='confirm_additional' else 'cancelled')
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='order.duplicate_decided'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='order.operationally_cancelled'",(oid,)).fetchone()['n']==(0 if winner=='confirm_additional' else 1)


@pytest.mark.postgres
def test_failed_duplicate_decision_rolls_back_operational_cancellation(ops,monkeypatch):
    actor,post,order,shifts=ops
    make_order([purchased()]);oid=make_order([purchased()])
    original_event=db.event
    def fault(c,order_id,actor_id,event_type,details):
        if event_type=='order.duplicate_decided':raise RuntimeError('synthetic decision write failure')
        return original_event(c,order_id,actor_id,event_type,details)
    monkeypatch.setattr(db,'event',fault)
    with pytest.raises(RuntimeError,match='synthetic decision write failure'):
        actor('manager').post('/api/orders/'+oid+'/duplicate-review',
            json={'action':'cancel_duplicate','version':1,'reason':'اختبار فشل كتابة القرار'},
            headers={'Idempotency-Key':uuid4().hex})
    with db.tx() as c:
        saved=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert saved['preparation_status']=='new' and saved['version']==1
        assert duplicates.review(c,saved)['status']=='pending'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type IN ('order.operationally_cancelled','order.duplicate_decided')",(oid,)).fetchone()['n']==0

"""Real PostgreSQL requests, isolation, cancellation and read-only inbox gates."""
from uuid import uuid4

import pytest

from app import db
from test_operations import ops
from test_order_amendments import amendment_case


@pytest.mark.postgres
@pytest.mark.parametrize('kind', ['cancel','duplicate'])
def test_read_does_not_clear_request_and_only_real_cancellation_resolves(ops, kind):
    actor,post,order,_=ops
    oid=order(); other=order()
    path='/api/order-requests/orders/'+oid
    body={'kind':kind,'note':'طلب تجريبي <script>plain text</script>',
          'related_order_id':other if kind=='duplicate' else None}
    key=uuid4().hex
    result=post('customer',path,body,key=key)
    assert post('customer',path,body,key=key)==result
    rid=result['id']
    assert result['label']=='بانتظار المعالجة'
    post('customer',path,body,status=409)
    for _ in range(2):
        inbox=actor('manager').get('/api/order-amendments/inbox').json()
        assert [r['request_id'] for r in inbox['requests']]==[rid]
        detail=actor('customer').get('/api/order-amendments/orders/'+oid).json()
        assert detail['requests'][0]['decision'] is None
    decision=path+'/'+rid+'/decision'
    post('customer',decision,{'action':'resolved','note':'unauthorized'},status=403)
    post('branch',decision,{'action':'rejected','note':'wrong role'},status=403)
    post('manager',decision,{'action':'resolved','note':'not cancelled'},status=409)
    post('manager','/api/orders/'+oid+'/preparation',{'status':'cancelled','version':1,'reason':'طلب العميل'})
    payload={'action':'resolved','note':'تم إلغاء هذا الطلب؛ الطلب الآخر محفوظ'}
    key=uuid4().hex
    resolved=post('manager',decision,payload,key=key)
    assert post('manager',decision,payload,key=key)==resolved
    assert not actor('manager').get('/api/order-amendments/inbox').json()['requests']
    saved=actor('customer').get('/api/order-amendments/orders/'+oid).json()['requests'][0]
    assert saved['decision']['status']=='resolved' and saved['decided_at']
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(other,)).fetchone()['preparation_status']=='ready'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer_request.opened'",(oid,)).fetchone()['n']==1
        e=c.execute("SELECT actor_id FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchone()
        assert e['actor_id']=='ops-manager'


@pytest.mark.postgres
def test_requests_scope_and_rejection_do_not_mutate_order(ops):
    actor,post,order,_=ops;oid=order();other='other-customer-order'
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('request-other-customer','Other','customer','ops-brand')")
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            SELECT %s,brand_id,branch_id,'request-other-customer',service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer
            FROM orders WHERE id=%s""",(other,oid))
    path='/api/order-requests/orders/'+oid
    post('customer',path,{'kind':'duplicate','note':'wrong account','related_order_id':other},status=404)
    rid=post('customer',path,{'kind':'cancel','note':'طلب إلغاء'})['id']
    assert actor('other').get('/api/order-amendments/orders/'+oid).status_code==404
    assert not actor('other').get('/api/order-amendments/inbox').json()['requests']
    assert not actor('branch').get('/api/order-amendments/inbox').json()['requests']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    assert not actor('manager').get('/api/order-amendments/inbox').json()['requests']
    post('manager',path+'/'+rid+'/decision',{'action':'rejected','note':'scope'},status=404)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[\"ops-branch\"]' WHERE id='ops-manager'")
    post('manager',path+'/'+rid+'/decision',{'action':'rejected','note':'   '},status=422)
    post('manager',path+'/'+rid+'/decision',{'action':'rejected','note':'تعذر الإلغاء بعد الاتفاق مع العميل'})
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0


@pytest.mark.postgres
def test_paid_order_cannot_be_marked_processed_without_refund(ops):
    actor,post,order,shifts=ops;shifts();oid=order('pickup')
    post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    path='/api/order-requests/orders/'+oid
    rid=post('customer',path,{'kind':'cancel','note':'استرداد وإلغاء'})['id']
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    cancellation={'status':'cancelled','version':version,'reason':'طلب العميل'}
    denied=post('branch','/api/orders/'+oid+'/preparation',cancellation,status=403)
    assert denied['detail']['code']=='COLLECTED_CANCELLATION_AUTHORITY'
    financial=post('owner','/api/orders/'+oid+'/preparation',cancellation,status=409)
    assert financial['detail']['code']=='REFUND_WORKFLOW_REQUIRED'
    post('branch',path+'/'+rid+'/decision',{'action':'resolved','note':'غير منفذ'},status=409)
    assert actor('branch').get('/api/order-amendments/inbox').json()['requests'][0]['request_id']==rid


@pytest.mark.postgres
def test_expired_amendment_stays_pending_until_explicit_decision(amendment_case):
    actor,post,make,propose,decision=amendment_case
    oid=make('delivery');aid,_=propose(oid)
    with db.tx() as c:c.execute("UPDATE order_amendments SET expires_at=now()-interval '1 second' WHERE id=%s",(aid,))
    assert actor('customer').get('/api/order-amendments/orders/'+oid).json()['active_id']==aid
    assert actor('manager').get('/api/order-amendments/inbox').json()['orders'][0]['amendment_id']==aid
    assert not actor('branch').get('/api/order-amendments/inbox').json()['orders']
    post('manager','/api/order-amendments/'+aid+'/decision',{'action':'offer'},status=409)
    decision(aid,'reject',who='manager',note='انتهى العرض؛ تواصلنا مع العميل')
    assert not actor('manager').get('/api/order-amendments/inbox').json()['orders']


@pytest.mark.postgres
def test_concurrent_requests_have_one_pending_winner(ops):
    from concurrent.futures import ThreadPoolExecutor
    actor,_,order,_=ops;oid=order();client=actor('customer')
    def submit(_):
        return client.post('/api/order-requests/orders/'+oid,
            json={'kind':'cancel','note':'طلب متزامن'},headers={'Idempotency-Key':uuid4().hex}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2)))==[200,409]
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer_request.opened'",(oid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_request_and_decision_survive_separate_schema_restore(ops,tmp_path):
    import os
    import psycopg
    from psycopg import sql
    from app.exports import build_archive,restore_archive
    actor,post,order,_=ops;oid=order();path='/api/order-requests/orders/'+oid
    rid=post('customer',path,{'kind':'duplicate','note':'اختبار حفظ الطلب'})['id']
    post('manager',path+'/'+rid+'/decision',{'action':'rejected','note':'طلبان مستقلان حسب تأكيد العميل'})
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        data,manifest=build_archive(c,full=True)
    target='kps_restore_'+uuid4().hex;base=os.environ['TEST_DATABASE_URL']
    try:
        result=restore_archive(data,target_url=base,target_schema=target,asset_directory=tmp_path/'assets')
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(base) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert c.execute("SELECT count(*) FROM order_events WHERE order_id=%s AND event_type IN ('customer_request.opened','customer_request.decided')",(oid,)).fetchone()[0]==2
            assert c.execute("SELECT details->>'request_id' FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchone()[0]==rid
    finally:
        with psycopg.connect(base,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))

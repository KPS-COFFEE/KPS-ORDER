"""Delivery decisions use canonical order state, scope and atomic assignment."""
import pytest
from app import db, operations, orders
from tests.test_operations import ops
from tests.test_hardening_operations import principal, request, race

pytestmark=pytest.mark.postgres

def pending(order,service='delivery'):
    oid=order(service=service)
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='new' WHERE id=%s",(oid,))
    return oid

def test_accept_and_assign_once(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=pending(order)
    body={'driver_shift_id':driver,'accept_order':True}
    result=post('manager',f'/api/orders/{oid}/assign',body,key='TEST-accept-assign')
    assert post('manager',f'/api/orders/{oid}/assign',body,key='TEST-accept-assign')==result
    post('manager',f'/api/orders/{oid}/assign',body,status=409)
    with db.tx() as c:
        row=c.execute('SELECT preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row=={'preparation_status':'accepted','delivery_status':'assigned'}
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'",(oid,)).fetchone()['n']==1

def test_failed_assignment_does_not_accept_and_contact_gate_preserved(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=pending(order)
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':'missing','accept_order':True},status=409)
    with db.tx() as c:
        c.execute("UPDATE orders SET customer=customer || '{\"requires_operational_confirmation\":true}'::jsonb WHERE id=%s",(oid,))
    result=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver,'accept_order':True},status=409)
    assert result['detail']['code']=='OPERATIONAL_CONTACT_CONFIRMATION_REQUIRED'
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='new'
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==0

def test_manager_reject_reason_replay_and_scope(ops):
    actor,post,order,shifts=ops
    oid=pending(order);body={'status':'cancelled','version':1,'reason':'TEST unavailable service'}
    post('manager',f'/api/orders/{oid}/preparation',{**body,'reason':''},status=422)
    post('other',f'/api/orders/{oid}/preparation',body,status=404)
    post('driver',f'/api/orders/{oid}/preparation',body,status=403)
    result=post('manager',f'/api/orders/{oid}/preparation',body,key='TEST-reject')
    assert post('manager',f'/api/orders/{oid}/preparation',body,key='TEST-reject')==result
    assert result['order']['preparation_status']=='cancelled'
    assert result['order']['delivery_status']=='cancelled'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.cancelled'",(oid,)).fetchone()['n']==1

@pytest.mark.parametrize('case',['pickup','paid','assigned','prepare','stale'])
def test_manager_cannot_bypass_fulfillment_or_financial_guards(ops,case):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=pending(order,'pickup' if case=='pickup' else 'delivery')
    body={'status':'accepted' if case=='prepare' else 'cancelled','version':1,'reason':'TEST reason'}
    if case=='paid':
        with db.tx() as c:c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s",(oid,))
    if case=='assigned':
        post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})
        body['version']=2
    if case=='stale':body['version']=999
    post('manager',f'/api/orders/{oid}/preparation',body,status=403 if case in ('pickup','prepare') else 409)
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='new'

def test_accept_assign_races_rejection_without_orphan_task(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=pending(order);user=principal('manager')
    results=race([
        lambda:operations.assign(oid,operations.Assignment(driver_shift_id=driver,accept_order=True),request('TEST-assign-race'),user),
        lambda:orders.preparation(oid,orders.PreparationBody(status='cancelled',version=1,reason='TEST race'),user,'TEST-reject-race'),
    ])
    assert sorted(r[0] for r in results)==[200,409]
    with db.tx() as c:
        row=c.execute('SELECT preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()
        count=c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']
        assert (row['preparation_status'],row['delivery_status'],count) in [('accepted','assigned',1),('cancelled','cancelled',0)]

def test_disabled_decisions_stay_disabled(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=pending(order)
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[\"delivery_assign\",\"orders_cancel\"]' WHERE id='ops-manager'")
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver,'accept_order':True},status=403)
    post('manager',f'/api/orders/{oid}/preparation',{'status':'cancelled','version':1,'reason':'TEST disabled'},status=403)

"""Separate manager acceptance: real database, no driver required or invented evidence."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db, orders
from test_operations import ops

pytestmark = pytest.mark.postgres


def new_order(confirmation=False):
    oid='accept-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','new','pending','cash',1000,0,1000,%s,%s)",
                  (oid,db.json({'items':[],'delivery_workflow':'manager_dispatch','delivery_period':'evening'}),
                   db.json({'name':'اختبار','requires_operational_confirmation':confirmation})))
    return oid


def test_accept_without_driver_and_replay_preserves_one_event(ops):
    actor,post,_,_=ops
    post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})
    oid=new_order();key=uuid4().hex
    result=post('manager',f'/api/orders/{oid}/manager-accept',{'version':1},key=key)
    assert result['order']['preparation_status']=='accepted'
    assert result['order']['delivery_status']=='pending'
    assert result['order']['version']==2
    assert post('manager',f'/api/orders/{oid}/manager-accept',{'version':1},key=key)==result
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==0
        events=c.execute('SELECT event_type FROM order_events WHERE order_id=%s',(oid,)).fetchall()
        assert [e['event_type'] for e in events]==['preparation.accepted']


def test_fresh_role_branch_and_open_shift_are_required_even_for_replay(ops):
    actor,post,_,_=ops
    oid=new_order();path=f'/api/orders/{oid}/manager-accept';body={'version':1};key=uuid4().hex
    assert actor('manager').post(path,json=body,headers={'Idempotency-Key':key}).status_code==409
    post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})
    for who in ['customer','driver','branch']:
        assert actor(who).post(path,json=body,headers={'Idempotency-Key':key}).status_code==403
    assert actor('other').post(path,json=body,headers={'Idempotency-Key':key}).status_code==404
    assert post('manager',path,body,key=key)['order']['version']==2
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    assert actor('manager').post(path,json=body,headers={'Idempotency-Key':key}).status_code==404


def test_unconfirmed_contact_is_not_accepted(ops):
    actor,post,_,_=ops
    post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})
    oid=new_order(confirmation=True)
    response=actor('manager').post(f'/api/orders/{oid}/manager-accept',json={'version':1},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==409,response.text
    with db.tx() as c:assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='new'


def test_concurrent_acceptance_cannot_double_record_or_reset_clock(ops):
    actor,post,_,_=ops
    post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})
    oid=new_order()
    with db.tx() as c:user=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    def accept(_):
        try:
            orders.manager_accept(oid,orders.ManagerAcceptanceBody(version=1),user=user,idempotency_key=uuid4().hex)
            return 200
        except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(accept,range(2)))==[200,409]
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'",(oid,)).fetchone()['n']==1


def test_assignment_after_separate_acceptance_preserves_acceptance_clock(ops):
    actor,post,_,shifts=ops
    _,sid=shifts();oid=new_order()
    post('manager',f'/api/orders/{oid}/manager-accept',{'version':1})
    with db.tx() as c:db.event(c,oid,'ops-manager','order.print_output_confirmed',{'branch_id':'ops-branch','revision':0})
    task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid,'accept_order':False})['task']
    post('driver',f"/api/tasks/{task['id']}/transition",{'status':'accepted'})
    with db.tx() as c:
        row=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row['preparation_status']=='accepted' and row['delivery_status']=='accepted'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'",(oid,)).fetchone()['n']==1
        assigned=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(task['id'],)).fetchone()
        assert assigned['accepted_at'] is not None



def test_manager_cannot_close_with_accepted_unassigned_order(ops):
    actor,post,_,_=ops
    sid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    oid=new_order()
    post('manager',f'/api/orders/{oid}/manager-accept',{'version':1})
    response=actor('manager').post(f'/api/shifts/{sid}/closing',json={},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='ACCEPTED_ORDERS_AWAITING_DISPATCH'
    with db.tx() as c:assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    post('manager',f'/api/orders/{oid}/preparation',{'version':2,'status':'cancelled','reason':'طلب العميل الإلغاء'})
    assert post('manager',f'/api/shifts/{sid}/closing')['status']=='closing'


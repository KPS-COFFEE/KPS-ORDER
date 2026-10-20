from uuid import uuid4
import pytest
from app import auth, db, morning_delivery, service_hours
from test_operations import ops
from test_morning_delivery import at

pytestmark = pytest.mark.postgres


def proposal():
    return {'policy': {'enabled': True, 'execution_branch_ids': ['kps-branch-1'],
            'preparer_user_ids': ['ops-branch'], 'manager_user_ids': ['ops-manager']},
            'expected_version': 0, 'reason': 'اعتماد تشغيل صباحي تجريبي'}


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_owner_policy_scope_version_replay_and_pause(ops, monkeypatch):
    actor, _, _, _ = ops
    monkeypatch.setattr(auth, 'require_owner_step_up', lambda c,u: None)
    body=proposal(); headers={'Idempotency-Key': uuid4().hex}
    assert actor('manager').put('/api/morning-delivery/policy', json=body).status_code == 403
    first=actor('owner').put('/api/morning-delivery/policy', json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert actor('owner').put('/api/morning-delivery/policy', json=body, headers=headers).json() == first.json()
    assert actor('owner').put('/api/morning-delivery/policy', json=body, headers={'Idempotency-Key':uuid4().hex}).status_code == 409
    with db.tx() as c:
        c.execute("UPDATE users SET active=false WHERE id='ops-manager'")
    body['expected_version']=1
    assert actor('owner').put('/api/morning-delivery/policy', json=body, headers={'Idempotency-Key':uuid4().hex}).status_code == 422
    body['policy']['enabled']=False
    paused=actor('owner').put('/api/morning-delivery/policy', json=body, headers={'Idempotency-Key':uuid4().hex})
    assert paused.status_code == 200, paused.text
    assert paused.json()['existing_orders_preserved']


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_morning_shift_inheritance_duty_and_assignment_revocation(ops, monkeypatch):
    actor, post, order, _ = ops
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('08:00:00'))
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'morning_delivery':proposal()['policy'] | {'version':1}}),))
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['kps-branch-1'],'delivery_period':'morning'})['shift']
    assert manager['delivery_window_snapshot']['period']=='morning'
    driver=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager['id']})['shift']
    assert driver['delivery_window_snapshot']['period']=='morning'
    with db.tx() as c: morning_delivery.require_duty(c,'kps','kps-branch-1')
    oid='morning-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'kps','kps-branch-1','ops-customer','delivery','new','pending','cash',1000,0,1000,%s,%s)",
                  (oid,db.json({'items':[],'delivery_period':'morning','delivery_workflow':'branch_fulfillment'}),db.json({'name':'اختبار'})))
    path=f'/api/orders/{oid}/assign';body={'driver_shift_id':driver['id'],'accept_order':True}
    assert actor('manager').post(path,json=body,headers={'Idempotency-Key':uuid4().hex}).status_code==409
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='ready' WHERE id=%s",(oid,))
    key=uuid4().hex
    assigned=post('manager',path,body,key=key)
    assert assigned['task']['order_id']==oid
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    assert actor('manager').post(path,json=body,headers={'Idempotency-Key':key}).status_code==403

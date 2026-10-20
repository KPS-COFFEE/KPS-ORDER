from uuid import uuid4
import pytest
from app import auth, db, morning_delivery, service_hours
from test_operations import ops, close_driver
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


def enable_morning(monkeypatch):
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('08:00:00'))
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",
                  (db.json({'morning_delivery':proposal()['policy'] | {'version':1}}),))


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_new_morning_order_blocks_closing_after_cutoff_and_policy_pause(ops, monkeypatch):
    actor, post, _, _ = ops
    enable_morning(monkeypatch)
    manager=post('manager','/api/shifts',{'kind':'manager','delivery_period':'morning'})['shift']['id']
    oid='morning-close-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'kps','kps-branch-1','ops-customer','delivery','new','pending','cash',1000,0,1000,%s,'{}')",
                  (oid,db.json({'items':[],'delivery_period':'morning'})))
        c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,enabled}','false') WHERE id='kps'")
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('16:30:00'))
    result=post('manager',f'/api/shifts/{manager}/closing',status=409)
    assert result['detail']['code']=='MORNING_ORDERS_AWAITING_DISPATCH'
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(manager,)).fetchone()['status']=='open'
        c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status='cancelled' WHERE id=%s",(oid,))
    assert post('manager',f'/api/shifts/{manager}/closing')['status']=='closing'
    with db.tx() as c:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            morning_delivery.require_duty(c,'kps','kps-branch-1')
        assert error.value.status_code==409


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('period', ['morning','evening'])
def test_driver_inherits_only_morning_intersection_and_cannot_edit_scope(ops, monkeypatch, period):
    actor, post, _, _ = ops
    enable_morning(monkeypatch)
    if period=='evening':
        monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('18:00:00'))
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-driver'",(db.json(['kps-branch-1','kps-branch-2']),))
    manager=post('manager','/api/shifts',{'kind':'manager','delivery_period':period})['shift']['id']
    available=actor('driver').get('/api/shifts/current').json()['available_manager_shifts']
    assert any(s['id']==manager for s in available) is (period=='morning')
    # Explicit self-selected narrower scope remains forbidden in both periods.
    post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager,'branch_ids':['kps-branch-1']},status=403)
    result=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager},status=200 if period=='morning' else 409)
    if period=='morning':
        assert result['shift']['branch_ids']==['kps-branch-1']
        with db.tx() as c:
            assert c.execute("SELECT branch_ids FROM users WHERE id='ops-driver'").fetchone()['branch_ids']==['kps-branch-1','kps-branch-2']


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_zero_task_morning_driver_must_close_before_manager_and_report_keeps_period(ops, monkeypatch):
    actor, post, _, _ = ops
    enable_morning(monkeypatch)
    manager=post('manager','/api/shifts',{'kind':'manager','delivery_period':'morning'})['shift']['id']
    driver=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager})['shift']['id']
    # Window expiry does not strand custody or silently close either shift.
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('16:30:00'))
    post('manager',f'/api/shifts/{manager}/closing')
    post('manager',f'/api/shifts/{manager}/submit',{'counted_halalas':0,'handover_halalas':0},status=409)
    result=close_driver(post,driver,manager,counted=0)
    report=actor('owner').get('/api/reports/'+result['report_id'])
    assert report.status_code==200,report.text
    assert report.json()['report']['snapshot']['shift']['delivery_window_snapshot']['period']=='morning'
    assert post('manager',f'/api/shifts/{manager}/submit',{'counted_halalas':0,'handover_halalas':0})['status']=='submitted'


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_morning_handover_cannot_move_drivers_to_evening_manager(ops, monkeypatch):
    _, post, _, _ = ops
    enable_morning(monkeypatch)
    manager=post('manager','/api/shifts',{'kind':'manager','delivery_period':'morning'})['shift']['id']
    driver=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager})['shift']['id']
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('18:00:00'))
    successor=post('owner','/api/shifts',{'kind':'manager','delivery_period':'evening'})['shift']['id']
    post('manager',f'/api/shifts/{manager}/handover-manager',
         {'target_manager_shift_id':successor,'reason':'اختبار منع تغيير فترة المناوبة'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT manager_shift_id FROM shifts WHERE id=%s',(driver,)).fetchone()['manager_shift_id']==manager
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(manager,)).fetchone()['status']=='open'
        assert c.execute('SELECT count(*) n FROM manager_handovers WHERE from_shift_id=%s',(manager,)).fetchone()['n']==0


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_accepted_morning_handover_releases_old_manager_only_with_live_successor(ops, monkeypatch):
    _, post, _, _ = ops
    enable_morning(monkeypatch)
    manager=post('manager','/api/shifts',{'kind':'manager','delivery_period':'morning'})['shift']['id']
    successor=post('owner','/api/shifts',{'kind':'manager','delivery_period':'morning'})['shift']['id']
    oid='morning-handover-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'kps','kps-branch-1','ops-customer','delivery','new','pending','cash',1000,0,1000,%s,'{}')",
                  (oid,db.json({'items':[],'delivery_period':'morning'})))
    handover=post('manager',f'/api/shifts/{manager}/handover-manager',
                  {'target_manager_shift_id':successor,'reason':'استلام مسؤولية الطلبات الصباحية'})['handover_id']
    body={'counted_halalas':0,'handover_halalas':0}
    assert post('manager',f'/api/shifts/{manager}/submit',body,status=409)['detail']['code']=='MORNING_ORDERS_AWAITING_DISPATCH'
    post('owner',f'/api/manager-handovers/{handover}/accept')
    assert post('manager',f'/api/shifts/{manager}/submit',body)['status']=='submitted'
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-owner'")
    assert post('manager',f'/api/shifts/{manager}/approve',status=409)['detail']['code']=='MORNING_ORDERS_AWAITING_DISPATCH'
    with db.tx() as c:c.execute("UPDATE users SET active=true WHERE id='ops-owner'")
    assert post('manager',f'/api/shifts/{manager}/approve')['status']=='closed'
    # Pending work now keeps the accepted successor on duty instead.
    assert post('owner',f'/api/shifts/{successor}/closing',status=409)['detail']['code']=='MORNING_ORDERS_AWAITING_DISPATCH'

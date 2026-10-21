from uuid import uuid4
import pytest
from app import auth, db, service_hours
from test_operations import ops
from test_morning_delivery import at

pytestmark=pytest.mark.postgres


def setup_trial(ops,monkeypatch):
    actor,post,_,_=ops
    monkeypatch.setattr(auth,'require_owner_step_up',lambda c,u:None)
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    with db.tx() as c:
        branch=c.execute("SELECT * FROM branches WHERE id='kps-branch-1'").fetchone()
        c.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",(db.json(branch['settings']),))
        c.execute("UPDATE users SET branch_ids='[\"kps-branch-2\"]' WHERE id IN ('ops-manager','ops-driver','ops-branch')")
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{'enabled':True,'version':1,'execution_branch_ids':['kps-branch-2'],'manager_user_ids':['ops-manager'],'preparer_user_ids':['ops-branch']}}),))
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['kps-branch-2'],'delivery_period':'morning'})['shift']
    body={'kind':'driver','user_id':'ops-driver','manager_shift_id':manager['id'],'receipt_trial':True,'reason':'Owner authorized receiving trial'}
    return actor,post,body,manager


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_owner_receipt_trial_has_no_invented_reading_and_replays(ops,monkeypatch):
    actor,post,body,manager=setup_trial(ops,monkeypatch)
    key=uuid4().hex
    r=actor('owner').post('/api/shifts',json=body,headers={'Idempotency-Key':key})
    assert r.status_code==200,r.text
    shift=r.json()['shift']
    assert shift['branch_ids']==['kps-branch-2'] and shift['manager_shift_id']==manager['id']
    assert shift['vehicle_log']['start'] is None
    assert shift['vehicle_log']['receipt_trial']['inspection_deferred']
    again=actor('owner').post('/api/shifts',json=body,headers={'Idempotency-Key':key})
    assert again.status_code==200 and again.json()==r.json()


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_manager_cannot_authorize_inspection_deferral(ops,monkeypatch):
    actor,post,body,_=setup_trial(ops,monkeypatch)
    r=actor('manager').post('/api/shifts',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==403,r.text
    with db.tx() as c:assert not c.execute("SELECT 1 FROM shifts WHERE user_id='ops-driver'").fetchone()


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_trial_receives_accepts_but_cannot_confirm_pickup(ops,monkeypatch):
    actor,post,body,_=setup_trial(ops,monkeypatch)
    r=actor('owner').post('/api/shifts',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==200,r.text
    sid=r.json()['shift']['id'];oid='trial-order-'+uuid4().hex
    readiness=actor('owner').get('/api/operations/drivers')
    assert readiness.status_code==200,readiness.text
    assert any(row['id']=='ops-driver' and row['assignment_eligible'] for row in readiness.json()['drivers'])
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-2','ops-customer','delivery','ready','pending','cash',1000,0,1000,%s,'{}')",(oid,db.json({'delivery_period':'morning','delivery_workflow':'branch_fulfillment','items':[]})))
    task=post('owner',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})['task']
    post('driver',f'/api/tasks/{task["id"]}/transition',{'status':'accepted'})
    r=actor('driver').post(f'/api/tasks/{task["id"]}/transition',json={'status':'picked_up'},headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==409 and r.json()['detail']['code']=='TRIAL_INSPECTION_DEFERRED',r.text
    with db.tx() as c:
        t=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(task['id'],)).fetchone()
        assert t['status']=='accepted' and t['picked_up_at'] is None and t['delivered_at'] is None


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_owner_step_up_is_required_before_trial_shift_creation(ops,monkeypatch):
    from fastapi import HTTPException
    actor,post,body,_=setup_trial(ops,monkeypatch)
    def denied(c,u):raise HTTPException(403,detail={'code':'OWNER_STEP_UP_REQUIRED'})
    monkeypatch.setattr(auth,'require_owner_step_up',denied)
    r=actor('owner').post('/api/shifts',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==403,r.text
    with db.tx() as c:assert not c.execute("SELECT 1 FROM shifts WHERE user_id='ops-driver'").fetchone()

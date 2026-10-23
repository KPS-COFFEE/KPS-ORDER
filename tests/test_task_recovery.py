"""Administrative review is scoped, explicit, durable and never a driver receipt."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import db, order_amendments, task_recovery, employee_permissions
from tests.test_operations import ops
from tests.test_order_amendments import amendment_case


def test_recovery_capabilities_and_strict_confirmation():
    for role in ('driver','branch_manager','admin','customer','delivery_staff'):
        with pytest.raises(HTTPException):task_recovery.authorize({'role':role})
    with pytest.raises(HTTPException):
        task_recovery.authorize({'role':'delivery_manager','permission_denials':['tasks_resolve_delivery']})
    task_recovery.authorize({'role':'owner'})
    task_recovery.authorize({'role':'delivery_manager'})
    assert employee_permissions.required_for(task_recovery.detail,'delivery_manager')=='tasks_resolve_delivery'
    body={'revision':1,'version':2,'reason':'Verified exception','evidence':'Called driver','confirmed':True}
    for change in ({'confirmed':'true'},{'reason':'  '},{'revision':True},{'version':0},{'evidence':'\x00bad'},{'unknown':'x'}):
        with pytest.raises(ValidationError):task_recovery.RevisionReview(**(body|change))


def test_administrative_review_is_bound_to_current_revision_and_driver():
    calls=[]
    def execute(sql,args):
        calls.append(args)
        return SimpleNamespace(fetchone=lambda: None if 'order_revision_receipts' in sql else args==('o','2','driver-1'))
    c=SimpleNamespace(execute=execute)
    assert order_amendments.driver_reviewed(c,{'id':'o','amendment_revision':2},'driver-1')
    assert not order_amendments.driver_reviewed(c,{'id':'o','amendment_revision':3},'driver-1')
    assert not order_amendments.driver_reviewed(c,{'id':'o','amendment_revision':2},'driver-2')
    assert order_amendments.driver_reviewed(c,{'id':'o','amendment_revision':0},'driver-1')


def case(ops, amendment_case):
    actor,post,order,shifts=ops
    _,driver=shifts()
    _,_,make,propose,decision=amendment_case
    oid=make('delivery')
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    aid,_=propose(oid,qty=1)
    decision(aid,'confirm_branch')
    decision(aid,'offer',who='manager')
    decision(aid,'accept',who='customer')
    with db.tx() as c:
        version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    body={'revision':1,'version':version,'reason':'TEST verified revised address','evidence':'TEST driver contacted and informed','confirmed':True}
    return actor,post,oid,tid,driver,body


@pytest.mark.postgres
def test_review_unlocks_only_revision_guard_and_preserves_receipts_money_and_time(ops,amendment_case):
    actor,post,oid,tid,shift,body=case(ops,amendment_case)
    endpoint=f'/api/task-recovery/{tid}/review-revision'
    before=actor('manager').get(f'/api/task-recovery/{tid}').json()
    assert 'driver_revision' in before['blockers'] and before['can_review_revision']
    assert 'pickup_missing' in before['blockers'] and 'payment_missing' in before['blockers']
    assert not before['can_resolve_delivery']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'},status=409)
    post('driver',endpoint,body,status=403)
    post('other',endpoint,body,status=404)
    result=post('manager',endpoint,body,key='TEST-administrative-review')
    assert post('manager',endpoint,body,key='TEST-administrative-review')==result
    post('manager',endpoint,body,key='TEST-second-administrative-review')
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM order_revision_receipts WHERE order_id=%s',(oid,)).fetchone()
        assert not c.execute('SELECT 1 FROM collections WHERE order_id=%s',(oid,)).fetchone()
        task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
        assert task['status']=='assigned' and task['picked_up_at'] is None and task['delivered_at'] is None
        rows=c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='amendment.administrative_review'",(oid,)).fetchall()
        assert len(rows)==1 and rows[0]['actor_id']=='ops-manager'
        assert rows[0]['details']['evidence']==body['evidence']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    after=actor('manager').get(f'/api/task-recovery/{tid}').json()
    assert 'driver_revision' not in after['blockers'] and 'branch_revision' in after['blockers']
    assert not after['can_resolve_delivery']
    _,_,_,propose,decision=amendment_case
    aid,_=propose(oid,qty=2)
    decision(aid,'confirm_branch')
    decision(aid,'offer',who='manager')
    decision(aid,'accept',who='customer')
    post('manager',endpoint,body,status=409)
    assert 'driver_revision' in actor('manager').get(f'/api/task-recovery/{tid}').json()['blockers']
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[\"tasks_resolve_delivery\"]' WHERE id='ops-manager'")
    post('manager',endpoint,body,key='TEST-administrative-review',status=403)


@pytest.mark.postgres
@pytest.mark.parametrize('change',['unconfirmed','stale','closed_shift','cancelled_task','revoked_branch','denied_amend'])
def test_recovery_rejects_invalid_interventions_without_events(ops,amendment_case,change):
    actor,post,oid,tid,shift,body=case(ops,amendment_case)
    status=409
    with db.tx() as c:
        if change=='unconfirmed':body['confirmed']=False;status=422
        if change=='stale':body['version']+=1
        if change=='closed_shift':c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(shift,))
        if change=='cancelled_task':c.execute("UPDATE driver_tasks SET status='cancelled' WHERE id=%s",(tid,))
        if change=='revoked_branch':c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'");status=404
        if change=='denied_amend':c.execute("UPDATE users SET permission_denials='[\"orders_amend\"]' WHERE id='ops-manager'");status=403
    post('manager',f'/api/task-recovery/{tid}/review-revision',body,status=status)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='amendment.administrative_review'",(oid,)).fetchone()


@pytest.mark.postgres
def test_recovery_listing_search_pagination_brand_and_shift_scope(ops,amendment_case):
    actor,post,oid,tid,shift,body=case(ops,amendment_case)
    assert actor('driver').get('/api/task-recovery').status_code==403
    assert actor('other').get('/api/task-recovery').json()['tasks']==[]
    rows=actor('manager').get('/api/task-recovery?search=driver&limit=1').json()
    assert rows['tasks'][0]['id']==tid and rows['next_offset'] is None
    reference=str(rows['tasks'][0]['order_no'])
    assert actor('manager').get('/api/task-recovery?search='+reference).json()['tasks'][0]['id']==tid
    assert actor('manager').get('/api/task-recovery?search=%25').json()['tasks']==[]
    assert actor('manager').get('/api/task-recovery?search=not-present').json()['tasks']==[]
    with db.tx() as c:
        c.execute("UPDATE shifts SET manager_shift_id=NULL WHERE id=%s",(shift,))
    assert actor('manager').get('/api/task-recovery').json()['tasks']==[]
    assert actor('manager').get(f'/api/task-recovery/{tid}').status_code==403

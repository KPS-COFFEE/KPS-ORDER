"""Missing milestones and explicit manager corrections must not invent time."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import performance, operations, employee_permissions, db
from tests.test_operations import ops

NOW = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)


def test_missing_ready_stops_preparation_after_pickup():
    events = [{'order_id': 'o', 'event_type': 'preparation.accepted', 'created_at': NOW-timedelta(hours=8)}]
    tasks = [{'order_id':'o','driver_id':'d','display_name':'Test','status':'picked_up',
              'picked_up_at':NOW-timedelta(hours=7),'delivered_at':None}]
    conn = SimpleNamespace(execute=lambda query, args: SimpleNamespace(fetchall=lambda: events if 'order_events' in query else tasks))
    row = {'id':'o','created_at':NOW-timedelta(hours=9),'service':'delivery',
           'preparation_status':'handed_over','delivery_status':'picked_up'}
    performance.enrich_orders(conn, [row], NOW)
    assert row['timings']['preparation'] == {'seconds':None,'state':'missing'}
    assert row['timings']['driver_wait'] == {'seconds':None,'state':'missing'}
    assert row['timings']['delivery'] == {'seconds':25200,'state':'running'}
    row['delivery_status']='delivered'
    performance.enrich_orders(conn, [row], NOW)
    assert row['timings']['delivery'] == {'seconds':None,'state':'missing'}
    assert row['timings']['total'] == {'seconds':None,'state':'missing'}


def valid_inputs():
    body=operations.DeliveryResolution(reason='Verified late recording',evidence='Customer call reference TEST',
                                      delivered_at=NOW-timedelta(minutes=10),confirmed=True)
    task={'status':'picked_up','picked_up_at':NOW-timedelta(minutes=30),'delivered_at':None}
    order={'service':'delivery','created_at':NOW-timedelta(hours=1),'completed_at':None,
           'preparation_status':'handed_over','delivery_status':'picked_up','payment_status':'paid'}
    return body,task,order


@pytest.mark.parametrize('change', ['future','before_pickup','naive','unconfirmed','no_pickup','unpaid','cancelled','already_done','assigned'])
def test_resolution_rejects_invalid_state_or_unverified_time(change):
    body,task,order=valid_inputs()
    if change=='future':body.delivered_at=NOW+timedelta(seconds=1)
    if change=='before_pickup':body.delivered_at=NOW-timedelta(hours=1)
    if change=='naive':body.delivered_at=body.delivered_at.replace(tzinfo=None)
    if change=='unconfirmed':body.confirmed=False
    if change=='no_pickup':task['picked_up_at']=None
    if change=='unpaid':order['payment_status']='pending'
    if change=='cancelled':order['preparation_status']='cancelled'
    if change=='already_done':order['completed_at']=NOW
    if change=='assigned':task['status']='assigned'
    with pytest.raises(HTTPException):operations.validate_delivery_resolution(body,task,order,NOW)


def test_verified_time_and_permission_contract():
    body,task,order=valid_inputs()
    operations.validate_delivery_resolution(body,task,order,NOW)
    code='tasks_resolve_delivery'
    assert code in employee_permissions.effective({'role':'delivery_manager'})
    assert code not in employee_permissions.effective({'role':'delivery_manager','permission_denials':[code]})
    for role in ('driver','delivery_staff','branch_manager','customer','admin'):
        assert code not in employee_permissions.effective({'role':role})
    assert employee_permissions.required_for(operations.resolve_delivery,'delivery_manager')==code
    with pytest.raises(ValidationError):
        operations.DeliveryResolution(**(body.model_dump()|{'evidence':'  '}))


@pytest.mark.postgres
def test_manager_resolution_keeps_actual_time_payment_and_replay_scoped(ops):
    actor,post,order,shifts=ops
    _,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    path=f'/api/tasks/{tid}/transition'
    post('driver',path,{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',path,{'status':'picked_up'})
    endpoint=f'/api/tasks/{tid}/resolve-delivery'
    actual=datetime.now(timezone.utc)
    body={'reason':'TEST delayed recording','evidence':'TEST verified customer contact','delivered_at':actual.isoformat(),'confirmed':True}
    post('manager',endpoint,body,status=409)
    post('driver',endpoint,body,status=403)
    post('other',endpoint,body,status=404)
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    with db.tx() as c:
        before=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()
        c.execute("UPDATE users SET permission_denials='[\"tasks_resolve_delivery\"]' WHERE id='ops-manager'")
    post('manager',endpoint,body,status=403)
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[]' WHERE id='ops-manager'")
    first=post('manager',endpoint,body,key='TEST-resolution')
    assert post('manager',endpoint,body,key='TEST-resolution')==first
    with db.tx() as c:
        saved=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
        assert saved['completed_at']==task['delivered_at']==actual
        assert saved['payment_status']=='paid' and task['status']=='delivered'
        assert before==c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()
        events=c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='task.delivered'",(oid,)).fetchall()
        assert len(events)==1 and events[0]['created_at']>=actual
        assert events[0]['details']['administrative_resolution'] is True
        performance.enrich_orders(c,[saved])
        assert saved['timings']['delivery']['seconds']==(actual-task['picked_up_at']).total_seconds()
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',endpoint,body,key='TEST-resolution',status=404)

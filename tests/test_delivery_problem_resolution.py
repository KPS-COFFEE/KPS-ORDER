"""Manager decisions preserve actual pickup evidence and do not fabricate delivery."""
from uuid import uuid4
import pytest
from app import db
from tests.test_operations import ops

pytestmark=pytest.mark.postgres


def test_problem_requires_manager_and_resumes_evidenced_pickup_once(ops):
    actor,post,order,shifts=ops;_,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    path=f'/api/tasks/{tid}/transition'
    post('driver',path,{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',path,{'status':'picked_up'})
    with db.tx() as c:pickup=c.execute('SELECT picked_up_at FROM driver_tasks WHERE id=%s',(tid,)).fetchone()['picked_up_at']
    post('driver',path,{'status':'problem','contact_method':'call','reason':'Synthetic customer unavailable'})
    post('driver',path,{'status':'picked_up','reason':'Driver self-resolution forbidden'},status=403)
    post('manager',path,{'status':'accepted','reason':'Cannot rewind an evidenced pickup'},status=409)
    post('manager',path,{'status':'picked_up'},status=422)
    assert actor('other').get('/api/tasks?order_id='+oid).json()['tasks']==[]
    assert len(actor('manager').get('/api/tasks?order_id='+oid).json()['tasks'])==1
    body={'status':'picked_up','reason':'Manager contacted customer and approved continuing'}
    headers={'Idempotency-Key':'manager-problem-decision'}
    first=actor('manager').post(path,json=body,headers=headers)
    assert first.status_code==200,first.text
    assert actor('manager').post(path,json=body,headers=headers).json()==first.json()
    with db.tx() as c:
        task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
        saved=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert task['picked_up_at']==pickup and task['status']=='picked_up'
        assert saved['delivery_status']=='picked_up' and saved['completed_at'] is None
        events=c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='task.picked_up' AND actor_id='ops-manager'",(oid,)).fetchall()
        assert len(events)==1 and events[0]['details']['reason']==body['reason']


def test_manager_cannot_invent_pickup_while_resolving_problem(ops):
    actor,post,order,shifts=ops;_,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    path=f'/api/tasks/{tid}/transition'
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',path,{'status':'problem','reason':'Synthetic problem before actual pickup'})
    result=actor('manager').post(path,json={'status':'picked_up','reason':'No evidence'},headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==409 and result.json()['detail']['code']=='PROBLEM_PICKUP_EVIDENCE_REQUIRED'
    assert actor('manager').get('/api/operations/alerts').json()['problems'][0]['id']==oid


def test_conversation_cards_preview_and_customer_name_stay_scoped(ops):
    actor,post,order,_=ops;oid=order()
    cid=post('customer','/api/conversations',{'order_id':oid})['conversation']['id']
    post('customer',f'/api/conversations/{cid}/messages',{'text':'Synthetic latest message <script>not executable</script>'})
    rows=actor('manager').get('/api/conversations').json()['conversations']
    assert len(rows)==1 and rows[0]['id']==cid
    assert rows[0]['last_message']=='Synthetic latest message <script>not executable</script>'
    assert rows[0]['customer_name'] and rows[0]['branch_name']
    assert actor('other').get('/api/conversations').json()['conversations']==[]

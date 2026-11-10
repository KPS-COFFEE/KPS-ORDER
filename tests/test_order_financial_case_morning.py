"""Canceled history is retained within the recorded duty, never widened by branch."""
import pytest
from uuid import uuid4
from app import db
from test_operations import ops
from test_morning_manager_context_postgres import morning_manager,post


def documented_cancellation(client,oid,*,key=None):
    """Use the actual customer request and scoped morning-manager approval."""
    from app import order_requests
    with db.tx() as c:customer=c.execute("SELECT * FROM users WHERE id='ops-customer'").fetchone()
    source=order_requests.create(oid,order_requests.RequestBody(kind='cancel',note='Customer requested cancellation'),user=customer,idempotency_key=uuid4().hex)
    request=post(client,'/api/order-interventions/orders/'+oid,{'kind':'cancel','reason_code':'customer_cancel','customer_request_id':source['id'],'version':1})
    assert request.status_code==200,request.text
    path='/api/order-interventions/orders/'+oid+'/'+request.json()['id']+'/decision'
    body={'version':1,'action':'approve','reason_code':'customer_cancel','contact_result':'agreed'}
    result=post(client,path,body,key=key)
    return result,path,body

@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_morning_unassigned_cancellation_keeps_journey_and_replay_without_other_duty_history(morning_manager):
    client,ids,_=morning_manager
    oid=ids['morning'];path='/api/order-financial-cases/orders/'+oid
    body={'version':1,'reason':'Customer cancellation before assignment'}
    assert post(client,path+'/cancel',body).status_code==409
    result,decision_path,decision_body=documented_cancellation(client,oid,key='morning-case-cancel')
    assert result.status_code==200,result.text
    replay=post(client,decision_path,decision_body,key='morning-case-cancel')
    assert replay.status_code==200,replay.text
    assert replay.json()==result.json()
    assert client.get('/api/order-interventions/orders/'+oid).status_code==200
    assert client.get(path+'/journey').status_code==200
    assert client.get('/api/orders/'+oid).status_code==200
    assert post(client,'/api/orders/'+oid+'/assign',{'driver_shift_id':'ctx-driver'}).status_code in (403,404,409)
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status='cancelled' WHERE id=%s",(ids['bound_other'],))
        c.execute("UPDATE driver_tasks SET status='cancelled' WHERE order_id=%s",(ids['bound_other'],))
    assert client.get('/api/order-interventions/orders/'+ids['bound_other']).status_code==404
    assert post(client,decision_path.replace(oid,ids['bound_other']),decision_body,key='other-duty-replay').status_code==404
    assert client.get('/api/order-financial-cases/orders/'+ids['bound_other']+'/journey').status_code==404
    assert client.get('/api/orders/'+ids['bound_other']).status_code==404

@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_morning_cancellation_requires_explicit_manager_incentive_review(morning_manager):
    client,ids,_=morning_manager;oid=ids['morning']
    with db.tx() as c:
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('ctx-cancel-review-task',%s,'ops-driver','ctx-driver','assigned','ops-manager')",(oid,))
    result,_,_=documented_cancellation(client,oid)
    assert result.status_code==200,result.text
    journey=client.get('/api/order-financial-cases/orders/'+oid+'/journey')
    assert journey.status_code==200,journey.text
    assert journey.json()['operational_followups'][0]['type']=='driver_incentive_review'
    response=client.get('/api/driver-incentives/shifts/ctx-driver')
    assert response.status_code==200,response.text
    review=response.json()['entries'][0]
    assert review['state']=='pending_review' and review['responsibility'] is None
    decision={'task_id':review['task_id'],'incident_key':review['incident_key'],'version':review['decision_version'],'responsibility':'customer','reason':'Verified customer cancellation, no driver responsibility'}
    decided=post(client,'/api/driver-incentives/orders/'+oid+'/decision',decision)
    assert decided.status_code==200,decided.text
    assert post(client,'/api/driver-incentives/orders/'+ids['bound_other']+'/decision',decision).status_code==404

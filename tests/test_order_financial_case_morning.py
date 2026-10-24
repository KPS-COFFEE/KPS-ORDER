"""Canceled history is retained within the recorded duty, never widened by branch."""
import pytest
from app import db
from test_operations import ops
from test_morning_manager_context_postgres import morning_manager,post

@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_morning_unassigned_cancellation_keeps_journey_and_replay_without_other_duty_history(morning_manager):
    client,ids,_=morning_manager
    oid=ids['morning'];path='/api/order-financial-cases/orders/'+oid
    body={'version':1,'reason':'Customer cancellation before assignment'}
    result=post(client,path+'/cancel',body,key='morning-case-cancel')
    assert result.status_code==200,result.text
    replay=post(client,path+'/cancel',body,key='morning-case-cancel')
    assert replay.status_code==200,replay.text
    assert replay.json()==result.json()
    assert client.get(path+'/journey').status_code==200
    assert client.get('/api/orders/'+oid).status_code==200
    assert post(client,'/api/orders/'+oid+'/assign',{'driver_shift_id':'ctx-driver'}).status_code in (403,404,409)
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status='cancelled' WHERE id=%s",(ids['bound_other'],))
        c.execute("UPDATE driver_tasks SET status='cancelled' WHERE order_id=%s",(ids['bound_other'],))
    assert client.get('/api/order-financial-cases/orders/'+ids['bound_other']+'/journey').status_code==404
    assert client.get('/api/orders/'+ids['bound_other']).status_code==404

@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_morning_cancellation_requires_explicit_manager_incentive_review(morning_manager):
    client,ids,_=morning_manager;oid=ids['morning']
    with db.tx() as c:
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('ctx-cancel-review-task',%s,'ops-driver','ctx-driver','assigned','ops-manager')",(oid,))
    result=post(client,'/api/order-financial-cases/orders/'+oid+'/cancel',{'version':1,'reason':'Customer requested cancellation'})
    assert result.status_code==200,result.text
    assert result.json()['operational_followups'][0]['type']=='driver_incentive_review'
    response=client.get('/api/driver-incentives/shifts/ctx-driver')
    assert response.status_code==200,response.text
    review=response.json()['entries'][0]
    assert review['state']=='pending_review' and review['responsibility'] is None
    decision={'task_id':review['task_id'],'incident_key':review['incident_key'],'version':review['decision_version'],'responsibility':'customer','reason':'Verified customer cancellation, no driver responsibility'}
    decided=post(client,'/api/driver-incentives/orders/'+oid+'/decision',decision)
    assert decided.status_code==200,decided.text
    assert post(client,'/api/driver-incentives/orders/'+ids['bound_other']+'/decision',decision).status_code==404

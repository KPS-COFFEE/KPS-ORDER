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

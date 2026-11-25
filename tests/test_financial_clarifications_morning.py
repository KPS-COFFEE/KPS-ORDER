"""Current approved morning duty parity without standing permissions or wider scope."""
from uuid import uuid4
import pytest
from app import db, auth
from test_operations import ops
from test_core import _authenticate
from test_morning_manager_context_postgres import morning_manager, post, B2, B3

pytestmark=pytest.mark.postgres


def accountant(client):
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ctx-clarifier','Actual accountant','admin','kps',%s,'[\"accounting_review\"]') ON CONFLICT(id) DO NOTHING",(db.json([B2,B3]),))
    login(client,'ctx-clarifier')


def login(client,uid,morning=False):
    with db.tx() as c:person=c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
    _authenticate(client,person)
    client.headers.pop('X-Delivery-Period',None)
    if morning:client.headers['X-Delivery-Period']='morning'


def seed_case(oid):
    with db.tx() as c:
        db.event(c,oid,'ctx-clarifier','financial_case.opened',{'kind':'collection_shortfall','reason':'Fixture: actual receipt evidence requires investigation'})
        return c.execute("SELECT id FROM order_events WHERE order_id=%s AND event_type='financial_case.opened' ORDER BY created_at DESC,id DESC LIMIT 1",(oid,)).fetchone()['id']


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_accountant_can_assign_current_approved_morning_manager_then_accept_and_respond(morning_manager):
    client,ids,before=morning_manager;oid=ids['morning'];accountant(client);seed_case(oid)
    url='/api/financial-clarifications/orders/'+oid
    context=client.get(url);assert context.status_code==200,context.text
    assert [x['id'] for x in context.json()['assignees']]==['ops-manager']
    payload={'version':1,'assignee_id':'ops-manager','question':'Confirm the actual customer payment and provide the receipt evidence'}
    created=post(client,url,payload);assert created.status_code==200,created.text
    cid=created.json()['clarification']['id'];path='/api/financial-clarifications/'+cid+'/decision'
    login(client,'ops-manager',morning=True)
    response=client.get(url);assert response.status_code==200,response.text
    assert response.json()['clarifications'][0]['actions']==['accept','decline']
    accepted=post(client,path,{'expected_version':1,'action':'accept','note':'I accept checking the actual evidence'})
    assert accepted.status_code==200,accepted.text
    reply={'expected_version':2,'action':'respond','note':'No funds were received; the difference must remain with accounting','evidence':['Actual branch statement 001']}
    key=uuid4().hex;response=post(client,path,reply,key=key);assert response.status_code==200,response.text
    assert response.json()['clarification']['responsible_user_id']=='ctx-clarifier'
    with db.tx() as c:
        after=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        assert after['permission_denials']==before['permission_denials'] and after['branch_ids']==before['branch_ids']
        assert not auth.has_permission(after,'support_manage')
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    assert post(client,path,reply,key=key).status_code==403
    login(client,'ctx-clarifier')
    context=client.get(url);assert context.status_code==200,context.text
    assert 'ops-manager' not in [x['id'] for x in context.json()['assignees']]
    assert post(client,url,payload).status_code==422
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[\"ops-manager\"]') WHERE id='kps'")
        c.execute("UPDATE shifts SET status='closed' WHERE id='ctx-duty'")
    assert post(client,url,payload).status_code==422


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_morning_clarification_resource_checks_and_zero_receipt_stay_in_current_duty(morning_manager):
    client,ids,before=morning_manager;accountant(client)
    hidden=[]
    for label in ('evening','branch3','bound_other','foreign'):
        oid=ids[label];case_id=seed_case(oid);identifier='ctx-hidden-clarification-'+label
        with db.tx() as c:
            c.execute("INSERT INTO financial_case_clarifications(id,order_id,case_event_id,requested_by,assignee_id,question,source_order_version,status) VALUES(%s,%s,%s,'ctx-clarifier','ops-manager','Historical assignment must not widen current morning authority',1,'requested')",(identifier,oid,case_id))
        hidden.append((oid,identifier))
    login(client,'ops-manager',morning=True)
    for oid,identifier in hidden:
        assert client.get('/api/financial-clarifications/orders/'+oid).status_code==404
        denied=post(client,'/api/financial-clarifications/'+identifier+'/decision',{'expected_version':1,'action':'accept','note':'Attempt must not escape current morning duty'})
        assert denied.status_code==404,denied.text
    oid=ids['morning']
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='handed_over',delivery_status='picked_up' WHERE id=%s",(oid,))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES('ctx-nonreceipt-task',%s,'ops-driver','ctx-driver','picked_up','ops-manager',now())",(oid,))
    path='/api/order-financial-cases/orders/'+oid+'/nonreceipt'
    body={'version':1,'reason':'Verified that no amount was received','evidence':'Customer and branch actual statements recorded','confirmed_zero_received':True}
    key=uuid4().hex;response=post(client,path,body,key=key);assert response.status_code==200,response.text
    assert response.json()['actual_collected_halalas']==0
    assert post(client,path,body,key=key).json()==response.json()
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        after=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        assert after['permission_denials']==before['permission_denials'] and after['branch_ids']==before['branch_ids']
        assert c.execute("SELECT count(*) n FROM financial_case_clarification_events WHERE clarification_id LIKE 'ctx-hidden-clarification-%%'").fetchone()['n']==0

"""Owner-authorized manager parity retains period, duty and receipt boundaries."""
from uuid import uuid4
import pytest
from app import db, employee_permissions
from test_morning_manager_context_postgres import morning_manager, post, B2

pytestmark = [pytest.mark.postgres, pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)]


def submitted_driver():
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='submitted',submission=%s WHERE id='ctx-driver'",(db.json({
            'counted_halalas':0,'handover_halalas':0,'retained_halalas':0,'expected_halalas':0,
            'difference_halalas':0,'terminal_total_halalas':0}),))


@pytest.mark.parametrize('paused',[False,True])
def test_manager_receives_and_closes_submitted_driver_once_without_otp(morning_manager,paused):
    client,_,before=morning_manager;submitted_driver()
    if paused:
        with db.tx() as c:c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,enabled}','false') WHERE id='kps'")
    path='/api/shifts/ctx-driver/driver-close';body={'received_halalas':0,'confirmed':True};key=uuid4().hex
    denied=post(client,path,body|{'confirmed':False})
    assert denied.status_code==422,denied.text
    with db.tx() as c:
        assert c.execute("SELECT status FROM shifts WHERE id='ctx-driver'").fetchone()['status']=='submitted'
        assert c.execute("SELECT count(*) n FROM custody_transfers WHERE from_shift_id='ctx-driver'").fetchone()['n']==0
    result=post(client,path,body,key);assert result.status_code==200,result.text
    replay=post(client,path,body,key);assert replay.status_code==200 and replay.json()==result.json(),replay.text
    with db.tx() as c:
        assert c.execute("SELECT status,approved_by FROM shifts WHERE id='ctx-driver'").fetchone()=={'status':'closed','approved_by':'ops-manager'}
        assert c.execute("SELECT count(*) n FROM custody_transfers WHERE from_shift_id='ctx-driver' AND status='confirmed'").fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM closure_reports WHERE shift_id='ctx-driver'").fetchone()['n']==1
        assert c.execute("SELECT permission_denials FROM users WHERE id='ops-manager'").fetchone()['permission_denials']==before['permission_denials']
    report=client.get('/api/reports/'+result.json()['report_id']);assert report.status_code==200,report.text


@pytest.mark.parametrize('sid',['ctx-other-driver','ctx-evening-driver','ctx-other-branch'])
def test_driver_close_cannot_cross_duty_period_or_branch(morning_manager,sid):
    client,_,_=morning_manager
    result=post(client,f'/api/shifts/{sid}/driver-close',{'received_halalas':0,'confirmed':True})
    assert result.status_code in (403,404),result.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==0


def test_order_chat_and_alerts_are_available_only_for_own_morning_scope(morning_manager):
    client,ids,_=morning_manager
    created=post(client,'/api/conversations',{'order_id':ids['morning'],'category':'help'})
    assert created.status_code==200,created.text
    cid=created.json()['conversation']['id']
    sent=post(client,f'/api/conversations/{cid}/messages',{'text':'Synthetic morning reply'})
    assert sent.status_code==200,sent.text
    with db.tx() as c:
        c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES('evening-chat',%s,'kps',%s,'ops-customer','delivery','delivery')",(ids['evening'],B2))
    inbox=client.get('/api/conversations');assert inbox.status_code==200,inbox.text
    assert {r['id'] for r in inbox.json()['conversations']}=={cid}
    assert client.get('/api/conversations/evening-chat/messages').status_code==404
    assert post(client,'/api/conversations/evening-chat/messages',{'text':'must not send'}).status_code==404


def test_driver_shift_detail_and_financial_pages_retain_base_permissions(morning_manager):
    client,_,before=morning_manager
    session=client.get('/api/session').json()['user']
    assert set(session['effective_permissions'])==employee_permissions.eligible('delivery_manager')|{'orders_prepare'}
    shifts=client.get('/api/shifts');assert shifts.status_code==200,shifts.text
    assert {r['id'] for r in shifts.json()['shifts']}=={'ctx-duty','ctx-driver'}
    assert 'authorized_receivers' in shifts.json()
    for path in ('/api/shifts/ctx-driver','/api/custody','/api/reports','/api/operations/people','/api/driver-closure'):
        result=client.get(path);assert result.status_code==200,(path,result.text)
    del client.headers['X-Delivery-Period']
    assert client.get('/api/shifts/ctx-driver').status_code==403
    assert client.get('/api/session').json()['user']['branch_ids']==before['branch_ids']


def test_revocation_prevents_saved_driver_closure_replay(morning_manager):
    client,_,_=morning_manager;submitted_driver();key=uuid4().hex
    path='/api/shifts/ctx-driver/driver-close';body={'received_halalas':0,'confirmed':True}
    result=post(client,path,body,key);assert result.status_code==200,result.text
    with db.tx() as c:c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    assert post(client,path,body,key).status_code==403

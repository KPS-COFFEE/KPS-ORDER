"""Real transaction, minimal capability and duplicate protection contracts."""
from uuid import uuid4
import pytest
from app import db, employee_permissions
from test_operations import ops

pytestmark = pytest.mark.postgres


def configure(order, *, period='morning', branch='kps-branch-2', devices=1, approved=True):
    oid = order('delivery')
    with db.tx() as c:
        policy = {'enabled':True,'version':1,'execution_branch_ids':[branch],
                  'preparer_user_ids':['ops-branch'],'manager_user_ids':['ops-manager']}
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'morning_delivery':policy}),))
        denied = [code for code,ar,en,group,roles in employee_permissions.DEFINITIONS
                  if 'branch_manager' in roles and code not in {'orders_view','orders_prepare'}]
        c.execute("UPDATE users SET branch_ids=%s,permission_denials=%s WHERE id='ops-branch'",
                  (db.json([branch]),db.json(denied)))
        c.execute("UPDATE orders SET branch_id=%s,preparation_status='new',snapshot=snapshot||%s WHERE id=%s",
                  (branch,db.json({'delivery_period':period}),oid))
        for index in range(devices):
            c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) "
                      "VALUES(%s,'kps',%s,'isolated printer',%s,'ops-owner',CASE WHEN %s THEN now() END)",
                      ('test-printer-'+uuid4().hex,branch,uuid4().hex,approved))
    return oid


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_minimal_preparer_acceptance_prints_once_and_can_mark_ready(ops):
    actor,post,order,_ = ops
    oid = configure(order)
    body = {'status':'accepted','version':1}
    key = uuid4().hex
    result = post('branch',f'/api/orders/{oid}/preparation',body,key=key)
    assert result['printing']['status'] == 'queued'
    assert post('branch',f'/api/orders/{oid}/preparation',body,key=key) == result
    post('branch',f'/api/orders/{oid}/preparation',body,status=409)
    with db.tx() as c:
        jobs = c.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchall()
        assert len(jobs) == 1
        assert jobs[0]['branch_id'] == 'kps-branch-2' and jobs[0]['kind'] == 'order'
        assert jobs[0]['status'] == 'queued' and jobs[0]['payload']['order_revision'] == 0
    post('branch',f'/api/orders/{oid}/preparation',{'status':'ready','version':2})
    assert actor('branch').get('/api/orders/'+oid).status_code == 200
    assert actor('branch').get('/api/identity/staff').status_code == 403
    assert actor('branch').post('/api/printing/orders/'+oid,json={'device_id':jobs[0]['device_id']},
                                headers={'Idempotency-Key':uuid4().hex}).status_code == 403


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('devices,approved',[(0,True),(1,False),(2,True)])
def test_printer_readiness_failure_rolls_back_acceptance(ops,devices,approved):
    _,post,order,_ = ops
    oid = configure(order,devices=devices,approved=approved)
    failed = post('branch',f'/api/orders/{oid}/preparation',{'status':'accepted','version':1},status=409)
    assert failed['detail']['code'] == 'MORNING_PRINTER_NOT_READY'
    with db.tx() as c:
        row = c.execute('SELECT preparation_status,version FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row == {'preparation_status':'new','version':1}
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()['n'] == 0
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'",(oid,)).fetchone()['n'] == 0


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('period,branch',[('evening','kps-branch-2'),('morning','kps-branch-1')])
def test_existing_other_branch_and_evening_workflow_is_unchanged(ops,period,branch):
    _,post,order,_ = ops
    oid = configure(order,period=period,branch=branch,devices=0)
    accepted = post('branch',f'/api/orders/{oid}/preparation',{'status':'accepted','version':1})
    assert 'printing' not in accepted
    post('branch',f'/api/orders/{oid}/preparation',{'status':'ready','version':2},status=409)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()['n'] == 0

"""Real transaction, minimal capability and duplicate protection contracts."""
from uuid import uuid4
import pytest
from app import auth, db, employee_permissions
from test_core import _authenticate
from test_operations import ops

pytestmark = pytest.mark.postgres


def configure(order, *, period='morning', branch='kps-branch-2', devices=1, approved=True):
    oid = 'morning-preparer-' + uuid4().hex
    with db.tx() as c:
        policy = {'enabled':True,'version':1,'execution_branch_ids':[branch],
                  'preparer_user_ids':['ops-branch'],'manager_user_ids':['ops-manager']}
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'morning_delivery':policy}),))
        denied = [code for code,ar,en,group,roles in employee_permissions.DEFINITIONS
                  if 'branch_manager' in roles and code not in {'orders_view','orders_prepare'}]
        c.execute("UPDATE users SET branch_ids=%s,permission_denials=%s WHERE id='ops-branch'",
                  (db.json([branch]),db.json(denied)))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'kps',%s,'ops-customer','delivery','new','pending','cash',1000,0,1000,%s,'{}')",
                  (oid,branch,db.json({'items':[],'delivery_period':period})))
        for index in range(devices):
            c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) "
                      "VALUES(%s,'kps',%s,'isolated printer',%s,'ops-owner',CASE WHEN %s THEN now() END)",
                      ('test-printer-'+uuid4().hex,branch,uuid4().hex,approved))
    return oid


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_minimal_preparer_acceptance_prints_once_and_can_mark_ready(ops):
    actor,post,order,_ = ops
    oid = configure(order)
    client = actor('branch')
    client.app.dependency_overrides.pop(auth.current_user, None)
    with db.tx() as c:
        employee = c.execute("SELECT * FROM users WHERE id='ops-branch'").fetchone()
    assert employee_permissions.effective(employee) == {'orders_view','orders_prepare'}
    _authenticate(client, employee)
    def send(body, key=None, status=200):
        response = client.post(f'/api/orders/{oid}/preparation',json=body,
                               headers={'Idempotency-Key':key or uuid4().hex})
        assert response.status_code == status, response.text
        return response.json()
    body = {'status':'accepted','version':1}
    key = uuid4().hex
    result = send(body,key=key)
    assert result['printing']['status'] == 'queued'
    assert send(body,key=key) == result
    send(body,status=409)
    with db.tx() as c:
        jobs = c.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchall()
        assert len(jobs) == 1
        assert jobs[0]['branch_id'] == 'kps-branch-2' and jobs[0]['kind'] == 'order'
        assert jobs[0]['status'] == 'queued' and jobs[0]['payload']['order_revision'] == 0
    send({'status':'ready','version':2})
    assert client.get('/api/orders/'+oid).status_code == 200
    assert client.get('/api/identity/staff').status_code == 403
    assert client.post('/api/printing/orders/'+oid,json={'device_id':jobs[0]['device_id']},
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


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_acceptance_queues_one_receipt(ops,same_key):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    actor,_,order,_ = ops
    oid = configure(order)
    existing = actor('branch')
    existing.app.dependency_overrides.pop(auth.current_user, None)
    with db.tx() as c:
        employee = c.execute("SELECT * FROM users WHERE id='ops-branch'").fetchone()
    start = Barrier(2)
    shared_key = uuid4().hex
    def accept(index):
        client = TestClient(existing.app, base_url='http://testserver',headers={'Origin':'http://testserver'})
        try:
            _authenticate(client,employee)
            start.wait(timeout=15)
            response = client.post(f'/api/orders/{oid}/preparation',json={'status':'accepted','version':1},
                                   headers={'Idempotency-Key':shared_key if same_key else shared_key+str(index)})
            return response.status_code,response.json()
        finally:
            client.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(accept,range(2)))
    assert sorted(status for status,_ in results) == ([200,200] if same_key else [200,409]), results
    if same_key:
        assert results[0][1] == results[1][1]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()['n'] == 1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'",(oid,)).fetchone()['n'] == 1
        row = c.execute('SELECT preparation_status,version FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row == {'preparation_status':'accepted','version':2}

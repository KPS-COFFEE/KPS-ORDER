"""Real PostgreSQL assertions for repeated, concurrent and failed print delivery.

No external printer is used: submitted means acknowledged spool submission,
and uncertain output always needs an operator's explicit audited reprint.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import auth, branch_printing, db
from test_branch_printing import approve_test_device
from test_operations import ops

pytestmark = pytest.mark.postgres


def paired(ops):
    actor, post, _, _ = ops
    pair = post('owner', '/api/printing/devices',
                {'branch_id': 'ops-branch', 'label': 'Isolated print hardening'})
    response = actor('customer').post('/api/printing/bridge/pair', json={
        'pairing_code': pair['pairing_code'], 'printer_name': 'Synthetic isolated spooler'})
    assert response.status_code == 200, response.text
    token = response.json()['token']
    approve_test_device(actor, post, pair['id'], token)
    with db.tx() as conn:
        user = conn.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        device = conn.execute('SELECT * FROM branch_print_devices WHERE id=%s', (pair['id'],)).fetchone()
    return pair['id'], token, user, device


def together(count, call):
    barrier = Barrier(count)
    def run(index):
        barrier.wait(timeout=15)
        return call(index)
    with ThreadPoolExecutor(max_workers=count) as workers:
        return list(workers.map(run, range(count)))


def outcome(call):
    try:
        return call()
    except HTTPException as error:
        return {'error': error.detail['code'], 'status': error.status_code}


def test_initial_print_concurrent_different_event_keys_has_one_effect(ops):
    _, _, order, _ = ops
    device_id, _, user, _ = paired(ops)
    oid = order('delivery')
    body = branch_printing.JobBody(device_id=device_id, kind='both')
    results = together(8, lambda index: outcome(lambda: branch_printing.enqueue(
        oid, body, user=user, idem='print-distinct-event-' + str(index))))
    successful = [row for row in results if 'id' in row]
    assert len(successful) == 1
    assert len(successful[0]['ids']) == 2
    assert all(row.get('error') == 'PRINT_REPRINT_REASON' for row in results if 'error' in row)
    with db.tx() as conn:
        rows = conn.execute('SELECT id,kind,status FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchall()
        assert {row['kind'] for row in rows} == {'order', 'preparation'}
        assert len(rows) == 2 and all(row['status'] == 'queued' for row in rows)
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='printer.job.queued' "
                            "AND details->>'order_id'=%s", (oid,)).fetchone()['n'] == 2


def test_concurrent_request_and_claim_replay_have_one_durable_job(ops):
    _, _, order, _ = ops
    device_id, _, user, device = paired(ops)
    oid = order('delivery')
    body = branch_printing.JobBody(device_id=device_id)
    results = together(8, lambda index: branch_printing.enqueue(
        oid, body, user=user, idem='print-concurrent-stable-request'))
    assert all(row == results[0] for row in results)
    claims = together(8, lambda index: branch_printing.claim(device=device))
    claimed = [row['job'] for row in claims if row['job']]
    assert len(claimed) == 1 and claimed[0]['id'] == results[0]['id']
    with db.tx() as conn:
        jobs = conn.execute('SELECT status FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchall()
        assert jobs == [{'status': 'claimed'}]
        assert conn.execute("SELECT count(*) AS n FROM idempotency_keys WHERE operation='printer.enqueue' "
                            "AND key='print-concurrent-stable-request'").fetchone()['n'] == 1


def test_lost_claim_or_ack_response_never_dispatches_again(ops):
    actor, post, order, _ = ops
    device_id, token, _, device = paired(ops)
    oid = order('delivery')
    job = post('manager', '/api/printing/orders/' + oid, {'device_id': device_id})
    headers = {'Authorization': 'Bearer ' + token}
    # Simulate the claim response being lost: no automatic queue retry is safe.
    assert branch_printing.claim(device=device)['job']['id'] == job['id']
    assert branch_printing.claim(device=device)['job'] is None
    with db.tx() as conn:
        assert conn.execute('SELECT status FROM branch_print_jobs WHERE id=%s', (job['id'],)).fetchone()['status'] == 'claimed'
    # A durable bridge journal can retry the ACK, without submitting paper again.
    body = branch_printing.AckBody(status='submitted')
    first = branch_printing.acknowledge(job['id'], body, device=device)
    with db.tx() as conn:
        finished = conn.execute('SELECT finished_at FROM branch_print_jobs WHERE id=%s', (job['id'],)).fetchone()['finished_at']
    assert all(row == first for row in together(8, lambda index: branch_printing.acknowledge(
        job['id'], body, device=device)))
    assert actor('customer').post('/api/printing/bridge/next', json={}, headers=headers).json()['job'] is None
    with db.tx() as conn:
        assert conn.execute('SELECT finished_at FROM branch_print_jobs WHERE id=%s', (job['id'],)).fetchone()['finished_at'] == finished
        assert conn.execute('SELECT count(*) AS n FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchone()['n'] == 1


def test_uncertain_failure_requires_authorized_reason_and_replays_reprint(ops):
    actor, post, order, _ = ops
    device_id, token, _, device = paired(ops)
    oid = order('delivery')
    path = '/api/printing/orders/' + oid
    original = post('manager', path, {'device_id': device_id})
    assert branch_printing.claim(device=device)['job']['id'] == original['id']
    branch_printing.acknowledge(original['id'], branch_printing.AckBody(status='uncertain'), device=device)
    assert branch_printing.claim(device=device)['job'] is None
    post('manager', path, {'device_id': device_id}, status=409)
    post('manager', path, {'device_id': device_id, 'reprint_reason': '   '}, status=409)
    reason = 'Operator inspected the spooler; approved isolated recovery'
    body = {'device_id': device_id, 'reprint_reason': reason}
    post('driver', path, body, status=403)
    post('branch', path, body, status=403)
    post('other', path, body, status=404)
    replay = post('manager', path, body, key='print-failure-recovery-stable')
    assert post('manager', path, body, key='print-failure-recovery-stable') == replay
    claim = actor('customer').post('/api/printing/bridge/next', json={},
        headers={'Authorization': 'Bearer ' + token}).json()['job']
    assert claim['id'] == replay['id'] and claim['payload']['lines'][0]['text'] == 'REPRINT'
    with db.tx() as conn:
        rows = conn.execute('SELECT id,status,reprint_reason FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchall()
        assert len(rows) == 2
        assert next(row for row in rows if row['id'] == original['id'])['status'] == 'uncertain'
        assert next(row for row in rows if row['id'] == replay['id'])['reprint_reason'] == reason
        audit = conn.execute("SELECT actor_id,details FROM audit_events WHERE action='printer.job.queued' "
                             "AND entity_id=%s", (replay['id'],)).fetchone()
        assert audit['actor_id'] == 'ops-manager' and audit['details']['reprint_reason'] == reason


def test_other_branch_bridge_cannot_claim_or_ack_order_print(ops):
    actor, post, order, _ = ops
    device_id, _, _, device = paired(ops)
    with db.tx() as conn:
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) "
                     "VALUES('ops-second',2,'print-second-source','ops-brand','Print second branch','Test','{}')")
    pair = post('owner', '/api/printing/devices', {'branch_id': 'ops-second', 'label': 'Other destination'})
    other = actor('customer').post('/api/printing/bridge/pair', json={
        'pairing_code': pair['pairing_code'], 'printer_name': 'Synthetic other branch'}).json()
    approve_test_device(actor, post, pair['id'], other['token'], who='owner')
    oid = order('delivery')
    job = post('manager', '/api/printing/orders/' + oid, {'device_id': device_id})
    headers = {'Authorization': 'Bearer ' + other['token']}
    assert actor('customer').post('/api/printing/bridge/next', json={}, headers=headers).json()['job'] is None
    assert branch_printing.claim(device=device)['job']['id'] == job['id']
    assert actor('customer').post('/api/printing/bridge/jobs/' + job['id'] + '/ack',
        json={'status': 'submitted'}, headers=headers).status_code == 404
    with db.tx() as conn:
        row = conn.execute('SELECT branch_id,device_id,status FROM branch_print_jobs WHERE id=%s', (job['id'],)).fetchone()
        assert row == {'branch_id': 'ops-branch', 'device_id': device_id, 'status': 'claimed'}


def test_khaliya_entire_order_prints_execution_branch_with_unchanged_total(client):
    from test_khaliya_routing import body, prepare
    with db.tx() as conn:
        prepare(conn)
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,phone) "
                     "VALUES('print-routing-customer','TEST','customer','kps','[]','+966500000001'),"
                     "('print-routing-manager','TEST','delivery_manager','kps',%s,NULL),"
                     "('print-routing-owner','TEST','owner','kps','[]',NULL)",
                     (db.json(['kps-branch-1', 'kps-branch-2']),))
        conn.execute("INSERT INTO shifts(id,user_id,brand_id,kind,branch_ids,status) "
                     "VALUES('print-routing-duty','print-routing-manager','kps','manager',%s,'open')",
                     (db.json(['kps-branch-1', 'kps-branch-2']),))
    def actor(uid):
        with db.tx() as conn:
            user = conn.execute('SELECT * FROM users WHERE id=%s', (uid,)).fetchone()
        client.app.dependency_overrides[auth.current_user] = lambda: user
        return client
    response = actor('print-routing-customer').post('/api/orders', json=body(),
        headers={'Idempotency-Key': 'khaliya-print-order-stable'})
    assert response.status_code == 200, response.text
    public = response.json()['order']
    oid = public['id']
    actor('print-routing-owner')
    pair = client.post('/api/printing/devices', json={
        'branch_id': 'kps-branch-2', 'label': 'Khaliya fulfillment'}).json()
    response = client.post('/api/printing/bridge/pair', json={
        'pairing_code': pair['pairing_code'], 'printer_name': 'Synthetic Khaliya spooler'})
    assert response.status_code == 200, response.text
    headers = {'Authorization': 'Bearer ' + response.json()['token']}
    test = client.post('/api/printing/devices/' + pair['id'] + '/test', json={},
        headers={'Idempotency-Key': uuid4().hex}).json()
    assert client.post('/api/printing/bridge/next', json={}, headers=headers).json()['job']['id'] == test['id']
    assert client.post('/api/printing/bridge/jobs/' + test['id'] + '/ack', json={'status': 'submitted'}, headers=headers).status_code == 200
    assert client.post('/api/printing/devices/' + pair['id'] + '/approve',
        json={'job_id': test['id'], 'paper_confirmed': True}).status_code == 200
    actor('print-routing-manager')
    response = client.post('/api/printing/orders/' + oid, json={
        'device_id': pair['id'], 'kind': 'both', 'language': 'ar'},
        headers={'Idempotency-Key': 'khaliya-fulfillment-both-print'})
    assert response.status_code == 200, response.text
    ids = response.json()['ids']
    with db.tx() as conn:
        stored = conn.execute('SELECT branch_id,total_halalas,snapshot FROM orders WHERE id=%s', (oid,)).fetchone()
        target = conn.execute("SELECT name FROM branches WHERE id='kps-branch-2'").fetchone()['name']
        jobs = conn.execute('SELECT branch_id,device_id,payload FROM branch_print_jobs WHERE order_id=%s ORDER BY created_at', (oid,)).fetchall()
        assert stored['branch_id'] == 'kps-branch-2' and public['branch_id'] == 'kps-branch-1'
        assert stored['total_halalas'] == public['total_halalas']
        assert len(jobs) == 2 and len(stored['snapshot']['items']) == 2
        for job in jobs:
            assert job['branch_id'] == 'kps-branch-2' and job['device_id'] == pair['id']
            lines = job['payload']['lines']
            assert any(line['text'] == target for line in lines)
            assert all(any(line.get('role') == 'item' and line['text'] == item['name'] for line in lines)
                       for item in stored['snapshot']['items'])
    for identifier in ids:
        assert client.post('/api/printing/bridge/next', json={}, headers=headers).json()['job']['id'] == identifier

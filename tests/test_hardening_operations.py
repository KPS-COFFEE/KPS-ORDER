"""Financial race/reconnect checks against actual, isolated PostgreSQL rows.

Concurrent controller calls keep a separate authenticated principal per worker;
they do not race a shared FastAPI dependency override in the browser fixture.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from app import db, operations as operations
from tests.test_operations import ops, delivered


def request(key):
    return Request({'type': 'http', 'headers': [(b'idempotency-key', key.encode())]})


def principal(name):
    with db.tx() as conn:
        return conn.execute('SELECT * FROM users WHERE id=%s', ('ops-' + name,)).fetchone()


def outcome(call):
    try:
        return 200, call()
    except HTTPException as exc:
        return exc.status_code, exc.detail


def race(calls):
    gate = Barrier(len(calls))
    def run(call):
        gate.wait(timeout=15)
        return outcome(call)
    with ThreadPoolExecutor(max_workers=len(calls)) as workers:
        return list(workers.map(run, calls))


def pickup(post, order, driver):
    oid = order()
    tid = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': driver})['task']['id']
    post('driver', f'/api/tasks/{tid}/transition', {'status': 'accepted'})
    with db.tx() as conn:
        conn.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s", (oid,))
    post('driver', f'/api/tasks/{tid}/transition', {'status': 'picked_up'})
    return oid, tid


@pytest.mark.parametrize('model,payload', [
    (operations.Refund, {'method': 'cash', 'amount_halalas': 100, 'reason': ' \t '}),
    (operations.TaskHandover, {'driver_shift_id': 'driver', 'reason': '\n '}),
    (operations.Custody, {'from_shift_id': 'shift', 'amount_halalas': 0, 'reference': ' '}),
    (operations.ClosureHandover, {'recipient_user_id': 'manager', 'reference': ' '}),
    (operations.AuthorizedCash, {'kind': 'expense', 'amount_halalas': -1, 'reason': ' ', 'reference': 'ref'}),
    (operations.AuthorizedCash, {'kind': 'expense', 'amount_halalas': -1, 'reason': 'actual reason', 'reference': ' '}),
    (operations.ManagerHandover, {'target_manager_shift_id': 'shift', 'reason': ' '}),
    (operations.Refund, {'method': 'cash', 'amount_halalas': 100, 'reason': 'invalid\x00reason'}),
    (operations.Refund, {'method': 'cash', 'amount_halalas': 100, 'reason': 'actual reason', 'line_quantities': {'bad\x00key': 1}}),
    (operations.ShiftOpen, {'kind': 'driver', 'branch_ids': ['bad\x00branch']}),
    (operations.ShiftOpen, {'kind': 'manager', 'opening_halalas': 2**63}),
    (operations.ClosureSubmit, {'counted_halalas': 2**63, 'handover_halalas': 0}),
    (operations.Collection, {'method': 'cash', 'amount_halalas': 100, 'tendered_halalas': 2**63}),
    (operations.AuthorizedCash, {'kind': 'expense', 'amount_halalas': -(2**63)-1, 'reason': 'actual reason', 'reference': 'ref'}),
])
def test_invalid_operational_audit_text_and_integer_overflow_fail_at_boundary(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.postgres
def test_two_driver_assignment_has_one_database_task_and_one_dispatch_event(ops):
    actor, post, order, shifts = ops
    manager, first = shifts()
    second = post('driver2', '/api/shifts', {'kind': 'driver', 'manager_shift_id': manager})['shift']['id']
    oid, user = order(), principal('manager')
    results = race([
        lambda: operations.assign(oid, operations.Assignment(driver_shift_id=first), request('race-driver-first'), user),
        lambda: operations.assign(oid, operations.Assignment(driver_shift_id=second), request('race-driver-second'), user),
    ])
    assert sorted(code for code, result in results) == [200, 409], results
    with db.tx() as conn:
        tasks = conn.execute('SELECT * FROM driver_tasks WHERE order_id=%s', (oid,)).fetchall()
        assert len(tasks) == 1 and tasks[0]['shift_id'] in (first, second)
        assert conn.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='task.assigned'", (oid,)).fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action='task.assign'", (oid,)).fetchone()['n'] == 1


@pytest.mark.postgres
def test_collection_concurrent_retry_has_one_payment_and_one_cash_effect(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid, tid = pickup(post, order, driver)
    user = principal('driver')
    body = operations.Collection(method='cash', amount_halalas=1000, tendered_halalas=2000)
    results = race([lambda: operations.collect(oid, body, request('race-collect-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in results), results
    assert len({result['collection_id'] for code, result in results}) == 1
    assert outcome(lambda: operations.collect(oid, body, request('race-collect-new-key'), user))[0] == 409
    modified = body.model_copy(update={'tendered_halalas': 3000})
    assert outcome(lambda: operations.collect(oid, modified, request('race-collect-reconnect'), user))[0] == 409
    assert outcome(lambda: operations.collect(oid, body, request('race-collect-outsider'), principal('driver2')))[0] == 404
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n,sum(amount_halalas) amount,sum(change_halalas) change FROM collections WHERE order_id=%s', (oid,)).fetchone() == {'n': 1, 'amount': 1000, 'change': 1000}
        assert conn.execute("SELECT count(*) n,sum(amount_halalas) amount FROM custody_events WHERE shift_id=%s AND kind='collection'", (driver,)).fetchone() == {'n': 1, 'amount': 1000}
        assert conn.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'paid'


@pytest.mark.postgres
def test_competing_full_refunds_cannot_overdraw_collection_or_duplicate_cash(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = delivered(post, order, driver)
    user = principal('manager')
    body = operations.Refund(method='cash', amount_halalas=1000, reason='Verified full customer refund')
    calls = [lambda key=key: operations.refund(oid, body, request(key), user) for key in ('race-refund-first', 'race-refund-second')]
    results = race(calls)
    assert sorted(code for code, result in results) == [200, 422], results
    successful = next(i for i, value in enumerate(results) if value[0] == 200)
    assert outcome(calls[successful]) == results[successful]
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n,sum(amount_halalas) amount FROM refunds WHERE order_id=%s', (oid,)).fetchone() == {'n': 1, 'amount': 1000}
        assert conn.execute("SELECT count(*) n,sum(amount_halalas) amount FROM custody_events WHERE shift_id=%s AND kind='refund'", (driver,)).fetchone() == {'n': 1, 'amount': -1000}
        assert conn.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'refunded'
        assert operations.balance(conn, {'id': driver, 'opening_halalas': 0}) == 0


@pytest.mark.postgres
def test_partial_refund_reconnect_replays_once_and_distinct_refund_remains_possible(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = delivered(post, order, driver)
    user = principal('manager')
    body = operations.Refund(method='cash', amount_halalas=100, reason='Verified missing item refund')
    results = race([lambda: operations.refund(oid, body, request('race-partial-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in results), results
    assert len({result['refund_id'] for code, result in results}) == 1
    distinct = operations.Refund(method='cash', amount_halalas=150, reason='Verified separate item refund')
    operations.refund(oid, distinct, request('race-partial-distinct'), user)
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n,sum(amount_halalas) amount FROM refunds WHERE order_id=%s', (oid,)).fetchone() == {'n': 2, 'amount': 250}
        assert operations.balance(conn, {'id': driver, 'opening_halalas': 0}) == 750


@pytest.mark.postgres
def test_delegated_closure_concurrent_replay_notifies_owner_once_and_is_immutable(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    delivered(post, order, driver)
    user = principal('manager')
    operations.request_close(driver, request('race-close-request'), user)
    invalid = operations.ClosureSubmit(terminal_total_halalas=0, counted_halalas=900, handover_halalas=800, retained_halalas=100)
    assert outcome(lambda: operations.submit_close(driver, invalid, request('race-close-no-reason'), user))[0] == 422
    body = operations.ClosureSubmit(terminal_total_halalas=0, counted_halalas=900, handover_halalas=800, retained_halalas=100,
        vehicle_inspection=__import__('app.shift_vehicle',fromlist=['Inspection']).Inspection(odometer_km=1025,fuel_percent=100,checks={k:'ok' for k in ('tyres','brakes','lights','oil','damage','cleanliness')}),
        exception_reason='Driver unavailable; actual count witnessed',
        handover=operations.ClosureHandover(recipient_user_id=user['id'], to_shift_id=manager, reference='BAG-WITNESSED'))
    results = race([lambda: operations.submit_close(driver, body, request('race-submit-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in results), results
    with db.tx() as conn:
        rows = conn.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s', (driver,)).fetchall()
        assert len(rows) == 1 and rows[0]['status'] == 'pending' and rows[0]['amount_halalas'] == 800
        tid = rows[0]['id']
    review = operations.Review(exception_reason='Actual shortage and retained float reviewed')
    assert outcome(lambda: operations.approve_close(driver, review, request('race-approve-no-receipt'), user))[0] == 409
    receipts = race([lambda: operations.confirm_custody(tid, request('race-receipt-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in receipts), receipts
    approved = race([lambda: operations.approve_close(driver, review, request('race-approve-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in approved), approved
    report_id = approved[0][1]['report_id']
    assert {result['report_id'] for code, result in approved} == {report_id}
    assert outcome(lambda: operations.approve_close(driver, review, request('race-approve-second-key'), user))[0] == 409
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s', (driver,)).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM report_notifications WHERE report_id=%s', (report_id,)).fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='shift.approve' AND entity_id=%s", (driver,)).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n,sum(amount_halalas) amount FROM retained_carryovers WHERE from_shift_id=%s', (driver,)).fetchone() == {'n': 1, 'amount': 100}
        assert conn.execute("SELECT count(*) n,sum(amount_halalas) amount FROM custody_events WHERE reference_id=%s AND kind='transfer_out'", (tid,)).fetchone() == {'n': 1, 'amount': -800}
        assert conn.execute("SELECT count(*) n,sum(amount_halalas) amount FROM custody_events WHERE reference_id=%s AND kind='transfer_in'", (tid,)).fetchone() == {'n': 1, 'amount': 800}
        assert conn.execute("SELECT count(*) n,sum(amount_halalas) amount FROM custody_events WHERE shift_id=%s AND kind='adjustment'", (driver,)).fetchone() == {'n': 1, 'amount': -100}
        shift = conn.execute('SELECT * FROM shifts WHERE id=%s', (driver,)).fetchone()
        assert shift['status'] == 'closed' and shift['vehicle_log']['end_exception']['recorded_by'] == user['id']
        snapshot = conn.execute('SELECT snapshot FROM closure_reports WHERE id=%s', (report_id,)).fetchone()['snapshot']
        assert snapshot['shift']['submission']['closure_exception']['delegated'] is True
    for statement, identifier in [
        ("UPDATE shifts SET status='open' WHERE id=%s", driver),
        ("UPDATE closure_reports SET snapshot='{}' WHERE id=%s", report_id),
        ("UPDATE custody_transfers SET amount_halalas=1 WHERE id=%s", tid),
    ]:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.tx() as conn:
                conn.execute(statement, (identifier,))


@pytest.mark.postgres
def test_competing_receipt_keys_post_one_debit_and_credit(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    delivered(post, order, driver)
    user = principal('driver')
    body = operations.Custody(from_shift_id=driver, to_shift_id=manager, amount_halalas=200, reference='BAG-REAL-01')
    requested = race([lambda: operations.custody_transfer(body, request('race-handover-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in requested), requested
    tids = {result['transfer']['id'] for code, result in requested}
    assert len(tids) == 1
    tid = tids.pop()
    receiver = principal('manager')
    receipts = race([lambda key=key: operations.confirm_custody(tid, request(key), receiver) for key in ('race-custody-first', 'race-custody-second')])
    assert sorted(code for code, result in receipts) == [200, 409], receipts
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s', (driver,)).fetchone()['n'] == 1
        rows = conn.execute('SELECT kind,amount_halalas FROM custody_events WHERE reference_id=%s ORDER BY kind', (tid,)).fetchall()
        assert rows == [{'kind': 'transfer_in', 'amount_halalas': 200}, {'kind': 'transfer_out', 'amount_halalas': -200}]


@pytest.mark.postgres
def test_failed_cash_posting_rolls_back_collection_and_idempotency_then_retries(ops, monkeypatch):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid, tid = pickup(post, order, driver)
    user = principal('driver')
    body = operations.Collection(method='cash', amount_halalas=1000, tendered_halalas=1000)
    original_event = operations.event
    def fail_after_collection(*args, **kwargs):
        raise RuntimeError('Synthetic failure after collection INSERT before order commit')
    monkeypatch.setattr(operations, 'event', fail_after_collection)
    with pytest.raises(RuntimeError, match='Synthetic failure'):
        operations.collect(oid, body, request('rollback-collect-reconnect'), user)
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM custody_events WHERE shift_id=%s', (driver,)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM idempotency_keys WHERE key=%s', ('rollback-collect-reconnect',)).fetchone()['n'] == 0
        assert conn.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'unpaid'
    monkeypatch.setattr(operations, 'event', original_event)
    result = operations.collect(oid, body, request('rollback-collect-reconnect'), user)
    assert operations.collect(oid, body, request('rollback-collect-reconnect'), user) == result
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 1


@pytest.mark.postgres
def test_task_handover_concurrent_acceptance_has_one_new_active_task(ops):
    actor, post, order, shifts = ops
    manager, first = shifts()
    second = post('driver2', '/api/shifts', {'kind': 'driver', 'manager_shift_id': manager})['shift']['id']
    oid = order()
    tid = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': first})['task']['id']
    post('manager', f'/api/tasks/{tid}/handover', {'driver_shift_id': second, 'reason': 'Verified route transfer'})
    target = principal('driver2')
    accepted = race([lambda: operations.accept_task_handover(tid, request('race-task-handover-reconnect'), target)] * 4)
    assert all(code == 200 for code, result in accepted), accepted
    assert len({result['task']['id'] for code, result in accepted}) == 1
    with db.tx() as conn:
        tasks = conn.execute('SELECT * FROM driver_tasks WHERE order_id=%s ORDER BY created_at,id', (oid,)).fetchall()
        assert len(tasks) == 2
        active = [task for task in tasks if task['status'] not in operations.TERMINAL]
        assert len(active) == 1 and active[0]['driver_id'] == 'ops-driver2'
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action='task.handover.accept'", (tid,)).fetchone()['n'] == 1
        assert conn.execute('SELECT delivery_status FROM orders WHERE id=%s', (oid,)).fetchone()['delivery_status'] == active[0]['status'] == 'accepted'
        assert conn.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='task.handover.accepted'", (oid,)).fetchone()['n'] == 1
    denied = outcome(lambda: operations.task_transition(active[0]['id'], operations.TaskTransition(status='problem', reason='Unauthorized'), request('race-wrong-driver'), principal('driver')))
    assert denied[0] == 403, denied


@pytest.mark.postgres
def test_violation_record_reconnect_has_one_case_and_no_automatic_cash_deduction(ops):
    from app.accounting import CaseBody, create_case
    from app import service_hours
    actor, post, order, shifts = ops
    manager, driver = shifts()
    user = principal('owner')
    body = CaseBody(branch_id='ops-branch', subject_id='ops-driver', category='traffic',
                    occurred_on=service_hours.now_riyadh().date(), amount_halalas=100,
                    note='Verified traffic violation pending responsibility review')
    results = race([lambda: create_case(body, request('race-violation-reconnect'), user)] * 4)
    assert all(code == 200 for code, result in results), results
    assert len({result['id'] for code, result in results}) == 1
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM accounting_cases WHERE subject_id='ops-driver'", ()).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM accounting_case_events', ()).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM custody_events', ()).fetchone()['n'] == 0


@pytest.mark.postgres
def test_cash_balance_overflow_rejects_atomically_before_confirmation(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    owner = principal('owner')
    receipt = operations.AuthorizedCash(kind='direct_receipt', amount_halalas=operations.MAX_HALALAS,
                                       reason='Boundary amount in isolated test', reference='BOUNDARY-01')
    operations.authorized_cash(manager, receipt, request('boundary-cash-first'), owner)
    extra = operations.AuthorizedCash(kind='direct_receipt', amount_halalas=1,
                                     reason='Overflow must roll back', reference='BOUNDARY-02')
    assert outcome(lambda: operations.authorized_cash(manager, extra, request('boundary-cash-overflow'), owner))[0] == 422
    delivered(post, order, driver)
    bag = post('driver', '/api/custody', {'from_shift_id': driver, 'to_shift_id': manager,
                                       'amount_halalas': 1000, 'reference': 'BOUNDARY-BAG'})['transfer']['id']
    assert outcome(lambda: operations.confirm_custody(bag, request('boundary-transfer-overflow'), principal('manager')))[0] == 422
    with db.tx() as conn:
        assert operations.balance(conn, {'id': manager, 'opening_halalas': 0}) == operations.MAX_HALALAS
        assert conn.execute('SELECT status FROM custody_transfers WHERE id=%s', (bag,)).fetchone()['status'] == 'pending'
        assert conn.execute('SELECT count(*) n FROM custody_events WHERE reference_id=%s', (bag,)).fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) n FROM idempotency_keys WHERE key IN ('boundary-cash-overflow','boundary-transfer-overflow')").fetchone()['n'] == 0

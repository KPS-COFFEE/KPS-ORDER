"""Focused queue contracts without a runtime or production database.

Import the production helpers directly; SQL effects and printer transport are
controlled test doubles. PostgreSQL transactional integration remains a CI gate.
"""
from types import SimpleNamespace
import pytest


class Failure(Exception):
    pass


class Connection:
    def __init__(self, devices=None, previous=None, branch_no=2):
        self.devices = devices if devices is not None else [{'id': 'printer-2'}]
        self.previous = previous
        self.branch = {'id': 'branch-2', 'branch_no': branch_no}
        self.statements = []
        self.inserted = []

    def execute(self, sql, params):
        self.statements.append((sql, params))
        if sql.startswith('INSERT'):
            self.inserted.append(params)
            self.previous = {'id': params[0]}
        return SimpleNamespace(fetchone=lambda: self.branch if 'FROM branches ' in sql else self.previous,
                               fetchall=lambda: self.devices)


@pytest.fixture
def helpers(monkeypatch):
    from app import branch_printing, morning_delivery, fiscal, fiscal_issuance
    def authorize(conn, user, order, preparation=False):
        if user.get('id') != 'approved' or user.get('brand_id') != order['brand_id'] or order['branch_id'] not in user.get('branch_ids', []):
            raise Failure('forbidden')
        assert preparation
    monkeypatch.setattr(morning_delivery, 'authorize_order', authorize)
    monkeypatch.setattr(fiscal_issuance, 'original', lambda conn, oid: None)
    monkeypatch.setattr(fiscal, 'print_order', lambda doc, order: {})
    monkeypatch.setattr(branch_printing.db, 'error', lambda *args: Failure(args[1]))
    monkeypatch.setattr(branch_printing.db, 'new_id', lambda prefix: prefix+'unique')
    monkeypatch.setattr(branch_printing.db, 'json', lambda value: value)
    monkeypatch.setattr(branch_printing.db, 'audit', lambda *args: None)
    monkeypatch.setattr(branch_printing.print_translations, 'for_order', lambda *args: {})
    monkeypatch.setattr(branch_printing, 'receipt', lambda *args: {'lines': [], 'kind': 'order'})
    return branch_printing


def user(**changes):
    return {'id':'approved', 'role':'branch_manager', 'brand_id':'kps', 'branch_ids':['branch-2']} | changes


def order(**changes):
    return {'id':'order-1', 'brand_id':'kps', 'branch_id':'branch-2', 'service':'delivery',
            'snapshot':{'delivery_period':'morning'}} | changes


@pytest.mark.parametrize('change', [{'brand_id':'enbishan'}, {'service':'pickup'}, {'snapshot':{'delivery_period':'evening'}}, {'snapshot':{}}])
def test_other_operations_never_enter_auto_print_flow(helpers, change):
    conn = Connection()
    assert helpers.morning_preparer_branch(conn, user(), order(**change)) is None
    assert conn.statements == []


def test_only_approved_branch_two_preparer_qualifies(helpers):
    assert helpers.morning_preparer_branch(Connection(), user(), order())['id'] == 'branch-2'
    assert helpers.morning_preparer_branch(Connection(branch_no=1), user(), order()) is None
    assert helpers.morning_preparer_branch(Connection(), user(role='delivery_manager'), order()) is None
    with pytest.raises(Failure):
        helpers.morning_preparer_branch(Connection(), user(id='unapproved'), order())
    with pytest.raises(Failure):
        helpers.morning_preparer_branch(Connection(), user(branch_ids=[]), order())


def test_queue_is_branch_local_and_repeated_acceptance_never_duplicates(helpers):
    conn = Connection()
    branch = helpers.morning_preparer_branch(conn, user(), order())
    first = helpers.enqueue_morning_preparer_receipt(conn, user(), order(), branch)
    assert first == {'id':'print_unique', 'status':'queued'}
    second = helpers.enqueue_morning_preparer_receipt(conn, user(), order(), branch)
    assert second == {'id':'print_unique', 'status':'already_requested'}
    assert len(conn.inserted) == 1
    assert conn.inserted[0][1:6] == ('kps', 'branch-2', 'printer-2', 'order-1', 'order')
    selection = next((sql,params) for sql,params in conn.statements if 'FROM branch_print_devices' in sql)
    assert selection[1] == ('kps','branch-2')
    for required in ('revoked_at IS NULL', 'token_hash IS NOT NULL', 'approved_at IS NOT NULL', 'FOR UPDATE'):
        assert required in selection[0]


@pytest.mark.parametrize('devices', [[], [{'id':'one'}, {'id':'two'}]])
def test_missing_unapproved_or_ambiguous_device_does_not_queue(helpers, devices):
    conn = Connection(devices=devices)
    with pytest.raises(Failure, match='MORNING_PRINTER_NOT_READY'):
        helpers.enqueue_morning_preparer_receipt(conn, user(), order(), conn.branch)
    assert conn.inserted == []


def test_preexisting_manual_receipt_is_never_automatically_reprinted(helpers):
    conn = Connection(devices=[], previous={'id':'manual-print'})
    assert helpers.enqueue_morning_preparer_receipt(conn, user(), order(), conn.branch) == {'id':'manual-print','status':'already_requested'}
    assert conn.inserted == []
    assert not any('FROM branch_print_devices' in sql for sql,_ in conn.statements)


@pytest.mark.parametrize('period,branch_no,current,target,allowed,print_count', [
    ('morning',2,'new','accepted',True,1),
    ('morning',2,'accepted','ready',True,0),
    ('morning',2,'new','ready',False,0),
    ('evening',2,'accepted','ready',False,0),
    ('morning',1,'accepted','ready',False,0),
])
def test_preparation_endpoint_only_shortcuts_approved_morning_branch_two(
        monkeypatch, period, branch_no, current, target, allowed, print_count):
    from contextlib import contextmanager
    from fastapi import HTTPException
    from app import orders, branch_printing, branch_payments, order_amendments, morning_delivery
    row = order(snapshot={'delivery_period':period}) | {'version':1,'preparation_status':current,'customer':{},'payment_status':'unpaid'}
    conn = Connection(branch_no=branch_no)
    execute = conn.execute
    def update(sql, params):
        if sql.startswith('UPDATE orders SET preparation_status='):
            row['preparation_status'] = params[0]
            row['version'] += 1
            return SimpleNamespace(fetchone=lambda:dict(row))
        return execute(sql, params)
    conn.execute = update
    @contextmanager
    def transaction():
        yield conn
    monkeypatch.setattr(orders.db, 'tx', transaction)
    monkeypatch.setattr(orders.db, 'idempotent', lambda c,u,k,op,body,fn:fn())
    monkeypatch.setattr(orders.db, 'event', lambda *args:None)
    monkeypatch.setattr(orders.db, 'audit', lambda *args:None)
    monkeypatch.setattr(orders, 'scoped_order', lambda *args,**kwargs:dict(row))
    monkeypatch.setattr(morning_delivery, 'authorize_order', lambda *args,**kwargs:None)
    monkeypatch.setattr(order_amendments, 'guard', lambda *args,**kwargs:None)
    monkeypatch.setattr(branch_payments, 'require_verified', lambda *args:None)
    queued = []
    def queue(*args):
        queued.append(args)
        return {'id':'queued-once','status':'queued'}
    monkeypatch.setattr(branch_printing, 'enqueue_morning_preparer_receipt', queue)
    body = orders.PreparationBody(status=target, version=1)
    if allowed:
        result = orders.preparation(row['id'], body, user=user(), idempotency_key='accept-once')
        assert result['order']['preparation_status'] == target
        if print_count:
            assert result['printing']['status'] == 'queued'
    else:
        with pytest.raises(HTTPException) as error:
            orders.preparation(row['id'], body, user=user(), idempotency_key='accept-once')
        assert error.value.status_code == 409
        assert row['preparation_status'] == current
    assert len(queued) == print_count

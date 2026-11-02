"""Owner-approved shift authority is scoped and independent from read access."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from app import auth, db, employee_permissions as policy, operations
from tests.test_core import _make_user, _authenticate
from tests.sms_policy_helpers import sms_login, sms_step_up
from tests.test_owner_extra_permissions import _save


def accountant(*grants, branches=None):
    return {'id': 'accountant', 'role': 'admin', 'brand_id': 'kps',
            'branch_ids': ['kps-branch-1'] if branches is None else branches,
            'permissions': ['accounting_reports'], 'extra_permissions': list(grants)}


@pytest.mark.parametrize('capability', sorted(policy.SHIFT_DELEGATIONS))
def test_accountant_grants_are_explicit_independent_scoped_and_revocable(capability):
    user = accountant(capability)
    original = deepcopy(user)
    shift = {'id': 'shift', 'kind': 'driver', 'user_id': 'driver',
             'brand_id': 'kps', 'branch_ids': ['kps-branch-1']}
    assert policy.effective(user) == {'accounting_reports', capability}
    operations.shift_access(None, user, shift, capability=capability)
    # Even a read grant never authorizes unrelated legacy mutation helpers.
    with pytest.raises(HTTPException):
        operations.shift_access(None, user, shift)
    for other in policy.SHIFT_DELEGATIONS - {capability}:
        with pytest.raises(HTTPException):
            operations.shift_access(None, user, shift, capability=other)
    for row in (dict(shift, brand_id='enbishan'),
                dict(shift, branch_ids=['kps-branch-1', 'kps-branch-2']),
                dict(shift, branch_ids=[])):
        with pytest.raises(HTTPException):
            operations.shift_access(None, user, row, capability=capability)
    with pytest.raises(HTTPException):
        operations.shift_access(None, dict(user, extra_permissions=[]), shift, capability=capability)
    with pytest.raises(HTTPException):
        operations.shift_access(None, dict(user, branch_ids=[]), shift, capability=capability)
    assert user == original


def test_read_grant_does_not_imply_receipt_or_financial_mutation():
    user = accountant('shifts_view')
    assert not policy.effective(user) & {'shifts_approve', 'custody_transfer', 'refunds_manage', 'payments_collect'}
    for role in ('driver', 'delivery_staff', 'branch_manager', 'customer'):
        assert not policy.grantable(role) & policy.SHIFT_DELEGATIONS
    assert policy.required_for(operations.shift_detail, 'admin') == 'shifts_view'
    assert policy.required_for(operations.approve_close, 'admin') == 'shifts_approve'
    assert policy.required_for(operations.submit_close, 'admin') == ('shifts_close', 'shifts_delegate_close')
    assert policy.required_for(operations.submit_close, 'driver') == 'shifts_close'
    assert policy.required_for(operations.accept_manager_handover, 'admin') == 'shifts_handover'
    for code in policy.SHIFT_DELEGATIONS:
        item = next(p for p in policy.catalog() if p['code'] == code)
        assert item['delegable_roles'] == ['admin'] and item['owner_approval_required']
        assert item['api_contract']['scope']


def seed_shifts():
    manager = _make_user('delivery_manager', branches=['kps-branch-1', 'kps-branch-2'])
    driver = _make_user('driver', branches=['kps-branch-1'])
    outside = _make_user('delivery_manager', branches=['kps-branch-1', 'kps-branch-2'])
    empty = _make_user('delivery_manager')
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('accountant-other','Other brand')")
    foreign = _make_user('delivery_manager', brand='accountant-other', branches=['kps-branch-1'])
    with db.tx() as c:
        for sid, principal, brand, branches in (
            ('accountant-manager', manager, 'kps', ['kps-branch-1']),
            ('accountant-outside', outside, 'kps', ['kps-branch-1', 'kps-branch-2']),
            ('accountant-foreign', foreign, 'accountant-other', ['kps-branch-1']),
            ('accountant-empty', empty, 'kps', []),
        ):
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,%s,%s)",
                      (sid, principal['id'], brand, db.json(branches)))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES('accountant-driver','driver',%s,'kps','[\"kps-branch-1\"]','accountant-manager')", (driver['id'],))
    return manager, driver


@pytest.mark.postgres
def test_owner_can_grant_read_only_and_revoke_in_the_same_live_session(client, monkeypatch):
    target = _make_user('admin', branches=['kps-branch-1'])
    owner = _make_user('owner')
    seed_shifts()
    _authenticate(client, target)
    cookies, csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    assert client.get('/api/shifts').status_code == 403
    sms_login(client, owner, monkeypatch)
    assert client.get('/api/session').json()['user']['owner_session_approved'] is True
    result = _save(client, target, {'shifts_view'})
    assert result.status_code == 200, result.text
    updated = result.json()['user']
    assert updated['role'] == 'admin' and updated['branch_ids'] == target['branch_ids']
    owner_cookies, owner_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    client.cookies.clear(); client.cookies.update(cookies); client.headers['X-CSRF-Token'] = csrf
    for path in ('/api/shifts', '/api/shifts/current', '/api/shifts?owner_view=current'):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert {s['id'] for s in response.json()['shifts']} == {'accountant-manager', 'accountant-driver'}
        if 'owner_view' in path:
            assert response.json()['total'] == 2
    assert client.get('/api/shifts/accountant-driver').status_code == 200
    assert client.get('/api/shifts/accountant-driver/draft').status_code == 200
    assert client.get('/api/driver-incentives/shifts/accountant-driver').status_code == 200
    for sid in ('accountant-outside', 'accountant-foreign', 'accountant-empty'):
        assert client.get('/api/shifts/' + sid).status_code in (403, 404)
    for path, payload in (
        ('/api/shifts', {'kind': 'manager', 'user_id': target['id'], 'branch_ids': target['branch_ids']}),
        ('/api/shifts/accountant-driver/closing', {}),
        ('/api/shifts/accountant-driver/approve', {}),
        ('/api/shifts/accountant-driver/return', {'reason': 'Unauthorized correction'}),
        ('/api/shifts/accountant-driver/driver-close', {'received_halalas': 0, 'confirmed': True}),
    ):
        assert client.post(path, json=payload).status_code == 403
    assert client.put('/api/identity/staff/' + target['id'] + '/permissions', json={
        'enabled_permissions': ['shifts_view', 'shifts_approve'], 'expected_permissions': ['shifts_view'],
        'reason': 'Cannot grant to myself'}).status_code == 403
    client.cookies.clear(); client.cookies.update(owner_cookies); client.headers['X-CSRF-Token'] = owner_csrf
    assert _save(client, updated, set()).status_code == 200
    client.cookies.clear(); client.cookies.update(cookies); client.headers['X-CSRF-Token'] = csrf
    assert client.get('/api/shifts').status_code == 403
    assert client.get('/api/shifts/accountant-driver').status_code == 403
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='staff.permissions.updated' AND entity_id=%s", (target['id'],)).fetchone()['n'] == 2


@pytest.mark.postgres
def test_approval_grant_preserves_actual_receipt_and_branch_guards(client, monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting, 'generate_files', lambda rid: None)
    target = _make_user('admin', branches=['kps-branch-1'])
    owner = _make_user('owner')
    manager, driver = seed_shifts()
    sms_login(client, owner, monkeypatch); sms_step_up(client, owner, monkeypatch)
    result = _save(client, target, {'shifts_view', 'shifts_approve'})
    assert result.status_code == 200, result.text
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='submitted',submission=%s WHERE id='accountant-driver'",
                  (db.json({'counted_halalas': 0, 'retained_halalas': 0, 'handover_halalas': 0,
                            'difference_halalas': 0, 'card_difference_halalas': 0}),))
    _authenticate(client, target)
    blocked = client.post('/api/shifts/accountant-driver/approve', json={}, headers={'Idempotency-Key': 'accountant-no-receipt'})
    assert blocked.status_code == 409, blocked.text
    with db.tx() as c:
        assert c.execute("SELECT status FROM shifts WHERE id='accountant-driver'").fetchone()['status'] == 'submitted'
    assert client.post('/api/shifts/accountant-outside/return', json={'reason': 'Out of scope'}, headers={'Idempotency-Key': 'accountant-outside'}).status_code == 403
    assert client.post('/api/shifts/accountant-foreign/return', json={'reason': 'Other brand'}, headers={'Idempotency-Key': 'accountant-foreign'}).status_code == 404
    assert client.post('/api/shifts/accountant-driver/driver-close', json={'received_halalas': 0, 'confirmed': True}).status_code == 403
    # The accountant cannot confirm a receipt assigned to the actual manager.
    with db.tx() as c:
        c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,closure,created_by) VALUES('accountant-receipt','accountant-driver','accountant-manager',%s,0,'Verified zero-cash handover',true,%s)", (manager['id'], driver['id']))
    assert client.post('/api/custody/accountant-receipt/confirm', json={}).status_code == 403
    target_cookies, target_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    _authenticate(client, manager)
    confirmed = client.post('/api/custody/accountant-receipt/confirm', json={}, headers={'Idempotency-Key': 'actual-manager-receipt'})
    assert confirmed.status_code == 200, confirmed.text
    client.cookies.clear(); client.cookies.update(target_cookies); client.headers['X-CSRF-Token'] = target_csrf
    approved = client.post('/api/shifts/accountant-driver/approve', json={}, headers={'Idempotency-Key': 'accountant-verified-approval'})
    assert approved.status_code == 200, approved.text
    replay = client.post('/api/shifts/accountant-driver/approve', json={}, headers={'Idempotency-Key': 'accountant-verified-approval'})
    assert replay.status_code == 200 and replay.json() == approved.json()
    with db.tx() as c:
        row = c.execute("SELECT status,approved_by FROM shifts WHERE id='accountant-driver'").fetchone()
        assert row['status'] == 'closed' and row['approved_by'] == target['id']
        assert c.execute("SELECT count(*) n FROM closure_reports WHERE shift_id='accountant-driver'").fetchone()['n'] == 1


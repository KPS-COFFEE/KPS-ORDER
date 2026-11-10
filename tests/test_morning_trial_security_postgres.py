"""Authenticated night rehearsal isolation; all data and SMS responses are synthetic.

The rehearsal may persist its own JSON state, audit/idempotency records and an
explicit test print job. It must never create operational or financial records.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg import sql

from app import auth, db, employee_permissions
from test_core import _authenticate, _branch
from test_operations import ops
from tests.sms_policy_helpers import sms_step_up

pytestmark = pytest.mark.postgres
B2 = 'kps-branch-2'
B3 = 'kps-branch-3'
BASE = '/api/morning-trial'


@pytest.fixture
def trial_accounts(ops, monkeypatch):
    actor, _, _, _ = ops
    client = actor('owner')
    client.app.dependency_overrides.pop(auth.current_user, None)
    with db.tx() as c:
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',
                  (db.json(_branch(B2)['settings']), B2))
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver','ops-driver2','ops-branch')",
                  (db.json([B2]),))
        for index, uid in enumerate(('ops-manager', 'ops-branch', 'ops-driver', 'ops-driver2')):
            c.execute("UPDATE users SET phone=%s,invitation_status='active' WHERE id=%s",
                      ('+96651000000'+str(index), uid))
        for uid, role in [('ops-manager', 'delivery_manager'), ('ops-branch', 'branch_manager'), ('ops-driver', 'driver')]:
            c.execute('UPDATE users SET permission_denials=%s,extra_permissions=\'[]\' WHERE id=%s',
                      (db.json(sorted(employee_permissions.eligible(role))), uid))
        # The rehearsal grant is independent from live intake and ordinary
        # employee permissions; starting a test must not enable public service.
        policy = {'enabled': False, 'version': 1, 'execution_branch_ids': [],
                  'manager_user_ids': [], 'preparer_user_ids': []}
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",
                  (db.json({'morning_delivery': policy}),))
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at,printer_name) "
                  "VALUES('trial-approved-printer','kps',%s,'TEST BRANCH 2',%s,'ops-owner',now(),'TEST PRINTER')",
                  (B2, uuid4().hex))
        # Preserve an actual evening duty/order and another branch's order.
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,delivery_window_snapshot) "
                  "VALUES('trial-preserved-evening','manager','ops-manager','kps',%s,%s)",
                  (db.json([B2]), db.json({'period': 'evening'})))
        for oid, branch in [('trial-preserved-evening-order', B2), ('trial-preserved-other-branch-order', B3)]:
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                      "VALUES(%s,'kps',%s,'ops-customer','delivery','ready','pending','cash',1000,0,1000,%s,'{}')",
                      (oid, branch, db.json({'items': [], 'delivery_period': 'evening'})))
        users = {name: c.execute('SELECT * FROM users WHERE id=%s', ('ops-'+name,)).fetchone()
                 for name in ('owner', 'manager', 'driver', 'driver2', 'branch', 'customer', 'other')}
    sessions = {}
    for name, principal in users.items():
        _authenticate(client, principal)
        if name == 'owner':
            # Normal owner request/check routes, with a mocked provider only.
            sms_step_up(client, principal, monkeypatch, phone='+966500000010')
        sessions[name] = (dict(client.cookies), dict(client.headers))

    def login(name):
        cookies, headers = sessions[name]
        client.cookies.clear(); client.cookies.update(cookies)
        client.headers.clear(); client.headers.update(headers)
        return client

    body = {'branch_id': B2, 'manager_user_id': 'ops-manager',
            'preparer_user_id': 'ops-branch', 'driver_user_id': 'ops-driver',
            'reason': 'TEST ONLY isolated night rehearsal'}
    return login, body


def start_trial(login, body, *, key=None):
    response = login('owner').post(BASE+'/start', json=body,
        headers={'Idempotency-Key': key or uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()['trial']['id'], response


def action(login, tid, who, name, *, key=None, extra=None):
    return login(who).post(f'{BASE}/{tid}/action', json={'action': name, **(extra or {})},
        headers={'Idempotency-Key': key or uuid4().hex})


def operational_snapshot():
    tables = ('orders', 'shifts', 'driver_tasks', 'collections', 'refunds', 'custody_events',
              'custody_transfers', 'closure_reports', 'fiscal_documents', 'order_branch_counters',
              'order_daily_counters', 'driver_accounting_settlements', 'channel_settlement_allocations',
              'retained_carryovers', 'users', 'branches', 'brands', 'branch_print_devices')
    with db.tx() as c:
        rows = {table: c.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text')
                               .format(sql.Identifier(table))).fetchall() for table in tables}
        settings = deepcopy(c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'])
    settings.pop('morning_trial_sessions', None)
    return rows, settings


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_rehearsal_completes_with_real_sessions_without_operational_or_financial_writes(trial_accounts):
    login, body = trial_accounts
    before = operational_snapshot()
    visible_before = {o['id'] for o in login('owner').get('/api/orders').json()['orders']}
    tid, opened = start_trial(login, body)
    assert opened.json()['trial']['simulated'] is True
    assert opened.json()['trial']['financial_effect'] is False
    trial = opened.json()['trial']
    duration = datetime.fromisoformat(trial['expires_at'])-datetime.fromisoformat(trial['created_at'])
    assert 0 < duration.total_seconds() <= 1200
    for who, name in [('branch', 'accept'), ('driver', 'driver_accept'), ('branch', 'preparing'),
                      ('branch', 'ready'), ('driver', 'picked_up'), ('driver', 'delivered'), ('manager', 'close')]:
        result = action(login, tid, who, name)
        assert result.status_code == 200, result.text
        assert result.json()['trial']['financial_effect'] is False
    assert result.json()['trial']['status'] == 'closed'
    assert operational_snapshot() == before
    assert login('manager').get('/api/orders').status_code == 403
    assert login('branch').get('/api/orders').status_code == 403
    assert {o['id'] for o in login('owner').get('/api/orders').json()['orders']} == visible_before
    with db.tx() as c:
        jobs = c.execute('SELECT * FROM branch_print_jobs').fetchall()
    assert len(jobs) == 1
    assert jobs[0]['kind'] == 'test' and jobs[0]['order_id'] is None and jobs[0]['branch_id'] == B2
    text = str(jobs[0]['payload'])
    assert ('NOT AN ORDER' in text or 'NOT AN INVOICE' in text) and ('تجريبي' in text or 'TRIAL' in text)


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_start_and_acceptance_replay_never_duplicate_trial_or_paper_job(trial_accounts):
    login, body = trial_accounts; key = uuid4().hex
    tid, first = start_trial(login, body, key=key)
    replay = login('owner').post(BASE+'/start', json=body, headers={'Idempotency-Key': key})
    assert replay.status_code == 200 and replay.json() == first.json(), replay.text
    accept_key = uuid4().hex
    accepted = action(login, tid, 'manager', 'accept', key=accept_key)
    assert accepted.status_code == 200, accepted.text
    assert action(login, tid, 'manager', 'accept', key=accept_key).json() == accepted.json()
    assert action(login, tid, 'branch', 'accept').status_code == 409
    assert action(login, tid, 'manager', 'ready', key=accept_key).status_code in (403, 409, 422)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs').fetchone()['n'] == 1


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('who,name', [('driver', 'accept'), ('owner', 'accept'), ('driver', 'preparing'),
    ('owner', 'ready'), ('branch', 'driver_accept'), ('manager', 'picked_up'),
    ('manager', 'delivered'), ('driver', 'close'), ('manager', 'stop')])
def test_each_selected_actor_has_only_their_rehearsal_actions(trial_accounts, who, name):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    result = action(login, tid, who, name)
    assert result.status_code == 403, result.text
    assert login('owner').get(f'{BASE}/{tid}').json()['trial']['order']['status'] == 'new'


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('who', ['driver2', 'customer', 'other'])
def test_unselected_same_branch_and_foreign_accounts_cannot_read_or_mutate(trial_accounts, who):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    assert login(who).get(f'{BASE}/{tid}').status_code in (403, 404)
    assert action(login, tid, who, 'accept').status_code in (403, 404)
    assert login(who).post(BASE+'/start', json=body,
        headers={'Idempotency-Key': uuid4().hex}).status_code == 403


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('changed', [
    {'branch_id': B3}, {'manager_user_id': 'ops-driver'},
    {'preparer_user_id': 'ops-manager'}, {'driver_user_id': 'ops-other'},
    {'driver_user_id': 'nonexistent'}, {'reason': ' '}, {'ttl_minutes': 1440}])
def test_start_rejects_actor_branch_and_duration_tampering(trial_accounts, changed):
    login, body = trial_accounts; before = operational_snapshot()
    response = login('owner').post(BASE+'/start', json=body | changed,
        headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code in (403, 404, 409, 422), response.text
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_owner_step_up_csrf_and_idempotency_key_are_not_optional(trial_accounts):
    login, body = trial_accounts
    client = login('owner')
    assert client.post(BASE+'/start', json=body).status_code in (400, 422)
    assert client.post(BASE+'/start', json=body, headers={
        'X-CSRF-Token': 'incorrect-proof', 'Idempotency-Key': uuid4().hex}).status_code == 403
    with db.tx() as c:
        c.execute("UPDATE sessions SET step_up_at=NULL WHERE user_id='ops-owner'")
    assert login('owner').post(BASE+'/start', json=body,
        headers={'Idempotency-Key': uuid4().hex}).status_code == 403


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('revocation', ['expired', 'stopped', 'inactive', 'branch', 'role', 'invitation', 'phone', 'ambiguous'])
def test_expiry_and_fresh_revocation_block_saved_action_replays(trial_accounts, revocation):
    login, body = trial_accounts; tid, _ = start_trial(login, body); key = uuid4().hex
    assert action(login, tid, 'manager', 'accept', key=key).status_code == 200
    if revocation == 'stopped':
        assert action(login, tid, 'owner', 'stop').status_code == 200
    else:
        with db.tx() as c:
            if revocation == 'expired':
                expiry = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
                c.execute("UPDATE app_settings SET settings=jsonb_set(settings,%s,%s) WHERE brand_id='kps'",
                          (['morning_trial_sessions', tid, 'expires_at'], db.json(expiry)))
            elif revocation == 'inactive':
                c.execute("UPDATE users SET active=false WHERE id='ops-manager'")
            elif revocation == 'branch':
                c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
            elif revocation == 'role':
                c.execute("UPDATE users SET role='driver' WHERE id='ops-manager'")
            elif revocation == 'invitation':
                c.execute("UPDATE users SET invitation_status='pending' WHERE id='ops-manager'")
            elif revocation == 'phone':
                c.execute("UPDATE users SET phone=NULL WHERE id='ops-manager'")
            else:
                c.execute("UPDATE users SET phone=(SELECT phone FROM users WHERE id='ops-manager') WHERE id='ops-driver2'")
    replay = action(login, tid, 'manager', 'accept', key=key)
    assert replay.status_code in (401, 403, 404, 409, 410), replay.text
    report = login('manager').get(f'{BASE}/{tid}')
    if revocation in {'expired', 'stopped'}:
        assert report.status_code == 200, report.text
        assert report.json()['trial']['financial_effect'] is False
        assert report.json()['trial']['expired'] or report.json()['trial']['status'] == 'stopped'
    else:
        assert report.status_code in (401, 403, 404, 409, 410), report.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs').fetchone()['n'] == 1


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_rehearsal_refuses_driver_already_working_a_real_shift(trial_accounts):
    login, body = trial_accounts
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,delivery_window_snapshot) "
                  "VALUES('trial-real-driver','driver','ops-driver','kps',%s,'trial-preserved-evening',%s)",
                  (db.json([B2]), db.json({'period': 'evening'})))
    before = operational_snapshot()
    result = login('owner').post(BASE+'/start', json=body,
        headers={'Idempotency-Key': uuid4().hex})
    assert result.status_code in (403, 409), result.text
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('identity_problem', ['paused_invitation', 'missing_phone', 'duplicate_phone'])
def test_trial_cannot_select_unactivated_or_ambiguous_employee_identity(trial_accounts, identity_problem):
    login, body = trial_accounts
    with db.tx() as c:
        if identity_problem == 'paused_invitation':
            c.execute("UPDATE users SET invitation_status='pending' WHERE id='ops-driver'")
        elif identity_problem == 'missing_phone':
            c.execute("UPDATE users SET phone=NULL WHERE id='ops-driver'")
        else:
            c.execute("UPDATE users SET phone=(SELECT phone FROM users WHERE id='ops-driver') WHERE id='ops-driver2'")
    before = operational_snapshot()
    result = login('owner').post(BASE+'/start', json=body,
        headers={'Idempotency-Key': uuid4().hex})
    assert result.status_code in (403, 409, 422), result.text
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_unknown_trial_or_extra_action_fields_never_touch_live_orders(trial_accounts):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    before = operational_snapshot()
    assert action(login, 'not-a-trial', 'manager', 'accept').status_code in (404, 422)
    result = action(login, tid, 'manager', 'accept', extra={
        'branch_id': B3, 'order_id': 'trial-preserved-evening-order', 'user_id': 'ops-owner'})
    assert result.status_code == 422, result.text
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('printer_state', ['unapproved', 'revoked', 'ambiguous'])
def test_unusable_or_ambiguous_print_destination_rolls_back_rehearsal_acceptance(trial_accounts, printer_state):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    with db.tx() as c:
        if printer_state == 'unapproved':
            c.execute("UPDATE branch_print_devices SET approved_at=NULL WHERE id='trial-approved-printer'")
        elif printer_state == 'revoked':
            c.execute("UPDATE branch_print_devices SET revoked_at=now() WHERE id='trial-approved-printer'")
        else:
            c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) "
                      "VALUES('trial-other-printer','kps',%s,'TEST OTHER',%s,'ops-owner',now())", (B2, uuid4().hex))
    before = operational_snapshot()
    result = action(login, tid, 'manager', 'accept')
    assert result.status_code == 409 and result.json()['detail']['code'] == 'MORNING_PRINTER_NOT_READY', result.text
    report = login('manager').get(f'{BASE}/{tid}').json()
    assert report['trial']['order']['status'] == 'new'
    assert report['trial']['order']['print_job_id'] is None
    assert report['trial']['events'] == []
    assert operational_snapshot() == before
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs').fetchone()['n'] == 0


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_live_morning_policy_changes_do_not_grant_or_revoke_simulated_actor_access(trial_accounts):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    assert action(login, tid, 'manager', 'accept').status_code == 200
    with db.tx() as c:
        settings = c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings']
        settings['morning_delivery'] = {'enabled': True, 'version': 99,
            'execution_branch_ids': [B3], 'manager_user_ids': [], 'preparer_user_ids': []}
        c.execute("UPDATE brands SET settings=%s WHERE id='kps'", (db.json(settings),))
    assert login('manager').get(f'{BASE}/{tid}').status_code == 200
    assert action(login, tid, 'driver', 'driver_accept').status_code == 200
    assert action(login, tid, 'branch', 'preparing').status_code == 200
    ready = action(login, tid, 'branch', 'ready')
    assert ready.status_code == 200, ready.text
    assert login('manager').get('/api/orders').status_code == 403
    with db.tx() as c:
        assert c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings'] == settings


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('steps,who,name', [
    ([], 'driver', 'driver_accept'),
    ([('branch','accept')], 'branch', 'preparing'),
    ([('branch','accept')], 'branch', 'ready'),
    ([('branch','accept')], 'driver', 'picked_up'),
    ([('branch','accept'),('driver','driver_accept')], 'branch', 'ready'),
    ([('branch','accept'),('driver','driver_accept')], 'driver', 'delivered'),
    ([('branch','accept'),('driver','driver_accept'),('branch','preparing')], 'driver', 'picked_up'),
])
def test_required_actual_driver_acceptance_and_preparation_sequence(trial_accounts, steps, who, name):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    for actor, step in steps:
        result = action(login, tid, actor, step)
        assert result.status_code == 200, result.text
    before = operational_snapshot()
    result = action(login, tid, who, name)
    assert result.status_code == 409, result.text
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manager_fallback_requires_reason_and_never_accepts_for_driver(trial_accounts):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    assert action(login, tid, 'manager', 'accept').status_code == 200
    assert action(login, tid, 'manager', 'driver_accept').status_code == 403
    assert action(login, tid, 'manager', 'preparing', extra={'reason':'preparer delayed'}).status_code == 409
    assert action(login, tid, 'driver', 'driver_accept').status_code == 200
    for step in ('preparing','ready'):
        assert action(login, tid, 'manager', step).status_code == 422
        assert action(login, tid, 'manager', step, extra={'reason':' '}).status_code == 422
        key=uuid4().hex
        result=action(login, tid, 'manager', step, key=key, extra={'reason':'preparer delayed'})
        assert result.status_code == 200, result.text
        assert action(login, tid, 'manager', step, key=key, extra={'reason':'preparer delayed'}).json() == result.json()
        assert action(login, tid, 'manager', step, key=key, extra={'reason':'changed reason'}).status_code == 409
    report=login('manager').get(f'{BASE}/{tid}').json()['trial']
    assert report['order']['assigned_driver_id']=='ops-driver'
    assert report['order']['driver_accepted_by']=='ops-driver'
    fallback=[event for event in report['events'] if event['action'] in ('preparing','ready')]
    assert len(fallback)==2 and all(event['actor_id']=='ops-manager' and event['on_behalf_of']=='ops-branch' and event['reason']=='preparer delayed' for event in fallback)
    for who,name in [('driver','picked_up'),('driver','delivered'),('manager','close')]:
        result=action(login,tid,who,name);assert result.status_code==200,result.text
    assert result.json()['trial']['status']=='closed'
    assert action(login,tid,'manager','ready',extra={'reason':'preparer delayed'}).status_code==409
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs').fetchone()['n']==1


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_owner_cannot_manually_assign_or_impersonate_selected_workers(trial_accounts):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    assert action(login,tid,'branch','accept').status_code==200
    assert action(login,tid,'owner','assign').status_code==422
    for name in ('driver_accept','preparing','ready','picked_up','delivered'):
        assert action(login,tid,'owner',name).status_code==403
    report=login('owner').get(f'{BASE}/{tid}').json()['trial']
    assert report['order']['status']=='assigned'
    assert [e['action'] for e in report['events']]==['accept','auto_assign']


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_legacy_closed_trial_report_remains_readable_without_migration(trial_accounts):
    login, body = trial_accounts; tid, _ = start_trial(login, body)
    with db.tx() as c:
        row=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()
        settings=row['settings'];legacy=settings['morning_trial_sessions'][tid]
        legacy.pop('flow_version');legacy['status']='closed';legacy['order']['status']='delivered'
        legacy['events']=[{'action':'assign','actor_id':'ops-owner','role':'owner','simulated':True}]
        c.execute("UPDATE app_settings SET settings=%s WHERE brand_id='kps'",(db.json(settings),))
    report=login('manager').get(f'{BASE}/{tid}')
    assert report.status_code==200 and report.json()['trial']['events'][0]['action']=='assign'

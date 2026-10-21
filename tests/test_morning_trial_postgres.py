"""The labeled rehearsal shares test printing, never real operating records."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app import auth, db, employee_permissions, service_hours
from app.morning_trial import KEY
from test_morning_delivery import at
from test_operations import ops

pytestmark = pytest.mark.postgres
B2 = 'kps-branch-2'


@pytest.fixture
def rehearsal(ops, monkeypatch):
    actor, _, _, _ = ops
    monkeypatch.setattr(auth, 'require_owner_step_up', lambda c, user: None)
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('21:00:00'))
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'morning_delivery': {
            'enabled': False, 'version': 1, 'execution_branch_ids': [],
            'manager_user_ids': [], 'preparer_user_ids': []}}),))
        for index, (name, role) in enumerate([('manager', 'delivery_manager'), ('branch', 'branch_manager'), ('driver', 'driver')]):
            c.execute('UPDATE users SET branch_ids=%s,permission_denials=%s,phone=%s WHERE id=%s',
                      (db.json([B2]), db.json(sorted(employee_permissions.eligible(role))), '+96651000000'+str(index), 'ops-'+name))
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at,approved_by) "
                  "VALUES('rehearsal-device','kps',%s,'test printer','test-token','ops-owner',now(),'ops-owner')", (B2,))
    return actor, {'branch_id': B2, 'manager_user_id': 'ops-manager', 'preparer_user_id': 'ops-branch',
                   'driver_user_id': 'ops-driver', 'reason': 'Owner authorized isolated nighttime rehearsal'}


def send(actor, who, path, body, key=None):
    return actor(who).post(path, json=body, headers={'Idempotency-Key': key or uuid4().hex})


def operational_snapshot():
    tables = ('orders', 'order_events', 'shifts', 'driver_tasks', 'collections', 'refunds',
              'custody_events', 'custody_transfers', 'closure_reports', 'fiscal_documents')
    with db.tx() as c:
        counts = {name: c.execute('SELECT count(*) n FROM '+name).fetchone()['n'] for name in tables}
        brand = c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings']
        settings = c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        settings.pop(KEY, None)
        device = c.execute("SELECT approved_at,approved_by,acceptance_job_id,revoked_at,token_hash FROM branch_print_devices WHERE id='rehearsal-device'").fetchone()
    return deepcopy((counts, brand, settings, device))


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('acceptor', ['manager', 'branch'])
def test_full_simulated_journey_at_night_does_not_create_real_operations(rehearsal, acceptor):
    actor, body = rehearsal
    before = operational_snapshot()
    key = uuid4().hex
    started = send(actor, 'owner', '/api/morning-trial/start', body, key)
    assert started.status_code == 200, started.text
    assert send(actor, 'owner', '/api/morning-trial/start', body, key).json() == started.json()
    trial = started.json()['trial']; tid = trial['id']
    assert trial['simulated'] and not trial['financial_effect']
    assert trial['order']['id'].startswith('TRIAL-') and trial['order']['status'] == 'new'
    assert actor('driver').get('/api/morning-trial/current').json()['trial']['id'] == tid
    path = f'/api/morning-trial/{tid}/action'
    accept_key = uuid4().hex
    accepted = send(actor, acceptor, path, {'action': 'accept'}, accept_key)
    assert accepted.status_code == 200, accepted.text
    assert send(actor, acceptor, path, {'action': 'accept'}, accept_key).json() == accepted.json()
    assert accepted.json()['print_job']['status'] == 'queued'
    with db.tx() as c:
        jobs = c.execute("SELECT * FROM branch_print_jobs WHERE payload->>'morning_trial_id'=%s", (tid,)).fetchall()
        assert len(jobs) == 1 and jobs[0]['kind'] == 'test' and jobs[0]['order_id'] is None
        assert jobs[0]['branch_id'] == B2 and jobs[0]['device_id'] == 'rehearsal-device'
        assert any('NOT AN INVOICE' in line['text'] for line in jobs[0]['payload']['lines'])
    for who, action, expected in [('branch', 'ready', 'ready'), ('owner', 'assign', 'assigned'),
                                  ('driver', 'driver_accept', 'driver_accepted'), ('driver', 'delivered', 'delivered'),
                                  ('owner', 'close', 'delivered')]:
        result = send(actor, who, path, {'action': action})
        assert result.status_code == 200, result.text
        assert result.json()['trial']['order']['status'] == expected
    report = actor('manager').get(f'/api/morning-trial/{tid}')
    assert report.status_code == 200 and report.json()['trial']['status'] == 'closed'
    assert [event['action'] for event in report.json()['trial']['events']] == ['accept', 'ready', 'assign', 'driver_accept', 'delivered', 'close']
    assert operational_snapshot() == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_stop_cancels_only_own_queued_test_job_and_keeps_report(rehearsal):
    actor, body = rehearsal
    created = send(actor, 'owner', '/api/morning-trial/start', body)
    assert created.status_code == 200, created.text
    tid = created.json()['trial']['id']; path = f'/api/morning-trial/{tid}/action'
    accepted = send(actor, 'branch', path, {'action': 'accept'})
    assert accepted.status_code == 200, accepted.text
    with db.tx() as c:
        c.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,kind,payload,requested_by) VALUES('unrelated-test','kps',%s,'rehearsal-device','test','{}','ops-owner')", (B2,))
    stopped = send(actor, 'owner', path, {'action': 'stop'})
    assert stopped.status_code == 200 and stopped.json()['trial']['status'] == 'stopped'
    assert stopped.json()['print_job']['status'] == 'cancelled'
    assert actor('driver').get(f'/api/morning-trial/{tid}').status_code == 200
    with db.tx() as c:
        assert c.execute("SELECT status FROM branch_print_jobs WHERE id='unrelated-test'").fetchone()['status'] == 'queued'


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_setup_discloses_only_exact_eligible_account_choices_to_owner(rehearsal):
    actor, _ = rehearsal
    result = actor('owner').get('/api/morning-trial/setup')
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['branch']['id'] == B2
    assert [row['id'] for row in data['managers']] == ['ops-manager']
    assert [row['id'] for row in data['preparers']] == ['ops-branch']
    assert {row['id'] for row in data['drivers']} == {'ops-driver'}
    assert actor('branch').get('/api/morning-trial/setup').status_code == 403

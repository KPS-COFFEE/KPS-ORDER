"""Registered work phones are visible only in the owning brand's owner policy."""
import pytest
from app import auth, db
from test_operations import ops
from test_morning_manager_context_postgres import morning_manager

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_policy_employee_identity_is_owner_only_and_never_changes_policy(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        c.execute("UPDATE users SET phone='+966531049596' WHERE id='ops-manager'")
        c.execute("UPDATE users SET phone='+966534301235' WHERE id='ops-branch'")
        before = c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings']
    owner = actor('owner').get('/api/morning-delivery/policy')
    assert owner.status_code == 200, owner.text
    people = {u['id']: u for u in owner.json()['users']}
    assert people['ops-manager']['phone'] == '+966531049596'
    assert people['ops-branch']['phone'] == '+966534301235'
    for role in ('manager', 'branch', 'driver', 'customer'):
        denied = actor(role).get('/api/morning-delivery/policy')
        assert denied.status_code == 403, denied.text
        assert '9596' not in denied.text and '1235' not in denied.text
    foreign = actor('other').get('/api/morning-delivery/policy')
    assert foreign.status_code == 200, foreign.text
    assert not {'ops-manager', 'ops-branch'} & {u['id'] for u in foreign.json()['users']}
    client = actor('owner')
    client.app.dependency_overrides.pop(auth.current_user, None)
    assert client.get('/api/morning-delivery/policy').status_code == 401
    with db.tx() as c:
        assert c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings'] == before


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_real_morning_manager_can_only_read_own_brand_bell_cadence(morning_manager):
    client, _, _ = morning_manager
    cadence = {'first_after_seconds': 2, 'second_after_seconds': 12,
               'continuous_after_seconds': 30, 'first_ring_seconds': 2, 'second_ring_seconds': 2}
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'", (db.json({'alert_policy': cadence}),))
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-other',%s) ON CONFLICT(brand_id) DO UPDATE SET settings=excluded.settings",
                  (db.json({'alert_policy': cadence | {'continuous_after_seconds': 999}}),))
    read = client.get('/api/operations/alert-policy', params={'brand_id': 'ops-other'})
    assert read.status_code == 200, read.text
    assert read.json() == {'mandatory': True, 'policy': cadence}
    denied = client.patch('/api/admin/alert-policy', json=cadence,
                          headers={'Idempotency-Key': 'morning-no-bell-write'})
    assert denied.status_code == 403, denied.text
    assert client.get('/api/operations/alerts').status_code == 200
    assert client.get('/api/admin/config').status_code == 403
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closed' WHERE id='ctx-duty'")
    assert client.get('/api/operations/alert-policy').status_code == 403

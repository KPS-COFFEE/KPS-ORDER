"""Central routing witnesses use real isolated PostgreSQL and canonical sessions."""
import pytest

from app import db
from test_enbishan_dual_brand import dual


@pytest.fixture
def central(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    return dual


@pytest.mark.postgres
def test_session_publishes_both_granted_branch_sets_without_projecting_user(central):
    _, actor, _, _, _ = central
    response = actor('manager').get('/api/session')
    assert response.status_code == 200, response.text
    user = response.json()['user']
    assert user['brand_id'] == 'kps'
    assert user['branch_ids'] == ['kps-branch-1']
    scopes = {b['id']: b for b in user['central_operations']['brands']}
    assert scopes['kps']['branch_ids'] == ['kps-branch-1']
    assert set(scopes['enbishan']['branch_ids']) == {'enbishan-test-1', 'enbishan-test-2'}


@pytest.mark.postgres
def test_resource_resolution_rechecks_membership_revocation(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    path = '/api/central-operations/resource-context?kind=order&resource_id=' + identifier
    assert actor('manager').get(path).json() == {'brand_id': 'enbishan'}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get(path).status_code == 404
    session = actor('manager').get('/api/session').json()['user']
    assert [b['id'] for b in session['central_operations']['brands']] == ['kps']


@pytest.mark.postgres
def test_a_brand_membership_does_not_authorize_ungranted_branches(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]' WHERE user_id='dual-manager' AND brand_id='enbishan'")
    path = '/api/central-operations/resource-context?kind=order&resource_id=' + identifier
    assert actor('manager').get(path).status_code == 404


@pytest.mark.postgres
def test_unassigned_driver_cannot_resolve_customer_orders(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    assert actor('driver').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': identifier,
    }).status_code == 404


@pytest.mark.postgres
def test_customers_do_not_receive_or_resolve_staff_context(central):
    _, actor, _, _, order = central
    identifier = order('enbishan')
    session = actor('enbishan-customer').get('/api/session').json()['user']
    assert 'central_operations' not in session
    assert actor('enbishan-customer').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': identifier,
    }).status_code == 404


@pytest.mark.postgres
def test_rollout_remains_off_until_explicitly_enabled(dual):
    _, actor, _, _, order = dual
    session = actor('manager').get('/api/session').json()['user']
    assert 'central_operations' not in session
    assert actor('manager').get('/api/central-operations/resource-context', params={
        'kind': 'order', 'resource_id': order('enbishan'),
    }).status_code == 404

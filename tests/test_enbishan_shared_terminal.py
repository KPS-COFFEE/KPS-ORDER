"""One physical driver terminal, explicit current brand/branch authority."""
import pytest
from app import db
from test_enbishan_dual_brand import dual

pytestmark = pytest.mark.postgres


def test_shared_driver_terminal_is_visible_in_both_authorized_brands(dual):
    _, actor, post, _, _ = dual
    path = '/api/drivers/dual-driver/payment-terminal'
    body = {'provider': 'SYNTHETIC', 'terminal_id': 'TEST-ONLY', 'reason': 'Synthetic shared device'}
    post('manager', path, body, method='put')
    for brand in ('kps', 'enbishan'):
        for role in ('manager', 'driver'):
            response = actor(role, brand).get(path)
            assert response.status_code == 200, response.text
            assert response.json()['payment_terminal']['terminal_id'] == 'TEST-ONLY'
    post('manager', path, {**body, 'terminal_id': 'TEST-ONLY-UPDATED'}, 'enbishan', method='put')
    assert actor('driver').get(path).json()['payment_terminal']['terminal_id'] == 'TEST-ONLY-UPDATED'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE id='dual-driver'").fetchone()['n'] == 1


def test_shared_terminal_revocation_and_branch_scope_apply_to_replays(dual):
    _, actor, post, _, _ = dual
    path = '/api/drivers/dual-driver/payment-terminal'
    body = {'provider': 'SYNTHETIC', 'terminal_id': 'TEST-ONLY', 'reason': 'Synthetic scope check'}
    post('manager', path, body, 'enbishan', method='put', key='terminal-scope')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-manager' AND brand_id='enbishan'", (db.json(['enbishan-test-1']),))
    assert actor('manager', 'enbishan').get(path).status_code == 403
    post('manager', path, body, 'enbishan', method='put', key='terminal-scope', status=403)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    assert actor('owner', 'enbishan').get(path).status_code == 404
    assert actor('driver').get(path).status_code == 200

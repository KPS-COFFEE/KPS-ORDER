"""Named response teams, real isolated DB, synthetic SMS only."""
import base64
import io
from uuid import uuid4

import pytest
from PIL import Image
from app import auth, db, employee_permissions, customer_message_routing as routing
from tests.test_core import _make_user, _authenticate
from tests.sms_policy_helpers import sms_step_up
from test_enbishan_dual_brand import dual

PATH = '/api/identity/customer-message-routing'


def actor(client, user):
    client.cookies.clear()
    client.headers['X-KPS-Context'] = 'customer' if user['role'] == 'customer' else 'work'
    _authenticate(client, user)
    return client


def save(client, delivery, care, expected=None):
    return client.put(PATH, json={'routing': {'kps': {'delivery': delivery, 'care': care}},
                                  'expected': {'kps': expected}, 'reason': 'Synthetic owner routing approval'})


def message(customer, category='help', route='delivery', branch='kps-branch-1', brand='kps'):
    cid = uuid4().hex
    with db.tx() as c:
        c.execute('INSERT INTO conversations(id,brand_id,branch_id,customer_id,route,context_service,category) '
                  "VALUES(%s,%s,%s,%s,%s,'delivery',%s)", (cid, brand, branch, customer['id'], route, category))
        c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',
                  (uuid4().hex, cid, customer['id'], 'SYNTHETIC customer message'))
    return cid


@pytest.fixture
def configured(client, monkeypatch):
    people = {name: _make_user(role, branches=['kps-branch-1']) for name, role in [
        ('owner', 'owner'), ('customer', 'customer'), ('other_customer', 'customer'),
        ('ahmed', 'delivery_manager'), ('ali', 'delivery_manager'),
        ('fawzi', 'admin'), ('general_manager', 'delivery_manager'),
        ('unselected_manager', 'delivery_manager'), ('driver', 'driver')]}
    delivery = [people[name]['id'] for name in ('ahmed', 'ali', 'fawzi', 'general_manager')]
    care = [people[name]['id'] for name in ('fawzi', 'general_manager')]
    actor(client, people['owner'])
    assert save(client, delivery, care).status_code == 403
    sms_step_up(client, people['owner'], monkeypatch)
    result = save(client, delivery, care)
    assert result.status_code == 200, result.text
    return people, result.json()['routing']['kps']


@pytest.mark.parametrize('category', ['privacy', 'complaint', 'refund', 'other', 'suggestion'])
def test_care_category_wins_over_delivery_route(category):
    assert routing.team({'route': 'delivery', 'category': category}) == 'care'


@pytest.mark.postgres
def test_grant_is_sms_guarded_audited_and_preserves_role_branches_and_financial_permissions(client, configured):
    people, current = configured
    with db.tx() as c:
        target = c.execute('SELECT * FROM users WHERE id=%s', (people['fawzi']['id'],)).fetchone()
        assert target['role'] == 'admin' and target['branch_ids'] == ['kps-branch-1']
        assert target['permissions'] == people['fawzi']['permissions']
        assert target['extra_permissions'] == people['fawzi']['extra_permissions']
        assert len(c.execute("SELECT id FROM audit_events WHERE action='customer_message_routing.updated'").fetchall()) == 1
    actor(client, people['fawzi'])
    permissions = set(client.get('/api/session').json()['user']['effective_permissions'])
    assert permissions == employee_permissions.effective(people['fawzi']) | routing.CAPABILITIES
    assert client.put(PATH, json={'routing': {'kps': current}, 'expected': {'kps': current}, 'reason': 'No owner access'}).status_code == 403


@pytest.mark.postgres
@pytest.mark.parametrize('name', ['ahmed', 'ali', 'fawzi', 'general_manager', 'unselected_manager', 'driver'])
def test_named_team_scope_matches_inbox_alerts_and_direct_messages(client, configured, name):
    people, _ = configured
    delivery = message(people['customer'])
    complaint = message(people['customer'], category='complaint')
    privacy = message(people['customer'], category='privacy', route='privacy', branch=None)
    outside = message(people['customer'], branch='kps-branch-2')
    actor(client, people[name])
    if name == 'driver':
        assert client.get('/api/conversations').status_code == 403
        assert client.get('/api/conversations/'+delivery+'/messages').status_code == 403
        return
    expected = {delivery, complaint, privacy} if name in ('fawzi', 'general_manager') else {delivery} if name in ('ahmed', 'ali') else set()
    inbox = client.get('/api/conversations')
    assert inbox.status_code == 200, inbox.text
    assert {row['id'] for row in inbox.json()['conversations']} == expected
    assert {row['id'] for row in client.get('/api/conversations/alerts').json()['awaiting_reply']} == expected
    for cid in (delivery, complaint, privacy, outside):
        assert client.get('/api/conversations/'+cid+'/messages').status_code == (200 if cid in expected else 404)


@pytest.mark.postgres
def test_care_can_reply_claim_close_and_read_images_but_not_transfer_private_history(client, configured):
    people, _ = configured
    cid = message(people['customer'], category='privacy', route='privacy', branch=None)
    actor(client, people['fawzi'])
    path = '/api/conversations/'+cid
    image = io.BytesIO(); Image.new('RGB', (2, 2), 'white').save(image, 'PNG')
    uploaded = client.post(path+'/attachments', json={'content_type': 'image/png', 'data_base64': base64.b64encode(image.getvalue()).decode()}, headers={'Idempotency-Key': uuid4().hex})
    assert uploaded.status_code == 200, uploaded.text
    aid = uploaded.json()['id']
    assert client.post(path+'/messages', json={'text': 'SYNTHETIC response', 'attachment_id': aid}, headers={'Idempotency-Key': uuid4().hex}).status_code == 200
    assert client.post(path+'/claim', json={}, headers={'Idempotency-Key': uuid4().hex}).status_code == 200
    assert client.post(path+'/read', json={'through_seq': 999999}).status_code == 200
    assert client.post(path+'/status', json={'status': 'resolved'}, headers={'Idempotency-Key': uuid4().hex}).status_code == 200
    assert client.get('/api/chat-attachments/'+aid+'?context=work').status_code == 200
    assert client.post(path+'/transfer', json={'target': 'delivery', 'reason': 'Do not expose privacy'}, headers={'Idempotency-Key': uuid4().hex}).status_code == 403
    actor(client, people['ahmed'])
    assert client.get('/api/chat-attachments/'+aid+'?context=work').status_code == 404
    actor(client, people['customer'])
    assert client.get(path+'/messages').status_code == 200
    actor(client, people['other_customer'])
    assert client.get(path+'/messages').status_code == 404


@pytest.mark.postgres
def test_owner_supervision_and_uncovered_message_alerts(client, configured):
    people, _ = configured
    delivery = message(people['customer'])
    privacy = message(people['customer'], category='privacy', route='privacy', branch=None)
    outside = message(people['customer'], branch='kps-branch-2')
    actor(client, people['owner'])
    assert {r['id'] for r in client.get('/api/conversations').json()['conversations']} == {delivery, privacy, outside}
    assert {r['id'] for r in client.get('/api/conversations/alerts').json()['awaiting_reply']} == {outside}
    with db.tx() as c:
        c.execute('UPDATE users SET active=false WHERE id=ANY(%s)', ([people[n]['id'] for n in ('fawzi', 'general_manager')],))
    assert {r['id'] for r in client.get('/api/conversations/alerts').json()['awaiting_reply']} == {privacy, outside}


@pytest.mark.postgres
def test_live_revocation_blocks_same_session_and_idempotent_replay(client, configured):
    people, current = configured
    cid = message(people['customer'])
    actor(client, people['ahmed'])
    path = '/api/conversations/'+cid+'/messages'
    headers = {'Idempotency-Key': uuid4().hex}
    body = {'text': 'SYNTHETIC prior response'}
    assert client.post(path, json=body, headers=headers).status_code == 200
    with db.tx() as c:
        reduced = {**current, 'delivery': [people['general_manager']['id']]}
        c.execute('UPDATE app_settings SET settings=settings||%s::jsonb WHERE brand_id=%s', (db.json({routing.KEY: reduced}), 'kps'))
    assert client.get(path).status_code == 404
    assert client.post(path, json=body, headers=headers).status_code == 404
    assert client.get('/api/conversations/alerts').json()['awaiting_reply'] == []


@pytest.mark.postgres
def test_reject_stale_wrong_role_and_empty_groups(client, configured, monkeypatch):
    people, current = configured
    actor(client, people['owner']); sms_step_up(client, people['owner'], monkeypatch)
    assert save(client, current['delivery'], current['care']).status_code == 409
    assert save(client, [people['driver']['id']], current['care'], current).status_code == 422
    assert save(client, [], current['care'], current).status_code == 422
    with db.tx() as c:
        assert routing.config(c, 'kps') == current


@pytest.mark.postgres
def test_without_configuration_legacy_policy_is_unchanged(client):
    admin = _make_user('admin', branches=['kps-branch-1'])
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    customer = _make_user('customer')
    cid = message(customer, category='privacy', route='privacy', branch=None)
    actor(client, admin)
    assert client.get('/api/conversations').status_code == 403
    actor(client, manager)
    assert client.get('/api/conversations/'+cid+'/messages').status_code == 404


@pytest.mark.postgres
def test_both_brands_share_identity_without_leaking_revoked_or_unassigned_scope(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    client, use, _, _, _ = dual
    selection = {'delivery': ['dual-manager', 'dual-accountant'], 'care': ['dual-accountant']}
    body = {'routing': {brand: selection for brand in ('kps', 'enbishan')},
            'expected': {'kps': None, 'enbishan': None}, 'reason': 'Synthetic shared team owner approval'}
    response = use('owner').put(PATH, json=body)
    assert response.status_code == 200, response.text
    cid = message({'id': 'dual-enbishan-customer'}, category='privacy', route='privacy', branch=None, brand='enbishan')
    url = '/api/conversations/'+cid+'/messages'
    assert use('accountant', 'enbishan').get(url).status_code == 200
    assert use('manager', 'enbishan').get(url).status_code == 404
    assert use('accountant').get('/api/central-operations/resource-context', params={'kind': 'conversation', 'resource_id': cid}).status_code == 200
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-accountant' AND brand_id='enbishan'")
    assert use('accountant').get(url).status_code == 404
    assert use('accountant').get('/api/central-operations/resource-context', params={'kind': 'conversation', 'resource_id': cid}).status_code == 404
    assert cid in {r['id'] for r in use('owner', 'enbishan').get('/api/conversations/alerts').json()['awaiting_reply']}
    # A partially scoped foreign owner cannot manage whole-brand recipients
    # or read a branchless request.
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-1\"]' WHERE user_id='dual-owner' AND brand_id='enbishan'")
    assert use('owner').put(PATH, json={'routing': {'enbishan': selection}, 'expected': {'enbishan': selection}, 'reason': 'Cannot administer whole brand'}).status_code == 403
    assert use('owner', 'enbishan').get(url).status_code == 404

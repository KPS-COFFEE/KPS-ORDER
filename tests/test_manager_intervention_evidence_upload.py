"""Real scoped evidence upload for a manager-created, unverified guest order.

Use the existing HTTP conversation/upload contract without sending a chat
message. Fixtures own an isolated PostgreSQL schema and synthetic identities.
"""
import base64
import io
from uuid import uuid4

import pytest
from PIL import Image

from app import db
from test_enbishan_dual_brand import dual


pytestmark = pytest.mark.postgres


def guest_center_order():
    guest_id = 'evidence-guest-' + uuid4().hex
    order_id = 'evidence-order-' + uuid4().hex
    snapshot = {
        'journey_controls_version': 1,
        'order_source': 'delivery_center',
        'created_by_staff_id': 'dual-manager',
        'delivery_workflow': 'manager_dispatch',
        'items': [],
        'branch_name': 'TEST ONLY evidence branch',
    }
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO users(id,display_name,role,brand_id,identity_kind,phone) "
            "VALUES(%s,'TEST ONLY unverified guest','customer','enbishan','guest','+966500009901')",
            (guest_id,),
        )
        conn.execute(
            "INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,"
            "delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
            "VALUES(%s,'enbishan','enbishan-test-1',%s,'delivery','ready','pending','cash',1000,0,1000,%s,%s)",
            (order_id, guest_id, db.json(snapshot), db.json({'name': 'TEST ONLY guest', 'identity_kind': 'guest'})),
        )
    return order_id, guest_id


def png_payload():
    buffer = io.BytesIO()
    Image.new('RGB', (24, 18), 'orange').save(buffer, 'PNG')
    data = buffer.getvalue()
    return data, {'content_type': 'image/png', 'data_base64': base64.b64encode(data).decode()}


def upload_evidence(post, order_id):
    created = post('manager', '/api/conversations', {'order_id': order_id}, brand='enbishan')
    conversation = created['conversation']
    data, payload = png_payload()
    uploaded = post('manager', f"/api/conversations/{conversation['id']}/attachments", payload, brand='enbishan')
    assert set(uploaded) == {'id', 'url'}
    assert uploaded['url'] == '/api/chat-attachments/' + uploaded['id'] + '?context=work'
    return conversation, uploaded, data


def evidence_counts(order_id):
    with db.tx() as conn:
        return {
            'attachments': conn.execute(
                'SELECT count(*) n FROM chat_attachments a JOIN conversations c ON c.id=a.conversation_id '
                'WHERE c.order_id=%s', (order_id,),
            ).fetchone()['n'],
            'messages': conn.execute(
                'SELECT count(*) n FROM chat_messages m JOIN conversations c ON c.id=m.conversation_id '
                'WHERE c.order_id=%s', (order_id,),
            ).fetchone()['n'],
        }


def test_manager_upload_for_enbishan_guest_is_usable_without_customer_message(dual, monkeypatch):
    _, actor, post, _, _ = dual
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    order_id, guest_id = guest_center_order()
    context = actor('manager').get('/api/central-operations/resource-context',
                                   params={'kind': 'order', 'resource_id': order_id})
    assert context.status_code == 200, context.text
    assert context.json()['brand_id'] == 'enbishan'
    with db.tx() as conn:
        original = conn.execute('SELECT * FROM orders WHERE id=%s', (order_id,)).fetchone()
    conversation, uploaded, data = upload_evidence(post, order_id)
    assert conversation['customer_id'] == guest_id
    assert conversation['brand_id'] == 'enbishan'
    assert conversation['branch_id'] == 'enbishan-test-1'
    assert conversation['route'] == 'delivery'
    assert evidence_counts(order_id) == {'attachments': 1, 'messages': 0}
    assert actor('manager', 'enbishan').get(uploaded['url']).content == data
    path = f'/api/order-interventions/orders/{order_id}'
    view = actor('manager', 'enbishan').get(path)
    assert view.status_code == 200, view.text
    assert uploaded['id'] in {image['id'] for image in view.json()['attachments']}
    body = {'kind': 'cancel', 'reason_code': 'customer_cancel',
            'version': view.json()['version'], 'evidence_attachment_id': uploaded['id']}
    requested = post('manager', path, body, brand='enbishan')
    other_order, _ = guest_center_order()
    rejected = post('manager', f'/api/order-interventions/orders/{other_order}', body,
                    brand='enbishan', status=422)
    assert rejected['detail']['code'] == 'CONVERSATION_IMAGE_REQUIRED'
    with db.tx() as conn:
        event = conn.execute('SELECT actor_id,details FROM order_events WHERE id=%s',
                             (requested['id'],)).fetchone()
        assert event['actor_id'] == 'dual-manager'
        assert event['details']['evidence_attachment_id'] == uploaded['id']
        saved = conn.execute('SELECT uploader_id,data FROM chat_attachments WHERE id=%s',
                             (uploaded['id'],)).fetchone()
        assert saved['uploader_id'] == 'dual-manager' and bytes(saved['data']) == data
        assert conn.execute('SELECT * FROM orders WHERE id=%s', (order_id,)).fetchone() == original
        guest = conn.execute('SELECT identity_kind,phone_verified_at,phone_sms_verified_at FROM users WHERE id=%s',
                             (guest_id,)).fetchone()
        assert guest == {'identity_kind': 'guest', 'phone_verified_at': None, 'phone_sms_verified_at': None}
    assert evidence_counts(order_id)['messages'] == 0


@pytest.mark.parametrize('operation', ['create', 'upload'])
def test_explicit_reply_denial_blocks_evidence_write(dual, operation):
    _, _, post, _, _ = dual
    order_id, _ = guest_center_order()
    path, body = '/api/conversations', {'order_id': order_id}
    if operation == 'upload':
        conversation = post('manager', path, body, brand='enbishan')['conversation']
        path = f"/api/conversations/{conversation['id']}/attachments"
        _, body = png_payload()
    with db.tx() as conn:
        conn.execute("UPDATE users SET permission_denials=%s WHERE id='dual-manager'",
                     (db.json(['chat_reply']),))
    rejected = post('manager', path, body, brand='enbishan', status=403)
    assert rejected['detail']['code'] == 'STAFF_PERMISSION_FORBIDDEN'
    assert evidence_counts(order_id) == {'attachments': 0, 'messages': 0}


@pytest.mark.parametrize('restriction', ['wrong_brand', 'revoked_membership', 'revoked_branch'])
def test_evidence_upload_rechecks_brand_and_current_membership(dual, restriction):
    _, _, post, _, _ = dual
    order_id, _ = guest_center_order()
    conversation = post('manager', '/api/conversations', {'order_id': order_id}, brand='enbishan')['conversation']
    brand, status = 'enbishan', 404
    if restriction == 'wrong_brand':
        brand = 'kps'
    else:
        with db.tx() as conn:
            if restriction == 'revoked_membership':
                conn.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
                status = 403
            else:
                conn.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-manager' AND brand_id='enbishan'",
                             (db.json(['enbishan-test-2']),))
    _, payload = png_payload()
    post('manager', '/api/conversations', {'order_id': order_id}, brand=brand, status=status)
    post('manager', f"/api/conversations/{conversation['id']}/attachments", payload, brand=brand, status=status)
    assert evidence_counts(order_id) == {'attachments': 0, 'messages': 0}


def test_returned_customer_cancellation_accepts_uploaded_evidence_and_preserves_original(dual):
    _, actor, post, _, _ = dual
    order_id, _ = guest_center_order()
    base = f'/api/order-interventions/orders/{order_id}'
    requested = post('manager', base, {'kind': 'cancel', 'reason_code': 'no_answer',
                                      'contact_result': 'no_answer', 'version': 1}, brand='enbishan')
    path = base + '/' + requested['id']
    with db.tx() as conn:
        original = conn.execute('SELECT * FROM order_events WHERE id=%s', (requested['id'],)).fetchone()
    post('owner', path + '/return', {
        'version': 1, 'workflow_revision': 0, 'corrected_reason_code': 'customer_cancel',
        'target_manager_id': 'dual-manager', 'note': 'TEST ONLY: preserve the actual reason and attach the external request.',
    }, brand='enbishan')
    body = {'version': 1, 'workflow_revision': 1, 'contact_result': 'agreed',
            'note': 'TEST ONLY: external customer cancellation evidence attached.'}
    rejected = post('manager', path + '/resubmit', body, brand='enbishan', status=422)
    assert rejected['detail']['code'] == 'CONVERSATION_IMAGE_REQUIRED'
    _, uploaded, _ = upload_evidence(post, order_id)
    result = post('manager', path + '/resubmit', {**body, 'evidence_attachment_id': uploaded['id']}, brand='enbishan')
    assert result == {'id': requested['id'], 'status': 'resubmitted', 'workflow_revision': 2}
    view = actor('manager', 'enbishan').get(base).json()['requests'][0]
    assert view['workflow']['original_reason_code'] == 'no_answer'
    assert view['workflow']['reason_code'] == 'customer_cancel'
    assert view['workflow']['status'] == 'resubmitted'
    assert evidence_counts(order_id) == {'attachments': 1, 'messages': 0}
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM order_events WHERE id=%s', (requested['id'],)).fetchone() == original
        resubmitted = conn.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.resubmitted'",
                                   (order_id,)).fetchone()['details']
        assert resubmitted['proof']['evidence_attachment_id'] == uploaded['id']
        order = conn.execute('SELECT preparation_status,payment_status,total_halalas,version FROM orders WHERE id=%s',
                             (order_id,)).fetchone()
        assert order == {'preparation_status': 'ready', 'payment_status': 'unpaid', 'total_halalas': 1000, 'version': 1}

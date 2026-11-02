"""Image requests select credentials without bypassing live chat authorization."""
import base64
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from PIL import Image

from app import auth, db
from tests.test_core import _make_user
from tests.test_context_cookie_isolation import establish
from test_enbishan_dual_brand import dual


@pytest.fixture(autouse=True)
def context_mode(monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED', 'true')
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED', 'false')


@pytest.mark.parametrize('path,method,query,header,expected', [
    ('/api/chat-attachments/test-image', 'GET', 'work', None, 'work'),
    ('/api/chat-attachments/test-image', 'GET', 'customer', None, 'customer'),
    ('/api/chat-attachments/test-image', 'HEAD', 'work', None, 'work'),
    ('/api/chat-attachments/test-image', 'GET', 'customer', 'work', 'work'),
    ('/api/chat-attachments/test-image', 'GET', None, None, None),
    ('/api/session', 'GET', 'work', None, None),
    ('/api/conversations', 'GET', 'work', None, None),
    ('/api/chat-attachments/test-image', 'POST', 'work', None, None),
    ('/api/chat-attachments/test-image/nested', 'GET', 'work', None, None),
])
def test_binary_context_hint_is_narrow(path, method, query, header, expected):
    scope = {'type': 'http', 'method': method, 'path': path,
             'query_string': ('context=' + query).encode() if query else b'',
             'headers': [(b'x-kps-context', header.encode())] if header else []}
    assert auth.session_context(Request(scope)) == expected


def test_invalid_image_context_is_rejected():
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/chat-attachments/test-image',
                       'query_string': b'context=owner', 'headers': []})
    with pytest.raises(HTTPException) as error:
        auth.session_context(request)
    assert error.value.status_code == 400


def image_bytes():
    image = io.BytesIO()
    Image.new('RGB', (24, 18), 'orange').save(image, 'PNG')
    return image.getvalue()


def create_image_conversation(post):
    """Use the real conversation, upload and message APIs, with synthetic bytes."""
    created = post('/api/conversations', {'branch_id': 'kps-branch-1', 'service': 'delivery',
                                        'details': 'TEST ONLY attachment session'})
    cid = created['conversation']['id']
    picture = image_bytes()
    uploaded = post(f'/api/conversations/{cid}/attachments', {
        'content_type': 'image/png', 'data_base64': base64.b64encode(picture).decode()})
    sent = post(f'/api/conversations/{cid}/messages', {'attachment_id': uploaded['id']})
    assert uploaded['url'].endswith('?context=customer')
    assert sent['message']['attachment_url'] == uploaded['url']
    return cid, uploaded, picture


@pytest.fixture
def image_conversation(client, monkeypatch):
    customer = _make_user('customer')
    personal = establish(client, customer, monkeypatch)
    client.headers.update({'X-KPS-Context': 'customer', 'X-CSRF-Token': personal['csrf_token']})

    def post(path, body):
        response = client.post(path, json=body, headers={'Idempotency-Key': str(uuid4())})
        assert response.status_code == 200, response.text
        return response.json()

    cid, uploaded, picture = create_image_conversation(post)
    client.headers.pop('X-KPS-Context', None)
    client.headers.pop('X-CSRF-Token', None)
    return customer, cid, uploaded, picture


@pytest.mark.postgres
@pytest.mark.parametrize('role,branches,permitted', [
    ('owner', [], True),
    ('delivery_manager', ['kps-branch-1'], True),
    ('delivery_staff', ['kps-branch-1'], True),
    ('delivery_manager', ['kps-branch-2'], False),
    ('branch_manager', ['kps-branch-1'], False),
    ('driver', ['kps-branch-1'], False),
])
def test_native_image_request_preserves_staff_scope(client, monkeypatch, image_conversation, role, branches, permitted):
    _, cid, uploaded, picture = image_conversation
    worker = _make_user(role, branches=branches)
    establish(client, worker, monkeypatch)
    client.headers.pop('X-KPS-Context', None)
    work_url = uploaded['url'].replace('context=customer', 'context=work')
    response = client.get(work_url)
    if permitted:
        assert response.status_code == 200
        assert response.content == picture
        assert response.headers['content-type'] == 'image/png'
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        history = client.get(f'/api/conversations/{cid}/messages', headers={'X-KPS-Context': 'work'})
        assert history.status_code == 200
        attachment_message = next(message for message in history.json()['messages']
                                  if message['attachment_id'] == uploaded['id'])
        assert attachment_message['attachment_url'] == work_url
        # Removing branch authorization takes effect even for an existing URL.
        with db.tx() as c:
            c.execute('UPDATE users SET active=false WHERE id=%s', (worker['id'],))
        assert client.get(work_url).status_code == 401
    else:
        assert response.status_code in (403, 404)
    # The independent, owning customer's cookie still works without a header.
    assert client.get(uploaded['url']).content == picture


@pytest.mark.postgres
def test_same_jar_image_context_never_falls_back_to_another_identity(client, monkeypatch, image_conversation):
    _, _, uploaded, picture = image_conversation
    owner = _make_user('owner')
    establish(client, owner, monkeypatch)
    work_url = uploaded['url'].replace('context=customer', 'context=work')
    stranger = _make_user('customer')
    establish(client, stranger, monkeypatch)
    client.headers.pop('X-KPS-Context', None)
    assert client.get(work_url).content == picture
    assert client.get(uploaded['url']).status_code == 404
    with db.tx() as c:
        c.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s', (owner['id'],))
    assert client.get(work_url).status_code == 401
    assert client.get(uploaded['url']).status_code == 404
    client.cookies.clear()
    assert client.get(work_url).status_code == 401
    assert client.get(uploaded['url']).status_code == 401


@pytest.mark.postgres
def test_context_hint_cannot_cross_brands(client, monkeypatch, image_conversation):
    _, _, uploaded, _ = image_conversation
    # The synthetic foreign brand uses the same mandatory SMS flow as KPS.
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED', 'true')
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('attachment-test-other','TEST ONLY')")
    foreign_owner = _make_user('owner', brand='attachment-test-other')
    establish(client, foreign_owner, monkeypatch)
    client.headers.pop('X-KPS-Context', None)
    assert client.get(uploaded['url'].replace('context=customer', 'context=work')).status_code == 404


@pytest.mark.postgres
@pytest.mark.parametrize('revocation',['membership','branch'])
def test_native_foreign_brand_image_rebuilds_live_membership_without_central_ui(dual,monkeypatch,revocation):
    _,actor,_,_,order=dual
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
    oid=order('enbishan');cid='image-conv-'+uuid4().hex;aid='image-'+uuid4().hex
    picture=image_bytes()
    import hashlib
    with db.tx() as conn:
        conn.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route) VALUES(%s,%s,'enbishan','enbishan-test-1','dual-enbishan-customer','delivery')",(cid,oid))
        conn.execute("INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,'dual-enbishan-customer','image/png',%s,%s,%s)",(aid,cid,len(picture),hashlib.sha256(picture).hexdigest(),picture))
    client=actor('manager')
    result=client.get('/api/chat-attachments/'+aid+'?context=work')
    assert result.status_code==200,result.text
    assert result.content==picture
    with db.tx() as conn:
        if revocation=='membership':
            conn.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
        else:
            conn.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]' WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get('/api/chat-attachments/'+aid+'?context=work').status_code==404

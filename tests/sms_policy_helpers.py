"""Canonical synthetic SMS fixtures; no real provider I/O or proof shortcuts.

Call only with the isolated database fixture. Helpers do not alter the mandatory
policy, erase password hashes, issue tokens directly or insert step-up proofs.
"""
from datetime import datetime, timedelta, timezone
import secrets
from app import db, sms_verification


def configure_sms(monkeypatch, brand='kps'):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_security_audit')
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED', 'false')
    calls = []
    references = set()

    def start(phone, reference, lang='ar'):
        calls.append(('start', phone, reference, lang))
        references.add(reference)
        return reference, datetime.now(timezone.utc) + timedelta(minutes=5)

    def check(reference, code):
        calls.append(('check', reference, code))
        return reference in references and code == '0123'

    monkeypatch.setattr(sms_verification, 'start', start)
    monkeypatch.setattr(sms_verification, 'check', check)
    with db.tx() as conn:
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,delivery_proven_at,sms_provider) VALUES(%s,'sms','active',true,now(),'tawked') ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true,delivery_proven_at=now(),sms_provider='tawked'", (brand,))
    return calls


def register_synthetic_mobile(user, phone=None):
    # The fixture DB contains only synthetic principals. Assigning a fixture
    # contact is setup, never an assertion of phone ownership or OTP proof.
    phone = phone or user.get('phone') or '+9665' + f'{secrets.randbelow(100_000_000):08d}'
    with db.tx() as conn:
        return conn.execute('UPDATE users SET phone=%s WHERE id=%s RETURNING *', (phone,user['id'])).fetchone()


def sms_login(client, user, monkeypatch, phone=None):
    user = register_synthetic_mobile(user, phone)
    configure_sms(monkeypatch, user['brand_id'])
    request = client.post('/api/identity/staff/code/request', json={'phone':user['phone'],'brand_id':user['brand_id']})
    assert request.status_code == 200, request.text
    assert request.json()['channel'] == 'sms' and request.json()['code_length'] == 4
    result = client.post('/api/identity/staff/code/verify', json={'challenge_id':request.json()['challenge_id'],'code':'0123'})
    assert result.status_code == 200, result.text
    assert result.json()['user']['id'] == user['id']
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']
    client.headers['X-KPS-Context'] = 'work'
    return result.json()


def sms_step_up(client, user, monkeypatch, phone=None):
    register_synthetic_mobile(user, phone)
    configure_sms(monkeypatch, user['brand_id'])
    request = client.post('/api/identity/owner/sms/request')
    assert request.status_code == 200, request.text
    result = client.post('/api/identity/owner/sms/verify', json={'challenge_id':request.json()['challenge_id'],'code':'0123'})
    assert result.status_code == 200, result.text
    return result.json()

"""Official Tawked Verify adapter. Secrets are read only from server environment."""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
import httpx
from . import db


def configured():
    return bool(re.fullmatch(r'tk_(?:live|test)_[A-Za-z0-9_-]+', os.getenv('TAWKED_API_KEY', '')))


def _call(path, payload, *, idempotency_key=None):
    if not configured():
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    headers = {'Authorization': 'Bearer ' + os.environ['TAWKED_API_KEY']}
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.post('https://tawked.com/v1/verify/' + path, json=payload, headers=headers)
        if response.status_code not in {200, 201}:
            raise ValueError('provider rejected request')
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('invalid response')
        return result
    except (httpx.HTTPError, ValueError):
        # Never expose provider bodies, bearer tokens, phone numbers or OTPs.
        raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.') from None


def start(phone, reference, lang='ar'):
    if not re.fullmatch(r'\+9665[0-9]{8}', phone):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال سعودي صحيحًا.')
    result = _call('start', {'to': phone, 'channel': 'sms', 'lang': lang,
                            'reference': reference}, idempotency_key=reference)
    try:
        expiry = datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00'))
        if (result.get('status') != 'pending' or not isinstance(result.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', result['id'])
                or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
            raise ValueError('invalid verification')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise db.error(503, 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال رمز التحقق.') from None
    return result['id'], expiry


def check(reference, code):
    result = _call('check', {'id': reference, 'code': code})
    return result.get('verified') is True and result.get('status') == 'verified'

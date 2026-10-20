"""Official Tawked Verify adapter. Secrets are read only from server environment."""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
import httpx
from . import db


def brand_ready(brand):
    if brand != 'enbishan':
        return
    from . import activation
    activation.require('customer_sms', 'enbishan')
    if (os.getenv('ENBISHAN_TAWKED_SENDER') != 'ENBISHAN'
            or not os.getenv('ENBISHAN_TAWKED_SENDER_APPROVAL_REFERENCE', '').strip()
            or not re.fullmatch(r'tk_(?:live|test)_[A-Za-z0-9_-]+', os.getenv('ENBISHAN_TAWKED_API_KEY', ''))
            or os.getenv('ENBISHAN_TAWKED_API_KEY') == os.getenv('TAWKED_API_KEY')):
        raise _failure('brand_sender_unapproved', 'OTP_BRAND_SENDER_UNAPPROVED',
                       'مرسل عنبيشن بانتظار اعتماد المزود. / Enbishan sender approval is pending.')


def configured():
    return bool(re.fullmatch(r'tk_(?:live|test)_[A-Za-z0-9_-]+', os.getenv('TAWKED_API_KEY', '')))


def _failure(reason, code='OTP_PROVIDER_FAILURE', message='تعذر الاتصال بمزود التحقق. حاول لاحقًا.'):
    error = db.error(503, code, message)
    # Internal metadata only; HTTPException.detail remains unchanged.
    error.sms_failure_reason = reason
    return error


def _call(path, payload, *, idempotency_key=None, brand=None):
    brand_ready(brand)
    if brand != 'enbishan' and not configured():
        raise _failure('provider_unconfigured', 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    key_name = 'ENBISHAN_TAWKED_API_KEY' if brand == 'enbishan' else 'TAWKED_API_KEY'
    headers = {'Authorization': 'Bearer ' + os.environ[key_name]}
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.post('https://tawked.com/v1/verify/' + path, json=payload, headers=headers)
        if response.status_code not in {200, 201}:
            raise _failure('provider_rejected')
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('invalid response')
        return result
    except httpx.HTTPError:
        raise _failure('provider_transport_error') from None
    except ValueError:
        raise _failure('provider_invalid_response') from None


def start(phone, reference, lang='ar', *, brand=None):
    if not re.fullmatch(r'\+9665[0-9]{8}', phone):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال سعودي صحيحًا.')
    result = _call('start', {'to': phone, 'channel': 'sms', 'lang': lang,
                            'reference': reference}, idempotency_key=reference, brand=brand)
    try:
        expiry = datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00'))
        if (result.get('status') != 'pending' or not isinstance(result.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', result['id'])
                or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
            raise ValueError('invalid verification')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _failure('provider_invalid_response', 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال رمز التحقق.') from None
    return result['id'], expiry


def check(reference, code, *, brand=None):
    result = _call('check', {'id': reference, 'code': code}, brand=brand)
    return result.get('verified') is True and result.get('status') == 'verified'

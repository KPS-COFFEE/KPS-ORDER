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


def _failure(reason, code='OTP_PROVIDER_FAILURE', message='تعذر الاتصال بمزود التحقق. حاول لاحقًا.', *, status_code=503):
    error = db.error(status_code, code, message)
    # Internal metadata only; HTTPException.detail remains unchanged.
    error.sms_failure_reason = reason
    return error


# Exact HTTP/error pairs from https://tawked.com/en/docs. Never forward the
# provider's message, response body or headers, and never retry a rejected send.
_PROVIDER_ERRORS = {
    (429, 'rate_limited'): (429, 'OTP_PROVIDER_DESTINATION_LIMIT',
        'بلغ رقم الجوال حد رسائل التحقق لدى المزود. انتظر قبل طلب رمز جديد. / The provider mobile send limit was reached. Wait before requesting another code.'),
    (429, 'too_many_requests'): (429, 'OTP_PROVIDER_RATE_LIMIT',
        'طلبات التحقق كثيرة لدى المزود. انتظر قبل المحاولة مجددًا. / The provider request limit was reached. Wait before trying again.'),
    (429, 'ip_rate_limited'): (429, 'OTP_PROVIDER_IP_LIMIT',
        'بلغ مصدر الطلب حد رسائل التحقق لدى المزود. انتظر قبل المحاولة مجددًا. / The provider source send limit was reached. Wait before trying again.'),
    (429, 'spend_cap_reached'): (429, 'OTP_PROVIDER_SPEND_CAP',
        'بلغ التطبيق حد الإنفاق اليومي للرسائل لدى المزود. / The provider daily messaging spend cap was reached.'),
    (429, 'sandbox_quota_exceeded'): (429, 'OTP_PROVIDER_SANDBOX_LIMIT',
        'بلغ التطبيق حد رسائل الاختبار لدى المزود. يلزم مراجعة الإعدادات. / The provider sandbox send quota was reached. Review the configuration.'),
    (402, 'insufficient_credits'): (503, 'OTP_PROVIDER_BALANCE_REQUIRED',
        'رصيد خدمة التحقق لدى المزود غير كافٍ للإرسال. / The provider messaging balance is insufficient.'),
    (403, 'application_paused'): (503, 'OTP_PROVIDER_APPLICATION_PAUSED',
        'تطبيق الرسائل موقوف لدى المزود ويحتاج مراجعة المسؤول. / The provider messaging application is paused and needs administrator review.'),
    (403, 'account_not_active'): (503, 'OTP_PROVIDER_ACCOUNT_INACTIVE',
        'خدمة الرسائل غير مفعلة لدى المزود وتحتاج مراجعة المسؤول. / The provider messaging service is inactive and needs administrator review.'),
    (401, 'unauthorized'): (503, 'OTP_PROVIDER_CONFIGURATION',
        'إعداد اتصال خدمة التحقق يحتاج مراجعة المسؤول. / The verification provider configuration needs administrator review.'),
    (401, 'key_expired'): (503, 'OTP_PROVIDER_CONFIGURATION',
        'إعداد اتصال خدمة التحقق يحتاج مراجعة المسؤول. / The verification provider configuration needs administrator review.'),
    (403, 'ip_not_allowed'): (503, 'OTP_PROVIDER_CONFIGURATION',
        'إعداد اتصال خدمة التحقق يحتاج مراجعة المسؤول. / The verification provider configuration needs administrator review.'),
    (403, 'insufficient_scope'): (503, 'OTP_PROVIDER_CONFIGURATION',
        'إعداد اتصال خدمة التحقق يحتاج مراجعة المسؤول. / The verification provider configuration needs administrator review.'),
}


def _rejection(response):
    """Classify only documented pairs; unknown or malformed errors stay generic."""
    try:
        body = response.json()
    except ValueError:
        body = None
    provider_error = body.get('error') if isinstance(body, dict) else None
    known = (_PROVIDER_ERRORS.get((response.status_code, provider_error))
             if isinstance(provider_error, str) else None)
    if known:
        status, code, message = known
        return _failure('provider_rejected', code, message, status_code=status)
    return _failure('provider_rejected')


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
            raise _rejection(response)
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

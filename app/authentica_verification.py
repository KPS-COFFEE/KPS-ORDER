"""ENBISHAN-only Authentica v2 transport for the gated canonical customer adapter.

Official contract: docs.authentica.sa/api-reference/otp-verification/{send,verify}-otp.
The canonical adapter independently requires exact-release approval and reviewed
application settings before a provider selection can be used.
"""
from __future__ import annotations

import os
import re

import httpx

from . import db

_BASE_URL = 'https://api.authentica.sa/api/v2/'
_SECRET_NAME = 'AUTHENTICA_ENBISHAN_API_KEY'


def _secret(brand_id: str) -> str:
    # Never reuse the global/KPS key, even when ENBISHAN has no credential.
    key = os.getenv(_SECRET_NAME, '') if brand_id == 'enbishan' else ''
    if (not key or not key.strip() or '\r' in key or '\n' in key
            or key == os.getenv('AUTHENTICA_API_KEY')):
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ لهذا البراند.')
    return key


def _call(path: str, payload: dict, *, brand_id: str) -> dict:
    if path not in {'send-otp', 'verify-otp'}:
        raise ValueError('Unsupported Authentica operation')
    headers = {'X-Authorization': _secret(brand_id), 'Accept': 'application/json'}
    try:
        # Fixed origin, no redirects/retries or client-controlled endpoint.
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            result = client.post(_BASE_URL + path, json=payload, headers=headers)
        if result.status_code == 429:
            raise db.error(429, 'OTP_PROVIDER_RATE_LIMIT', 'طلبات تحقق كثيرة. انتظر قبل المحاولة مجددًا.')
        if result.status_code != 200:
            raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.')
        body = result.json()
        if not isinstance(body, dict):
            raise ValueError('Invalid Authentica response')
        return body
    except (httpx.HTTPError, ValueError):
        # Provider headers/body, recipient, code and key never enter the error.
        raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.') from None


def _phone(phone: str) -> None:
    if not isinstance(phone, str) or not re.fullmatch(r'\+9665[0-9]{8}', phone):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال سعودي صحيحًا.')


def send(phone: str, *, brand_id: str) -> None:
    """Accept only the documented send success; return no provider payload.

    Digit length, expiry and sender are approved application dashboard settings,
    not guessed fields appended to this public v2 payload.
    """
    _phone(phone)
    body = _call('send-otp', {'method': 'sms', 'phone': phone}, brand_id=brand_id)
    if body.get('success') is not True:
        raise db.error(503, 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال رمز التحقق.')


def verify(phone: str, code: str, *, brand_id: str) -> bool:
    """Transport result only; authentica_challenges owns canonical proof consumption.

    The runtime integration must enforce the approved application's digit
    length. This transport accepts ASCII digits and never interprets truthy
    strings or the older example's `verified` field as successful proof.
    """
    _phone(phone)
    if not isinstance(code, str) or not code or not re.fullmatch(r'[0-9]+', code):
        raise db.error(422, 'OTP_INVALID', 'أدخل رمز تحقق صحيحًا.')
    body = _call('verify-otp', {'phone': phone, 'otp': code}, brand_id=brand_id)
    return body.get('status') is True

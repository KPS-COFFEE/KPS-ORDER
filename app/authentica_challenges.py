"""Dormant ENBISHAN provider integrated with canonical customer OTP state.

Authentica verifies by phone, not by a provider challenge ID. A nonblocking
PostgreSQL phone lock therefore serializes every send/resend/verify, across
customer login, phone changes and owner delivery probes. Expiring older local
challenges before a send makes the replacement generation explicit. Dashboard
semantics are required evidence, never assumed from the public API examples.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal
from contextlib import contextmanager

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from . import activation, authentica_verification, db

_CONTRACT = 'AUTHENTICA_ENBISHAN_APPLICATION_CONTRACT'
_CONTRACT_HASH = 'AUTHENTICA_ENBISHAN_APPLICATION_CONTRACT_SHA256'


class ApplicationContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    application_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    sender: Literal['ENBISHAN']
    provider_approval_reference: str = Field(min_length=1, max_length=200, pattern=r'^[^\r\n]+$')
    settings_evidence_reference: str = Field(min_length=1, max_length=200, pattern=r'^[^\r\n]+$')
    code_length: int = Field(ge=4, le=10)
    expiry_seconds: int = Field(ge=30, le=3600)
    resend_cooldown_seconds: int = Field(ge=60, le=600)
    resend_replaces_code: StrictBool
    verification_consumes_code: StrictBool


def ready(brand: str) -> tuple[ApplicationContract, str]:
    """A dedicated key alone can never approve or select this application."""
    if brand != 'enbishan':
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ لهذا البراند.')
    activation.require('customer_sms', brand)
    activation.require('authentica_customer_sms', brand)
    raw = os.getenv(_CONTRACT, '')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    try:
        contract = ApplicationContract.model_validate_json(raw)
        if (not re.fullmatch(r'[a-f0-9]{64}', os.getenv(_CONTRACT_HASH, ''))
                or digest != os.getenv(_CONTRACT_HASH)
                or not contract.resend_replaces_code or not contract.verification_consumes_code):
            raise ValueError('unreviewed application semantics')
    except (ValidationError, ValueError):
        # Validation errors can contain the input; never include them in a log
        # or exception chain, even though the contract contains no credential.
        raise db.error(503, 'OTP_APPLICATION_CONTRACT_UNVERIFIED',
                       'إعدادات تطبيق التحقق بانتظار الاعتماد. / Verification application settings await approval.') from None
    authentica_verification._secret(brand)
    return contract, digest


@contextmanager
def _locked_phone(phone):
    # Session lock survives the durable pre-send commit. An xact lock would
    # leave an unsafe crash window between commit and the external request.
    key = 'authentica:enbishan:' + phone
    with db.pooled_connection() as conn:
        row = conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (key,)).fetchone()
        if not row['locked']:
            raise db.error(429, 'OTP_REQUEST_IN_PROGRESS', 'طلب تحقق قيد التنفيذ. انتظر قليلًا. / A verification request is in progress. Wait briefly.')
        try:
            yield conn
        finally:
            # Never return a possibly locked connection to another request.
            # Closing the session is the safe fallback if release is uncertain.
            try:
                released = conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0)) AS released', (key,)).fetchone()
                if not released or released['released'] is not True:
                    raise RuntimeError('Verification serialization lock release failed.')
            except BaseException:
                conn.close()
                raise


def _channel(conn, request):
    from . import auth
    from .storefront_scope import require_brand
    require_brand('enbishan')
    if os.getenv('OTP_CHANNEL', 'sms') != 'sms':
        raise db.error(503, 'OTP_CHANNEL_UNAVAILABLE', 'مزود عنبيشن مخصص لرسائل SMS. / The Enbishan provider requires SMS.')
    conn.execute("SELECT channel FROM identity_channels WHERE brand_id='enbishan' AND channel='sms' FOR UPDATE").fetchone()
    row = auth._channel(conn, 'enbishan', 'sms')
    # Explicit per-brand selection only; no global environment fallback.
    if row.get('sms_provider') != 'authentica':
        raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير مزود التحقق. اطلب رمزًا جديدًا.')
    auth._otp_channel_ready(conn, 'enbishan', 'sms')
    auth._otp_test_access(conn, 'enbishan', 'sms', request)
    return row


def start(body, request, phone, *, source_user=None, guest=None):
    contract, digest = ready(body.brand_id)
    authentica_verification._phone(phone)
    owner_probe = request.url.path.startswith('/api/identity/owner/sms-channel-test/')
    identifier = db.new_id('owner_probe_' if owner_probe else 'otp_')
    with _locked_phone(phone) as conn:
        with conn.transaction():
            channel_version = _channel(conn, request)['authentica_configuration_generation']
            recent = conn.execute("SELECT 1 FROM otp_challenges WHERE brand_id='enbishan' AND phone=%s "
                                  "AND provider_name='authentica' AND created_at>clock_timestamp()-(%s*interval '1 second') LIMIT 1",
                                  (phone, contract.resend_cooldown_seconds)).fetchone()
            if recent:
                raise db.error(429, 'OTP_RESEND_COOLDOWN', 'انتظر قبل طلب رمز جديد. / Wait before requesting another code.')
            # DURABLE BEFORE NETWORK: neither a process crash nor a later SQL/
            # audit/commit failure can revive a previous phone-only challenge.
            conn.execute("UPDATE otp_challenges SET expires_at=LEAST(expires_at,clock_timestamp()) "
                         "WHERE brand_id='enbishan' AND phone=%s AND provider_name='authentica' AND verified_at IS NULL", (phone,))
            conn.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name,"
                         "source_user_id,source_guest_id,authentica_contract_hash,authentica_code_length) "
                         "VALUES(%s,'enbishan',%s,%s,'sms',clock_timestamp(),'authentica',%s,%s,%s,%s)",
                         (identifier, phone, identifier, source_user['id'] if source_user else None,
                          guest['id'] if guest else None, digest, contract.code_length))
            db.audit(conn, None, 'identity.authentica.requested', 'otp_challenge', identifier,
                     {'brand_id': 'enbishan', 'contract_hash': digest, 'test_only': owner_probe})
        # No open transaction or brand-wide channel lock during the network.
        # Every exception leaves the already committed generation expired.
        authentica_verification.send(phone, brand_id='enbishan')
        with conn.transaction():
            current_channel = _channel(conn, request)
            # Switching away and back must not revive a generation retired by
            # identity settings, even if provider/contract now match again.
            if current_channel['authentica_configuration_generation'] != channel_version:
                raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير إعداد التحقق. اطلب رمزًا جديدًا.')
            if ready(body.brand_id)[1] != digest:
                raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير إعداد التحقق. اطلب رمزًا جديدًا.')
            expiry = conn.execute("UPDATE otp_challenges SET expires_at=created_at+(%s*interval '1 second') "
                                  "WHERE id=%s RETURNING GREATEST(0,extract(epoch FROM expires_at-clock_timestamp())::int) AS seconds",
                                  (min(300, contract.expiry_seconds), identifier)).fetchone()['seconds']
            db.audit(conn, None, 'identity.authentica.sent', 'otp_challenge', identifier,
                     {'brand_id': 'enbishan', 'contract_hash': digest, 'test_only': owner_probe})
        return {'challenge_id': identifier, 'expires_in': expiry, 'channel': 'sms', 'code_length': contract.code_length}


def check(body, request, response):
    """Durable attempts and atomic canonical claim, never a second identity store."""
    from . import auth
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn, 'otp-verify-ip:' + ip, 20, 600)
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'محاولات تحقق كثيرة. انتظر قليلًا.')
    contract, digest = ready('enbishan')
    with db.tx() as conn:
        row = conn.execute("SELECT phone FROM otp_challenges WHERE id=%s AND brand_id='enbishan' AND provider_name='authentica'",
                           (body.challenge_id,)).fetchone()
    if not row:
        raise db.error(400, 'OTP_EXPIRED', 'رمز التحقق منتهي أو مستهلك. اطلب رمزًا جديدًا.')
    temporary_response = Response()
    with _locked_phone(row['phone']) as conn:
        with conn.transaction():
            _channel(conn, request)
            challenge = conn.execute("SELECT * FROM otp_challenges WHERE id=%s AND provider_name='authentica' "
                                     "AND brand_id='enbishan' AND verified_at IS NULL AND expires_at>clock_timestamp() AND attempts<3 FOR UPDATE",
                                     (body.challenge_id,)).fetchone()
            if not challenge:
                raise db.error(400, 'OTP_EXPIRED', 'رمز التحقق منتهي أو مستهلك. اطلب رمزًا جديدًا.')
            if challenge['authentica_contract_hash'] != digest or challenge['authentica_code_length'] != contract.code_length:
                raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير إعداد التحقق. اطلب رمزًا جديدًا.')
            if not re.fullmatch(r'[0-9]{' + str(contract.code_length) + '}', body.code):
                raise db.error(422, 'OTP_INVALID', 'أدخل رمز تحقق صحيحًا. / Enter the verification code with the required length.')
            if challenge.get('source_user_id'):
                source = auth.current_user(request)
                if source['id'] != challenge['source_user_id'] or not source.get('customer_verified_at'):
                    raise db.error(403, 'CUSTOMER_PHONE_VERIFICATION_REQUIRED', 'سجل الدخول بجوالك الحالي أولًا.')
            conn.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s', (body.challenge_id,))
        # Attempts remain consumed if the provider succeeds and this process
        # fails before finalization. No channel/row lock spans the provider call.
        try:
            approved = authentica_verification.verify(challenge['phone'], body.code, brand_id='enbishan')
        except BaseException:
            # An unknown result may already have consumed provider proof.
            with conn.transaction():
                conn.execute('UPDATE otp_challenges SET expires_at=LEAST(expires_at,clock_timestamp()) WHERE id=%s', (body.challenge_id,))
            raise
        if not approved:
            raise db.error(400, 'OTP_INVALID', 'رمز التحقق غير صحيح.')
        try:
            with conn.transaction():
                _channel(conn, request)
                if ready('enbishan')[1] != digest:
                    raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير إعداد التحقق. اطلب رمزًا جديدًا.')
                output = auth._complete_customer_otp(conn, challenge, body, request, temporary_response, 'authentica')
        except BaseException:
            # Finalization rolled back all partial identity writes. Retire proof
            # already consumed by the provider, then preserve the original error.
            with conn.transaction():
                conn.execute('UPDATE otp_challenges SET expires_at=LEAST(expires_at,clock_timestamp()) WHERE id=%s', (body.challenge_id,))
            raise
    # Cookies leave the server only after the canonical transaction commits.
    for key, value in temporary_response.raw_headers:
        if key.lower() == b'set-cookie':
            response.raw_headers.append((key, value))
    return output

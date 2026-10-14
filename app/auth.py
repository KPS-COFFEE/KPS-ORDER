"""Revocable PostgreSQL sessions and independently verified customer identities."""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db, employee_permissions, sms_verification

router = APIRouter(prefix='/api')
COOKIE = 'kps_session'
CSRF_COOKIE = 'kps_csrf'
GUEST_SESSION_SECONDS = 30 * 24 * 3600
ROLES = {'owner', 'admin', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver', 'customer'}
CONFIGURATION_PERMISSIONS = {'branches_manage', 'products_manage', 'banners_manage', 'appearance_manage', 'brand_services_manage'}
ADMIN_PERMISSIONS = CONFIGURATION_PERMISSIONS | {'vehicle_maintenance_manage', 'branch_printers_manage', 'accounting_reports', 'accounting_review', 'accounting_cases_followup'}


def validate_admin_permissions(values):
    if not isinstance(values, list) or len(values) != len(set(str(x) for x in values)) or any(not isinstance(x, str) or x not in ADMIN_PERMISSIONS for x in values):
        raise db.error(422, 'ADMIN_PERMISSIONS_INVALID', 'اختر صلاحيات الإدارة المحددة فقط.')
    return values


def has_permission(user, permission):
    return permission in employee_permissions.effective(user)


def validate_permission_denials(values, role):
    if (not isinstance(values, list) or any(not isinstance(x, str) for x in values)
            or len(values) != len(set(values)) or not set(values) <= employee_permissions.eligible(role)
            or (role in {'owner', 'customer'} and values)):
        raise db.error(422, 'STAFF_PERMISSIONS_INVALID', 'اختر صلاحيات متاحة لدور الموظف فقط.')
    return sorted(values)


def require_permission(permission):
    if permission not in ADMIN_PERMISSIONS:
        raise ValueError('Unknown admin permission')
    def dependency(user=Depends(current_user)):
        if not has_permission(user, permission):
            raise db.error(403, 'ROLE_FORBIDDEN', 'هذه الصلاحية لم يمنحها المالك لحسابك.')
        return user
    return dependency


def require_configuration():
    def dependency(user=Depends(current_user)):
        if not employee_permissions.effective(user) & CONFIGURATION_PERMISSIONS:
            raise db.error(403, 'ROLE_FORBIDDEN', 'لا توجد صلاحيات إعدادات مخولة لحسابك.')
        return user
    return dependency
IDENTITY_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, *, min_length: int = 12) -> str:
    if not isinstance(password, str) or not min_length <= len(password) <= 200:
        raise db.error(422, 'PASSWORD_LENGTH', f'كلمة المرور يجب أن تكون بين {min_length} و200 حرف.')
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024)
    return 'scrypt$32768$8$3$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(derived).decode()


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or len(password) > 200:
        # Keep nonexistent account handling expensive, without a public bootstrap password.
        hashlib.scrypt(password[:200].encode(), salt=b'no-user-constant', n=32768, r=8, p=3, maxmem=64 * 1024 * 1024)
        return False
    try:
        algorithm, n, r, p, salt, expected = encoded.split('$')
        if algorithm != 'scrypt' or (int(n), int(r), int(p)) != (32768, 8, 3):
            return False
        candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(candidate, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def public_user(user: dict) -> dict:
    output = {key: user.get(key) for key in ('id', 'role', 'brand_id', 'branch_ids', 'display_name', 'phone', 'active', 'identity_kind', 'email', 'email_verified_at', 'phone_verified_at', 'phone_sms_verified_at', 'phone_whatsapp_verified_at', 'invitation_status', 'employee_ref', 'vehicle', 'permissions', 'permission_denials')}
    output['effective_permissions'] = sorted(employee_permissions.effective(user))
    output['available_permissions'] = sorted(employee_permissions.eligible(user.get('role')))
    return output


def secure_cookie() -> bool:
    return os.getenv('SESSION_COOKIE_SECURE', 'true' if os.getenv('APP_ENV', 'production') == 'production' else 'false').lower() == 'true'


def check_origin(request: Request):
    origin = request.headers.get('origin', '').rstrip('/')
    allowed = {entry.rstrip('/') for entry in os.getenv('APP_ALLOWED_ORIGINS', '').split(',') if entry}
    configured = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    if configured:
        allowed.add(configured)
    if os.getenv('APP_ENV') in {'testing', 'development'}:
        allowed.add(str(request.base_url).rstrip('/'))
    if not allowed:
        raise db.error(503, 'PUBLIC_ORIGIN_UNCONFIGURED', 'عنوان الموقع الآمن غير مهيأ بعد.')
    if not origin or origin not in allowed:
        raise db.error(403, 'ORIGIN_REJECTED', 'مصدر الطلب غير مسموح.')


def _find_session(conn, request: Request):
    token = request.cookies.get(COOKIE)
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', token):
        return None
    return conn.execute("SELECT u.*,COALESCE(a.settings->'customer_phone_required',to_jsonb(u.brand_id='kps'))<>'false'::jsonb AS customer_phone_required,CASE WHEN s.customer_verified_at IS NULL THEN ARRAY[]::text[] ELSE ARRAY(SELECT source_id FROM customer_identity_links l WHERE l.customer_id=u.id AND l.brand_id=u.brand_id) END AS customer_alias_ids,s.id AS session_id,s.csrf_hash,s.customer_verified_at,s.expires_at AS session_expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
                        'JOIN brands b ON b.id=u.brand_id LEFT JOIN app_settings a ON a.brand_id=u.brand_id '
                        "WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now() AND (s.customer_verified_at IS NULL OR s.customer_verified_at+interval '30 days'>now()) AND u.active AND b.active",
                        (token_hash(token),)).fetchone()


def current_user(request: Request) -> dict:
    with db.tx(pipeline=True) as conn:
        user = _find_session(conn, request)
    if not user:
        raise db.error(401, 'AUTHENTICATION_REQUIRED', 'سجّل الدخول للوصول إلى هذه البيانات.')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        check_origin(request)
        token = request.headers.get('x-csrf-token', '')
        if not token or not hmac.compare_digest(token_hash(token), user['csrf_hash']):
            raise db.error(403, 'CSRF_REJECTED', 'انتهى التحقق الآمن للطلب. حدّث الصفحة.')
    # Database result is fresh on every request: disabling an employee or changing branch access is immediate.
    required = employee_permissions.required_for(request.scope.get('endpoint'), user['role'])
    if required and not any(has_permission(user, value) for value in ((required,) if isinstance(required, str) else required)):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'هذه الصلاحية متوقفة لحسابك. تواصل مع المالك.')
    return user


def require_roles(*roles):
    if not set(roles).issubset(ROLES):
        raise ValueError('Unknown role in authorization policy')

    # The upstream session dependency already performs its fresh database read
    # in a worker thread. This role-only check has no I/O: scheduling another
    # synchronous worker unnecessarily queues every protected request twice.
    async def dependency(user=Depends(current_user)):
        if user['role'] not in roles:
            raise db.error(403, 'ROLE_FORBIDDEN', 'هذه العملية غير مسموحة لحسابك.')
        return user
    return dependency


def customer_ids(user):
    return [user['id'],*user.get('customer_alias_ids',[])]


def can_access_order(user: dict, order: dict, conn) -> bool:
    if not user.get('active', True) or user['brand_id'] != order['brand_id']:
        return False
    role = user['role']
    if role == 'owner':
        return True
    if role == 'customer':
        return order['customer_id'] in customer_ids(user)
    if role == 'branch_manager':
        return order['branch_id'] in user.get('branch_ids', [])
    if role in {'delivery_manager', 'delivery_staff'}:
        return order['service'] == 'delivery' and order['branch_id'] in user.get('branch_ids', [])
    if role == 'driver':
        if order['service'] != 'delivery' or order['branch_id'] not in user.get('branch_ids', []):
            return False
        exists = conn.execute("SELECT to_regclass('driver_tasks') AS table_name").fetchone()
        if not exists['table_name']:
            return False
        return bool(conn.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND driver_id=%s AND status NOT IN ('cancelled','reassigned','transferred','delivered')",
                                 (order['id'], user['id'])).fetchone())
    return False


def establish_session(conn, user: dict, response: Response, *, owner_step_up=False, customer_verified=False) -> dict:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hours = max(1, min(72, int(os.getenv('SESSION_TTL_HOURS', '12'))))
    # An unverified contact number is never a recovery credential. Remember a
    # guest only through this browser's random, HttpOnly session cookie.
    # Verified customers receive a fixed deadline from this verification.
    if user['role'] == 'customer' and (user.get('identity_kind') == 'guest' or customer_verified):
        hours = GUEST_SESSION_SECONDS // 3600
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
    conn.execute('INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at,step_up_at,customer_verified_at) '
                 'VALUES(%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END,CASE WHEN %s THEN now() ELSE NULL END)',
                 (db.new_id('session_'), token_hash(token), token_hash(csrf), user['id'], expiry,
                  bool(owner_step_up and user['role'] == 'owner'), bool(customer_verified and user['role'] == 'customer')))
    response.set_cookie(COOKIE, token, max_age=hours * 3600, httponly=True, secure=secure_cookie(), samesite='strict', path='/')
    response.set_cookie(CSRF_COOKIE, csrf, max_age=hours * 3600, httponly=False, secure=secure_cookie(), samesite='strict', path='/')
    db.audit(conn, user['id'], 'session.created', 'user', user['id'])
    return {'user': public_user(user), 'csrf_token': csrf, 'customer_verified': bool(customer_verified)}


@router.get('/session')
def session(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with db.tx() as conn:
        user = _find_session(conn, request)
    if not user:
        response.delete_cookie(COOKIE, path='/')
        response.delete_cookie(CSRF_COOKIE, path='/')
        return {'user': None, 'csrf_token': None}
    csrf = request.cookies.get(CSRF_COOKIE, '')
    if not csrf or not hmac.compare_digest(token_hash(csrf), user['csrf_hash']):
        # Do not reconstruct secret CSRF tokens from their hashes.
        return {'user': public_user(user), 'csrf_token': None, 'reauthentication_required': True}
    if user['role']=='customer' and user.get('customer_verified_at'):
        deadline=user['customer_verified_at']+timedelta(days=30)
        if user['session_expires_at']<deadline:
            # Upgrade an existing short SMS login to its original proof's
            # deadline once. Reading the session never moves that deadline.
            with db.tx() as conn:
                upgraded=conn.execute(
                    "UPDATE sessions SET expires_at=customer_verified_at+interval '30 days' "
                    "WHERE id=%s AND revoked_at IS NULL AND expires_at>now() "
                    "AND customer_verified_at=%s AND customer_verified_at+interval '30 days'>now() "
                    "AND expires_at<customer_verified_at+interval '30 days' RETURNING expires_at",
                    (user['session_id'],user['customer_verified_at'])).fetchone()
            if upgraded:
                remaining=max(1,int((upgraded['expires_at']-datetime.now(timezone.utc)).total_seconds()))
                response.set_cookie(COOKIE,request.cookies[COOKIE],max_age=remaining,
                                    httponly=True,secure=secure_cookie(),samesite='strict',path='/')
                response.set_cookie(CSRF_COOKIE,csrf,max_age=remaining,
                                    httponly=False,secure=secure_cookie(),samesite='strict',path='/')
    if user['role'] == 'customer' and user.get('identity_kind') == 'guest' and not user.get('customer_verified_at'):
        # Refresh only an already valid browser session, never by phone lookup.
        # This also upgrades existing short-lived guests without another login.
        with db.tx() as conn:
            renewed = conn.execute(
                "UPDATE sessions SET expires_at=now()+interval '30 days' "
                "WHERE id=%s AND revoked_at IS NULL AND expires_at>now() RETURNING id",
                (user['session_id'],)).fetchone()
        if renewed:
            response.set_cookie(COOKIE, request.cookies[COOKIE], max_age=GUEST_SESSION_SECONDS,
                                httponly=True, secure=secure_cookie(), samesite='strict', path='/')
            response.set_cookie(CSRF_COOKIE, csrf, max_age=GUEST_SESSION_SECONDS,
                                httponly=False, secure=secure_cookie(), samesite='strict', path='/')
    return {'user': public_user(user), 'csrf_token': csrf, 'customer_verified': bool(user.get('customer_verified_at') and user['customer_verified_at'] + timedelta(days=30) > datetime.now(timezone.utc))}


class LoginBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=200)


@router.post('/auth/login')
def login(body: LoginBody, request: Request, response: Response):
    check_origin(request)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn, 'staff-login-ip:' + ip, 30, 900)
        allowed = db.rate_limit(conn, 'staff-login-user:' + body.username.casefold(), 8, 900) and allowed
    if not allowed:
        raise db.error(429, 'LOGIN_RATE_LIMIT', 'محاولات دخول كثيرة. انتظر قليلًا.')
    with db.tx() as conn:
        user = conn.execute('SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id WHERE lower(u.username)=lower(%s) AND u.role<>%s AND u.active AND u.invitation_status=%s AND b.active',
                            (body.username, 'customer', 'active')).fetchone()
        valid = verify_password(body.password, user['password_hash'] if user else None)
        if not valid or not user:
            raise db.error(401, 'INVALID_CREDENTIALS', 'بيانات الدخول غير صحيحة.')
        return establish_session(conn, user, response)


@router.post('/auth/logout')
def logout(request: Request, response: Response, user=Depends(current_user)):
    with db.tx() as conn:
        conn.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s AND user_id=%s', (user['session_id'], user['id']))
        db.audit(conn, user['id'], 'session.revoked', 'user', user['id'])
    response.delete_cookie(COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return {'ok': True}


def normalize_phone(value: str) -> str:
    value = value.translate(IDENTITY_DIGITS)
    value = re.sub(r'[\s()-]', '', value)
    if re.fullmatch(r'05[0-9]{8}', value):
        value = '+966' + value[1:]
    elif value.startswith('00'):
        value = '+' + value[2:]
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال صحيحًا مع رمز الدولة.')
    return value


def provider_config() -> tuple[str, str, str]:
    if os.getenv('OTP_PROVIDER') == 'tawked':
        if not sms_verification.configured():
            raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
        return ('tawked', '', '')
    if os.getenv('OTP_PROVIDER') != 'twilio_verify':
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'تعذر إرسال الرمز حاليًا. حاول لاحقًا.')
    sid, secret, service = (os.getenv(name, '') for name in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID'))
    if not re.fullmatch(r'AC[a-fA-F0-9]{32}', sid) or not re.fullmatch(r'VA[a-fA-F0-9]{32}', service) or not secret:
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    return sid, secret, service


def _provider_post(path: str, data: dict):
    sid, secret, service = (os.getenv(name, '') for name in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID'))
    _provider_ready('twilio_verify')
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            result = client.post(f'https://verify.twilio.com/v2/Services/{service}/{path}', data=data, auth=(sid, secret))
        if result.status_code not in {200,201}:
            raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.')
        payload = result.json()
        if not isinstance(payload, dict):
            raise ValueError('invalid provider response')
        return payload
    except (httpx.HTTPError, ValueError):
        raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.') from None


def _sms_provider(conn, brand):
    return _selected_sms_provider(_channel(conn, brand, 'sms'))


def _selected_sms_provider(row):
    return row.get('sms_provider') or os.getenv('OTP_PROVIDER', 'disabled')


def _provider_ready(name):
    if name == 'tawked':
        if not sms_verification.configured():
            raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    elif name == 'twilio_verify':
        if (not re.fullmatch(r'AC[a-fA-F0-9]{32}', os.getenv('TWILIO_ACCOUNT_SID',''))
                or not re.fullmatch(r'VA[a-fA-F0-9]{32}', os.getenv('TWILIO_VERIFY_SERVICE_SID',''))
                or not os.getenv('TWILIO_AUTH_TOKEN')):
            raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    else:
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'التحقق بالجوال غير مفعّل بعد.')


def _otp_test_access(conn, brand, channel, request):
    if _channel(conn, brand, channel)['state'] == 'testing':
        user = _optional_identity(request, conn)
        if not user or user['role'] != 'owner' or user['brand_id'] != brand:
            raise db.error(403, 'OTP_OWNER_TEST_ONLY', 'اختبار الرسائل متاح للمالك فقط قبل الاعتماد.')


class OTPRequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=8, max_length=30)
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    lang: Literal['ar', 'en'] = 'ar'
    purpose: Literal['login','change_phone']='login'


def _otp_channel_ready(conn, brand_id: str, channel: str):
    """An installed provider is not permission to bypass a paused identity channel.

    Testing permits the owner's real delivery proof before activation. Existing
    authenticated customers and guest ordering are independent of this check.
    """
    if channel not in {'sms', 'whatsapp'}:
        raise db.error(503, 'OTP_CHANNEL_UNAVAILABLE', 'قناة التحقق غير مهيأة.')
    row = _channel(conn, brand_id, channel)
    if row['state'] not in {'testing', 'active'} or (
            row['state'] == 'active' and (not row['enabled'] or not row['delivery_proven_at'])):
        raise db.error(503, 'OTP_CHANNEL_UNAVAILABLE', 'تعذر إرسال الرمز حاليًا. حاول لاحقًا.')


@router.post('/auth/otp/request')
def request_otp(body: OTPRequestBody, request: Request):
    check_origin(request)
    phone = normalize_phone(body.phone)
    if body.brand_id == 'kps' and not re.fullmatch(r'\+9665[0-9]{8}', phone):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال سعودي صحيحًا.')
    source_user = current_user(request) if body.purpose=='change_phone' else None
    if source_user and (source_user['role']!='customer' or source_user['brand_id']!=body.brand_id or not source_user.get('customer_verified_at')):
        raise db.error(403,'CUSTOMER_PHONE_VERIFICATION_REQUIRED','سجل الدخول بجوالك الحالي أولًا.')
    channel = os.getenv('OTP_CHANNEL', 'sms')
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (body.brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        guest=_optional_identity(request,conn) if body.purpose=='login' else None
        if guest and guest['role']=='customer' and guest['identity_kind']=='guest' and guest['brand_id']==body.brand_id:
            guest=current_user(request)
        else:guest=None
        provider = _sms_provider(conn, body.brand_id)
        _provider_ready(provider)
        _otp_channel_ready(conn, body.brand_id, channel)
        _otp_test_access(conn, body.brand_id, channel, request)
        if provider == 'tawked' and channel != 'sms':
            raise db.error(503, 'OTP_CHANNEL_UNAVAILABLE', 'توكيد مخصص للتحقق برسائل SMS.')
        allowed = db.rate_limit(conn, 'otp-request-phone:' + phone, 3, 600)
        allowed = db.rate_limit(conn, 'otp-request-ip:' + ip, 10, 600) and allowed
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'طلبات تحقق كثيرة. انتظر قبل المحاولة مجددًا.')
    challenge_id = db.new_id('otp_')
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    if provider == 'tawked':
        reference, provider_expiry = sms_verification.start(phone, challenge_id, body.lang)
        expires_at = min(expires_at, provider_expiry)
    else:
        result = _provider_post('Verifications', {'To': phone, 'Channel': channel})
        if result.get('status') != 'pending' or not result.get('sid') or result.get('channel', channel) != channel:
            raise db.error(503, 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال رمز التحقق.')
        reference = result['sid']
    with db.tx() as conn:
        _otp_channel_ready(conn, body.brand_id, channel)
        _otp_test_access(conn, body.brand_id, channel, request)
        if _sms_provider(conn, body.brand_id) != provider:
            raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير مزود التحقق. اطلب رمزًا جديدًا.')
        conn.execute('INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name,source_user_id,source_guest_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (challenge_id, body.brand_id, phone, reference, channel, expires_at, provider,source_user['id'] if source_user else None,guest['id'] if guest else None))
    return {'challenge_id': challenge_id, 'expires_in': max(0, int((expires_at-datetime.now(timezone.utc)).total_seconds())), 'channel': channel}



class OTPVerifyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(min_length=5, max_length=80)
    code: str = Field(pattern=r'^[0-9]{4}$')
    display_name: str = Field(default='', max_length=100)


@router.post('/auth/otp/verify')
def verify_otp(body: OTPVerifyBody, request: Request, response: Response):
    check_origin(request)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn, 'otp-verify-ip:' + ip, 20, 600)
        challenge = conn.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s AND verified_at IS NULL AND expires_at>now() AND attempts<3 RETURNING *',
                                 (body.challenge_id,)).fetchone() if allowed else None
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'محاولات تحقق كثيرة. انتظر قليلًا.')
    if not challenge:
        raise db.error(400, 'OTP_EXPIRED', 'رمز التحقق منتهي أو مستهلك. اطلب رمزًا جديدًا.')
    channel = challenge['delivery_channel']
    if challenge.get('source_user_id'):
        source_user=current_user(request)
        if source_user['id']!=challenge['source_user_id'] or not source_user.get('customer_verified_at'):
            raise db.error(403,'CUSTOMER_PHONE_VERIFICATION_REQUIRED','سجل الدخول بجوالك الحالي أولًا.')
    with db.tx() as conn:
        conn.execute('SELECT channel FROM identity_channels WHERE brand_id=%s AND channel=%s FOR UPDATE',(challenge['brand_id'],channel)).fetchone()
        _otp_channel_ready(conn, challenge['brand_id'], channel)
        _otp_test_access(conn, challenge['brand_id'], channel, request)
        provider = challenge['provider_name']
        if _sms_provider(conn, challenge['brand_id']) != provider:
            raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير مزود التحقق. اطلب رمزًا جديدًا.')
        _provider_ready(provider)
    # Check the stored provider reference, never a client supplied phone/reference.
    if provider == 'tawked':
        approved = channel == 'sms' and sms_verification.check(challenge['provider_reference'], body.code)
    else:
        result = _provider_post('VerificationCheck', {'VerificationSid': challenge['provider_reference'], 'Code': body.code})
        approved = result.get('status') == 'approved' and result.get('to') == challenge['phone'] and result.get('channel', channel) == channel
    if not approved:
        raise db.error(400, 'OTP_INVALID', 'رمز التحقق غير صحيح.')
    with db.tx() as conn:
        conn.execute('SELECT channel FROM identity_channels WHERE brand_id=%s AND channel=%s FOR UPDATE',(challenge['brand_id'],channel)).fetchone()
        _otp_channel_ready(conn, challenge['brand_id'], channel)
        _otp_test_access(conn, challenge['brand_id'], channel, request)
        if _sms_provider(conn, challenge['brand_id']) != provider:
            raise db.error(409, 'OTP_PROVIDER_CHANGED', 'تغير مزود التحقق. اطلب رمزًا جديدًا.')
        claimed = conn.execute('UPDATE otp_challenges SET verified_at=now() WHERE id=%s AND verified_at IS NULL AND expires_at>now() RETURNING id',
                               (body.challenge_id,)).fetchone()
        if not claimed:
            raise db.error(409, 'OTP_ALREADY_USED', 'تم استخدام طلب التحقق بالفعل.')
        if _channel(conn, challenge['brand_id'], channel)['state'] == 'testing':
            # Owner smoke test proves delivery without creating/logging in a customer.
            owner = _optional_identity(request, conn)
            conn.execute('UPDATE identity_channels SET delivery_proven_at=now(),updated_at=now() WHERE brand_id=%s AND channel=%s', (challenge['brand_id'], channel))
            db.audit(conn, owner['id'], 'identity.sms.test_verified', 'brand', challenge['brand_id'], {'provider': provider})
            return {'verified': True, 'test_only': True}
        proof_column = 'phone_whatsapp_verified_at' if channel == 'whatsapp' else 'phone_sms_verified_at'
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(challenge['brand_id']+':customer-phone:'+challenge['phone'],))
        if challenge.get('source_user_id'):
            source=current_user(request)
            if source['id']!=challenge['source_user_id']:raise db.error(403,'CUSTOMER_PHONE_VERIFICATION_REQUIRED','أعد التحقق من الجوال.')
            conflict=conn.execute("SELECT 1 FROM users WHERE brand_id=%s AND phone=%s AND phone_verified_at IS NOT NULL AND role='customer' AND id<>%s",(challenge['brand_id'],challenge['phone'],source['id'])).fetchone()
            if conflict:raise db.error(409,'PHONE_CHANGE_UNAVAILABLE','لا يمكن تغيير الجوال إلى هذا الرقم.')
            user=conn.execute("UPDATE users SET phone=%s,phone_sms_verified_at=NULL,phone_whatsapp_verified_at=NULL WHERE id=%s AND active RETURNING *",(challenge['phone'],source['id'])).fetchone()
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(source['id'],))
            db.audit(conn,source['id'],'customer.phone.changed','user',source['id'])
        else:
            conn.execute("INSERT INTO users(id,display_name,role,brand_id,phone,identity_kind,phone_verified_at," + proof_column + ") VALUES(%s,%s,'customer',%s,%s,%s,now(),now()) ON CONFLICT(brand_id,phone) WHERE role='customer' AND phone_verified_at IS NOT NULL DO NOTHING",
                         (db.new_id('customer_'), body.display_name, challenge['brand_id'], challenge['phone'], 'phone_'+channel+'_verified'))
            user = conn.execute("SELECT * FROM users WHERE brand_id=%s AND phone=%s AND role='customer' AND phone_verified_at IS NOT NULL AND active", (challenge['brand_id'], challenge['phone'])).fetchone()
        if user:
            user = conn.execute("UPDATE users SET "+proof_column+"=now(),phone_verified_at=now(),display_name=CASE WHEN %s<>'' THEN %s ELSE display_name END,updated_at=now() WHERE id=%s RETURNING *",(body.display_name.strip(),body.display_name.strip(),user['id'])).fetchone()
            conn.execute('UPDATE identity_channels SET delivery_proven_at=now(),updated_at=now() WHERE brand_id=%s AND channel=%s',(challenge['brand_id'],channel))
        if not user:
            raise db.error(403, 'CUSTOMER_DISABLED', 'هذا الحساب موقوف. تواصل مع الإدارة.')
        if customer_phone_required(conn,challenge['brand_id']) and not user['display_name'].strip():
            raise db.error(422, 'CUSTOMER_NAME_REQUIRED', 'أدخل اسمك لإكمال التحقق.')
        if challenge.get('source_guest_id'):
            owned=current_user(request)
            if owned['id']!=challenge['source_guest_id'] or owned['brand_id']!=user['brand_id']:
                raise db.error(403,'GUEST_LINK_FORBIDDEN','أعد التحقق من نفس جلسة التواصل.')
            conn.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(owned['id'],)).fetchone()
            if not _find_session(conn,request):raise db.error(403,'GUEST_LINK_FORBIDDEN','أعد التحقق من نفس جلسة التواصل.')
            conn.execute('INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,%s)',(owned['id'],user['id'],user['brand_id']))
            conn.execute('UPDATE conversations SET customer_id=%s WHERE customer_id=%s AND brand_id=%s',(user['id'],owned['id'],user['brand_id']))
            conn.execute('UPDATE customer_addresses SET customer_id=%s WHERE customer_id=%s AND brand_id=%s',(user['id'],owned['id'],user['brand_id']))
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(owned['id'],))
            db.audit(conn,user['id'],'customer.owned_guest_linked','user',user['id'],{'source_user_id':owned['id']})
        return establish_session(conn, user, response, customer_verified=True)


def bootstrap_owner(username: str, password: str, brand_id: str = 'kps', display_name: str = 'الإدارة'):
    """Private one-time CLI path; never served by an HTTP route and never resets an existing owner."""
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,150}', username):
        raise ValueError('Choose an explicit private owner username.')
    encoded = hash_password(password)
    with db.tx() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (764832611,))
        if conn.execute("SELECT 1 FROM users WHERE role='owner' AND brand_id=%s", (brand_id,)).fetchone():
            raise RuntimeError('An owner already exists; bootstrap cannot overwrite or reset it.')
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand_id,)).fetchone():
            raise RuntimeError('Initialize the brand first.')
        user_id = db.new_id('owner_')
        conn.execute("INSERT INTO users(id,username,display_name,password_hash,role,brand_id) VALUES(%s,%s,%s,%s,'owner',%s)",
                     (user_id, username, display_name, encoded, brand_id))
        db.audit(conn, user_id, 'owner.bootstrap', 'user', user_id)
    return user_id


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Private, non-destructive owner bootstrap')
    parser.add_argument('--bootstrap-owner', action='store_true', required=True)
    parser.add_argument('--brand', default='kps')
    args = parser.parse_args()
    db.migrate()
    chosen = input('Private owner username: ').strip()
    password = getpass.getpass('New owner password (12+ characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords differ.')
    bootstrap_owner(chosen, password, args.brand)
    print('Owner account created. No password or session token has been printed.')

# V5 identities reuse canonical users and revocable sessions. No role is inferred from contact data.
from . import email_delivery

CHANNEL_STATES = {'unconfigured','testing','active','paused','fault'}
TOKEN_PURPOSES = {'customer_link','email_login','staff_login','owner_link','recovery'}


def identity_snapshot(user):
    return {'identity_kind': user.get('identity_kind','legacy'),
            'email_verified': bool(user.get('email_verified_at')),
            'phone_verified': bool(user.get('phone_verified_at')),
            'phone_sms_verified': bool(user.get('phone_sms_verified_at')),
            'phone_whatsapp_verified': bool(user.get('phone_whatsapp_verified_at'))}


def _channel(conn, brand, channel):
    row = conn.execute('SELECT * FROM identity_channels WHERE brand_id=%s AND channel=%s',(brand,channel)).fetchone()
    return row or {'state':'unconfigured','enabled':False,'delivery_proven_at':None}


def _guest_channel_available(conn, brand):
    # Guest checkout has no external identity provider. Read its current gate
    # directly instead of probing all optional channels on every order/replay.
    row = _channel(conn, brand, 'guest')
    return bool(row['enabled'] and row['state'] == 'active')


def channel_readiness(conn, brand):
    channels = {row['channel']: row for row in conn.execute(
        'SELECT * FROM identity_channels WHERE brand_id=%s', (brand,)).fetchall()}
    rows = {}
    for channel in ('guest','email','sms','whatsapp'):
        row = channels.get(channel) or {'state':'unconfigured','enabled':False,'delivery_proven_at':None}
        configured = True if channel=='guest' else email_delivery.configuration()['configured'] if channel=='email' else False
        if channel in {'sms','whatsapp'}:
            try:
                provider = _selected_sms_provider(channels.get('sms') or {})
                _provider_ready(provider)
                configured = os.getenv('OTP_CHANNEL','sms') == channel and (provider != 'tawked' or channel == 'sms')
            except Exception:
                configured = False
        available = bool(row['enabled'] and row['state']=='active' and configured)
        if channel!='guest':
            available = available and bool(row['delivery_proven_at'])
        rows[channel] = {'state':row['state'],'enabled':row['enabled'],'available':available,
                         'configured':configured,'delivery_proven':bool(row['delivery_proven_at'])}
    return rows


def customer_phone_required(conn,brand):
    row=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(brand,)).fetchone()
    return (row or {}).get('settings',{}).get('customer_phone_required',brand=='kps') is not False


def assert_customer_order_eligible(conn, user, *, phone_required=None):
    if user['role']!='customer' or not user.get('active',True):
        raise db.error(403,'CUSTOMER_REQUIRED','الطلب يتطلب جلسة عميل مخوّلة.')
    if (customer_phone_required(conn,user['brand_id']) if phone_required is None else phone_required):
        proof=user.get('customer_verified_at')
        if not user.get('phone_sms_verified_at') or not proof or proof + timedelta(days=30) <= datetime.now(timezone.utc):
            raise db.error(403,'CUSTOMER_PHONE_VERIFICATION_REQUIRED','تحقق من جوالك لإتمام الطلب. حاول لاحقًا إذا تعذر إرسال الرمز.')
        if not user.get('display_name','').strip():
            raise db.error(422,'CUSTOMER_NAME_REQUIRED','أدخل اسمك لإتمام الطلب.')
        return
    if user.get('identity_kind')=='guest':
        if not _guest_channel_available(conn,user['brand_id']):
            raise db.error(403,'GUEST_ORDERING_PAUSED','الطلب كضيف متوقف حاليًا.')
        if not user.get('display_name') or not user.get('phone'):
            raise db.error(422,'GUEST_CONTACT_REQUIRED','أدخل الاسم ورقم الاتصال.')
        normalize_phone(user['phone'])
    elif not (user.get('email_verified_at') or user.get('phone_verified_at') or user.get('identity_kind','legacy')=='legacy'):
        raise db.error(403,'CUSTOMER_IDENTITY_UNVERIFIED','تحقق من الهوية أو اختر الطلب كضيف.')


@router.get('/identity/channels')
def public_channels(brand_id: str='kps'):
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(brand_id,)).fetchone():
            raise db.error(404,'BRAND_NOT_FOUND','البراند غير متاح.')
        return {'customer_phone_required':customer_phone_required(conn,brand_id),'channels':channel_readiness(conn,brand_id),
                'session_recovery':'البريد الموثق يستعيد الحساب. عند فقد جلسة ضيف بلا بريد موثق تواصل مع فريق التشغيل؛ رقم الجوال والطلب وحدهما لا يمنحان الوصول.'}


class GuestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1,max_length=100)
    phone: str = Field(min_length=8,max_length=30)
    brand_id: str = Field(default='kps',min_length=1,max_length=80)
    email: str | None = Field(default=None,max_length=254)

    @field_validator('*')
    @classmethod
    def persistable_guest_text(cls, value):
        if isinstance(value, str) and '\x00' in value:
            raise ValueError('Text must not contain a NUL character.')
        return value


@router.post('/identity/guest')
def guest_session(body:GuestBody,request:Request,response:Response):
    check_origin(request)
    phone = normalize_phone(body.phone)
    name = body.display_name.strip()
    if not name or '\x00' in name:
        raise db.error(422,'GUEST_NAME_REQUIRED','أدخل الاسم.')
    # Optional email is a hint, never used for identity matching or ownership.
    contact_email=email_delivery.normalize_email(body.email) if body.email else None
    ip = request.client.host if request.client else 'unknown'
    # Batch transaction commands without merging the durable abuse guard
    # with identity creation: rejected/failed identities still consume limits.
    with db.tx(pipeline=True) as conn:
        allowed = db.rate_limit(conn,'guest-create-contact:'+body.brand_id+':'+token_hash(phone),10,600)
        allowed = db.rate_limit(conn,'guest-create-ip:'+ip,200,600) and allowed
    if not allowed:
        raise db.error(429,'GUEST_RATE_LIMIT','طلبات كثيرة. انتظر قليلًا.')
    with db.tx(pipeline=True) as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(body.brand_id,)).fetchone():
            raise db.error(404,'BRAND_NOT_FOUND','البراند غير متاح.')
        if not _guest_channel_available(conn,body.brand_id):
            raise db.error(403,'GUEST_ORDERING_PAUSED','الطلب كضيف متوقف حاليًا.')
        existing = _find_session(conn,request)
        if existing:
            csrf=request.headers.get('x-csrf-token','')
            if not csrf or not hmac.compare_digest(token_hash(csrf),existing['csrf_hash']):
                raise db.error(403,'CSRF_REJECTED','حدّث الصفحة قبل تعديل جلسة الضيف.')
            if existing['role']!='customer':
                raise db.error(403,'STAFF_SESSION_PROTECTED','استخدم جلسة عميل منفصلة عن الموظف.')
        if existing and existing['brand_id']==body.brand_id and existing.get('identity_kind')=='guest':
            user = conn.execute('UPDATE users SET display_name=%s,phone=%s,contact_email=%s,updated_at=now() WHERE id=%s RETURNING *',(name,phone,contact_email,existing['id'])).fetchone()
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s',(existing['session_id'],))
        else:
            identifier=db.new_id('guest_')
            user=conn.execute("INSERT INTO users(id,display_name,phone,role,brand_id,identity_kind,contact_email) VALUES(%s,%s,%s,'customer',%s,'guest',%s) RETURNING *",(identifier,name,phone,body.brand_id,contact_email)).fetchone()
        return establish_session(conn,user,response)


def _optional_identity(request, conn):
    user=_find_session(conn,request)
    if not user:
        return None
    csrf=request.headers.get('x-csrf-token','')
    if not csrf or not hmac.compare_digest(token_hash(csrf),user['csrf_hash']):
        raise db.error(403,'CSRF_REJECTED','انتهى التحقق الآمن. حدّث الصفحة.')
    return user


def _email_ready(conn,brand,testing=False):
    row=_channel(conn,brand,'email')
    if not email_delivery.configuration()['configured'] or row['state'] not in ({'active','testing'} if testing else {'active'}) or (row['state']=='active' and not row['enabled']):
        raise db.error(503,'EMAIL_CHANNEL_UNAVAILABLE','قناة البريد غير مهيأة ومختبرة بعد. يبقى الطلب كضيف متاحًا.')
    if not testing and (not row['enabled'] or not row['delivery_proven_at']):
        raise db.error(503,'EMAIL_CHANNEL_UNAVAILABLE','قناة البريد بانتظار إثبات وصول فعلي.')


def _staff_code_hash(identifier, code):
    # The small numeric code space must not be brute-forced from a stolen database.
    # Domain-separated HMAC uses the existing secret encrypted-outbox key.
    key=email_delivery._key()
    if not key:
        raise db.error(503,'EMAIL_CHANNEL_UNAVAILABLE','إعداد حماية أكواد البريد غير مكتمل.')
    return hmac.new(key,b'KPS-STAFF-CODE-V1\x00'+identifier.encode()+b'\x00'+code.encode(),hashlib.sha256).hexdigest()


def _staff_context(user):
    context = {'phone':normalize_phone(user['phone']) if user.get('phone') else None,
            'role':user['role'],'branch_ids':sorted(user.get('branch_ids',[])),
            'permissions':sorted(user.get('permissions') or [])}
    # Empty defaults keep previously issued unmodified invitations valid.
    if user.get('permission_denials'):
        context['permission_denials'] = sorted(user['permission_denials'])
    return context


def _same_email(left, right):
    """Compare registered contacts canonically without replacing their owners."""
    try:
        return bool(left and right and email_delivery.normalize_email(left) == email_delivery.normalize_email(right))
    except HTTPException:
        return False


def _invitation_authorized(conn, row, user):
    inviter=conn.execute('SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id WHERE u.id=%s AND u.active AND b.active',(row['created_by'],)).fetchone()
    normal=bool(inviter and inviter['brand_id']==row['brand_id'] and inviter['role'] in {'owner','delivery_manager'} and
                (inviter['role']=='owner' or (user['role']=='driver' and set(user['branch_ids'])<=set(inviter['branch_ids']))))
    # Preserve the explicit, audited first-owner invitation for a newly provisioned brand.
    first_owner=bool(inviter and inviter['role']=='owner' and row['purpose']=='add_owner' and user['role']=='owner' and
        conn.execute("SELECT 1 FROM audit_events WHERE actor_id=%s AND action='brand.created' AND entity_type='brand' AND entity_id=%s AND details->>'owner_user_id'=%s",(inviter['id'],row['brand_id'],user['id'])).fetchone() and
        not conn.execute("SELECT 1 FROM users WHERE brand_id=%s AND role='owner' AND id<>%s",(row['brand_id'],user['id'])).fetchone())
    return bool((normal or first_owner) and (row['purpose']!='add_owner' or (inviter['role']=='owner' and user['role']=='owner')))


def _staff_token_authorized(conn, row, user):
    if not user or user['role']=='customer' or user['brand_id']!=row['brand_id'] or not _same_email(user.get('email'),row['email']):
        return False
    if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(row['brand_id'],)).fetchone():
        return False
    context=row.get('staff_context')
    if context is not None:
        try:
            expected=_staff_context(user)
        except HTTPException:
            return False
        # Earlier tokens did not snapshot delegated grants. Preserve their
        # unprivileged flows, but require a new token for an admin with grants.
        if 'permissions' not in context and not expected['permissions']:
            expected.pop('permissions')
        if context!=expected:
            return False
    branches=user.get('branch_ids',[])
    if user['role']!='owner' and not branches:
        return False
    if len(set(branches))!=len(branches) or any(not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(branch,row['brand_id'])).fetchone() for branch in branches):
        return False
    invitation=row['purpose'] in {'invitation','add_owner'}
    if invitation:
        return user['invitation_status']=='pending_email' and _invitation_authorized(conn,row,user)
    return bool(user['active'] and user['invitation_status']=='active' and
                (user['email_verified_at'] or (user['role']=='driver' and user.get('identity_kind')=='staff_owner_approved')))


def identity_mail_authorized(conn, mail):
    """Recheck a queued login before disclosing its token to its recipient."""
    row=conn.execute('SELECT * FROM identity_tokens WHERE id=%s AND consumed_at IS NULL '
                     'AND revoked_at IS NULL AND expires_at>now()',
                     (mail['dedupe_key'].removeprefix('identity:'),)).fetchone()
    if not row or row['brand_id']!=mail['brand_id'] or not _same_email(row['email'],mail['recipient']):
        return False
    if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(row['brand_id'],)).fetchone():
        return False
    user=conn.execute('SELECT * FROM users WHERE id=%s',(row['user_id'],)).fetchone() if row['user_id'] else None
    if row['purpose'] in {'staff_login','invitation','add_owner'}:
        return _staff_token_authorized(conn,row,user)
    if user and (not user['active'] or user['brand_id']!=row['brand_id']):
        return False
    if row['session_id'] and not conn.execute('SELECT 1 FROM sessions WHERE id=%s AND user_id=%s '
                                            'AND revoked_at IS NULL AND expires_at>now()',
                                            (row['session_id'],row['user_id'])).fetchone():
        return False
    if row['purpose']=='recovery':
        return bool(user and user['invitation_status']=='active' and user['email_verified_at']
                    and _same_email(user.get('email'),row['email']))
    if row['purpose'] in {'customer_link','owner_link'}:
        role='owner' if row['purpose']=='owner_link' else 'customer'
        return bool(user and user['role']==role and row['session_id'] and
                    (not user['email_verified_at'] or _same_email(user.get('email'),row['email'])))
    if row['purpose']=='email_login':
        if user:
            return bool(user['role']=='customer' and user['email_verified_at']
                        and _same_email(user.get('email'),row['email']))
        return not conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND lower(btrim(email))=%s',
                                (row['brand_id'],email_delivery.normalize_email(row['email']))).fetchone()
    return False


def _create_email_token(conn,*,brand,email,purpose,user_id=None,session_id=None,created_by=None):
    email=email_delivery.normalize_email(email)
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(brand+':email-token:'+email+':'+purpose,))
    identifier=db.new_id('email_')
    secret=secrets.token_urlsafe(32)
    # Superseding a purpose invalidates prior links and their unsent durable deliveries.
    prior=conn.execute('SELECT id FROM identity_tokens WHERE brand_id=%s AND email=%s AND purpose=%s AND consumed_at IS NULL AND revoked_at IS NULL',(brand,email,purpose)).fetchall()
    conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE brand_id=%s AND email=%s AND purpose=%s AND consumed_at IS NULL AND revoked_at IS NULL',(brand,email,purpose))
    for row in prior:
        conn.execute("UPDATE email_outbox SET status='cancelled',updated_at=now() WHERE brand_id=%s AND dedupe_key=%s AND status IN ('pending','failed')",(brand,'identity:'+row['id']))
    numeric=purpose in {'staff_login','invitation','add_owner'}
    staff=conn.execute('SELECT * FROM users WHERE id=%s',(user_id,)).fetchone() if numeric and user_id else None
    code=f'{secrets.randbelow(1_000_000):06d}' if numeric and staff else None
    context=_staff_context(staff) if code else None
    conn.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,session_id,created_by,expires_at,code_hash,staff_context) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now()+interval '15 minutes',%s,%s)",(identifier,brand,user_id,purpose,email,token_hash(secret),session_id,created_by,_staff_code_hash(identifier,code) if code else None,db.json(context) if context else None))
    base=os.getenv('PUBLIC_BASE_URL','').rstrip('/')
    if not base.startswith(('https://','http://')) or (not base.startswith('https://') and os.getenv('APP_ENV') not in {'testing','development'}):
        raise db.error(503,'PUBLIC_ORIGIN_UNCONFIGURED','عنوان البريد الآمن غير مهيأ.')
    link=base+'/#identity_challenge='+identifier+'&identity_token='+secret
    text='طلب تحقق قصير الصلاحية لمرة واحدة. إذا لم تطلبه فتجاهله.\n'
    if code:
        text+='كود التفعيل والدخول: '+code+'\nصالح لمدة 15 دقيقة، ويستخدم مرة واحدة فقط. أدخل الكود في شاشة دخول الموظفين برقم جوالك المسجل.\n'
    mail=email_delivery.enqueue_mail(conn,brand_id=brand,recipient=email,kind='identity',dedupe_key='identity:'+identifier,
        payload={'subject':'KPS COFFEE — كود التفعيل والدخول' if code else 'KPS COFFEE — تحقق من البريد', 'text':text+link})
    if not mail:
        raise db.error(503,'EMAIL_CHANNEL_UNAVAILABLE','تعذر حفظ إرسال البريد الآمن.')
    db.audit(conn,created_by,'identity.email.queued','identity_token',identifier,{'purpose':purpose})
    return identifier


class EmailRequestBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str=Field(min_length=3,max_length=254)
    brand_id:str=Field(default='kps',min_length=1,max_length=80)
    purpose:Literal['customer_link','email_login','staff_login','owner_link','recovery']='email_login'


@router.post('/identity/email/request')
def email_request(body:EmailRequestBody,request:Request):
    check_origin(request)
    email=email_delivery.normalize_email(body.email)
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'email-request:'+body.brand_id+':'+token_hash(email),3,600)
        allowed=db.rate_limit(conn,'email-request-ip:'+ip,20,600) and allowed
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','انتظر قبل إعادة طلب البريد.')
    identifier=db.new_id('email_')  # Same response for unknown/disabled accounts.
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(body.brand_id,)).fetchone():
            return {'accepted':True,'challenge_id':identifier,'expires_in':900}
        current=_optional_identity(request,conn)
        linking=body.purpose in {'customer_link','owner_link'}
        _email_ready(conn,body.brand_id,testing=linking and bool(current))
        if linking:
            role='owner' if body.purpose=='owner_link' else 'customer'
            if not current or current['role']!=role or current['brand_id']!=body.brand_id:
                raise db.error(403,'LINK_SESSION_REQUIRED','يتطلب ربط البريد جلسة الحساب الحالي.')
            if current.get('email_verified_at') and current.get('email')!=email:
                raise db.error(409,'EMAIL_CHANGE_REQUIRES_RECOVERY','تغيير بريد موثق يتطلب مسار استرداد مخوّل.')
            conflict=conn.execute('SELECT id FROM users WHERE brand_id=%s AND lower(email)=%s AND id<>%s',(body.brand_id,email,current['id'])).fetchone()
            if not conflict:
                identifier=_create_email_token(conn,brand=body.brand_id,email=email,purpose=body.purpose,user_id=current['id'],session_id=current['session_id'],created_by=current['id'])
        else:
            account=conn.execute('SELECT * FROM users WHERE brand_id=%s AND lower(email)=%s AND active AND email_verified_at IS NOT NULL AND invitation_status=%s',(body.brand_id,email,'active')).fetchone()
            valid=account and (body.purpose=='recovery' or (body.purpose=='staff_login' and account['role']!='customer') or (body.purpose=='email_login' and account['role']=='customer'))
            if valid or (body.purpose=='email_login' and not conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND lower(email)=%s',(body.brand_id,email)).fetchone()):
                identifier=_create_email_token(conn,brand=body.brand_id,email=email,purpose=body.purpose,user_id=account['id'] if valid else None)
    return {'accepted':True,'challenge_id':identifier,'expires_in':900}


class EmailVerifyBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    challenge_id:str=Field(min_length=5,max_length=80)
    token:str=Field(min_length=20,max_length=120)
    display_name:str|None=Field(default=None,min_length=1,max_length=100)
    phone:str|None=Field(default=None,min_length=8,max_length=30)


class StaffCodeRequestBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    phone:str=Field(min_length=8,max_length=30)
    brand_id:str=Field(default='kps',min_length=1,max_length=80)


@router.post('/identity/staff/code/request')
def request_staff_code(body:StaffCodeRequestBody,request:Request):
    check_origin(request)
    phone=normalize_phone(body.phone)
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'staff-code-request:'+body.brand_id+':'+phone,3,600)
        allowed=db.rate_limit(conn,'staff-code-request-ip:'+ip,20,600) and allowed
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','انتظر قبل إعادة طلب الكود.')
    identifier=db.new_id('email_')
    with db.tx() as conn:
        # Request bodies cannot select a delivery email or supply grants.
        _email_ready(conn,body.brand_id,testing=True)
        if conn.execute('SELECT 1 FROM brands WHERE id=%s AND active',(body.brand_id,)).fetchone():
            matches=conn.execute("SELECT * FROM users WHERE brand_id=%s AND role<>'customer' AND normalized_identity_phone(phone)=%s ORDER BY id",(body.brand_id,phone)).fetchall()
            if len(matches)==1:
                user=matches[0]
                pending=user['invitation_status']=='pending_email'
                invitation=conn.execute("SELECT * FROM identity_tokens WHERE user_id=%s AND purpose IN ('invitation','add_owner') AND consumed_at IS NULL AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",(user['id'],)).fetchone() if pending else None
                purpose=invitation['purpose'] if invitation else 'staff_login'
                candidate=invitation or {'brand_id':body.brand_id,'email':user.get('email'),'purpose':purpose,'staff_context':_staff_context(user)}
                if _staff_token_authorized(conn,candidate,user) and (not pending or invitation):
                    identifier=_create_email_token(conn,brand=body.brand_id,email=user['email'],purpose=purpose,user_id=user['id'],created_by=invitation['created_by'] if invitation else None)
    return {'accepted':True,'challenge_id':identifier,'expires_in':900,'code_length':6}


class StaffCodeVerifyBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    challenge_id:str=Field(min_length=5,max_length=80)
    code:str=Field(min_length=6,max_length=12)
    display_name:str|None=Field(default=None,min_length=1,max_length=100)


@router.post('/identity/staff/code/verify')
def verify_staff_code(body:StaffCodeVerifyBody,request:Request,response:Response):
    check_origin(request)
    code=body.code.translate(IDENTITY_DIGITS).strip()
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'staff-code-verify-ip:'+ip,30,600)
        challenge=conn.execute("UPDATE identity_tokens SET attempts=attempts+1 WHERE id=%s AND purpose IN ('staff_login','invitation','add_owner') AND code_hash IS NOT NULL AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>now() AND attempts<5 RETURNING *",(body.challenge_id,)).fetchone()
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','محاولات كثيرة. انتظر قليلًا.')
    if not re.fullmatch(r'[0-9]{6}',code):
        raise db.error(422,'STAFF_CODE_INVALID','أدخل كود البريد المكوّن من 6 أرقام.')
    if not challenge or not hmac.compare_digest(challenge['code_hash'],_staff_code_hash(body.challenge_id,code)):
        raise db.error(400,'STAFF_CODE_INVALID','الكود منتهي أو غير صحيح أو مستخدم. اطلب كودًا جديدًا.')
    try:
        return _accept_email_identity(challenge,request,response,body.display_name)
    except HTTPException as exc:
        if exc.status_code==400 and isinstance(exc.detail,dict) and exc.detail.get('code')=='EMAIL_TOKEN_INVALID':
            raise db.error(400,'STAFF_CODE_INVALID','الكود أو صلاحية الحساب لم يعودا صالحين. اطلب كودًا جديدًا.') from None
        raise


@router.post('/identity/email/verify')
def email_verify(body:EmailVerifyBody,request:Request,response:Response):
    check_origin(request)
    ip=request.client.host if request.client else 'unknown'
    # Attempts are committed before errors; rollback cannot reset brute-force limits.
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'email-verify-ip:'+ip,30,600)
        challenge=conn.execute('UPDATE identity_tokens SET attempts=attempts+1 WHERE id=%s AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>now() AND attempts<5 RETURNING *',(body.challenge_id,)).fetchone()
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','محاولات كثيرة. انتظر قليلًا.')
    if not challenge or not hmac.compare_digest(challenge['token_hash'],token_hash(body.token)):
        raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط منتهي أو غير صالح أو مستخدم.')
    return _accept_email_identity(challenge,request,response,body.display_name,body.phone)


def _accept_email_identity(challenge,request,response,display_name=None,phone=None):
    """One canonical atomic acceptance path for email links and staff numeric codes."""
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM identity_tokens WHERE id=%s AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>now() FOR UPDATE',(challenge['id'],)).fetchone()
        if not row:
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط منتهي أو غير صالح أو مستخدم.')
        if row['session_id']:
            current=_optional_identity(request,conn)
            if not current or current['session_id']!=row['session_id'] or current['id']!=row['user_id']:
                raise db.error(403,'LINK_SESSION_REQUIRED','افتح الرابط من جلسة الحساب الذي طلب ربط البريد.')
        user=conn.execute('SELECT * FROM users WHERE id=%s FOR UPDATE',(row['user_id'],)).fetchone() if row['user_id'] else None
        invitation=row['purpose'] in {'invitation','add_owner'}
        if user and ((not user['active'] and not invitation) or user['brand_id']!=row['brand_id']):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        if invitation and (not user or user['invitation_status']!='pending_email' or not _same_email(user.get('email'),row['email'])):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الدعوة ملغاة أو مستهلكة.')
        if row['purpose'] in {'staff_login','invitation','add_owner'} and not _staff_token_authorized(conn,row,user):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الحساب أو الدعوة لم يعودا مخولين.')
        if row['purpose']=='email_login' and user and user['role']!='customer':
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        channel=_channel(conn,row['brand_id'],'email')
        if channel['state'] not in {'testing','active'} or (channel['state']=='active' and not channel['enabled']):
            raise db.error(400,'EMAIL_TOKEN_INVALID','قناة البريد متوقفة حاليًا.')
        owner_approved_driver = bool(user and row['purpose']=='staff_login' and user['role']=='driver' and user.get('identity_kind')=='staff_owner_approved')
        if row['purpose'] in {'staff_login','recovery'} and (not user or (not user['email_verified_at'] and not owner_approved_driver) or not _same_email(user.get('email'),row['email'])):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        if row['purpose']=='staff_login' and user['role']=='customer':
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        if row['purpose']=='owner_link' and (not user or user['role']!='owner'):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        if row['purpose']=='customer_link' and (not user or user['role']!='customer'):
            raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
        conflict=conn.execute('SELECT id FROM users WHERE brand_id=%s AND lower(btrim(email))=%s',(row['brand_id'],email_delivery.normalize_email(row['email']))).fetchone()
        if conflict and (not user or conflict['id']!=user['id']):
            raise db.error(409,'EMAIL_IDENTITY_CONFLICT','البريد مرتبط بحساب آخر. لا يتم دمج الحسابات تلقائيًا.')
        if not user:
            if row['purpose']!='email_login':
                raise db.error(400,'EMAIL_TOKEN_INVALID','الرابط غير صالح.')
            user=conn.execute("INSERT INTO users(id,role,brand_id,display_name,email,email_verified_at,identity_kind,phone) VALUES(%s,'customer',%s,%s,%s,now(),'email_verified',%s) RETURNING *",(db.new_id('customer_'),row['brand_id'],display_name or '',row['email'],normalize_phone(phone) if phone else None)).fetchone()
        else:
            name=display_name.strip() if display_name else user['display_name']
            chosen_phone=normalize_phone(phone) if phone else user['phone']
            if user['role']!='customer' and phone and user.get('phone') and chosen_phone!=normalize_phone(user['phone']):
                raise db.error(409,'STAFF_PHONE_REGISTERED','استخدم رقم الجوال المسجل؛ تغييره من الإدارة المخولة.')
            if invitation and (not name or not chosen_phone):
                raise db.error(422,'STAFF_PROFILE_REQUIRED','أكمل الاسم ورقم الاتصال.')
            if user.get('phone_verified_at') and chosen_phone!=user['phone']:
                raise db.error(409,'PHONE_CHANGE_REQUIRES_VERIFICATION','تغيير الجوال الموثق يتطلب تحققًا مستقلاً.')
            if user['role']!='customer' and chosen_phone:
                _assert_staff_phone_available(conn,user['brand_id'],normalize_phone(chosen_phone),user['id'])
            user=conn.execute("UPDATE users SET email=%s,email_verified_at=now(),identity_kind=%s,display_name=%s,phone=%s,active=true,invitation_status='active',updated_at=now() WHERE id=%s RETURNING *",(row['email'],'email_verified' if user['role']=='customer' else 'staff_email_verified',name,chosen_phone,user['id'])).fetchone()
        conn.execute('UPDATE identity_tokens SET consumed_at=now() WHERE id=%s',(row['id'],))
        conn.execute('UPDATE identity_channels SET delivery_proven_at=now(),updated_at=now() WHERE brand_id=%s AND channel=%s',(row['brand_id'],'email'))
        conn.execute("UPDATE email_outbox SET status='delivered',delivery_at=now(),updated_at=now() WHERE brand_id=%s AND dedupe_key=%s AND status IN ('pending','provider_accepted','sending','failed')",(row['brand_id'],'identity:'+row['id']))
        if row['purpose'] in {'owner_link','recovery'}:
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(user['id'],))
        elif row['session_id']:
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s',(row['session_id'],))
        # Proof belongs only to the session created by this challenge. An
        # independent older browser session must never inherit this step-up.
        result=establish_session(conn,user,response,owner_step_up=user['role']=='owner')
        db.audit(conn,user['id'],'identity.email.verified','user',user['id'],{'purpose':row['purpose']})
        return result


def require_owner_step_up(conn,user):
    if user['role']!='owner' or not user.get('session_id') or not conn.execute("SELECT 1 FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() AND step_up_at>now()-interval '10 minutes'",(user.get('session_id'),user['id'])).fetchone():
        raise db.error(403,'OWNER_STEP_UP_REQUIRED','أعد تحقق المالك قبل دعوة مالك أو مدير إضافي.')


class StepUpBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:str=Field(min_length=1,max_length=200)


@router.post('/identity/step-up')
def owner_step_up(body:StepUpBody,user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'owner-step-up:'+user['id'],5,600)
    if not allowed:
        raise db.error(429,'STEP_UP_RATE_LIMIT','انتظر قبل إعادة التحقق.')
    with db.tx() as conn:
        if not verify_password(body.password,user.get('password_hash')):
            raise db.error(401,'INVALID_CREDENTIALS','تعذر إعادة التحقق من الحساب.')
        conn.execute('UPDATE sessions SET step_up_at=now() WHERE id=%s AND revoked_at IS NULL',(user['session_id'],))
        db.audit(conn,user['id'],'owner.step_up','user',user['id'])
    return {'verified':True,'expires_in':600}


def _staff_scope(conn,user,branches):
    if len(branches)>500 or len(set(branches))!=len(branches):
        raise db.error(422,'BRANCH_SCOPE_INVALID','أزل التكرار وحدد الفروع المصرح بها.')
    for branch in branches:
        if not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(branch,user['brand_id'])).fetchone():
            raise db.error(403,'BRANCH_SCOPE_FORBIDDEN','الفرع خارج البراند المصرح به.')
    if user['role']!='owner' and not set(branches)<=set(user.get('branch_ids',[])):
        raise db.error(403,'BRANCH_SCOPE_FORBIDDEN','الفرع خارج نطاق المدير.')
    return branches


def _assert_staff_phone_available(conn,brand,phone,exclude=None):
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(brand+':staff-phone:'+phone,))
    if conn.execute("SELECT 1 FROM users WHERE brand_id=%s AND role<>'customer' AND normalized_identity_phone(phone)=%s AND (%s::text IS NULL OR id<>%s)",(brand,phone,exclude,exclude)).fetchone():
        raise db.error(409,'STAFF_IDENTITY_CONFLICT','رقم الجوال مسجل لحساب موظف آخر؛ لا يتم تكرار هوية الموظف.')


def ensure_safe_staff_scope_change(conn,row,new_scope,new_active):
    """Never strand a running task or the driver's custody by removing its executor."""
    if row['role']=='owner' and not new_active:
        conn.execute("SELECT id FROM users WHERE brand_id=%s AND role='owner' AND active ORDER BY id FOR UPDATE",(row['brand_id'],)).fetchall()
        if not conn.execute("SELECT 1 FROM users WHERE brand_id=%s AND role='owner' AND active AND id<>%s",(row['brand_id'],row['id'])).fetchone():
            raise db.error(409,'LAST_OWNER_PROTECTED','لا يمكن إيقاف آخر مالك نشط.')
    removed=set(row.get('branch_ids',[]))-set(new_scope)
    if (not new_active) and conn.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(row['id'],)).fetchone():
        raise db.error(409,'STAFF_OPEN_SHIFT','أغلق الشفت وسلّم العهدة قبل إيقاف الحساب.')
    if (not new_active) and conn.execute("SELECT 1 FROM driver_tasks WHERE driver_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned')",(row['id'],)).fetchone():
        raise db.error(409,'STAFF_OPEN_TASKS','أعد إسناد مهام الموظف قبل إيقاف حسابه.')
    if removed and conn.execute("SELECT 1 FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.driver_id=%s AND o.branch_id=ANY(%s) AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')",(row['id'],list(removed))).fetchone():
        raise db.error(409,'STAFF_OPEN_TASKS','أعد إسناد المهام مع قبول التسليم قبل سحب فرع السائق.')
    if removed and row['role'] in {'driver','delivery_manager','delivery_staff'}:
        if conn.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed' AND branch_ids ?| %s",(row['id'],list(removed))).fetchone():
            raise db.error(409,'STAFF_OPEN_SHIFT','سلّم المناوبة والعهدة قبل سحب نطاق الشفت المفتوح.')


def _staff_record(conn,identifier,user):
    row=conn.execute("SELECT * FROM users WHERE id=%s AND brand_id=%s AND role<>'customer' FOR UPDATE",(identifier,user['brand_id'])).fetchone()
    if not row or (user['role']!='owner' and (row['invitation_status']=='archived' or row['role']!='driver' or not set(row['branch_ids'])<=set(user.get('branch_ids',[])))):
        raise db.error(404,'STAFF_NOT_FOUND','السجل غير موجود ضمن صلاحياتك.')
    return row


def staff_public(conn,row):
    output=public_user(row)
    shift=conn.execute("SELECT id,kind,status,available FROM shifts WHERE user_id=%s AND status<>'closed' ORDER BY opened_at DESC LIMIT 1",(row['id'],)).fetchone()
    output['open_shift']=shift
    busy=bool(conn.execute("SELECT 1 FROM driver_tasks WHERE driver_id=%s AND status NOT IN ('delivered','cancelled','transferred')",(row['id'],)).fetchone())
    output['available']=bool(shift and shift['status']=='open' and shift.get('available') and not busy and row['active'])
    return output


class StaffInvitationBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    display_name:str=Field(min_length=1,max_length=100)
    email:str=Field(min_length=3,max_length=254)
    phone:str=Field(min_length=8,max_length=30)
    branch_ids:list[str]=Field(default_factory=list,max_length=500)
    employee_ref:str|None=Field(default=None,max_length=80)
    vehicle:dict=Field(default_factory=dict)
    role:Literal['driver','owner','admin','delivery_manager','delivery_staff','branch_manager']='driver'
    permissions:list[str]=Field(default_factory=list,max_length=20)
    permission_denials:list[str]=Field(default_factory=list,max_length=80)


def _vehicle(value):
    from .vehicle_profile import validate
    return validate(value)


@router.put('/identity/driver/vehicle')
def register_driver_vehicle(body: dict, user=Depends(require_roles('driver'))):
    if {'baseline_kml','baseline_note'} & body.keys():
        raise db.error(403,'VEHICLE_MANAGER_ONLY','معدل الوقود يحدده مدير التوصيل.')
    vehicle = _vehicle(body)
    if not str(vehicle.get('plate','')).strip() or 'owned_by_kps' not in vehicle:
        raise db.error(422,'VEHICLE_REQUIRED','أدخل لوحة المركبة وحدد هل هي تابعة لكبز.')
    with db.tx() as conn:
        previous = conn.execute('SELECT vehicle FROM users WHERE id=%s FOR UPDATE',(user['id'],)).fetchone()['vehicle'] or {}
        if previous.get('plate'):
            raise db.error(409,'VEHICLE_MANAGER_ONLY','بيانات مركبتك مسجلة؛ تعديلها متاح لمدير التوصيل فقط.')
        conn.execute('UPDATE users SET vehicle=%s,updated_at=now() WHERE id=%s',(db.json(vehicle),user['id']))
        db.audit(conn,user['id'],'driver.vehicle.registered','user',user['id'],{'vehicle':vehicle})
        return {'vehicle':vehicle}


@router.post('/identity/staff/invitations')
def invite_staff(body:StaffInvitationBody,user=Depends(require_roles('owner','delivery_manager'))):
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'staff-invite:'+user['id'],30,3600)
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','دعوات كثيرة. انتظر قليلًا.')
    with db.tx() as conn:
        return create_staff_invitation(conn,body,user)


def create_staff_invitation(conn,body:StaffInvitationBody,actor,*,draft_only=False,maintenance_provenance=None):
    """Canonical invitation creation for HTTP and audited internal draft import.

    The maintenance option has no public route. It can only register dormant
    delivery accounts attributed to the actual active owner. It never issues a
    token, sends mail, creates a session, or asserts verification. The existing
    resend/acceptance policies govern all subsequent activation.
    """
    if not isinstance(body,StaffInvitationBody):
        body=StaffInvitationBody.model_validate(body)
    user=conn.execute('SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id '
                      'WHERE u.id=%s AND u.active AND b.active', (actor['id'],)).fetchone()
    if not user or user['role'] not in {'owner','delivery_manager'}:
        raise db.error(403,'ROLE_FORBIDDEN','إنشاء الدعوة يتطلب مالكًا أو مدير توصيل مخولًا.')
    if user['brand_id']!=actor.get('brand_id'):
        raise db.error(403,'BRAND_SCOPE_FORBIDDEN','الدعوة خارج نطاق البراند المخول.')
    user=dict(user,session_id=actor.get('session_id'))
    if draft_only:
        provenance=maintenance_provenance or {}
        if user['role']!='owner' or body.role not in {'delivery_manager','driver'}:
            raise db.error(403,'DELIVERY_DRAFT_OWNER_REQUIRED','تسجيل مسودة التوصيل الداخلي للمالك فقط.')
        if set(provenance)!={'decision_id','source_sha256'} or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}',str(provenance.get('decision_id',''))) or not re.fullmatch(r'[a-f0-9]{64}',str(provenance.get('source_sha256',''))):
            raise db.error(422,'DRAFT_APPROVAL_PROVENANCE_REQUIRED','يلزم مرجع اعتماد وبصمة الملف المعتمد لتسجيل المسودة.')
    elif maintenance_provenance is not None:
        raise db.error(422,'DRAFT_APPROVAL_PROVENANCE_REQUIRED','مرجع الصيانة مخصص لتسجيل المسودة فقط.')
    permissions=validate_admin_permissions(body.permissions)
    denials=validate_permission_denials(body.permission_denials,body.role)
    if denials and user['role']!='owner':
        raise db.error(403,'STAFF_PERMISSION_OWNER_REQUIRED','المالك فقط يحدد صلاحيات الموظفين.')
    if permissions and body.role != 'admin':
        raise db.error(422, 'ADMIN_ROLE_REQUIRED', 'صلاحيات الإدارة مخصصة لدور أدمن فقط.')
    email=email_delivery.normalize_email(body.email)
    phone=normalize_phone(body.phone)
    name=body.display_name.strip()
    if not name or '\x00' in name:
        raise db.error(422,'STAFF_PROFILE_REQUIRED','أدخل الاسم الكامل.')
    if user['role']=='delivery_manager' and body.role!='driver':
        raise db.error(403,'DRIVER_ROLE_ONLY','مدير التوصيل يدعو سائقًا فقط.')
    vehicle=_vehicle(body.vehicle)
    channel=_channel(conn,user['brand_id'],'email')
    ready=not draft_only and email_delivery.configuration()['configured'] and (
        channel['state']=='testing' or (channel['state']=='active' and channel['enabled']))
    scope=_staff_scope(conn,user,body.branch_ids)
    if body.role!='owner' and not scope:
        raise db.error(422,'BRANCH_SCOPE_REQUIRED','حدد فرعًا أو أكثر للموظف.')
    if (body.role in {'owner','admin','delivery_manager'} or denials) and not draft_only:
        require_owner_step_up(conn,user)
    # Serialize identity conflicts without revealing the matched account's attributes.
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(user['brand_id']+':email:'+email,))
    if conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND lower(btrim(email))=%s',(user['brand_id'],email)).fetchone():
        raise db.error(409,'STAFF_IDENTITY_CONFLICT','تعذر إنشاء الدعوة. راجع البريد أو الدعوات المخولة.')
    _assert_staff_phone_available(conn,user['brand_id'],phone)
    identifier=db.new_id('staff_')
    invitation_state='pending_email' if ready else 'draft'
    row=conn.execute("INSERT INTO users(id,display_name,email,phone,role,brand_id,branch_ids,active,identity_kind,invitation_status,employee_ref,vehicle) VALUES(%s,%s,%s,%s,%s,%s,%s,false,'pending_email',%s,%s,%s) RETURNING *",(identifier,name,email,phone,body.role,user['brand_id'],db.json(scope),invitation_state,body.employee_ref,db.json(vehicle))).fetchone()
    row=conn.execute('UPDATE users SET permissions=%s,permission_denials=%s WHERE id=%s RETURNING *',(db.json(permissions),db.json(denials),identifier)).fetchone()
    challenge=_create_email_token(conn,brand=user['brand_id'],email=email,purpose='add_owner' if body.role=='owner' else 'invitation',user_id=identifier,created_by=user['id']) if ready else None
    details={'role':body.role,'branch_ids':scope,'permissions':permissions,'permission_denials':denials}
    if draft_only:
        details['owner_decision']=maintenance_provenance
    db.audit(conn,user['id'],'staff.invited' if ready else 'staff.draft.created','user',identifier,details)
    return {'user':staff_public(conn,row),'challenge_id':challenge,'invitation_status':invitation_state,
            'delivery_status':'queued' if ready else 'draft_only' if draft_only else 'channel_unavailable'}


@router.get('/identity/staff')
def staff_list(user=Depends(require_roles('owner','delivery_manager'))):
    with db.tx() as conn:
        rows=conn.execute("SELECT * FROM users WHERE brand_id=%s AND role<>'customer' ORDER BY role,display_name LIMIT 500",(user['brand_id'],)).fetchall()
        if user['role']=='delivery_manager':
            rows=[r for r in rows if r['invitation_status']!='archived' and r['role']=='driver' and set(r['branch_ids'])<=set(user.get('branch_ids',[]))]
        return {'users':[staff_public(conn,row) for row in rows], 'permission_catalog':employee_permissions.catalog()}


class EmployeePermissionsBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled_permissions:list[str]=Field(max_length=80)
    expected_permissions:list[str]=Field(max_length=80)
    reason:str=Field(min_length=3,max_length=500)


@router.put('/identity/staff/{identifier}/permissions')
def set_employee_permissions(identifier:str,body:EmployeePermissionsBody,user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        require_owner_step_up(conn,user)
        row=_staff_record(conn,identifier,user)
        if row['role']=='owner' or row['invitation_status']=='archived':
            raise db.error(409,'STAFF_PERMISSIONS_LOCKED','صلاحيات المالك كاملة؛ استعد الموظف المحذوف قبل تعديل صلاحياته.')
        values=body.enabled_permissions
        if len(values)!=len(set(values)) or not set(values)<=employee_permissions.eligible(row['role']) or not body.reason.strip():
            raise db.error(422,'STAFF_PERMISSIONS_INVALID','اختر صلاحيات متاحة لدور الموظف فقط وسجل سبب التغيير.')
        before=sorted(employee_permissions.effective(row))
        if sorted(body.expected_permissions)!=before:
            raise db.error(409,'STAFF_PERMISSIONS_CHANGED','تغيرت صلاحيات الموظف؛ حدّث القائمة ثم أعد المراجعة.')
        grants=sorted(values) if row['role']=='admin' else []
        denials=sorted(employee_permissions.eligible(row['role'])-set(values)) if row['role']!='admin' else []
        output=conn.execute('UPDATE users SET permissions=%s,permission_denials=%s,updated_at=now() WHERE id=%s RETURNING *',
                            (db.json(grants),db.json(denials),identifier)).fetchone()
        _invalidate_staff_tokens(conn,identifier)
        # Sessions remain valid; current_user reloads grants before every action.
        db.audit(conn,user['id'],'staff.permissions.updated','user',identifier,
                 {'before':before,'after':sorted(values),'reason':body.reason.strip()})
        return {'user':staff_public(conn,output)}


def _invalidate_staff_tokens(conn,identifier):
    conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL AND revoked_at IS NULL',(identifier,))
    conn.execute("UPDATE email_outbox SET status='cancelled' WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s AND revoked_at IS NOT NULL) AND status IN ('pending','failed')",(identifier,))


class OwnerDriverActivationBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=3,max_length=500)


class AdditionalOwnerBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    display_name:str=Field(min_length=1,max_length=100)
    username:str=Field(min_length=3,max_length=150)
    password:str=Field(min_length=8,max_length=200,repr=False)
    password_confirmation:str=Field(min_length=8,max_length=200,repr=False)
    reason:str=Field(min_length=3,max_length=500)


@router.post('/identity/owners/password')
def create_additional_password_owner(body:AdditionalOwnerBody,user=Depends(require_roles('owner'))):
    """Explicit full-owner grant with step-up; never overwrite an existing account."""
    with db.tx() as conn:
        require_owner_step_up(conn,user)
        username=body.username.strip()
        name=body.display_name.strip()
        if not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,150}',username) or not name or '\x00' in name or not body.reason.strip():
            raise db.error(422,'OWNER_PROFILE_INVALID','أدخل الاسم واسم مستخدم صحيحًا وسبب الإضافة.')
        if body.password!=body.password_confirmation:
            raise db.error(422,'PASSWORD_CONFIRMATION','كلمتا المرور غير متطابقتين.')
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('staff-username:'+username.casefold(),))
        if conn.execute('SELECT 1 FROM users WHERE lower(username)=lower(%s)',(username,)).fetchone():
            raise db.error(409,'USERNAME_UNAVAILABLE','اسم المستخدم مستخدم بالفعل؛ اختر اسمًا آخر. لم يتغير أي حساب.')
        identifier=db.new_id('owner_')
        row=conn.execute("INSERT INTO users(id,username,display_name,password_hash,role,brand_id,active,invitation_status,identity_kind) VALUES(%s,%s,%s,%s,'owner',%s,true,'active','staff_password') RETURNING *",(identifier,username,name,hash_password(body.password,min_length=8),user['brand_id'])).fetchone()
        db.audit(conn,user['id'],'owner.password_account.created','user',identifier,{'reason':body.reason.strip(),'username':username,'role':'owner'})
        return {'user':public_user(row),'username':username}


@router.post('/identity/staff/{identifier}/owner-activate')
def owner_activate_driver(identifier:str,body:OwnerDriverActivationBody,user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        require_owner_step_up(conn,user)
        row=conn.execute('SELECT * FROM users WHERE id=%s AND brand_id=%s FOR UPDATE',(identifier,user['brand_id'])).fetchone()
        if not row or row['role']!='driver':
            raise db.error(404,'DRIVER_NOT_FOUND','حساب السائق غير موجود ضمن البراند.')
        if row['invitation_status']=='archived':
            raise db.error(409,'STAFF_ARCHIVED','استعد الموظف المحذوف أولًا.')
        if not body.reason.strip() or not row['email'] or not row['phone'] or not row['branch_ids']:
            raise db.error(422,'STAFF_PROFILE_REQUIRED','أكمل البريد والجوال والفروع وسبب التفعيل أولًا.')
        _staff_scope(conn,user,row['branch_ids'])
        if row['active'] and row['invitation_status']=='active':
            return {'user':staff_public(conn,row),'already_active':True}
        output=conn.execute("UPDATE users SET active=true,invitation_status='active',identity_kind=CASE WHEN email_verified_at IS NULL THEN 'staff_owner_approved' ELSE identity_kind END,updated_at=now() WHERE id=%s RETURNING *",(identifier,)).fetchone()
        conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL AND revoked_at IS NULL',(identifier,))
        conn.execute("UPDATE email_outbox SET status='cancelled' WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s AND revoked_at IS NOT NULL) AND status IN ('pending','failed')",(identifier,))
        db.audit(conn,user['id'],'driver.owner_activated','user',identifier,{'reason':body.reason.strip(),'email_verified':bool(row['email_verified_at'])})
        return {'user':staff_public(conn,output),'sign_in':'registered_email_code','email_verification_unchanged':True}


class StaffUpdateBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str|None=Field(default=None,min_length=3,max_length=254)
    display_name:str|None=Field(default=None,min_length=1,max_length=100)
    phone:str|None=Field(default=None,min_length=8,max_length=30)
    branch_ids:list[str]|None=Field(default=None,max_length=500)
    active:bool|None=None
    employee_ref:str|None=Field(default=None,max_length=80)
    vehicle:dict|None=None
    permissions:list[str]|None=Field(default=None,max_length=20)
    permission_denials:list[str]|None=Field(default=None,max_length=80)
    role:Literal['driver','owner','admin','delivery_manager','delivery_staff','branch_manager']|None=None
    reason:str|None=Field(default=None,min_length=3,max_length=500)


@router.patch('/identity/staff/{identifier}')
def update_staff(identifier:str,body:StaffUpdateBody,user=Depends(require_roles('owner','delivery_manager'))):
    with db.tx() as conn:
        row=_staff_record(conn,identifier,user)
        if row['invitation_status']=='archived':
            raise db.error(409,'STAFF_ARCHIVED','استعد الموظف المحذوف قبل تعديل بياناته.')
        role=body.role if body.role is not None else row['role']
        role_changed=role!=row['role']
        if role_changed:
            require_owner_step_up(conn,user)
            if identifier==user['id'] or not (body.reason or '').strip():
                raise db.error(409,'STAFF_ROLE_CHANGE_BLOCKED','لا تغير دور حسابك الحالي، وسجل سبب تغيير دور الموظف.')
            ensure_safe_staff_scope_change(conn,row,[],False)
        if body.permissions is not None:
            if user['role']!='owner' or role!='admin':
                raise db.error(403,'ADMIN_PERMISSION_OWNER_REQUIRED','المالك فقط يحدد صلاحيات الأدمن.')
            permissions=validate_admin_permissions(body.permissions)
            require_owner_step_up(conn,user)
        else:
            permissions=(row.get('permissions') or []) if role=='admin' else []
        denials=[] if role_changed else row.get('permission_denials') or []
        if body.permission_denials is not None:
            if user['role']!='owner':
                raise db.error(403,'STAFF_PERMISSION_OWNER_REQUIRED','المالك فقط يحدد صلاحيات الموظفين.')
            denials=validate_permission_denials(body.permission_denials,role)
            if denials!=(row.get('permission_denials') or []):
                require_owner_step_up(conn,user)
        scope=_staff_scope(conn,user,body.branch_ids if body.branch_ids is not None else row['branch_ids'])
        active=body.active if body.active is not None else row['active']
        if role_changed and not row['email_verified_at']:
            active=False
        email=email_delivery.normalize_email(body.email) if body.email is not None else row['email']
        email_changed=email!=row['email']
        if email_changed:
            if row['role']=='owner':
                require_owner_step_up(conn,user)
            if conn.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(row['id'],)).fetchone():
                raise db.error(409,'STAFF_OPEN_SHIFT','أغلق الشفت وسلّم العهدة قبل تغيير بريد الدخول.')
            if conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND lower(email)=%s AND id<>%s',(user['brand_id'],email,identifier)).fetchone():
                raise db.error(409,'STAFF_IDENTITY_CONFLICT','البريد غير متاح لحساب الموظف.')
            if body.active:
                raise db.error(403,'STAFF_EMAIL_VERIFICATION_REQUIRED','تغيير البريد يتطلب دعوة وتحققًا جديدين.')
            active=False
        if active and (row['invitation_status'] not in {'active','paused'} or not row['email_verified_at']):
            # Legacy active staff remain during migration, but cannot be activated by bypassing proof.
            if not (row['active'] and row['identity_kind'] in {'legacy','staff_owner_approved'}):
                raise db.error(403,'STAFF_EMAIL_VERIFICATION_REQUIRED','ينشط الموظف بعد تحقق بريده؛ لا توثيق بالنيابة عنه.')
        ensure_safe_staff_scope_change(conn,row,scope,active)
        name=body.display_name.strip() if body.display_name is not None else row['display_name']
        if not name or '\x00' in name:
            raise db.error(422,'STAFF_PROFILE_REQUIRED','أدخل الاسم الكامل.')
        phone=normalize_phone(body.phone) if body.phone else row['phone']
        if row['phone_verified_at'] and phone!=row['phone']:
            raise db.error(409,'PHONE_CHANGE_REQUIRES_VERIFICATION','رقم موثق يحتاج تحققًا مستقلًا لتغييره.')
        if phone and (body.phone is not None or email_changed):
            _assert_staff_phone_available(conn,user['brand_id'],normalize_phone(phone),identifier)
        vehicle=_vehicle(body.vehicle) if body.vehicle is not None else row['vehicle']
        status=row['invitation_status'] if active else 'paused' if row['invitation_status']=='active' else row['invitation_status']
        if active and row['email_verified_at']:
            status='active'
        output=conn.execute('UPDATE users SET display_name=%s,phone=%s,branch_ids=%s,active=%s,employee_ref=%s,vehicle=%s,invitation_status=%s,updated_at=now() WHERE id=%s RETURNING *',(name,phone,db.json(scope),active,body.employee_ref if 'employee_ref' in body.model_fields_set else row['employee_ref'],db.json(vehicle),status,identifier)).fetchone()
        if body.permissions is not None or role_changed:
            output=conn.execute('UPDATE users SET permissions=%s WHERE id=%s RETURNING *',(db.json(permissions),identifier)).fetchone()
        if body.permission_denials is not None or role_changed:
            output=conn.execute('UPDATE users SET permission_denials=%s WHERE id=%s RETURNING *',(db.json(denials),identifier)).fetchone()
            if denials!=(row.get('permission_denials') or []):
                _invalidate_staff_tokens(conn,identifier)
        if role_changed:
            output=conn.execute('UPDATE users SET role=%s WHERE id=%s RETURNING *',(role,identifier)).fetchone()
            if not row['email_verified_at']:
                output=conn.execute("UPDATE users SET invitation_status='draft' WHERE id=%s RETURNING *",(identifier,)).fetchone()
        if email_changed:
            output=conn.execute("UPDATE users SET email=%s,email_verified_at=NULL,identity_kind='pending_email',invitation_status='draft' WHERE id=%s RETURNING *",(email,identifier)).fetchone()
        if role_changed or scope!=row['branch_ids'] or active!=row['active'] or email_changed or phone!=row['phone'] or permissions!=(row.get('permissions') or []):
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(identifier,))
            conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL',(identifier,))
            conn.execute("UPDATE email_outbox SET status='cancelled' WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s) AND status IN ('pending','failed')",(identifier,))
        db.audit(conn,user['id'],'staff.updated','user',identifier,{'fields':sorted(body.model_fields_set),'reason':body.reason,**({'old_role':row['role'],'role':role} if role_changed else {}),**({'permissions':permissions} if body.permissions is not None else {}),**({'old_permission_denials':row.get('permission_denials') or [],'permission_denials':denials} if body.permission_denials is not None else {})})
        return {'user':staff_public(conn,output)}


class StaffLifecycleBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['pause','archive','restore','reinvite']
    reason:str=Field(min_length=3,max_length=500)


@router.post('/identity/staff/{identifier}/manage')
def manage_staff(identifier:str,body:StaffLifecycleBody,user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        require_owner_step_up(conn,user)
        row=_staff_record(conn,identifier,user)
        if identifier==user['id'] or not body.reason.strip():
            raise db.error(409,'STAFF_SELF_CHANGE_BLOCKED','استخدم حساب مالك آخر لإيقاف حسابك وسجل السبب.')
        archived=row['invitation_status']=='archived'
        if body.action=='restore' and not archived:
            raise db.error(409,'STAFF_NOT_ARCHIVED','الموظف غير محذوف.')
        if archived and body.action not in {'restore','archive'}:
            raise db.error(409,'STAFF_ARCHIVED','استعد الموظف المحذوف أولًا.')
        ensure_safe_staff_scope_change(conn,row,[],False)
        # Archive only the account; operational and financial history is retained.
        state={'pause':'paused','archive':'archived','restore':'paused','reinvite':'pending_email'}[body.action]
        if body.action=='reinvite':
            if not row['email'] or not row['phone'] or (row['role']!='owner' and not row['branch_ids']):
                raise db.error(422,'STAFF_PROFILE_REQUIRED','أكمل البريد والجوال والفروع قبل الدعوة.')
            _email_ready(conn,user['brand_id'],testing=True)
            if not db.rate_limit(conn,'invite-resend:'+identifier,3,600):
                raise db.error(429,'EMAIL_RATE_LIMIT','انتظر قبل إعادة الدعوة.')
        output=conn.execute('UPDATE users SET active=false,invitation_status=%s,updated_at=now() WHERE id=%s RETURNING *',(state,identifier)).fetchone()
        conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(identifier,))
        conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL AND revoked_at IS NULL',(identifier,))
        conn.execute("UPDATE email_outbox SET status='cancelled' WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s) AND status IN ('pending','failed')",(identifier,))
        if body.action=='reinvite':
            _create_email_token(conn,brand=user['brand_id'],email=row['email'],purpose='add_owner' if row['role']=='owner' else 'invitation',user_id=identifier,created_by=user['id'])
        db.audit(conn,user['id'],'staff.'+body.action,'user',identifier,{'reason':body.reason.strip(),'previous_status':row['invitation_status']})
        return {'user':staff_public(conn,output),**({'delivery_status':'queued'} if body.action=='reinvite' else {})}


@router.post('/identity/staff/{identifier}/resend')
def resend_invitation(identifier:str,user=Depends(require_roles('owner','delivery_manager'))):
    with db.tx() as conn:
        allowed=db.rate_limit(conn,'invite-resend:'+identifier,3,600)
    if not allowed:
        raise db.error(429,'EMAIL_RATE_LIMIT','انتظر قبل إعادة الدعوة.')
    with db.tx() as conn:
        row=_staff_record(conn,identifier,user)
        _email_ready(conn,user['brand_id'],testing=True)
        if row['invitation_status'] not in {'pending_email','draft'} or not row['email']:
            raise db.error(409,'INVITATION_NOT_PENDING','لا توجد دعوة معلقة قابلة للإرسال.')
        if row['role'] in {'owner','admin','delivery_manager'}:
            require_owner_step_up(conn,user)
        conn.execute("UPDATE users SET invitation_status='pending_email' WHERE id=%s",(identifier,))
        challenge=_create_email_token(conn,brand=user['brand_id'],email=row['email'],purpose='add_owner' if row['role']=='owner' else 'invitation',user_id=identifier,created_by=user['id'])
        return {'accepted':True,'challenge_id':challenge,'delivery_status':'queued'}


@router.post('/identity/staff/{identifier}/revoke-invitation')
def revoke_invitation(identifier:str,user=Depends(require_roles('owner','delivery_manager'))):
    with db.tx() as conn:
        row=_staff_record(conn,identifier,user)
        if row['invitation_status'] not in {'pending_email','draft'}:
            raise db.error(409,'INVITATION_NOT_PENDING','الحساب مفعل؛ استخدم الإيقاف المخوّل بعد تسليم مهامه.')
        conn.execute("UPDATE users SET active=false,invitation_status='revoked',updated_at=now() WHERE id=%s",(identifier,))
        conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL',(identifier,))
        conn.execute("UPDATE email_outbox SET status='cancelled' WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s) AND status IN ('pending','failed')",(identifier,))
        db.audit(conn,user['id'],'staff.invitation.revoked','user',identifier)
        return {'revoked':True}


@router.get('/identity/settings')
def identity_settings(user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        rows=conn.execute('SELECT channel,state,enabled,delivery_proven_at,updated_at,sms_provider FROM identity_channels WHERE brand_id=%s ORDER BY channel',(user['brand_id'],)).fetchall()
        return {'channels':rows,'readiness':channel_readiness(conn,user['brand_id']),
                'email_provider':email_delivery.configuration(),
                'sms_provider':_sms_provider(conn,user['brand_id']),
                'sms_providers':['disabled','tawked','twilio_verify'],
                'required_secret_settings':['TAWKED_API_KEY','EMAIL_PROVIDER','EMAIL_FROM','MAIL_OUTBOX_KEY','RESEND_API_KEY or SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD'],
                'email_security':'لا تستخدم كلمة مرور Gmail في المحادثة. استخدم مزودًا مخولًا أو بيانات SMTP مخصصة في إعدادات الخادم السرية.'}


class ChannelUpdateBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    channel:Literal['guest','email','sms','whatsapp']
    state:Literal['unconfigured','testing','active','paused','fault']
    enabled:bool=False
    sms_provider:Literal['disabled','tawked','twilio_verify'] | None = None


@router.patch('/identity/settings')
def update_identity_settings(body:ChannelUpdateBody,user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        conn.execute('SELECT channel FROM identity_channels WHERE brand_id=%s AND channel=%s FOR UPDATE',(user['brand_id'],body.channel)).fetchone()
        row=_channel(conn,user['brand_id'],body.channel)
        if body.sms_provider is not None:
            if body.channel != 'sms':
                raise db.error(422,'SMS_PROVIDER_CHANNEL','اختر قناة SMS لتغيير المزود.')
            if body.sms_provider != _sms_provider(conn,user['brand_id']):
                if body.state == 'active' or body.enabled:
                    raise db.error(409,'CHANNEL_DELIVERY_PROOF_REQUIRED','اختبر المزود الجديد قبل التفعيل.')
                conn.execute("UPDATE identity_channels SET sms_provider=%s,delivery_proven_at=NULL WHERE brand_id=%s AND channel='sms'",(body.sms_provider,user['brand_id']))
                conn.execute("UPDATE otp_challenges SET expires_at=LEAST(expires_at,now()) WHERE brand_id=%s AND delivery_channel='sms' AND verified_at IS NULL",(user['brand_id'],))
        if body.channel!='guest' and body.state=='active':
            configured=channel_readiness(conn,user['brand_id'])[body.channel]['configured']
            if not configured or not row['delivery_proven_at']:
                raise db.error(409,'CHANNEL_DELIVERY_PROOF_REQUIRED','يلزم إعداد مخوّل وإثبات وصول واستخدام رمز فعلي قبل التفعيل.')
        if body.channel=='guest' and body.state not in {'active','paused'}:
            raise db.error(422,'GUEST_POLICY_INVALID','سياسة الضيف مفعلة أو متوقفة.')
        if body.enabled and body.state!='active':
            raise db.error(422,'CHANNEL_STATE_INVALID','القناة تعمل فقط في الحالة المجربة والمفعلة.')
        conn.execute('INSERT INTO identity_channels(brand_id,channel,state,enabled,updated_by) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(brand_id,channel) DO UPDATE SET state=excluded.state,enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=now()',(user['brand_id'],body.channel,body.state,body.enabled,user['id']))
        db.audit(conn,user['id'],'identity.channel.updated','brand',user['brand_id'],body.model_dump())
        return {'channels':channel_readiness(conn,user['brand_id'])}


@router.get('/identity/email/outbox')
def email_outbox_status(user=Depends(require_roles('owner'))):
    with db.tx() as conn:
        return {'messages':conn.execute('SELECT id,kind,status,attempts,last_error,created_at,delivery_at FROM email_outbox WHERE brand_id=%s ORDER BY created_at DESC LIMIT 100',(user['brand_id'],)).fetchall()}

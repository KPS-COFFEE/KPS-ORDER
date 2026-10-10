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
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import db

router = APIRouter(prefix='/api')
COOKIE = 'kps_session'
CSRF_COOKIE = 'kps_csrf'
ROLES = {'owner', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver', 'customer'}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 200:
        raise db.error(422, 'PASSWORD_LENGTH', 'كلمة المرور يجب أن تكون بين 12 و200 حرف.')
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
    return {key: user.get(key) for key in ('id', 'role', 'brand_id', 'branch_ids', 'display_name', 'phone', 'active')}


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
    return conn.execute('SELECT u.*,s.id AS session_id,s.csrf_hash FROM sessions s JOIN users u ON u.id=s.user_id '
                        'JOIN brands b ON b.id=u.brand_id '
                        'WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now() AND u.active AND b.active',
                        (token_hash(token),)).fetchone()


def current_user(request: Request) -> dict:
    with db.tx() as conn:
        user = _find_session(conn, request)
    if not user:
        raise db.error(401, 'AUTHENTICATION_REQUIRED', 'سجّل الدخول للوصول إلى هذه البيانات.')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        check_origin(request)
        token = request.headers.get('x-csrf-token', '')
        if not token or not hmac.compare_digest(token_hash(token), user['csrf_hash']):
            raise db.error(403, 'CSRF_REJECTED', 'انتهى التحقق الآمن للطلب. حدّث الصفحة.')
    # Database result is fresh on every request: disabling an employee or changing branch access is immediate.
    return user


def require_roles(*roles):
    if not set(roles).issubset(ROLES):
        raise ValueError('Unknown role in authorization policy')

    def dependency(user=Depends(current_user)):
        if user['role'] not in roles:
            raise db.error(403, 'ROLE_FORBIDDEN', 'هذه العملية غير مسموحة لحسابك.')
        return user
    return dependency


def can_access_order(user: dict, order: dict, conn) -> bool:
    if not user.get('active', True) or user['brand_id'] != order['brand_id']:
        return False
    role = user['role']
    if role == 'owner':
        return True
    if role == 'customer':
        return user['id'] == order['customer_id']
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


def establish_session(conn, user: dict, response: Response) -> dict:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hours = max(1, min(72, int(os.getenv('SESSION_TTL_HOURS', '12'))))
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
    conn.execute('INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,%s,%s,%s)',
                 (db.new_id('session_'), token_hash(token), token_hash(csrf), user['id'], expiry))
    response.set_cookie(COOKIE, token, max_age=hours * 3600, httponly=True, secure=secure_cookie(), samesite='strict', path='/')
    response.set_cookie(CSRF_COOKIE, csrf, max_age=hours * 3600, httponly=False, secure=secure_cookie(), samesite='strict', path='/')
    db.audit(conn, user['id'], 'session.created', 'user', user['id'])
    return {'user': public_user(user), 'csrf_token': csrf}


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
    return {'user': public_user(user), 'csrf_token': csrf}


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
        user = conn.execute('SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id WHERE lower(u.username)=lower(%s) AND u.role<>%s AND u.active AND b.active',
                            (body.username, 'customer')).fetchone()
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
    value = re.sub(r'[\s()-]', '', value)
    if re.fullmatch(r'05\d{8}', value):
        value = '+966' + value[1:]
    if not re.fullmatch(r'\+[1-9]\d{7,14}', value):
        raise db.error(422, 'PHONE_INVALID', 'أدخل رقم جوال صحيحًا مع رمز الدولة.')
    return value


def provider_config() -> tuple[str, str, str]:
    if os.getenv('OTP_PROVIDER') != 'twilio_verify':
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'التحقق بالجوال غير مفعّل بعد. لا يمكن تأكيد الطلب قبل تفعيله.')
    sid, secret, service = (os.getenv(name, '') for name in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID'))
    if not re.fullmatch(r'AC[a-fA-F0-9]{32}', sid) or not re.fullmatch(r'VA[a-fA-F0-9]{32}', service) or not secret:
        raise db.error(503, 'OTP_PROVIDER_UNAVAILABLE', 'مزود التحقق غير مهيأ بعد.')
    return sid, secret, service


def _provider_post(path: str, data: dict):
    sid, secret, service = provider_config()
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            result = client.post(f'https://verify.twilio.com/v2/Services/{service}/{path}', data=data, auth=(sid, secret))
        if result.status_code >= 400:
            raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.')
        return result.json()
    except (httpx.HTTPError, ValueError):
        raise db.error(503, 'OTP_PROVIDER_FAILURE', 'تعذر الاتصال بمزود التحقق. حاول لاحقًا.') from None


class OTPRequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=8, max_length=30)
    brand_id: str = Field(default='kps', min_length=1, max_length=80)


@router.post('/auth/otp/request')
def request_otp(body: OTPRequestBody, request: Request):
    check_origin(request)
    provider_config()
    phone = normalize_phone(body.phone)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (body.brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        allowed = db.rate_limit(conn, 'otp-request-phone:' + phone, 3, 600)
        allowed = db.rate_limit(conn, 'otp-request-ip:' + ip, 10, 600) and allowed
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'طلبات تحقق كثيرة. انتظر قبل المحاولة مجددًا.')
    channel = os.getenv('OTP_CHANNEL', 'sms')
    if channel not in {'sms', 'whatsapp'}:
        raise db.error(503, 'OTP_CHANNEL_UNAVAILABLE', 'قناة التحقق غير مهيأة.')
    result = _provider_post('Verifications', {'To': phone, 'Channel': channel})
    if result.get('status') != 'pending' or not result.get('sid'):
        raise db.error(503, 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال رمز التحقق.')
    challenge_id = db.new_id('otp_')
    with db.tx() as conn:
        conn.execute('INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,expires_at) VALUES(%s,%s,%s,%s,now()+interval \'10 minutes\')',
                     (challenge_id, body.brand_id, phone, result['sid']))
    return {'challenge_id': challenge_id, 'expires_in': 600, 'channel': channel}


class OTPVerifyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(min_length=5, max_length=80)
    code: str = Field(pattern=r'^\d{4,10}$')
    display_name: str = Field(default='', max_length=100)


@router.post('/auth/otp/verify')
def verify_otp(body: OTPVerifyBody, request: Request, response: Response):
    check_origin(request)
    provider_config()
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn, 'otp-verify-ip:' + ip, 20, 600)
        challenge = conn.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s AND verified_at IS NULL AND expires_at>now() AND attempts<5 RETURNING *',
                                 (body.challenge_id,)).fetchone()
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'محاولات تحقق كثيرة. انتظر قليلًا.')
    if not challenge:
        raise db.error(400, 'OTP_EXPIRED', 'رمز التحقق منتهي أو مستهلك. اطلب رمزًا جديدًا.')
    # Only the provider's approved result authenticates a phone; typed phone/code never does.
    result = _provider_post('VerificationCheck', {'VerificationSid': challenge['provider_reference'], 'Code': body.code})
    if result.get('status') != 'approved' or result.get('to') != challenge['phone']:
        raise db.error(400, 'OTP_INVALID', 'رمز التحقق غير صحيح.')
    with db.tx() as conn:
        claimed = conn.execute('UPDATE otp_challenges SET verified_at=now() WHERE id=%s AND verified_at IS NULL AND expires_at>now() RETURNING id',
                               (body.challenge_id,)).fetchone()
        if not claimed:
            raise db.error(409, 'OTP_ALREADY_USED', 'تم استخدام طلب التحقق بالفعل.')
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,phone) VALUES(%s,%s,'customer',%s,%s) ON CONFLICT(brand_id,phone) WHERE role='customer' DO NOTHING",
                     (db.new_id('customer_'), body.display_name, challenge['brand_id'], challenge['phone']))
        user = conn.execute("SELECT * FROM users WHERE brand_id=%s AND phone=%s AND role='customer' AND active", (challenge['brand_id'], challenge['phone'])).fetchone()
        if not user:
            raise db.error(403, 'CUSTOMER_DISABLED', 'هذا الحساب موقوف. تواصل مع الإدارة.')
        return establish_session(conn, user, response)


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

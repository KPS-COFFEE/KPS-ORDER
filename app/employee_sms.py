"""Employee SMS proof without activation or customer-identity fallback.

Shares existing canonical challenges and sessions. No SMS is sent unless the
brand channel already has live proof. Reuses Tawked’s existing four-digit
contract, or a separately configured four-digit Twilio employee service.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
import httpx
from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, portals, sms_verification



class EmployeeSMSRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=8, max_length=30)
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    lang: Literal['ar','en'] = 'ar'


class EmployeeSMSVerify(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(pattern=r'^employee_otp_[A-Za-z0-9_-]{1,65}$')
    code: str = Field(pattern=r'^[0-9]{4}$')


def provider_ready(conn, brand):
    channel = auth._channel(conn, brand, 'sms')
    if channel['state'] != 'active' or not channel['enabled'] or not channel['delivery_proven_at']:
        raise db.error(503, 'EMPLOYEE_SMS_NOT_CERTIFIED', 'رسائل الموظفين غير معتمدة بعد. / Employee SMS is not certified yet.')
    provider = auth._sms_provider(conn, brand)
    auth._provider_ready(provider)
    if provider == 'tawked':
        return provider
    employee_service = os.getenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','')
    if not re.fullmatch(r'VA[a-fA-F0-9]{32}',employee_service) or employee_service == os.getenv('TWILIO_VERIFY_SERVICE_SID'):
        raise db.error(503,'EMPLOYEE_OTP_DEDICATED_SERVICE_REQUIRED','يلزم إعداد خدمة SMS مستقلة من 4 أرقام لحماية تحقق العملاء الحالي. / Configure a separate four-digit service to preserve existing customer verification.')
    return provider


def verify_service_length(service_sid=None):
    """Read the configured service; never alter a provider's shared settings."""
    sid, secret = (os.getenv(k, '') for k in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN'))
    service = service_sid or os.getenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','')
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            result = client.get('https://verify.twilio.com/v2/Services/' + service, auth=(sid,secret))
        data = result.json()
        if result.status_code != 200 or data.get('sid') != service or data.get('code_length') != 4:
            raise ValueError('service not four digits')
    except (httpx.HTTPError, ValueError, AttributeError):
        raise db.error(503, 'EMPLOYEE_OTP_PROVIDER_CONFIGURATION', 'لم يعتمد إعداد رمز SMS من 4 أرقام. / Four-digit SMS service configuration is not verified.') from None


def _eligible(conn, brand, phone, identifier=None, created_at=None):
    rows = conn.execute("SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id WHERE u.brand_id=%s AND normalized_identity_phone(u.phone)=%s AND u.role<>'customer' AND u.active AND u.invitation_status='active' AND b.active" + (' AND u.id=%s' if identifier else '') + (' AND u.updated_at<=%s' if created_at else '') + ' FOR UPDATE OF u', (brand,phone) + ((identifier,) if identifier else ()) + ((created_at,) if created_at else ())).fetchall()
    # Ambiguous registered numbers must be corrected by the owner, never guessed.
    return rows[0] if len(rows) == 1 else None


def request_sms(body: EmployeeSMSRequest, request: Request):
    auth.check_origin(request)
    phone = auth.normalize_phone(body.phone)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn,'employee-sms-ip:'+ip,10,600)
        allowed = db.rate_limit(conn,'employee-sms-phone:'+phone,3,600) and allowed
        provider = provider_ready(conn,body.brand_id)
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if provider == 'twilio_verify':
        verify_service_length()
    challenge_id = db.new_id('employee_otp_')
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    with db.tx() as conn:
        user = _eligible(conn,body.brand_id,phone)
        if user and portals.enabled() and user['role'] not in portals.PORTAL_ROLES.get(request.url.hostname,frozenset()):
            user=None
    if user:
        if provider == 'tawked':
            reference, provider_expiry = sms_verification.start(phone, challenge_id, body.lang)
            expires = min(expires, provider_expiry)
        else:
            result = auth._provider_post('Verifications',{'To':phone,'Channel':'sms','Locale':body.lang},service_sid=os.environ['EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'])
            if result.get('status') != 'pending' or result.get('to') != phone or result.get('channel') != 'sms' or not result.get('sid'):
                raise db.error(503,'OTP_NOT_SENT','لم يؤكد المزود إرسال الرمز. / SMS delivery was not accepted.')
            reference = result['sid']
        with db.tx() as conn:
            if provider_ready(conn,body.brand_id) != provider:
                raise db.error(503,'EMPLOYEE_SMS_NOT_CERTIFIED','تغير إعداد مزود الرسائل. / SMS provider configuration changed.')
            current = _eligible(conn,body.brand_id,phone,user['id'])
            if not current:
                raise db.error(403,'EMPLOYEE_LOGIN_UNAVAILABLE','تعذر تسجيل الدخول. / Login unavailable.')
            conn.execute('INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name,source_user_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(challenge_id,body.brand_id,phone,reference,'sms',expires,provider,current['id']))
            db.audit(conn,current['id'],'employee.sms.requested','user',current['id'])
    # Identical response for unknown/inactive identities; no account is created.
    return {'accepted':True,'challenge_id':challenge_id,'expires_in':max(0,int((expires-datetime.now(timezone.utc)).total_seconds())),'channel':'sms','code_length':4}


def verify_sms(body: EmployeeSMSVerify, request: Request, response: Response):
    auth.check_origin(request)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn,'employee-sms-verify-ip:'+ip,20,600)
        challenge = conn.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s AND source_user_id IS NOT NULL AND delivery_channel='sms' AND provider_name IN ('twilio_verify','tawked') AND verified_at IS NULL AND expires_at>now() AND attempts<3 RETURNING *",(body.challenge_id,)).fetchone() if allowed else None
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if not challenge:
        raise db.error(400,'OTP_EXPIRED','الرمز منتهي أو مستهلك. / Code expired or already used.')
    with db.tx() as conn:
        if provider_ready(conn,challenge['brand_id']) != challenge['provider_name']:
            raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
        user = _eligible(conn,challenge['brand_id'],challenge['phone'],challenge['source_user_id'],challenge['created_at'])
        if not user:
            raise db.error(403,'EMPLOYEE_LOGIN_UNAVAILABLE','تعذر تسجيل الدخول. / Login unavailable.')
        portals.enforce_role(request,user)
    with db.tx() as conn:
        account_allowed = db.rate_limit(conn,'employee-sms-verify-account:'+challenge['source_user_id'],3,600)
    if not account_allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if challenge['provider_name'] == 'tawked':
        approved = sms_verification.check(challenge['provider_reference'], body.code)
    else:
        result = auth._provider_post('VerificationCheck',{'VerificationSid':challenge['provider_reference'],'Code':body.code},service_sid=os.environ['EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'])
        approved = (result.get('status') == 'approved' and result.get('sid') == challenge['provider_reference']
                    and result.get('to') == challenge['phone'] and result.get('channel') == 'sms')
    if not approved:
        raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
    with db.tx() as conn:
        if provider_ready(conn,challenge['brand_id']) != challenge['provider_name']:
            raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
        user = _eligible(conn,challenge['brand_id'],challenge['phone'],challenge['source_user_id'],challenge['created_at'])
        if not user:
            raise db.error(403,'EMPLOYEE_LOGIN_UNAVAILABLE','تعذر تسجيل الدخول. / Login unavailable.')
        portals.enforce_role(request,user)
        claimed = conn.execute('UPDATE otp_challenges SET verified_at=now() WHERE id=%s AND verified_at IS NULL AND expires_at>now() RETURNING id',(body.challenge_id,)).fetchone()
        if not claimed:
            raise db.error(409,'OTP_ALREADY_USED','تم استخدام الرمز. / Code already used.')
        # Proof never changes activation, identity, permission, role or branch access.
        db.audit(conn,user['id'],'employee.sms.verified','user',user['id'])
        return {**auth.establish_session(conn,user,response),'portal_url':portals.home_for_role(user['role'])}


# A sensitive proof belongs to one existing owner session, never to login.
from fastapi import APIRouter, Depends
from pydantic import field_validator

router = APIRouter(prefix='/api/identity/owner/sms', tags=['identity'])


class OwnerSMSVerify(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(pattern=r'^owner_sms_[A-Za-z0-9_-]{1,65}$')
    code: str

    @field_validator('code')
    @classmethod
    def normalize_code(cls, value):
        normalized = value.translate(auth.IDENTITY_DIGITS).strip()
        if not re.fullmatch(r'[0-9]{4}', normalized):
            raise ValueError('Enter the four-digit SMS code')
        return normalized


def _owner_sms_enabled():
    if os.getenv('EMPLOYEE_SMS_LOGIN_ENABLED') != 'true':
        raise db.error(503, 'EMPLOYEE_SMS_NOT_CERTIFIED', 'رسائل الموظفين غير معتمدة بعد. / Employee SMS is not certified yet.')


def _owner_provider_service():
    # Twilio reuses a verification for the same phone/service. A separate
    # service is required so ordinary login codes never prove a sensitive act.
    service = os.getenv('OWNER_SENSITIVE_TWILIO_VERIFY_SERVICE_SID','')
    if not re.fullmatch(r'VA[a-fA-F0-9]{32}', service) or service in {os.getenv('TWILIO_VERIFY_SERVICE_SID'), os.getenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID')}:
        raise db.error(503,'OWNER_SMS_DEDICATED_SERVICE_REQUIRED','يلزم إعداد تحقق مستقل للإجراءات الحساسة. / A separate sensitive verification service is required.')
    return service


def _current_owner(conn, request, expected):
    current = auth._find_session(conn, request)
    if not current or current['id'] != expected['id'] or current['session_id'] != expected['session_id'] or current['role'] != 'owner':
        raise db.error(403, 'OWNER_SMS_SESSION_REQUIRED', 'أعد التحقق من جلسة المالك. / Owner session required.')
    phone = auth.normalize_phone(current.get('phone') or '')
    eligible = _eligible(conn, current['brand_id'], phone, current['id'])
    if not eligible:
        raise db.error(403, 'EMPLOYEE_LOGIN_UNAVAILABLE', 'تعذر التحقق. / Verification unavailable.')
    return current, phone


@router.post('/request')
def request_owner_sms(request: Request, user=Depends(auth.require_roles('owner'))):
    _owner_sms_enabled()
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        current, phone = _current_owner(conn, request, user)
        allowed = db.rate_limit(conn, 'owner-sms-request-account:'+user['id'], 3, 600)
        allowed = db.rate_limit(conn, 'owner-sms-request-phone:'+phone, 3, 600) and allowed
        allowed = db.rate_limit(conn, 'owner-sms-request-ip:'+ip, 10, 600) and allowed
        provider = provider_ready(conn, current['brand_id'])
    if not allowed:
        raise db.error(429, 'OTP_RATE_LIMIT', 'انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if provider == 'twilio_verify':
        verify_service_length(_owner_provider_service())
    cid = db.new_id('owner_sms_')
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    if provider == 'tawked':
        reference, provider_expiry = sms_verification.start(phone, cid, 'ar')
        expires = min(expires, provider_expiry)
    else:
        result = auth._provider_post('Verifications', {'To':phone,'Channel':'sms'}, service_sid=_owner_provider_service())
        if result.get('status') != 'pending' or result.get('to') != phone or result.get('channel') != 'sms' or not result.get('sid'):
            raise db.error(503, 'OTP_NOT_SENT', 'لم يؤكد المزود إرسال الرمز. / SMS delivery was not accepted.')
        reference = result['sid']
    with db.tx() as conn:
        fresh, fresh_phone = _current_owner(conn, request, user)
        if fresh_phone != phone or provider_ready(conn, fresh['brand_id']) != provider:
            raise db.error(403, 'OWNER_SMS_SESSION_REQUIRED', 'أعد التحقق من جلسة المالك. / Owner session required.')
        conn.execute('INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name,source_user_id,source_session_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (cid,fresh['brand_id'],phone,reference,'sms',expires,provider,fresh['id'],fresh['session_id']))
        db.audit(conn, fresh['id'], 'owner.sms.requested', 'session', fresh['session_id'])
    return {'accepted':True,'challenge_id':cid,'expires_in':max(0,int((expires-datetime.now(timezone.utc)).total_seconds())),'code_length':4,'channel':'sms'}


@router.post('/verify')
def verify_owner_sms(body: OwnerSMSVerify, request: Request, user=Depends(auth.require_roles('owner'))):
    _owner_sms_enabled()
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        current, phone = _current_owner(conn, request, user)
        allowed = db.rate_limit(conn, 'owner-sms-verify-account:'+user['id'], 3, 600)
        allowed = db.rate_limit(conn, 'owner-sms-verify-phone:'+phone, 3, 600) and allowed
        allowed = db.rate_limit(conn, 'owner-sms-verify-ip:'+ip, 20, 600) and allowed
        challenge = conn.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s AND source_user_id=%s AND source_session_id=%s AND brand_id=%s AND phone=%s AND delivery_channel='sms' AND verified_at IS NULL AND expires_at>now() AND attempts<3 RETURNING *", (body.challenge_id,user['id'],user['session_id'],user['brand_id'],phone)).fetchone() if allowed else None
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if not challenge:
        raise db.error(400,'OTP_EXPIRED','الرمز منتهي أو مستهلك. / Code expired or already used.')
    with db.tx() as conn:
        if provider_ready(conn, user['brand_id']) != challenge['provider_name']:
            raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
    if challenge['provider_name'] == 'tawked':
        approved = sms_verification.check(challenge['provider_reference'], body.code)
    else:
        result = auth._provider_post('VerificationCheck', {'VerificationSid':challenge['provider_reference'],'Code':body.code}, service_sid=_owner_provider_service())
        approved = result.get('status') == 'approved' and result.get('sid') == challenge['provider_reference'] and result.get('to') == phone and result.get('channel') == 'sms'
    if not approved:
        raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
    with db.tx() as conn:
        fresh, fresh_phone = _current_owner(conn, request, user)
        if fresh_phone != challenge['phone'] or provider_ready(conn,user['brand_id']) != challenge['provider_name'] or not _eligible(conn,user['brand_id'],fresh_phone,user['id'],challenge['created_at']):
            raise db.error(403,'OWNER_SMS_SESSION_REQUIRED','أعد التحقق من جلسة المالك. / Owner session required.')
        claimed = conn.execute('UPDATE otp_challenges SET verified_at=now() WHERE id=%s AND source_session_id=%s AND verified_at IS NULL AND expires_at>now() RETURNING id',(body.challenge_id,user['session_id'])).fetchone()
        if not claimed:
            raise db.error(409,'OTP_ALREADY_USED','تم استخدام الرمز. / Code already used.')
        updated = conn.execute('UPDATE sessions SET step_up_at=now() WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() RETURNING id',(user['session_id'],user['id'])).fetchone()
        if not updated:
            raise db.error(403,'OWNER_SMS_SESSION_REQUIRED','أعد التحقق من جلسة المالك. / Owner session required.')
        db.audit(conn,user['id'],'owner.sms.verified','session',user['session_id'])
    return {'verified':True,'expires_in':600}

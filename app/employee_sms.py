"""Employee SMS proof without activation or customer-identity fallback.

Shares existing canonical challenges and sessions. No SMS is sent unless the
brand channel already has live proof and Twilio reports a six-digit service.
Tawked's existing four-digit customer contract is deliberately not weakened.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
import httpx
from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, portals



class EmployeeSMSRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=8, max_length=30)
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    lang: Literal['ar','en'] = 'ar'


class EmployeeSMSVerify(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(pattern=r'^employee_otp_[A-Za-z0-9_-]{1,65}$')
    code: str = Field(pattern=r'^[0-9]{6}$')


def provider_ready(conn, brand):
    channel = auth._channel(conn, brand, 'sms')
    if channel['state'] != 'active' or not channel['enabled'] or not channel['delivery_proven_at']:
        raise db.error(503, 'EMPLOYEE_SMS_NOT_CERTIFIED', 'رسائل الموظفين غير معتمدة بعد. / Employee SMS is not certified yet.')
    provider = auth._sms_provider(conn, brand)
    if provider != 'twilio_verify':
        raise db.error(503, 'EMPLOYEE_OTP_SIX_DIGIT_PROVIDER_REQUIRED', 'يلزم مزود معتمد لرمز موظف من 6 أرقام. / A certified six-digit employee SMS provider is required.')
    auth._provider_ready(provider)
    employee_service = os.getenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','')
    if not re.fullmatch(r'VA[a-fA-F0-9]{32}',employee_service) or employee_service == os.getenv('TWILIO_VERIFY_SERVICE_SID'):
        raise db.error(503,'EMPLOYEE_OTP_DEDICATED_SERVICE_REQUIRED','يلزم إعداد خدمة SMS مستقلة من 6 أرقام لحماية تحقق العملاء الحالي. / Configure a separate six-digit service to preserve existing customer verification.')
    return provider


def verify_service_length():
    """Read the configured service; never alter a provider's shared settings."""
    sid, secret, service = (os.getenv(k, '') for k in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'))
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            result = client.get('https://verify.twilio.com/v2/Services/' + service, auth=(sid,secret))
        data = result.json()
        if result.status_code != 200 or data.get('sid') != service or data.get('code_length') != 6:
            raise ValueError('service not six digits')
    except (httpx.HTTPError, ValueError, AttributeError):
        raise db.error(503, 'EMPLOYEE_OTP_PROVIDER_CONFIGURATION', 'لم يعتمد إعداد رمز SMS من 6 أرقام. / Six-digit SMS service configuration is not verified.') from None


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
    verify_service_length()
    challenge_id = db.new_id('employee_otp_')
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    with db.tx() as conn:
        user = _eligible(conn,body.brand_id,phone)
        if user and portals.enabled() and user['role'] not in portals.PORTAL_ROLES.get(request.url.hostname,frozenset()):
            user=None
    if user:
        result = auth._provider_post('Verifications',{'To':phone,'Channel':'sms','Locale':body.lang},service_sid=os.environ['EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'])
        if result.get('status') != 'pending' or result.get('to') != phone or result.get('channel') != 'sms' or not result.get('sid'):
            raise db.error(503,'OTP_NOT_SENT','لم يؤكد المزود إرسال الرمز. / SMS delivery was not accepted.')
        with db.tx() as conn:
            provider_ready(conn,body.brand_id)
            current = _eligible(conn,body.brand_id,phone,user['id'])
            if not current:
                raise db.error(403,'EMPLOYEE_LOGIN_UNAVAILABLE','تعذر تسجيل الدخول. / Login unavailable.')
            conn.execute('INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name,source_user_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(challenge_id,body.brand_id,phone,result['sid'],'sms',expires,provider,current['id']))
            db.audit(conn,current['id'],'employee.sms.requested','user',current['id'])
    # Identical response for unknown/inactive identities; no account is created.
    return {'accepted':True,'challenge_id':challenge_id,'expires_in':max(0,int((expires-datetime.now(timezone.utc)).total_seconds())),'channel':'sms','code_length':6}


def verify_sms(body: EmployeeSMSVerify, request: Request, response: Response):
    auth.check_origin(request)
    ip = request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        allowed = db.rate_limit(conn,'employee-sms-verify-ip:'+ip,20,600)
        challenge = conn.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=%s AND source_user_id IS NOT NULL AND delivery_channel='sms' AND provider_name='twilio_verify' AND verified_at IS NULL AND expires_at>now() AND attempts<3 RETURNING *",(body.challenge_id,)).fetchone() if allowed else None
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if not challenge:
        raise db.error(400,'OTP_EXPIRED','الرمز منتهي أو مستهلك. / Code expired or already used.')
    with db.tx() as conn:
        provider_ready(conn,challenge['brand_id'])
        user = _eligible(conn,challenge['brand_id'],challenge['phone'],challenge['source_user_id'],challenge['created_at'])
        if not user:
            raise db.error(403,'EMPLOYEE_LOGIN_UNAVAILABLE','تعذر تسجيل الدخول. / Login unavailable.')
        portals.enforce_role(request,user)
    result = auth._provider_post('VerificationCheck',{'VerificationSid':challenge['provider_reference'],'Code':body.code},service_sid=os.environ['EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'])
    if result.get('status') != 'approved' or result.get('to') != challenge['phone'] or result.get('channel') != 'sms':
        raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Invalid code.')
    with db.tx() as conn:
        provider_ready(conn,challenge['brand_id'])
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

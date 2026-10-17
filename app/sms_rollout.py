"""Audited staged SMS policy in the existing settings store. No provider sends."""
import os
from fastapi import APIRouter, Depends, Request
from . import db

router = APIRouter(tags=['identity'])
_KEY = 'employee_sms_rollout'


def _policy(conn, brand='kps', *, lock=False):
    if brand != 'kps':
        return {}
    row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s' + (' FOR UPDATE' if lock else ''), (brand,)).fetchone()
    value = (row or {}).get('settings', {}).get(_KEY, {})
    return value if isinstance(value, dict) else {}


def _read_policy(brand='kps'):
    if not os.getenv('DATABASE_URL'):
        return {}
    with db.tx() as conn:
        return _policy(conn, brand)


def employee_sms_enabled(user=None, *, brand='kps', phone=None, request=None):
    if os.getenv('EMPLOYEE_SMS_LOGIN_ENABLED') == 'true':
        return True
    if user:
        brand = user.get('brand_id', brand)
    policy = _read_policy(brand)
    if policy.get('mode') == 'active':
        return True
    if policy.get('mode') != 'pilot' or not policy.get('pilot_user_id'):
        return False
    if user:
        return user.get('id') == policy['pilot_user_id']
    if phone:
        # The limited pilot may only be requested from the verified owner's
        # current session. Anonymous users keep the same legacy response.
        if request is None:
            return False
        from . import auth
        with db.tx() as conn:
            current = auth._optional_identity(request, conn)
            if not current or current['id'] != policy['pilot_user_id'] or current['brand_id'] != brand or current['role'] != 'owner':
                return False
            return bool(conn.execute("SELECT 1 FROM users WHERE id=%s AND brand_id=%s AND role='owner' AND active AND invitation_status='active' AND normalized_identity_phone(phone)=%s", (policy['pilot_user_id'], brand, phone)).fetchone())
    return False


def email_retired(brand='kps'):
    if brand != 'kps':
        return False
    if os.getenv('EMPLOYEE_SMS_EMAIL_RETIRED') == 'true' and os.getenv('EMPLOYEE_SMS_LOGIN_ENABLED') == 'true':
        return True
    policy = _read_policy(brand)
    return policy.get('mode') == 'active' and policy.get('email_retired') is True


def context_cookies_enabled():
    override = os.getenv('SESSION_CONTEXT_COOKIES_ENABLED')
    if override is not None:
        return override == 'true'
    return _read_policy().get('mode') in {'pilot', 'active'}


def record_employee_proof(conn, user, challenge_id):
    """Called only in the atomic successful canonical employee SMS verifier."""
    policy = _policy(conn, user['brand_id'], lock=True)
    if policy.get('mode') == 'pilot' and policy.get('pilot_user_id') == user['id']:
        # Save only IDs and database-generated proof timestamp; never OTP/phone.
        conn.execute("UPDATE app_settings SET settings=jsonb_set(settings,%s,%s::jsonb,true) WHERE brand_id=%s", ([_KEY], db.json({**policy, 'employee_verified_challenge_id':challenge_id}), user['brand_id']))
        db.audit(conn,user['id'],'employee.sms.pilot_verified','user',user['id'])


def _authorized(conn, request, expected):
    from . import auth
    current = auth._find_session(conn, request)
    if not current or current['id'] != expected['id'] or current['session_id'] != expected['session_id'] or current['role'] != 'owner' or current['brand_id'] != 'kps':
        raise db.error(403,'SMS_ROLLOUT_OWNER_REQUIRED','جلسة المالك المخولة مطلوبة. / Authorized owner session required.')
    auth.require_owner_step_up(conn, current)
    return current


# Dependency import is deliberately after pure helpers to avoid auth cycles.
from . import auth


@router.post('/api/identity/owner/sms/rollout/pilot')
def start_pilot(request: Request, user=Depends(auth.require_roles('owner'))):
    from .employee_sms import provider_ready
    with db.tx() as conn:
        current = _authorized(conn,request,user)
        policy = _policy(conn,current['brand_id'],lock=True)
        if policy.get('mode') == 'active':
            raise db.error(409,'SMS_ROLLOUT_ALREADY_ACTIVE','التفعيل العام قائم. / General rollout already active.')
        if not conn.execute("SELECT 1 FROM owner_phone_challenges c JOIN users u ON u.id=c.user_id WHERE c.user_id=%s AND c.session_id=%s AND c.brand_id=%s AND c.phone=u.phone AND u.phone_sms_verified_at IS NOT NULL AND c.verified_at IS NOT NULL", (current['id'], current['session_id'], current['brand_id'])).fetchone():
            raise db.error(403,'SMS_ROLLOUT_PHONE_PROOF_REQUIRED','تحقق من ربط جوالك من الجلسة نفسها أولًا. / Verify the owner mobile from this session first.')
        provider_ready(conn,current['brand_id'])
        value = {'mode':'pilot','pilot_user_id':current['id'],'email_retired':False}
        conn.execute('UPDATE app_settings SET settings=jsonb_set(settings,%s,%s::jsonb,true) WHERE brand_id=%s', ([_KEY],db.json(value),current['brand_id']))
        db.audit(conn,current['id'],'employee.sms.pilot_started','brand',current['brand_id'])
    return {'mode':'pilot','email_retired':False,'sms_sent':False}


@router.post('/api/identity/owner/sms/rollout/activate')
def activate(request: Request, user=Depends(auth.require_roles('owner'))):
    from .employee_sms import provider_ready
    with db.tx() as conn:
        current = _authorized(conn,request,user)
        policy = _policy(conn,current['brand_id'],lock=True)
        if policy.get('mode') not in {'pilot','active'} or policy.get('pilot_user_id') != current['id']:
            raise db.error(403,'SMS_ROLLOUT_PILOT_REQUIRED','اختبار المالك مطلوب قبل التفعيل العام. / Owner pilot required before general activation.')
        if not conn.execute("SELECT 1 FROM otp_challenges WHERE id=%s AND source_user_id=%s AND brand_id=%s AND id LIKE 'employee_otp_%%' AND verified_at IS NOT NULL", (policy.get('employee_verified_challenge_id',''),current['id'],current['brand_id'])).fetchone():
            raise db.error(403,'SMS_ROLLOUT_LOGIN_PROOF_REQUIRED','اختبر دخول الموظف الفعلي أولًا. / Complete the employee SMS login pilot first.')
        if not conn.execute("SELECT 1 FROM otp_challenges WHERE source_user_id=%s AND source_session_id=%s AND brand_id=%s AND id LIKE 'owner_sms_%%' AND verified_at>now()-interval '10 minutes'", (current['id'],current['session_id'],current['brand_id'])).fetchone():
            raise db.error(403,'SMS_ROLLOUT_SENSITIVE_PROOF_REQUIRED','تحقق SMS حساس من الجلسة نفسها مطلوب. / Recent sensitive SMS proof from this session required.')
        provider_ready(conn,current['brand_id'])
        # Only login/activation challenges belonging to noncustomer principals.
        retired=conn.execute("UPDATE identity_tokens t SET revoked_at=now() FROM users u WHERE t.user_id=u.id AND t.brand_id=%s AND u.brand_id=t.brand_id AND u.role<>'customer' AND t.purpose IN ('staff_login','owner_link','invitation','add_owner','email_login','recovery') AND t.consumed_at IS NULL AND t.revoked_at IS NULL RETURNING t.id", (current['brand_id'],)).fetchall()
        ids=[r['id'] for r in retired]
        if ids:
            conn.execute("UPDATE email_outbox SET status='cancelled',updated_at=now() WHERE brand_id=%s AND kind='identity' AND dedupe_key=ANY(%s) AND status IN ('pending','failed') AND provider_reference IS NULL", (current['brand_id'],['identity:'+i for i in ids]))
        conn.execute('UPDATE app_settings SET settings=jsonb_set(settings,%s,%s::jsonb,true) WHERE brand_id=%s', ([_KEY], db.json({**policy,'mode':'active','email_retired':True}), current['brand_id']))
        db.audit(conn,current['id'],'employee.sms.activated','brand',current['brand_id'],{'retired_pending_tokens':len(ids)})
    return {'mode':'active','email_retired':True,'sms_sent':False}


@router.get('/api/identity/staff/login-policy')
def login_policy(brand_id: str='kps'):
    # Pilot identity and its enrollment are never exposed publicly.
    active = os.getenv('EMPLOYEE_SMS_LOGIN_ENABLED') == 'true' or _read_policy(brand_id).get('mode') == 'active'
    return {'channel':'sms' if active else 'email','code_length':4 if active else 6}

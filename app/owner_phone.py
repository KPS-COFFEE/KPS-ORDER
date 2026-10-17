"""Owner self-service mobile binding; a contact is saved only after fresh SMS proof.

Independent of employee rollout and customer purchase identities. No email proof,
new account, role grant, paid service or global login-policy change is performed.
"""
import os
import re
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import auth, db, sms_verification

router = APIRouter()


class PhoneRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=8,max_length=30)


class PhoneVerify(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(pattern=r'^owner_phone_[A-Za-z0-9_-]{1,65}$')
    code: str

    @field_validator('code')
    @classmethod
    def normalize_code(cls,value):
        value=value.translate(auth.IDENTITY_DIGITS).strip()
        if not re.fullmatch(r'[0-9]{4}',value):
            raise ValueError('Four digit SMS code required')
        return value


def _current(conn,request,expected):
    current=auth._find_session(conn,request)
    if not current or current['id']!=expected['id'] or current['session_id']!=expected['session_id'] or current['role']!='owner':
        raise db.error(403,'OWNER_PHONE_SESSION_REQUIRED','أعد الدخول من جلسة المالك نفسها. / Use the same owner session.')
    row=conn.execute("SELECT * FROM users WHERE id=%s AND role='owner' AND active AND invitation_status='active' FOR UPDATE",(current['id'],)).fetchone()
    if not row:
        raise db.error(403,'OWNER_PHONE_SESSION_REQUIRED','الحساب لم يعد مخولًا. / Account no longer authorized.')
    auth.require_owner_step_up(conn,current)
    return current,row


def _provider(conn,brand):
    channel=auth._channel(conn,brand,'sms')
    if channel['state']!='active' or not channel['enabled'] or not channel['delivery_proven_at']:
        raise db.error(503,'OWNER_PHONE_SMS_UNAVAILABLE','قناة SMS غير جاهزة. / SMS channel unavailable.')
    provider=auth._sms_provider(conn,brand)
    auth._provider_ready(provider)
    return provider


def _twilio_service():
    service=os.getenv('OWNER_PHONE_TWILIO_VERIFY_SERVICE_SID','')
    if not re.fullmatch(r'VA[a-fA-F0-9]{32}',service) or service in {os.getenv('TWILIO_VERIFY_SERVICE_SID'),os.getenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID'),os.getenv('OWNER_SENSITIVE_TWILIO_VERIFY_SERVICE_SID')}:
        raise db.error(503,'OWNER_PHONE_SERVICE_REQUIRED','يلزم تحقق SMS مستقل لربط الجوال. / A dedicated SMS verification service is required.')
    return service


@router.post('/api/identity/owner-phone/request')
def request_phone(body:PhoneRequest,request:Request,user=Depends(auth.require_roles('owner'))):
    phone=auth.normalize_phone(body.phone)
    if not re.fullmatch(r'\+9665[0-9]{8}',phone):
        raise db.error(422,'PHONE_INVALID','أدخل رقم جوال سعودي صحيحًا. / Enter a valid Saudi mobile.')
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        current,row=_current(conn,request,user)
        auth._assert_staff_phone_available(conn,current['brand_id'],phone,current['id'])
        allowed=db.rate_limit(conn,'owner-phone-send-account:'+user['id'],3,600)
        allowed=db.rate_limit(conn,'owner-phone-send-phone:'+phone,3,600) and allowed
        allowed=db.rate_limit(conn,'owner-phone-send-ip:'+ip,10,600) and allowed
        provider=_provider(conn,user['brand_id'])
        snapshot=(row['phone'],row['updated_at'])
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    identifier=db.new_id('owner_phone_')
    expiry=datetime.now(timezone.utc)+timedelta(minutes=5)
    if provider=='tawked':
        reference,provider_expiry=sms_verification.start(phone,identifier)
        expiry=min(expiry,provider_expiry)
    else:
        service=_twilio_service()
        try:
            with httpx.Client(timeout=12,follow_redirects=False) as client:
                response=client.get('https://verify.twilio.com/v2/Services/'+service,auth=(os.getenv('TWILIO_ACCOUNT_SID',''),os.getenv('TWILIO_AUTH_TOKEN','')))
            data=response.json()
            if response.status_code!=200 or data.get('sid')!=service or data.get('code_length')!=4:
                raise ValueError('Invalid service')
        except (httpx.HTTPError,ValueError,AttributeError):
            raise db.error(503,'OWNER_PHONE_SERVICE_REQUIRED','لم يعتمد إعداد الأربع أرقام. / Four digit configuration not verified.') from None
        result=auth._provider_post('Verifications',{'To':phone,'Channel':'sms'},service_sid=service)
        if result.get('status')!='pending' or result.get('to')!=phone or result.get('channel')!='sms' or not result.get('sid'):
            raise db.error(503,'OTP_NOT_SENT','لم يؤكد المزود الإرسال. / Provider did not confirm sending.')
        reference=result['sid']
    with db.tx() as conn:
        current,row=_current(conn,request,user)
        if snapshot!=(row['phone'],row['updated_at']) or _provider(conn,user['brand_id'])!=provider:
            raise db.error(409,'OWNER_PHONE_CHANGED','تغير الحساب؛ أعد التحقق. / Account changed; verify again.')
        auth._assert_staff_phone_available(conn,user['brand_id'],phone,user['id'])
        conn.execute('INSERT INTO owner_phone_challenges(id,user_id,session_id,brand_id,phone,old_phone,account_updated_at,provider,provider_reference,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['id'],user['session_id'],user['brand_id'],phone,*snapshot,provider,reference,expiry))
        db.audit(conn,user['id'],'owner.phone.verification_requested','user',user['id'])
    return {'accepted':True,'challenge_id':identifier,'code_length':4,'expires_in':max(0,int((expiry-datetime.now(timezone.utc)).total_seconds()))}


@router.post('/api/identity/owner-phone/verify')
def verify_phone(body:PhoneVerify,request:Request,user=Depends(auth.require_roles('owner'))):
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as conn:
        _current(conn,request,user)
        allowed=db.rate_limit(conn,'owner-phone-check-account:'+user['id'],3,600)
        allowed=db.rate_limit(conn,'owner-phone-check-ip:'+ip,20,600) and allowed
        challenge=conn.execute('UPDATE owner_phone_challenges SET attempts=attempts+1 WHERE id=%s AND user_id=%s AND session_id=%s AND brand_id=%s AND verified_at IS NULL AND expires_at>now() AND attempts<3 RETURNING *',(body.challenge_id,user['id'],user['session_id'],user['brand_id'])).fetchone() if allowed else None
    if not allowed:
        raise db.error(429,'OTP_RATE_LIMIT','انتظر قبل المحاولة مجددًا. / Wait before trying again.')
    if not challenge:
        raise db.error(400,'OTP_EXPIRED','رمز غير صالح أو منتهي أو مستخدم. / Invalid, expired or used code.')
    with db.tx() as conn:
        if _provider(conn,user['brand_id'])!=challenge['provider']:
            raise db.error(409,'OWNER_PHONE_CHANGED','تغيرت قناة التحقق. / Verification channel changed.')
    if challenge['provider']=='tawked':
        approved=sms_verification.check(challenge['provider_reference'],body.code)
    else:
        result=auth._provider_post('VerificationCheck',{'VerificationSid':challenge['provider_reference'],'Code':body.code},service_sid=_twilio_service())
        approved=result.get('status')=='approved' and result.get('sid')==challenge['provider_reference'] and result.get('to')==challenge['phone'] and result.get('channel')=='sms'
    if not approved:
        raise db.error(400,'OTP_INVALID','رمز غير صحيح. / Incorrect code.')
    with db.tx() as conn:
        current,row=_current(conn,request,user)
        if row['phone']!=challenge['old_phone'] or row['updated_at']!=challenge['account_updated_at'] or _provider(conn,user['brand_id'])!=challenge['provider']:
            raise db.error(409,'OWNER_PHONE_CHANGED','تغير الحساب؛ أعد التحقق. / Account changed; verify again.')
        auth._assert_staff_phone_available(conn,user['brand_id'],challenge['phone'],user['id'])
        claimed=conn.execute('UPDATE owner_phone_challenges SET verified_at=now() WHERE id=%s AND verified_at IS NULL AND expires_at>now() RETURNING id',(body.challenge_id,)).fetchone()
        if not claimed:
            raise db.error(409,'OTP_ALREADY_USED','تم استخدام الرمز. / Code already used.')
        conn.execute('UPDATE users SET phone=%s,phone_verified_at=now(),phone_sms_verified_at=now(),updated_at=now() WHERE id=%s',(challenge['phone'],user['id']))
        conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND id<>%s AND revoked_at IS NULL',(user['id'],user['session_id']))
        auth._invalidate_staff_tokens(conn,user['id'])
        db.audit(conn,user['id'],'owner.phone.sms_linked','user',user['id'],{'channel':'sms','current_session_preserved':True})
    return {'verified':True,'phone_masked':'*******'+challenge['phone'][-3:],'current_session_preserved':True}


@router.get('/owner-phone',response_class=HTMLResponse)
def owner_phone_page(user=Depends(auth.require_roles('owner'))):
    return HTMLResponse(PAGE,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@router.get('/owner-phone.js')
def owner_phone_script(user=Depends(auth.require_roles('owner'))):
    return Response(SCRIPT,media_type='application/javascript',headers={'Cache-Control':'no-store'})


SCRIPT=r'''
let lang=new URLSearchParams(location.search).get('lang')==='en'?'en':'ar';const tr=(ar,en)=>lang==='en'?en:ar;function labels(){document.documentElement.lang=lang;document.documentElement.dir=lang==='en'?'ltr':'rtl';const texts={title:['ربط جوال المالك','Owner mobile verification'],instructions:['يبقى دخولك الحالي محفوظًا. يُربط الرقم بعد إثبات ملكيته برسالة SMS؛ دون اشتراط البريد أو تغيير الصلاحيات.','Your current sign-in stays available. The mobile is linked after SMS proof, without email verification or permission changes.'],'phone-label':['رقم الجوال السعودي','Saudi mobile number'],'password-label':['كلمة المرور الحالية للتحقق من الإجراء','Current password for authorization'],'code-label':['رمز SMS من 4 أرقام','Four-digit SMS code'],send:['إرسال رمز SMS واحد','Send one SMS code'],confirm:['تحقق وربط الجوال','Verify and link mobile'],back:['العودة إلى التشغيل','Back to operations']};for(const[id,text]of Object.entries(texts))document.getElementById(id).textContent=tr(...text);document.getElementById('language').textContent=lang==='en'?'العربية':'English';}document.getElementById('language').onclick=()=>{lang=lang==='en'?'ar':'en';labels();};labels();let csrf='',challenge='';const status=document.getElementById('status');
async function call(path,body){const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',headers:{'Content-Type':'application/json',...(body?{'X-CSRF-Token':csrf}:{})},...(body?{body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw new Error(d.detail?.message||'تعذر إتمام الطلب.');return d;}
document.getElementById('request').onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('button');b.disabled=true;status.textContent='';try{const session=await call('/api/session');if(session.user?.role!=='owner'||!session.csrf_token)throw new Error('افتح الصفحة من جلسة مالك مخولة.');csrf=session.csrf_token;await call('/api/identity/step-up',{password:f.elements.password.value});f.elements.password.value='';const sent=await call('/api/identity/owner-phone/request',{phone:f.elements.phone.value});challenge=sent.challenge_id;f.hidden=true;document.getElementById('verify').hidden=false;status.textContent=tr('طلب المزود الإرسال. أدخل الرمز الذي يصلك؛ لا ترسله في المحادثة.','The provider accepted the request. Enter the received SMS code here, never in chat.');}catch(error){status.textContent=error.message;f.elements.password.value='';}finally{b.disabled=false;}};
document.getElementById('verify').onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('button');b.disabled=true;try{const result=await call('/api/identity/owner-phone/verify',{challenge_id:challenge,code:f.elements.code.value});f.elements.code.value='';f.hidden=true;status.textContent=tr('تم توثيق الجوال وربطه: ','Mobile verified and linked: ')+result.phone_masked+tr(' — جلسة المالك الحالية محفوظة.',' — Current owner session preserved.');}catch(error){status.textContent=error.message;}finally{b.disabled=false;}};
'''


PAGE='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ربط جوال المالك — KPS ORDER</title>
<style>body{font:17px/1.8 system-ui;background:#f5f4ef;color:#222;max-width:480px;margin:50px auto;padding:24px}form{background:white;padding:24px;border-radius:18px}label{display:block;margin:18px 0}input,button{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #aaa;border-radius:9px}button{background:#222;color:white;cursor:pointer}#status{white-space:pre-wrap}a{color:inherit}</style>
<button id="language" type="button">English</button><h1 id="title">ربط جوال المالك</h1><p>Owner mobile verification</p><p id="instructions">يبقى دخولك الحالي محفوظًا. يُربط الرقم بعد إثبات ملكيته برسالة SMS؛ دون اشتراط البريد أو تغيير الصلاحيات.</p>
<form id="request"><label><span id="phone-label">رقم الجوال السعودي</span><input name="phone" type="tel" autocomplete="tel" dir="ltr" required></label><label><span id="password-label">كلمة المرور الحالية للتحقق من الإجراء</span><input name="password" type="password" autocomplete="current-password" required></label><button id="send">إرسال رمز SMS واحد</button></form>
<form id="verify" hidden><label><span id="code-label">رمز SMS من 4 أرقام</span><input name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="4" dir="ltr" required></label><button id="confirm">تحقق وربط الجوال</button></form>
<p id="status" role="status"></p><a id="back" href="/ops">العودة إلى التشغيل</a>
<script src="/owner-phone.js" defer></script></html>'''

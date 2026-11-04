"""Append-only service follow-up and separately explicit marketing consent."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import auth, customer_crm, db
from .engagement_validation import EngagementModel

router=APIRouter(tags=['customer-engagement'])


class Followup(EngagementModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=1,max_length=2000)
    status:Literal['open','resolved']
    next_followup_at:datetime|None=None

    @field_validator('next_followup_at')
    @classmethod
    def aware(cls,value):
        if value is not None and value.utcoffset() is None:
            raise ValueError('Timezone required')
        return value


class MarketingConsent(EngagementModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    channel:Literal['sms','whatsapp']
    granted:bool
    notice_version:str=Field(min_length=1,max_length=100)


class MarketingNotice(EngagementModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:str=Field(min_length=1,max_length=100)
    text_ar:str=Field(min_length=1,max_length=5000)
    text_en:str=Field(min_length=1,max_length=5000)


def selected_notice(c,brand):
    return c.execute("SELECT n.version,n.text_ar,n.text_en FROM customer_marketing_notices n JOIN app_settings a ON a.brand_id=n.brand_id WHERE n.brand_id=%s AND n.id=a.settings->>'customer_marketing_notice_id'",(brand,)).fetchone()


@router.post('/api/crm/marketing-notices')
def publish_notice(body:MarketingNotice,user=Depends(auth.require_roles('owner')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        def save():
            identifier=db.new_id('notice_')
            row=c.execute('INSERT INTO customer_marketing_notices(id,brand_id,version,text_ar,text_en,actor_id) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(brand_id,version) DO NOTHING RETURNING id',(identifier,user['brand_id'],body.version,body.text_ar,body.text_en,user['id'])).fetchone()
            if not row:raise db.error(409,'CONSENT_NOTICE_EXISTS','أنشئ إصدار إشعار جديدًا. / Create a new notice version.')
            c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{customer_marketing_notice_id}',to_jsonb(%s::text)) WHERE brand_id=%s",(identifier,user['brand_id']))
            db.audit(c,user['id'],'crm.marketing.notice','marketing_notice',identifier,{'version':body.version})
            return {'id':identifier,'version':body.version}
        return db.idempotent(c,user['id'],idempotency_key,'crm.marketing.notice:'+user['brand_id'],body.model_dump(),save)


def owner_customer(c,user,customer_id):
    if not c.execute(customer_crm.BASE+'SELECT id FROM verified WHERE id=%s',(*customer_crm.arguments(user),customer_id)).fetchone():
        raise db.error(404,'CUSTOMER_NOT_FOUND','العميل غير موجود في نطاقك. / Customer not found in your scope.')


def history(c,brand,customer_id,after='',wallet_after='',benefit_after='',consent_before=None):
    notes=c.execute('SELECT id,actor_id,status,note,next_followup_at,created_at FROM customer_followups WHERE brand_id=%s AND customer_id=%s AND id>%s ORDER BY id LIMIT 51',(brand,customer_id,after)).fetchall()
    consent=c.execute('SELECT DISTINCT ON(channel) channel,granted,notice_version,created_at FROM customer_marketing_consents WHERE brand_id=%s AND customer_id=%s ORDER BY channel,seq DESC',(brand,customer_id)).fetchall()
    consent_history=c.execute('SELECT id,seq,channel,granted,notice_version,created_at FROM customer_marketing_consents WHERE brand_id=%s AND customer_id=%s AND (%s::bigint IS NULL OR seq<%s::bigint) ORDER BY seq DESC LIMIT 101',(brand,customer_id,consent_before,consent_before)).fetchall()
    wallet=c.execute('SELECT balance_halalas FROM customer_wallets WHERE brand_id=%s AND customer_id=%s',(brand,customer_id)).fetchone()
    entries=c.execute('SELECT id,order_id,kind,amount_halalas,created_at FROM customer_wallet_entries WHERE brand_id=%s AND customer_id=%s AND id>%s ORDER BY id LIMIT 101',(brand,customer_id,wallet_after)).fetchall()
    usage=c.execute('SELECT order_id,rule_id,discount_halalas,redeemed_halalas,earned_halalas,returned_halalas,created_at FROM benefit_order_states WHERE brand_id=%s AND customer_id=%s AND order_id>%s ORDER BY order_id LIMIT 101',(brand,customer_id,benefit_after)).fetchall()
    return {'followups':notes[:50],'next_after':notes[49]['id'] if len(notes)>50 else None,
            'marketing':{channel:next((x['granted'] for x in consent if x['channel']==channel),False) for channel in ('sms','whatsapp')},
            'consent_history':consent_history[:100],'wallet':{'balance_halalas':(wallet or {}).get('balance_halalas',0),'entries':entries[:100]},'benefit_history':usage[:100],
            'wallet_next_after':entries[99]['id'] if len(entries)>100 else None,
            'benefit_next_after':usage[99]['order_id'] if len(usage)>100 else None,
            'consent_next_before':consent_history[99]['seq'] if len(consent_history)>100 else None,
            'history_limits':{'consent':100,'wallet':100,'benefit':100},'registration_is_marketing_consent':False}


@router.get('/api/crm/customers/{customer_id}/engagement')
def detail(customer_id:str,response:Response,after:str=Query(default='',max_length=150),
           wallet_after:str=Query(default='',max_length=150),benefit_after:str=Query(default='',max_length=150),
           consent_before:int|None=Query(default=None,ge=1),user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control']='no-store'
    with db.tx() as c:
        owner_customer(c,user,customer_id)
        return {'brand_id':user['brand_id'],**history(c,user['brand_id'],customer_id,after,wallet_after,benefit_after,consent_before)}


@router.post('/api/crm/customers/{customer_id}/followups')
def followup(customer_id:str,body:Followup,user=Depends(auth.require_roles('owner')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        owner_customer(c,user,customer_id)
        def save():
            identifier=db.new_id('followup_')
            c.execute('INSERT INTO customer_followups(id,brand_id,customer_id,actor_id,status,note,next_followup_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],customer_id,user['id'],body.status,body.note,body.next_followup_at))
            # Audit links the record, without duplicating a customer's note/PII.
            db.audit(c,user['id'],'crm.followup','customer_followup',identifier,{'customer_id':customer_id,'status':body.status})
            return {'id':identifier}
        return db.idempotent(c,user['id'],idempotency_key,'crm.followup:'+customer_id,body.model_dump(mode='json'),save)


@router.get('/api/customer/marketing-consent')
def consents(response:Response,user=Depends(auth.require_roles('customer'))):
    response.headers['Cache-Control']='no-store'
    with db.tx() as c:
        auth.assert_customer_order_eligible(c,user,phone_required=True)
        rows=c.execute('SELECT DISTINCT ON(channel) channel,granted,notice_version FROM customer_marketing_consents WHERE brand_id=%s AND customer_id=%s ORDER BY channel,seq DESC',(user['brand_id'],user['id'])).fetchall()
        notice=selected_notice(c,user['brand_id'])
    return {'brand_id':user['brand_id'],'notice':notice,'marketing':{ch:next((r['granted'] for r in rows if r['channel']==ch),False) for ch in ('sms','whatsapp')},'affects_order_messages':False}


@router.post('/api/customer/marketing-consent')
def consent(body:MarketingConsent,user=Depends(auth.require_roles('customer')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        auth.assert_customer_order_eligible(c,user,phone_required=True)
        def save():
            c.execute('SELECT id FROM users WHERE id=%s AND brand_id=%s FOR UPDATE',(user['id'],user['brand_id']))
            notice=selected_notice(c,user['brand_id'])
            if body.granted and (not notice or notice['version']!=body.notice_version):
                raise db.error(409,'CONSENT_NOTICE_CHANGED','راجع إشعار الموافقة الحالي. / Review the current consent notice.')
            identifier=db.new_id('consent_')
            c.execute('INSERT INTO customer_marketing_consents(id,brand_id,customer_id,channel,granted,notice_version) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],user['id'],body.channel,body.granted,body.notice_version))
            db.audit(c,user['id'],'customer.marketing.consent','marketing_consent',identifier,{'channel':body.channel,'granted':body.granted,'notice_version':body.notice_version})
            return {'id':identifier,'channel':body.channel,'granted':body.granted,'affects_order_messages':False}
        return db.idempotent(c,user['id'],idempotency_key,'customer.marketing.consent:'+user['brand_id'],body.model_dump(),save)

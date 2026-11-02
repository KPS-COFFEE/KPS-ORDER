"""Owner approval gates all storefront advertising, independently of gifts."""
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import auth, db

router=APIRouter(prefix='/api/advertising',tags=['advertising'])


def policy(settings=None):
    return {'version':0,'advertising_enabled':False}|((settings or {}).get('storefront_advertising') or {})


def enabled(conn,brand_id):
    row=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(brand_id,)).fetchone()
    return policy((row or {}).get('settings'))['advertising_enabled'] is True


class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    advertising_enabled:StrictBool=False
    expected_version:StrictInt=Field(ge=0)
    reason:str=Field(min_length=3,max_length=500)


@router.get('/policy')
def read_policy(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        row=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()
        return {'policy':policy((row or {}).get('settings'))}


@router.put('/policy')
def update_policy(body:Change,user=Depends(auth.require_roles('owner')),idem:str|None=Header(None,alias='Idempotency-Key')):
    if len(body.reason.strip())<3 or '\x00' in body.reason:
        raise db.error(422,'REASON_REQUIRED','أدخل سبب التعديل.')
    with db.tx() as c:
        # An authenticated owner confirms this operational change in the UI.
        def perform():
            row=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE',(user['brand_id'],)).fetchone()
            if not row:raise db.error(404,'BRAND_NOT_FOUND','إعدادات البراند غير متاحة.')
            before=policy(row['settings'])
            if before['version']!=body.expected_version:
                raise db.error(409,'POLICY_CHANGED','تغيرت الإعدادات؛ أعد فتحها قبل الحفظ.')
            after={'version':before['version']+1,'advertising_enabled':body.advertising_enabled}
            c.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',
                      (db.json(row['settings']|{'storefront_advertising':after}),user['brand_id']))
            db.audit(c,user['id'],'advertising.policy.updated','brand',user['brand_id'],{'before':before,'after':after,'reason':body.reason.strip()})
            return {'policy':after,'gift_policy_unchanged':True}
        return db.idempotent(c,user['id'],idem,'advertising.policy.update',body.model_dump(),perform)

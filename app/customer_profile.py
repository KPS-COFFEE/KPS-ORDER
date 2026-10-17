"""Verified customer self-service; no phone-number based data lookup."""
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import auth, db
router=APIRouter(prefix='/api/customer',tags=['customer-profile'])

def verified(conn,user):
    auth.assert_customer_order_eligible(conn,user)

@router.get('/profile')
def profile(user=Depends(auth.require_roles('customer'))):
    with db.tx() as c:
        verified(c,user)
        addresses=c.execute('SELECT id,label,address,neighborhood,lat,lng FROM customer_addresses WHERE customer_id=%s AND brand_id=%s ORDER BY updated_at DESC LIMIT 50',(user['id'],user['brand_id'])).fetchall()
        return {'user':auth.public_user(user),'addresses':addresses}

class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    display_name:str=Field(min_length=1,max_length=100)

@router.patch('/profile')
def update(body:Profile,user=Depends(auth.require_roles('customer'))):
    if not body.display_name.strip():raise db.error(422,'CUSTOMER_NAME_REQUIRED','أدخل اسمك.')
    with db.tx() as c:
        verified(c,user)
        row=c.execute('UPDATE users SET display_name=%s,updated_at=now() WHERE id=%s AND brand_id=%s RETURNING *',(body.display_name.strip(),user['id'],user['brand_id'])).fetchone()
        db.audit(c,user['id'],'customer.profile.updated','user',user['id'])
        return {'user':auth.public_user(row)}

class Address(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True,allow_inf_nan=False)
    label:str=Field(min_length=1,max_length=100)
    address:str=Field(min_length=1,max_length=500)
    neighborhood:str=Field(default='',max_length=120)
    lat:float|None=Field(default=None,ge=-90,le=90,strict=True)
    lng:float|None=Field(default=None,ge=-180,le=180,strict=True)

    @model_validator(mode='after')
    def paired_location(self):
        if (self.lat is None)!=(self.lng is None):
            raise ValueError('Location requires both coordinates or neither.')
        return self

@router.post('/addresses')
def add_address(body:Address,user=Depends(auth.require_roles('customer')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        verified(c,user)
        def perform():
            # Serialize this customer's address book, including concurrent tabs.
            c.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(user['id'],))
            if c.execute('SELECT count(*) AS n FROM customer_addresses WHERE customer_id=%s AND brand_id=%s',(user['id'],user['brand_id'])).fetchone()['n']>=50:
                raise db.error(409,'ADDRESS_LIMIT','يمكنك حفظ 50 عنوانًا. احذف عنوانًا قديمًا أولًا.')
            data=body.model_dump()
            row=c.execute('INSERT INTO customer_addresses(id,customer_id,brand_id,label,address,neighborhood,lat,lng) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,label,address,neighborhood,lat,lng',
                (db.new_id('addr_'),user['id'],user['brand_id'],data['label'],data['address'],data['neighborhood'],data['lat'],data['lng'])).fetchone()
            db.audit(c,user['id'],'customer.address.created','address',row['id'])
            return {'address':row}
        return db.idempotent(c,user['id'],idempotency_key,'customer.address.create',body.model_dump(),perform)

@router.patch('/addresses/{identifier}')
def edit_address(identifier:str,body:Address,user=Depends(auth.require_roles('customer')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        verified(c,user)
        # Re-check ownership before replay; a deleted address is not resurrected.
        if not c.execute('SELECT id FROM customer_addresses WHERE id=%s AND customer_id=%s AND brand_id=%s FOR UPDATE',(identifier,user['id'],user['brand_id'])).fetchone():
            raise db.error(404,'ADDRESS_NOT_FOUND','العنوان غير موجود.')
        def perform():
            row=c.execute('UPDATE customer_addresses SET label=%s,address=%s,neighborhood=%s,lat=%s,lng=%s,updated_at=now() WHERE id=%s AND customer_id=%s AND brand_id=%s RETURNING id,label,address,neighborhood,lat,lng',
                (body.label,body.address,body.neighborhood,body.lat,body.lng,identifier,user['id'],user['brand_id'])).fetchone()
            db.audit(c,user['id'],'customer.address.updated','address',identifier)
            return {'address':row}
        return db.idempotent(c,user['id'],idempotency_key,'customer.address.update:'+identifier,body.model_dump(),perform)

@router.delete('/addresses/{identifier}')
def delete(identifier:str,user=Depends(auth.require_roles('customer'))):
    with db.tx() as c:
        verified(c,user)
        row=c.execute('DELETE FROM customer_addresses WHERE id=%s AND customer_id=%s AND brand_id=%s RETURNING id',(identifier,user['id'],user['brand_id'])).fetchone()
        if not row:raise db.error(404,'ADDRESS_NOT_FOUND','العنوان غير موجود.')
        db.audit(c,user['id'],'customer.address.deleted','address',identifier)
        return {'ok':True}

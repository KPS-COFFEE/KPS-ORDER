"""Verified customer self-service; no phone-number based data lookup."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
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

@router.delete('/addresses/{identifier}')
def delete(identifier:str,user=Depends(auth.require_roles('customer'))):
    with db.tx() as c:
        verified(c,user)
        row=c.execute('DELETE FROM customer_addresses WHERE id=%s AND customer_id=%s AND brand_id=%s RETURNING id',(identifier,user['id'],user['brand_id'])).fetchone()
        if not row:raise db.error(404,'ADDRESS_NOT_FOUND','العنوان غير موجود.')
        db.audit(c,user['id'],'customer.address.deleted','address',identifier)
        return {'ok':True}

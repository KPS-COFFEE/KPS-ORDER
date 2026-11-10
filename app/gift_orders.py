"""The verified purchaser owns the order; recipient contact grants no access."""
from . import activation, auth, db


def validate(c, body, user):
    if not body.gift:
        return
    activation.require('gift_orders',body.brand_id)
    if not user or user.get('role')!='customer' or user['brand_id']!=body.brand_id:
        raise db.error(403,'GIFT_PURCHASER_REQUIRED','الإهداء يتطلب حساب المشتري. / Gift requires the purchaser account.')
    auth.assert_customer_order_eligible(c,user,phone_required=True)
    auth.normalize_phone(body.gift.recipient_phone)


def capture(body, user, customer, quote):
    if not body.gift:
        return
    quote['gift']={**body.gift.model_dump(), 'recipient_phone':auth.normalize_phone(body.gift.recipient_phone),
                   'purchaser':{'customer_id':user['id'],'name':customer['name'],'phone':user['phone'],
                                'phone_verified':True}}
    customer.update(name=body.gift.recipient_name,phone=quote['gift']['recipient_phone'],
                    phone_verified=False,phone_sms_verified=False,phone_whatsapp_verified=False,
                    gift_recipient=True)
    if body.gift.message:
        quote['notes']='\n'.join(filter(None,[quote.get('notes',''),'[Gift / إهداء] '+body.gift.message]))

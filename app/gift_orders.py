"""The verified purchaser owns the order; recipient contact grants no access."""
from . import activation, auth, db


def validate(c, body, user):
    recipient = getattr(body, 'delivery_recipient', None)
    if recipient:
        if body.gift or body.service != 'delivery':
            raise db.error(422, 'DELIVERY_RECIPIENT_INVALID', 'اختر مستلمًا واحدًا لطلب التوصيل.')
        if not user or user.get('role') != 'customer' or user['brand_id'] != body.brand_id:
            raise db.error(403, 'GIFT_PURCHASER_REQUIRED', 'سجل الدخول بحساب صاحب الطلب.')
        auth.assert_customer_order_eligible(c, user, phone_required=True)
        auth.normalize_phone(recipient.recipient_phone)
    if not body.gift:
        return
    activation.require('gift_orders',body.brand_id)
    if not user or user.get('role')!='customer' or user['brand_id']!=body.brand_id:
        raise db.error(403,'GIFT_PURCHASER_REQUIRED','الإهداء يتطلب حساب المشتري. / Gift requires the purchaser account.')
    auth.assert_customer_order_eligible(c,user,phone_required=True)
    auth.normalize_phone(body.gift.recipient_phone)


def capture(body, user, customer, quote):
    recipient = body.gift or getattr(body, 'delivery_recipient', None)
    if not recipient:
        return
    phone = auth.normalize_phone(recipient.recipient_phone)
    kind = 'gift' if body.gift else 'delivery_recipient'
    quote[kind] = {**recipient.model_dump(), 'recipient_phone': phone,
                   'purchaser': {'customer_id': user['id'], 'name': customer['name'],
                                 'phone': user['phone'], 'phone_verified': True}}
    customer.update(name=recipient.recipient_name, phone=phone, phone_verified=False,
                    phone_sms_verified=False, phone_whatsapp_verified=False)
    customer['gift_recipient' if body.gift else 'delivery_recipient'] = True
    if body.gift and body.gift.message:
        quote['notes']='\n'.join(filter(None,[quote.get('notes',''),'[Gift / إهداء] '+body.gift.message]))

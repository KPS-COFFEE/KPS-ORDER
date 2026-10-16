"""Durable sandbox checkout acquisition. Provider I/O never holds an order lock."""
import os
import re
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from . import db, payment_gateways as gateways, payment_intents


def settings(attempt):
    if os.getenv('KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED') != 'true':
        raise db.error(409,'BRANCH_CHECKOUT_DISABLED','الدفع التجريبي غير مفعل.')
    try:
        cipher = Fernet(os.environ['KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY'].encode())
    except (KeyError, ValueError, TypeError):
        raise db.error(503,'PAYMENT_SECRET_STORAGE_REQUIRED','لم يهيأ الحفظ الآمن لجلسة الدفع.') from None
    key = os.getenv(gateways.credential_prefix(attempt['provider'],attempt['brand_id'],attempt['seller_id'])+'_TEST_PUBLIC_KEY','')
    if not re.fullmatch(r'pk_test_[A-Za-z0-9_-]{8,256}',key):
        raise db.error(503,'PAYMENT_TEST_CREDENTIALS_REQUIRED','لم تضبط مفاتيح الاختبار لهذه المنشأة.')
    return cipher,key


def eligible(c, attempt, user):
    from .branch_payments import require_sandbox
    from .orders import scoped_order
    order = scoped_order(c,attempt['order_id'],user,lock=True)
    require_sandbox(order)
    if (attempt['status']!='pending' or attempt['expires_at']<=datetime.now(timezone.utc)
            or order['preparation_status']!='accepted' or order['payment_status']!='unpaid'
            or order['total_halalas']!=attempt['amount_halalas']
            or order.get('amendment_revision',0)!=attempt['order_revision']):
        raise db.error(409,'PAYMENT_CHECKOUT_UNAVAILABLE','جلسة الدفع غير متاحة لهذا الطلب.')
    return order


def present(row, attempt, cipher, public_key):
    if row['state']!='ready':
        raise db.error(409,'PAYMENT_CHECKOUT_RECONCILIATION_REQUIRED','جلسة الدفع قيد التحقق. لا تعاود الدفع.')
    if row['expires_at']<=datetime.now(timezone.utc):
        raise db.error(409,'PAYMENT_CHECKOUT_EXPIRED','انتهت صلاحية جلسة الدفع.')
    try:
        secret = cipher.decrypt(bytes(row['encrypted_secret'])).decode()
    except (InvalidToken, UnicodeError):
        raise db.error(503,'PAYMENT_SECRET_STORAGE_REQUIRED','تعذر فتح جلسة الدفع الآمنة.') from None
    return {'provider':'moyasar','mode':'test','amount':attempt['amount_halalas'],'currency':attempt['currency'],
            'given_id':str(attempt['provider_payment_id']),'metadata':gateways.PaymentContext(
                attempt['brand_id'],attempt['seller_id'],attempt['order_id'],attempt['id'],attempt['amount_halalas']).metadata,
            'publishable_api_key':public_key,'callback_url':row['callback_url'],
            'payment_intent':{'id':str(row['provider_intent_id']),'client_secret':secret}}


def acquire(aid, user):
    from .branch_payments import context
    from .orders import scoped_order
    with db.tx() as c:
        attempt = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s',(aid,)).fetchone()
        if not attempt:
            raise db.error(404,'PAYMENT_NOT_FOUND','محاولة الدفع غير موجودة.')
        # Ownership is checked before reading or exposing any credential configuration.
        scoped_order(c,attempt['order_id'],user)
        cipher,public_key = settings(attempt)
        eligible(c,attempt,user)
        row = c.execute('SELECT * FROM branch_checkout_sessions WHERE attempt_id=%s',(aid,)).fetchone()
        if row:
            return present(row,attempt,cipher,public_key)
        c.execute('INSERT INTO branch_checkout_sessions(attempt_id,brand_id) VALUES(%s,%s)',(aid,attempt['brand_id']))
        db.audit(c,user['id'],'payment.checkout.claimed','order',attempt['order_id'],{'attempt_id':aid,'mode':'test'})
    try:
        intent = payment_intents.create_moyasar_sandbox_intent(context(attempt))
        if intent.context!=context(attempt) or intent.provider!=attempt['provider'] or intent.mode!='test':
            raise db.error(409,'PAYMENT_EVIDENCE_MISMATCH','جلسة الدفع لا تطابق المحاولة.')
    except Exception:
        with db.tx() as c:
            c.execute("UPDATE branch_checkout_sessions SET state='uncertain' WHERE attempt_id=%s AND state='creating'",(aid,))
        raise
    with db.tx() as c:
        row = c.execute("UPDATE branch_checkout_sessions SET state='ready',provider_intent_id=%s,encrypted_secret=%s,callback_url=%s,expires_at=%s WHERE attempt_id=%s AND state='creating' RETURNING *",
                        (intent.intent_id,cipher.encrypt(intent.client_secret.encode()),intent.callback_url,intent.expires_at,aid)).fetchone()
        if not row:
            raise db.error(409,'PAYMENT_CHECKOUT_RECONCILIATION_REQUIRED','يلزم التحقق من جلسة الدفع.')
        db.audit(c,user['id'],'payment.checkout.prepared','order',attempt['order_id'],{'attempt_id':aid,'mode':'test'})
    # A cancellation during provider I/O must not release a payment credential.
    with db.tx() as c:
        latest=c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s',(aid,)).fetchone()
        eligible(c,latest,user)
        return present(row,latest,cipher,public_key)

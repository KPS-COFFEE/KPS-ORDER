"""Explicit safe renewal, never a retry of an uncertain charge."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import Depends
from . import auth, db, branch_payments as payments, payment_intents


def eligible(c, attempt, user):
    order = payments.attempt_order(c, attempt, user, lock=True)
    if (order['preparation_status'] != 'accepted' or order['payment_status'] != 'unpaid'
            or order['total_halalas'] != attempt['amount_halalas']
            or order.get('amendment_revision', 0) != attempt['order_revision']):
        raise db.error(409, 'PAYMENT_RENEWAL_UNAVAILABLE', 'لا يمكن تجديد الدفع لهذا الطلب.')
    from .order_amendments import guard
    guard(c, order)
    return order


@payments.router.post('/attempts/{aid}/renew')
def renew(aid: str, user=Depends(auth.require_roles('customer'))):
    with db.tx() as c:
        attempt = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (aid,)).fetchone()
        if not attempt:
            raise db.error(404, 'PAYMENT_NOT_FOUND', 'محاولة الدفع غير موجودة.')
        eligible(c, attempt, user)
        existing = c.execute('SELECT a.* FROM branch_payment_renewals r JOIN branch_payment_attempts a ON a.id=r.next_attempt_id WHERE r.previous_attempt_id=%s', (aid,)).fetchone()
        if existing:
            return {'attempt': payments.view(existing)}
        if attempt['status'] != 'pending':
            raise db.error(409, 'PAYMENT_RENEWAL_UNAVAILABLE', 'تحقق من الدفع السابق أولًا.')
        if not db.rate_limit(c, 'payment-renew:'+user['id'], 10, 60):
            raise db.error(429, 'PAYMENT_RATE_LIMIT', 'انتظر قليلًا قبل المحاولة.')
        session = c.execute('SELECT * FROM branch_checkout_sessions WHERE attempt_id=%s', (aid,)).fetchone()
    # No lock is held during the provider GET. A timeout/404/fulfilled intent
    # cannot establish that another charge is safe and therefore cannot renew.
    if session and session['state'] == 'ready':
        reason = payment_intents.safe_unused_intent(payments.context(attempt), session,
                    cancel_unused=attempt['expires_at'] <= datetime.now(timezone.utc))
    elif not session or (session['state'] in {'creating','uncertain'} and
                          session['created_at'] < datetime.now(timezone.utc)-timedelta(seconds=60)):
        reason = 'never_exposed'
    else:
        raise db.error(409, 'PAYMENT_RECONCILIATION_REQUIRED', 'جلسة الدفع قيد التحقق. لا تعاود الدفع.')
    with db.tx() as c:
        eligible(c, attempt, user)
        current = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s FOR UPDATE', (aid,)).fetchone()
        existing = c.execute('SELECT a.* FROM branch_payment_renewals r JOIN branch_payment_attempts a ON a.id=r.next_attempt_id WHERE r.previous_attempt_id=%s', (aid,)).fetchone()
        if existing:
            return {'attempt': payments.view(existing)}
        latest = c.execute('SELECT * FROM branch_checkout_sessions WHERE attempt_id=%s FOR UPDATE', (aid,)).fetchone()
        if current['status'] != 'pending' or latest != session:
            raise db.error(409, 'PAYMENT_RECONCILIATION_REQUIRED', 'تغيرت جلسة الدفع. أعد التحقق.')
        c.execute("UPDATE branch_payment_attempts SET status='superseded' WHERE id=%s", (aid,))
        successor = c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (db.new_id('pay_'), attempt['order_id'], attempt['brand_id'], attempt['branch_id'], attempt['seller_id'], attempt['provider'], str(uuid4()), attempt['mode'], attempt['amount_halalas'], attempt['currency'], attempt['order_revision'], attempt['accepted_by'])).fetchone()
        c.execute('INSERT INTO branch_payment_renewals(previous_attempt_id,next_attempt_id,brand_id,reason,actor_id) VALUES(%s,%s,%s,%s,%s)',
            (aid, successor['id'], attempt['brand_id'], reason, user['id']))
        details = {'previous_attempt_id': aid, 'next_attempt_id': successor['id'], 'reason': reason, 'mode': attempt['mode']}
        db.event(c, attempt['order_id'], user['id'], 'payment.attempt.renewed', details)
        db.audit(c, user['id'], 'payment.attempt.renewed', 'order', attempt['order_id'], details)
        return {'attempt': payments.view(successor)}

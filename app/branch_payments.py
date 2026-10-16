"""Durable, disabled-by-default sandbox payments for accepted branch orders.

Only explicitly marked sandbox orders can enter this foundation. A credential,
browser return, or callback cannot enable a service or declare a payment paid.
No live charging endpoint exists. Provider I/O runs outside DB transactions.
"""
import os
from uuid import uuid4
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db, payment_gateways as gateways
from .orders import scoped_order

router = APIRouter(prefix='/api/branch-payments')


def online_order(order):
    return order.get('service') in {'pickup', 'car'} and order.get('snapshot', {}).get('branch_payment_mode') == 'test'


def require_sandbox(order):
    if not online_order(order) or os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED') != 'true':
        raise db.error(409, 'BRANCH_PAYMENT_DISABLED', 'الدفع الإلكتروني لهذه الخدمة غير مفعل.')
    seller = order['snapshot'].get('branch_payment_seller')
    if not isinstance(seller, str) or not gateways.IDENTIFIER.fullmatch(seller):
        raise db.error(409, 'PAYMENT_SELLER_REQUIRED', 'لم تعتمد منشأة الدفع لهذا الطلب.')
    return seller


def context(attempt):
    return gateways.PaymentContext(attempt['brand_id'], attempt['seller_id'],
                                   attempt['order_id'], attempt['id'], attempt['amount_halalas'])


def view(attempt):
    return {key: str(attempt[key]) if key in {'provider_payment_id','expires_at'} else attempt[key]
            for key in ('id','order_id','status','amount_halalas','currency','provider',
                        'provider_payment_id','mode','expires_at')}


class AttemptBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1)


@router.post('/orders/{oid}/attempt')
def create_attempt(oid: str, body: AttemptBody, user=Depends(auth.require_roles('customer')),
                   idem: str | None=Header(None, alias='Idempotency-Key')):
    with db.tx() as c:
        scoped_order(c, oid, user)  # Reauthorize even a replay.
        def perform():
            order = scoped_order(c, oid, user, lock=True)
            seller = require_sandbox(order)
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب. حدّث الصفحة.')
            acceptance = c.execute("SELECT actor_id FROM order_events WHERE order_id=%s AND event_type='preparation.accepted' ORDER BY created_at DESC,id DESC LIMIT 1", (oid,)).fetchone()
            if order['preparation_status'] != 'accepted' or not acceptance or not acceptance['actor_id']:
                raise db.error(409, 'CASHIER_ACCEPTANCE_REQUIRED', 'انتظر موافقة الكاشير قبل الدفع.')
            if order['payment_status'] != 'unpaid':
                raise db.error(409, 'ORDER_ALREADY_PAID', 'الطلب غير متاح لمحاولة دفع جديدة.')
            from .order_amendments import guard
            guard(c, order)
            existing = c.execute('SELECT * FROM branch_payment_attempts WHERE order_id=%s', (oid,)).fetchone()
            if existing:
                # Reuse the same provider given_id, including retries after an
                # uncertain network outcome. Never create a second charge ID.
                if existing['amount_halalas'] != order['total_halalas'] or existing['order_revision'] != order.get('amendment_revision', 0):
                    raise db.error(409, 'PAYMENT_RECONCILIATION_REQUIRED', 'يلزم تسوية محاولة الدفع السابقة.')
                return {'attempt': view(existing)}
            attempt_id = db.new_id('pay_')
            try:
                gateways.PaymentContext(order['brand_id'], seller, oid, attempt_id, order['total_halalas'])
            except ValueError:
                raise db.error(422,'PAYMENT_AMOUNT_INVALID','مبلغ الطلب غير صالح للدفع الإلكتروني.') from None
            row = c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by) VALUES(%s,%s,%s,%s,%s,'moyasar',%s,'test',%s,'SAR',%s,%s) RETURNING *",
                (attempt_id,oid,order['brand_id'],order['branch_id'],seller,str(uuid4()),order['total_halalas'],order.get('amendment_revision',0),acceptance['actor_id'])).fetchone()
            db.audit(c,user['id'],'payment.attempt.created','order',oid,{'attempt_id':attempt_id,'mode':'test','amount_halalas':row['amount_halalas']})
            return {'attempt': view(row)}
        return db.idempotent(c,user['id'],idem,'branch.payment.attempt:'+oid,body.model_dump(),perform)


@router.post('/attempts/{aid}/reconcile')
def reconcile(aid: str, user=Depends(auth.require_roles('customer','owner','branch_manager'))):
    with db.tx() as c:
        attempt = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (aid,)).fetchone()
        if not attempt:
            raise db.error(404, 'PAYMENT_NOT_FOUND', 'محاولة الدفع غير موجودة.')
        order = scoped_order(c, attempt['order_id'], user)
        require_sandbox(order)
        if attempt['status'] != 'pending':
            return {'attempt':view(attempt)}
        if not db.rate_limit(c,'payment-reconcile:'+user['id'],30,60):
            raise db.error(429,'PAYMENT_RATE_LIMIT','انتظر قليلًا قبل التحقق مرة أخرى.')
    return verify_attempt(attempt, user)


def attempt_order(c, attempt, user=None, lock=False):
    """Internal workers use persisted scope, never a fabricated customer role."""
    if user is not None:
        order = scoped_order(c, attempt['order_id'], user, lock=lock)
    else:
        order = c.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''),
                          (attempt['order_id'],)).fetchone()
    if (not order or order['brand_id'] != attempt['brand_id'] or
            order['branch_id'] != attempt['branch_id'] or require_sandbox(order) != attempt['seller_id']):
        raise db.error(409, 'PAYMENT_SCOPE_MISMATCH', 'نطاق الدفع غير مطابق للطلب.')
    return order


def verify_attempt(attempt, user=None):
    """Shared provider verification; callbacks are not evidence of payment."""
    aid = attempt['id']
    with db.tx() as c:
        attempt_order(c, attempt, user)
        current = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (aid,)).fetchone()
        if current['status'] != 'pending':
            return {'attempt': view(current)}
    expected = context(attempt)
    proof = gateways.adapter_for(attempt['provider']).verify_sandbox_payment(expected, str(attempt['provider_payment_id']))
    if proof.context != expected or proof.payment_id != str(attempt['provider_payment_id']) or proof.mode != 'test' or proof.provider != attempt['provider']:
        raise db.error(409,'PAYMENT_EVIDENCE_MISMATCH','إثبات الدفع لا يطابق المحاولة.')
    with db.tx() as c:
        order = attempt_order(c, attempt, user, lock=True)
        current = c.execute('SELECT *,expires_at<now() expired FROM branch_payment_attempts WHERE id=%s FOR UPDATE',(aid,)).fetchone()
        if current['status'] != 'pending':
            return {'attempt':view(current)}
        # A late or changed-order payment is durably recorded for reconciliation,
        # never silently discarded or used to resume a cancelled order.
        applicable = (order['preparation_status']=='accepted' and order['payment_status']=='unpaid'
                      and not current['expired'] and order['total_halalas']==current['amount_halalas']
                      and order.get('amendment_revision',0)==current['order_revision'])
        disposition = 'applied' if applicable else 'review_required'
        c.execute("INSERT INTO branch_online_receipts(id,attempt_id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,disposition) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'test',%s,'SAR',%s)",
            (db.new_id('online_'),aid,order['id'],current['brand_id'],current['branch_id'],current['seller_id'],current['provider'],current['provider_payment_id'],current['amount_halalas'],disposition))
        row = c.execute('UPDATE branch_payment_attempts SET status=%s,verified_at=now() WHERE id=%s RETURNING *',('verified' if applicable else 'review_required',aid)).fetchone()
        if applicable:
            c.execute("UPDATE orders SET payment_status='paid',version=version+1 WHERE id=%s",(order['id'],))
        details={'attempt_id':aid,'provider':proof.provider,'payment_reference':proof.payment_id,'mode':'test','disposition':disposition}
        actor_id = user['id'] if user is not None else None
        db.event(c,order['id'],actor_id,'payment.verified',details)
        db.audit(c,actor_id,'payment.verified','order',order['id'],details)
        return {'attempt':view(row)}


def require_verified(c, order):
    if online_order(order) and (order['payment_status'] != 'paid' or not c.execute(
            "SELECT 1 FROM branch_online_receipts WHERE order_id=%s AND amount_halalas=%s AND disposition='applied'",
            (order['id'],order['total_halalas'])).fetchone()):
        raise db.error(409,'VERIFIED_ONLINE_PAYMENT_REQUIRED','يلزم دفع إلكتروني موثق قبل التجهيز والتسليم.')


def enrich_orders(c, rows):
    targets = [row for row in rows if online_order(row)]
    if not targets:
        return
    receipts = {r['order_id']:r for r in c.execute(
        "SELECT order_id,amount_halalas,disposition FROM branch_online_receipts WHERE order_id=ANY(%s)",
        ([row['id'] for row in targets],)).fetchall()}
    for row in targets:
        receipt = receipts.get(row['id']) or {}
        row['online_payment_verified'] = (row['payment_status']=='paid' and
            receipt.get('disposition')=='applied' and receipt.get('amount_halalas')==row['total_halalas'])


@router.post('/attempts/{aid}/checkout')
def checkout(aid: str, user=Depends(auth.require_roles('customer'))):
    from fastapi.responses import JSONResponse
    from .branch_checkout import acquire
    return JSONResponse(acquire(aid,user),headers={'Cache-Control':'no-store','Pragma':'no-cache'})

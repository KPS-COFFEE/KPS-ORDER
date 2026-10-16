"""Owner-authorized sandbox refund ledger. Existing Delivery refunds are untouched."""
from fastapi import APIRouter, Depends, Header
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, branch_payments as payments, payment_refunds as provider
from .orders import scoped_order

router = APIRouter(prefix='/api/branch-payments')


class RefundBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1)
    amount_halalas: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError('Refund reason is required')
        return value.strip()


def view(row):
    return {k: row[k] for k in ('id', 'order_id', 'status', 'amount_halalas', 'previous_refunded_halalas')}


@router.get('/orders/{oid}/refunds')
def history(oid: str, user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        order = scoped_order(c, oid, user)
        if not payments.online_order(order):
            raise db.error(404, 'REFUND_NOT_FOUND', 'لا يوجد دفع إلكتروني لهذا الطلب.')
        attempt = c.execute("SELECT * FROM branch_payment_attempts WHERE order_id=%s AND status<>'superseded'", (oid,)).fetchone()
        rows = c.execute('SELECT * FROM branch_online_refunds WHERE order_id=%s ORDER BY created_at,id', (oid,)).fetchall()
        enabled = False
        if attempt and attempt['status'] in {'verified', 'review_required'}:
            try:
                payments.require_sandbox(order)
                provider.configuration(payments.context(attempt))
                enabled = not bool(c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s', (oid,)).fetchone())
            except HTTPException:
                pass
        verified = sum(r['amount_halalas'] for r in rows if r['status']=='verified')
        available = max(0, attempt['amount_halalas']-verified) if attempt and attempt['status'] in {'verified','review_required'} else 0
        return {'refunds': [view(r) for r in rows], 'enabled': enabled,
                'available_halalas': available, 'version': order['version']}


@router.post('/orders/{oid}/refunds')
def authorize(oid: str, body: RefundBody, user=Depends(auth.require_roles('owner')),
              idem: str | None=Header(None, alias='Idempotency-Key')):
    with db.tx() as c:
        scoped_order(c, oid, user)
        def perform():
            order = scoped_order(c, oid, user, lock=True)
            payments.require_sandbox(order)
            attempt = c.execute("SELECT * FROM branch_payment_attempts WHERE order_id=%s AND status<>'superseded'", (oid,)).fetchone()
            if not attempt or attempt['status'] not in {'verified', 'review_required'}:
                raise db.error(409, 'CONFIRMED_PAYMENT_REQUIRED', 'تحقق من الدفع قبل الاسترداد.')
            payments.attempt_order(c, attempt, user)
            provider.configuration(payments.context(attempt))
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب. حدّث الصفحة.')
            if c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s', (oid,)).fetchone():
                raise db.error(409, 'FISCAL_REFUND_REVIEW_REQUIRED', 'يلزم مسار الإشعار الدائن قبل الاسترداد.')
            if c.execute("SELECT 1 FROM branch_online_refunds WHERE order_id=%s AND status<>'verified'", (oid,)).fetchone():
                raise db.error(409, 'REFUND_RECONCILIATION_REQUIRED', 'تحقق من الاسترداد السابق أولًا.')
            previous = c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM branch_online_refunds WHERE order_id=%s AND status='verified'", (oid,)).fetchone()['n']
            if previous+body.amount_halalas > attempt['amount_halalas']:
                raise db.error(422, 'REFUND_AMOUNT_INVALID', 'مبلغ الاسترداد أكبر من الرصيد المدفوع.')
            row = c.execute('INSERT INTO branch_online_refunds(id,attempt_id,order_id,brand_id,branch_id,amount_halalas,previous_refunded_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (db.new_id('or_'), attempt['id'], oid, order['brand_id'], order['branch_id'], body.amount_halalas, previous, body.reason, user['id'])).fetchone()
            # Freeze further fulfillment while funds are being returned; never
            # rewrite completed order snapshots or their historical status.
            if order['preparation_status'] != 'completed':
                c.execute("UPDATE orders SET payment_status='refund_pending',version=version+1 WHERE id=%s", (oid,))
            db.audit(c, user['id'], 'payment.refund.authorized', 'order', oid,
                     {'refund_id': row['id'], 'amount_halalas': body.amount_halalas, 'reason': body.reason, 'mode': attempt['mode']})
            return {'refund': view(row)}
        return db.idempotent(c, user['id'], idem, 'branch.refund:'+oid, body.model_dump(), perform)


def load(c, rid, user):
    row = c.execute('SELECT * FROM branch_online_refunds WHERE id=%s', (rid,)).fetchone()
    if not row:
        raise db.error(404, 'REFUND_NOT_FOUND', 'الاسترداد غير موجود.')
    attempt = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (row['attempt_id'],)).fetchone()
    order = payments.attempt_order(c, attempt, user, lock=True)
    if any(row[key] != attempt[key] for key in ('order_id', 'brand_id', 'branch_id')):
        raise db.error(409, 'REFUND_SCOPE_MISMATCH', 'نطاق الاسترداد غير مطابق للطلب.')
    row = c.execute('SELECT * FROM branch_online_refunds WHERE id=%s FOR UPDATE', (rid,)).fetchone()
    return row, attempt, order


def confirmed(rid, user, observed):
    with db.tx() as c:
        row, attempt, order = load(c, rid, user)
        if row['status'] == 'verified':
            return {'refund': view(row)}
        if observed != row['previous_refunded_halalas']+row['amount_halalas']:
            raise db.error(409, 'REFUND_RECONCILIATION_REQUIRED', 'لم يتأكد مبلغ الاسترداد المطابق.')
        result = c.execute("UPDATE branch_online_refunds SET status='verified',verified_at=now() WHERE id=%s RETURNING *", (rid,)).fetchone()
        if order['preparation_status'] != 'completed':
            status = 'refunded' if observed == attempt['amount_halalas'] else 'partially_refunded'
            c.execute('UPDATE orders SET payment_status=%s,version=version+1 WHERE id=%s', (status, order['id']))
        details = {'refund_id': rid, 'amount_halalas': row['amount_halalas'], 'mode': attempt['mode']}
        actor_id = user['id'] if user is not None else None
        details['authorized_by'] = row['authorized_by']
        db.event(c, order['id'], actor_id, 'payment.refund.verified', details)
        db.audit(c, actor_id, 'payment.refund.verified', 'order', order['id'], details)
        return {'refund': view(result)}


@router.post('/refunds/{rid}/execute')
def execute(rid: str, user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        row, attempt, order = load(c, rid, user)
        provider.configuration(payments.context(attempt))
        if row['status'] == 'verified':
            return {'refund': view(row)}
        if row['status'] != 'requested':
            raise db.error(409, 'REFUND_RECONCILIATION_REQUIRED', 'تحقق من الاسترداد دون إعادة إرساله.')
    # A failed read-only preflight has not sent money. Leave the authorization
    # retryable until that read succeeds; never claim a POST before this GET.
    expected = payments.context(attempt)
    pid = str(attempt['provider_payment_id'])
    before = provider.request(expected, pid)
    target = row['previous_refunded_halalas']+row['amount_halalas']
    if before not in (row['previous_refunded_halalas'], target):
        raise db.error(409, 'REFUND_RECONCILIATION_REQUIRED', 'رصيد المزود يحتاج مراجعة.')
    with db.tx() as c:
        row, attempt, order = load(c, rid, user)
        provider.configuration(payments.context(attempt))
        if row['status'] == 'verified':
            return {'refund': view(row)}
        if row['status'] != 'requested':
            raise db.error(409, 'REFUND_RECONCILIATION_REQUIRED', 'تحقق من الاسترداد دون إعادة إرساله.')
        c.execute("UPDATE branch_online_refunds SET status='submitting' WHERE id=%s", (rid,))
    # Committed single-submit claim: another process/retry cannot repeat the POST.
    try:
        if before != target:
            provider.request(expected, pid, row['amount_halalas'])
        # Even a successful POST response is checked with a separate GET.
        return confirmed(rid, user, provider.request(expected, pid))
    except Exception:
        with db.tx() as c:
            c.execute("UPDATE branch_online_refunds SET status='uncertain' WHERE id=%s AND status='submitting'", (rid,))
        raise


@router.post('/refunds/{rid}/reconcile')
def reconcile(rid: str, user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        row, attempt, order = load(c, rid, user)
        if row['status'] == 'verified':
            return {'refund': view(row)}
        if row['status'] == 'requested':
            raise db.error(409, 'REFUND_NOT_SUBMITTED', 'لم يرسل الاسترداد بعد.')
    return confirmed(rid, user, provider.request(payments.context(attempt), str(attempt['provider_payment_id'])))

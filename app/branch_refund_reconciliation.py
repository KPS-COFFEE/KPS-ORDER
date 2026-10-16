"""Disabled-by-default, GET-only recovery of already submitted refunds.

Never authorizes or submits a refund. A missing/unchanged provider result stays
unresolved; only matching seller-scoped evidence finalizes the existing ledger.
"""
import asyncio
import logging
import os
from . import db, branch_refunds, branch_payments, payment_refunds


def enabled():
    provider_ready = (os.getenv('KPS_BRANCH_PAYMENT_LIVE_ENABLED')=='true' or
        (os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED')=='true' and
         os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED')=='true'))
    return provider_ready and all(os.getenv(flag) == 'true' for flag in (
        'KPS_BRANCH_REFUND_RECONCILIATION_ENABLED',
        'KPS_BRANCH_PAYMENT_REFUNDS_ENABLED'))


def enqueue_pending():
    if not enabled():
        return 0
    with db.tx() as c:
        rows = c.execute("""INSERT INTO branch_refund_reconciliation(refund_id,brand_id)
            SELECT id,brand_id FROM branch_online_refunds
            WHERE status IN ('submitting','uncertain')
              AND NOT EXISTS (SELECT 1 FROM branch_refund_reconciliation j
                              WHERE j.refund_id=branch_online_refunds.id)
            ORDER BY created_at,id LIMIT 100
            ON CONFLICT(refund_id) DO NOTHING RETURNING refund_id""").fetchall()
        return len(rows)


def dispatch_once():
    if not enabled():
        return False
    lease = db.new_id('refund_lease_')
    with db.tx() as c:
        job = c.execute("""SELECT * FROM branch_refund_reconciliation
            WHERE processed_at IS NULL AND next_attempt_at<=now()
              AND (lease_until IS NULL OR lease_until<now())
            ORDER BY next_attempt_at,refund_id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not job:
            return False
        c.execute("UPDATE branch_refund_reconciliation SET lease_id=%s,lease_until=now()+interval '60 seconds',attempts=attempts+1 WHERE refund_id=%s",
                  (lease, job['refund_id']))
    try:
        with db.tx() as c:
            row, attempt, order = branch_refunds.load(c, job['refund_id'], None)
            if row['brand_id'] != job['brand_id']:
                raise ValueError('Refund scope mismatch')
            if row['status'] not in {'submitting', 'uncertain', 'verified'}:
                raise ValueError('Refund was not submitted')
        if row['status'] != 'verified':
            observed = payment_refunds.request(branch_payments.context(attempt),
                                              str(attempt['provider_payment_id']))
            branch_refunds.confirmed(row['id'], None, observed)
    except Exception:
        # No provider payloads, card details, secrets or exception text in logs.
        with db.tx() as c:
            c.execute("""UPDATE branch_refund_reconciliation
                SET lease_id=NULL,lease_until=NULL,
                    next_attempt_at=now()+least(3600,60*greatest(attempts,1))*interval '1 second'
                WHERE refund_id=%s AND lease_id=%s AND processed_at IS NULL""",
                (job['refund_id'], lease))
        return False
    with db.tx() as c:
        c.execute("UPDATE branch_refund_reconciliation SET processed_at=now(),lease_id=NULL,lease_until=NULL WHERE refund_id=%s AND lease_id=%s AND processed_at IS NULL",
                  (job['refund_id'], lease))
    return True


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(enqueue_pending)
            await asyncio.to_thread(dispatch_once)
        except Exception:
            logging.getLogger('kps_order').error('branch_refund_reconciliation_unavailable')
        await asyncio.sleep(5)

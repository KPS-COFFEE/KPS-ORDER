"""OFF-default sandbox inbox and retry worker. No live payment activation.

Moyasar's documented secret_token authenticates a notification, not its monetary
claims. Only a separate seller-scoped provider GET can write the payment ledger.
"""
import asyncio
import hmac
import logging
import os
from uuid import UUID
from fastapi import APIRouter, Request
from . import db, branch_payments, payment_gateways

router = APIRouter(prefix='/paymenthooks')


def enabled():
    return (os.getenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED') == 'true' and
            runtime_enabled())


def runtime_enabled():
    return (os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED') == 'true' or
            os.getenv('KPS_BRANCH_PAYMENT_LIVE_ENABLED') == 'true')


def worker_enabled():
    return (runtime_enabled() and
            (os.getenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED') == 'true' or
             os.getenv('KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED') == 'true'))


def enqueue_pending():
    """Recover missing callbacks and paid attempts after a checkout process crash.

    A persistent verification request does not create another checkout or charge.
    Sweep at most 100 previously unseen attempts; failure/backoff uses the inbox.
    """
    if not worker_enabled() or os.getenv('KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED') != 'true':
        return 0
    with db.tx() as c:
        rows = c.execute("""INSERT INTO branch_payment_webhooks
            (id,brand_id,attempt_id,provider_event_id,event_type)
            SELECT 'sweep_'||a.id,a.brand_id,a.id,'system_reconcile','reconciliation_requested'
            FROM branch_payment_attempts a
            JOIN branch_checkout_sessions s ON s.attempt_id=a.id
            WHERE a.status='pending' AND s.created_at<now()-interval '30 seconds'
            AND NOT EXISTS (SELECT 1 FROM branch_payment_webhooks w WHERE w.attempt_id=a.id AND w.provider_event_id='system_reconcile')
            ORDER BY a.created_at,a.id LIMIT 100
            ON CONFLICT(attempt_id,provider_event_id) DO NOTHING RETURNING id""").fetchall()
        return len(rows)


def notification(body, mode='test'):
    """Discard all unneeded provider fields, including card data and secrets."""
    if mode not in {'test','live'} or not isinstance(body, dict) or body.get('live') is not (mode=='live'):
        raise ValueError('Invalid sandbox notification')
    event = body.get('id')
    kind = body.get('type')
    token = body.get('secret_token')
    data = body.get('data')
    if (not isinstance(event, str) or not payment_gateways.IDENTIFIER.fullmatch(event) or
            not isinstance(kind, str) or len(kind) > 80 or
            not isinstance(token, str) or not 32 <= len(token) <= 512 or not isinstance(data, dict)):
        raise ValueError('Invalid notification')
    payment_id = str(UUID(data['id']))
    return event, kind, token, payment_id


@router.post('/moyasar', status_code=202)
async def receive(request: Request):
    if not enabled():
        raise db.error(404, 'PAYMENT_WEBHOOK_DISABLED', 'الخدمة غير مفعلة.')
    try:
        body = await request.json()
        mode = 'live' if isinstance(body,dict) and body.get('live') is True else 'test'
        event, kind, token, payment_id = notification(body,mode)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise db.error(400, 'PAYMENT_WEBHOOK_INVALID', 'إشعار غير صالح.') from None
    # No network I/O in this transaction. A 202 is sent only after durable commit.
    with db.tx() as c:
        attempt = c.execute("SELECT * FROM branch_payment_attempts WHERE provider='moyasar' AND provider_payment_id=%s AND mode=%s", (payment_id,mode)).fetchone()
        secret = '' if not attempt else os.getenv(payment_gateways.credential_prefix(
            'moyasar', attempt['brand_id'], attempt['seller_id']) + '_'+mode.upper()+'_WEBHOOK_SECRET', '')
        if not 32 <= len(secret) <= 512 or not hmac.compare_digest(token.encode(), secret.encode()):
            raise db.error(401, 'PAYMENT_WEBHOOK_UNAUTHORIZED', 'إشعار غير مصرح.')
        branch_payments.attempt_order(c, attempt)
        if kind not in {'payment_paid', 'payment_captured'}:
            return {'accepted': True, 'queued': False}
        row = c.execute('INSERT INTO branch_payment_webhooks(id,brand_id,attempt_id,provider_event_id,event_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(attempt_id,provider_event_id) DO NOTHING RETURNING id',
                        (db.new_id('pwh_'), attempt['brand_id'], attempt['id'], event, kind)).fetchone()
        if row:
            db.audit(c, None, 'payment.webhook.received', 'order', attempt['order_id'],
                     {'event_id': event, 'event_type': kind, 'attempt_id': attempt['id'], 'mode': attempt['mode']})
    return {'accepted': True, 'queued': True}


def dispatch_once():
    if not worker_enabled():
        return False
    lease = db.new_id('lease_')
    with db.tx() as c:
        row = c.execute("SELECT * FROM branch_payment_webhooks WHERE processed_at IS NULL AND next_attempt_at<=now() AND (lease_until IS NULL OR lease_until<now()) ORDER BY received_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if not row:
            return False
        c.execute("UPDATE branch_payment_webhooks SET lease_id=%s,lease_until=now()+interval '60 seconds',attempts=attempts+1 WHERE id=%s", (lease, row['id']))
        attempt = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (row['attempt_id'],)).fetchone()
    try:
        branch_payments.verify_attempt(attempt)
    except Exception:
        # Never log provider response, payload, exception text or token. Retry
        # durable work after outages; a process crash is recovered by its lease.
        with db.tx() as c:
            c.execute("UPDATE branch_payment_webhooks SET lease_id=NULL,lease_until=NULL,next_attempt_at=now()+least(3600,60*greatest(attempts,1))*interval '1 second' WHERE id=%s AND lease_id=%s AND processed_at IS NULL", (row['id'], lease))
        return False
    with db.tx() as c:
        c.execute('UPDATE branch_payment_webhooks SET processed_at=now(),lease_id=NULL,lease_until=NULL WHERE id=%s AND lease_id=%s AND processed_at IS NULL', (row['id'], lease))
    return True


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(enqueue_pending)
            await asyncio.to_thread(dispatch_once)
        except Exception:
            logging.getLogger('kps_order').error('branch_payment_worker_unavailable')
        await asyncio.sleep(5)

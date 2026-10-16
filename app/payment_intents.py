"""Server-approved checkout intents; no card data or implicit live activation.

The caller must durably own an accepted order/payment attempt before calling.
This adapter alone does not expose a checkout route or authorize a payment.
Provider contract: https://docs.moyasar.com/api/payment_intents/01-create-payment-intent
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import re
from urllib.parse import urlsplit
from uuid import UUID
import httpx
from . import db, payment_gateways as gateways


@dataclass(frozen=True)
class CheckoutIntent:
    provider: str
    intent_id: str
    context: gateways.PaymentContext
    expires_at: datetime
    callback_url: str
    client_secret: str = field(repr=False)
    mode: str = 'test'


def safe_unused_intent(context, session, *, cancel_unused=False):
    """Return only normalized retirement proof; do not expose fetched secrets."""
    if context.mode == 'test' and (os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED') != 'true' or
            os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED') != 'true'):
        raise db.error(409, 'PAYMENT_SANDBOX_DISABLED', 'اختبار بوابة الدفع غير مفعل.')
    if context.mode == 'test' and not gateways.sandbox_key_configured('moyasar', context.brand_id, context.entity_id):
        raise db.error(503, 'PAYMENT_TEST_CREDENTIALS_REQUIRED', 'لم تضبط مفاتيح الاختبار لهذه المنشأة.')
    from .payment_runtime import secret as provider_secret
    key = provider_secret(context)
    try:
        identifier = str(UUID(str(session['provider_intent_id'])))
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
            response = client.get('https://api.moyasar.com/v1/payment_intents/'+identifier,
                                  auth=(key, ''), headers={'Accept': 'application/json'})
        if response.status_code != 200:
            raise ValueError('No conclusive intent evidence')
        data = response.json()
        expiry = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
        if (data.get('id') != identifier or type(data.get('amount')) is not int or
                data['amount'] != context.amount_halalas or data.get('currency') != context.currency or
                data.get('description') != 'KPS order '+context.order_id+' / '+context.attempt_id or
                data.get('callback_url') != session['callback_url'] or expiry.tzinfo is None or
                data.get('fulfilled_at') is not None or 'fulfilled_at' not in data):
            raise ValueError('Intent evidence mismatch')
        if data.get('status') == 'canceled':
            return 'provider_canceled'
        # An application clock running ahead must never retire a still-usable
        # secret. Require the authenticated provider response's clock as well.
        observed = parsedate_to_datetime(response.headers.get('Date', ''))
        if (data.get('status') == 'created' and observed.tzinfo is not None and
                expiry.timestamp()+60 < observed.timestamp()):
            return 'provider_expired'
        if data.get('status') == 'created' and cancel_unused:
            # The application deadline may precede provider expiry. Cancel only
            # an unused intent, then independently fetch conclusive proof. A
            # concurrent fulfillment wins atomically at the provider and blocks
            # renewal; neither a cancel response nor its browser claim is proof.
            with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
                client.post('https://api.moyasar.com/v1/payment_intents/'+identifier+'/cancel',
                            auth=(key, ''), headers={'Accept': 'application/json'})
            return safe_unused_intent(context, session)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        pass
    raise db.error(409, 'PAYMENT_RECONCILIATION_REQUIRED', 'لم يتأكد انتهاء جلسة الدفع دون استخدام. لا تعاود الدفع.')


def create_moyasar_sandbox_intent(context: gateways.PaymentContext) -> CheckoutIntent:
    if context.mode != 'test':
        raise db.error(409, 'PAYMENT_MODE_INVALID', 'وضع الدفع لا يطابق مسار الاختبار.')
    return _create_intent(context)


def create_moyasar_live_intent(context: gateways.PaymentContext) -> CheckoutIntent:
    if context.mode != 'live':
        raise db.error(409, 'PAYMENT_MODE_INVALID', 'وضع الدفع لا يطابق المسار الفعلي.')
    return _create_intent(context)


def _create_intent(context):
    """One provider request, no automatic retry after an uncertain POST outcome."""
    if context.mode == 'test' and (os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED') != 'true' or os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED') != 'true'):
        raise db.error(409, 'PAYMENT_SANDBOX_DISABLED', 'اختبار بوابة الدفع غير مفعل.')
    if context.mode == 'test' and not gateways.sandbox_key_configured('moyasar', context.brand_id, context.entity_id):
        raise db.error(503, 'PAYMENT_TEST_CREDENTIALS_REQUIRED', 'لم تضبط مفاتيح الاختبار لهذه المنشأة.')
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path in {'', '/'} and parsed.port in {None, 443}
                 and not any(ch.isspace() or ord(ch)<32 for ch in base))
    except ValueError:
        valid = False
    if not valid:
        raise db.error(503, 'PAYMENT_RETURN_URL_REQUIRED', 'لم يهيأ رابط العودة الآمن للدفع.')
    callback = base+'/payment-return?attempt='+context.attempt_id
    payload = {'amount': context.amount_halalas, 'currency': context.currency,
               'description': 'KPS order '+context.order_id+' / '+context.attempt_id,
               'callback_url': callback}
    from .payment_runtime import secret as provider_secret
    key = provider_secret(context)
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
            response = client.post('https://api.moyasar.com/v1/payment_intents',
                                   json=payload, auth=(key, ''), headers={'Accept':'application/json'})
        if response.status_code != 201:
            raise ValueError('Intent request not confirmed')
        result = response.json()
        identifier = str(UUID(result['id']))
        expires = datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00'))
        secret = result['client_secret']
        if (result.get('status') != 'created' or type(result.get('amount')) is not int
                or any(result.get(k) != v for k,v in payload.items())
                or not isinstance(secret, str) or not re.fullmatch(r'pi_secret_[A-Za-z0-9_-]{8,512}', secret)
                or expires.tzinfo is None or expires <= datetime.now(timezone.utc)):
            raise ValueError('Intent evidence mismatch')
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        # Never forward provider bodies, credentials or card-related diagnostics.
        raise db.error(503, 'PAYMENT_INTENT_UNCONFIRMED', 'تعذر تأكيد جلسة الدفع. يلزم التحقق قبل إعادة المحاولة.') from None
    return CheckoutIntent('moyasar', identifier, context, expires, callback, secret, mode=context.mode)

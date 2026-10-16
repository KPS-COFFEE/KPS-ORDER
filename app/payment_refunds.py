"""Seller-scoped Moyasar sandbox refunds; no automatic retry of a refund POST."""
import os
from uuid import UUID
import httpx
from . import db, payment_gateways as gateways


def configuration(context):
    if (os.getenv('KPS_BRANCH_PAYMENT_REFUNDS_ENABLED') != 'true' or
            os.getenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED') != 'true' or
            os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED') != 'true'):
        raise db.error(409, 'PAYMENT_REFUND_DISABLED', 'الاسترداد التجريبي غير مفعل.')
    if not gateways.sandbox_key_configured('moyasar', context.brand_id, context.entity_id):
        raise db.error(503, 'PAYMENT_TEST_CREDENTIALS_REQUIRED', 'مفاتيح المنشأة التجريبية غير مهيأة.')
    return os.environ[gateways.credential_prefix('moyasar', context.brand_id, context.entity_id)+'_TEST_SECRET_KEY']


def request(context, payment_id, amount=None):
    key = configuration(context)
    identifier = str(UUID(payment_id))
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
            url = 'https://api.moyasar.com/v1/payments/'+identifier
            if amount is None:
                response = client.get(url, auth=(key, ''), headers={'Accept': 'application/json'})
            else:
                if type(amount) is not int or not 1 <= amount <= context.amount_halalas:
                    raise ValueError('Invalid refund amount')
                response = client.post(url+'/refund', json={'amount': amount}, auth=(key, ''), headers={'Accept': 'application/json'})
            if response.status_code != 200:
                raise ValueError('Unconfirmed provider response')
            payload = response.json()
        if (not isinstance(payload, dict) or payload.get('id') != identifier or
                payload.get('live') is not False or payload.get('currency') != context.currency or
                type(payload.get('amount')) is not int or payload['amount'] != context.amount_halalas or
                type(payload.get('refunded')) is not int or not 0 <= payload['refunded'] <= context.amount_halalas or
                payload.get('status') not in {'paid', 'refunded'} or
                not isinstance(payload.get('metadata'), dict) or
                any(payload['metadata'].get(k) != v for k, v in context.metadata.items())):
            raise ValueError('Mismatched provider evidence')
        return payload['refunded']
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise db.error(503, 'PAYMENT_REFUND_UNCONFIRMED', 'نتيجة الاسترداد غير مؤكدة. تحقق قبل أي محاولة جديدة.') from None

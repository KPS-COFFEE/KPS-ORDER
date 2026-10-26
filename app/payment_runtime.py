"""Separate test/live contracts. No environment or activation is changed here."""
import os
import re
from . import db


def mode():
    value = os.getenv('KPS_BRANCH_PAYMENT_MODE', 'test')
    if value not in {'test', 'live'}:
        raise db.error(503, 'PAYMENT_MODE_INVALID', 'إعداد وضع الدفع غير صالح.')
    return value


def live_blockers(brand_id, seller_id):
    from .payment_gateways import credential_prefix
    prefix = credential_prefix('moyasar', brand_id, seller_id)
    from .runtime_revision import revision as source_revision
    revision = source_revision()
    missing = []
    if os.getenv('KPS_BRANCH_PAYMENT_LIVE_ENABLED') != 'true':
        missing.append('live_payments_disabled')
    for name in ('KPS_BRANCH_PAYMENT_LIVE_ACCEPTANCE_SHA', 'KPS_BRANCH_PAYMENT_LIVE_OWNER_APPROVAL_SHA'):
        if not re.fullmatch(r'[0-9a-f]{40}', revision) or os.getenv(name) != revision:
            missing.append(name.lower()+'_required')
    # Deployment operators may set these only after documented ownership review.
    # A valid-looking API key alone is not merchant approval or activation.
    if (os.getenv(prefix+'_LIVE_ACCOUNT_VERIFIED') != 'true' or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', os.getenv(prefix+'_LIVE_ACCOUNT_ID', '')) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}', os.getenv(prefix+'_LIVE_OWNERSHIP_EVIDENCE', ''))):
        missing.append('verified_live_merchant_required')
    return missing


def require_live(brand_id, seller_id):
    if live_blockers(brand_id, seller_id):
        raise db.error(409, 'LIVE_PAYMENT_NOT_APPROVED', 'الدفع الفعلي غير معتمد أو غير مفعل.')


def secret(context):
    from .payment_gateways import credential_prefix, sandbox_key_configured
    prefix = credential_prefix('moyasar', context.brand_id, context.entity_id)
    if context.mode == 'test':
        if os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED') != 'true':
            raise db.error(409, 'PAYMENT_SANDBOX_DISABLED', 'اختبار بوابة الدفع غير مفعل.')
        if not sandbox_key_configured('moyasar', context.brand_id, context.entity_id):
            raise db.error(503, 'PAYMENT_TEST_CREDENTIALS_REQUIRED', 'مفاتيح اختبار المنشأة غير مهيأة.')
        return os.environ[prefix+'_TEST_SECRET_KEY']
    if context.mode != 'live':
        raise db.error(409, 'PAYMENT_MODE_INVALID', 'وضع الدفع غير صالح.')
    require_live(context.brand_id, context.entity_id)
    key = os.getenv(prefix+'_LIVE_SECRET_KEY', '')
    if not re.fullmatch(r'sk_live_[A-Za-z0-9_-]{8,256}', key):
        raise db.error(503, 'PAYMENT_LIVE_CREDENTIALS_REQUIRED', 'مفاتيح الدفع الفعلي غير مهيأة.')
    return key

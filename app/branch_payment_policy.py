"""New KPS branch checkout is fail-closed; historical orders remain readable."""
import os
import re
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends
from . import auth, db, invoice_profiles, payment_gateways as gateways

router = APIRouter(prefix='/api/admin/branch-payment-policy')


def required(brand_id, settings):
    # A retained explicit legacy policy is used by historical regression fixtures.
    # No public administration schema accepts this compatibility setting.
    return settings.get('branch_online_required', brand_id == 'kps') is True


def blockers(branch):
    result = []
    seller = branch['settings'].get('online_payment_seller')
    if not isinstance(seller, str) or not gateways.IDENTIFIER.fullmatch(seller):
        return ['merchant_selection_required']
    revision = os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT')
    if not revision or not re.fullmatch(r'[0-9a-f]{40}', revision) or os.getenv('KPS_BRANCH_PAYMENT_ACCEPTANCE_SHA') != revision:
        result.append('exact_release_payment_acceptance_required')
    for name in ('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED', 'KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED',
                 'KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED', 'KPS_BRANCH_PAYMENT_REFUNDS_ENABLED'):
        if os.getenv(name) != 'true': result.append(name.lower()+'_required')
    if os.getenv('KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED') != 'true':
        result.append('reconciliation_required')
    prefix = gateways.credential_prefix('moyasar', branch['brand_id'], seller)
    if not gateways.sandbox_key_configured('moyasar', branch['brand_id'], seller):
        result.append('seller_test_secret_required')
    if not re.fullmatch(r'pk_test_[A-Za-z0-9_-]{8,256}', os.getenv(prefix+'_TEST_PUBLIC_KEY', '')):
        result.append('seller_test_public_key_required')
    try: Fernet(os.environ['KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY'].encode())
    except (KeyError, ValueError, TypeError): result.append('encrypted_checkout_storage_required')
    try:
        base = urlsplit(os.getenv('PUBLIC_BASE_URL', ''))
        if (base.scheme != 'https' or not base.hostname or base.username or base.password or
                base.query or base.fragment or base.path not in ('', '/') or base.port not in (None, 443)):
            result.append('secure_callback_required')
    except ValueError: result.append('secure_callback_required')
    return result


@router.get('')
def readiness(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        brand = c.execute('SELECT settings FROM brands WHERE id=%s', (user['brand_id'],)).fetchone()
        branches = c.execute('SELECT id,brand_id,name,settings FROM branches WHERE brand_id=%s ORDER BY branch_no,id', (user['brand_id'],)).fetchall()
        profiles = invoice_profiles.settings(c, user['brand_id'])
    services = brand['settings'].get('services', {})
    return {'mode': 'test', 'live_payments_available': False,
            'sellers': [{'id': key, 'name': value.get('legal_name', key)} for key, value in profiles.get('entities', {}).items()],
            'branches': [{'id': row['id'], 'name': row['name'], 'seller_id': row['settings'].get('online_payment_seller'),
                          'payment_ready': not blockers(row), 'blockers': blockers(row),
                          'pickup_owner_enabled': services.get('pickup') is True and row['settings'].get('pickup_enabled') is True,
                          'car_owner_enabled': services.get('car_pickup') is True and row['settings'].get('car_enabled') is True}
                         for row in branches]}

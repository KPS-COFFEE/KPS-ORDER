"""Owner-only provider selection for a later payment activation release.

All providers stay disabled. Saving a planned provider never updates branch
payment methods, creates a checkout, performs network I/O, or changes an order.
"""
from copy import deepcopy

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import auth, db, invoice_profiles, payment_gateways as gateways

router = APIRouter(prefix='/api/admin/online-payments', tags=['payment-preparation'])
owner = auth.require_roles('owner')


class PlannedAccount(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: str | None = None

    @field_validator('provider')
    @classmethod
    def valid_provider(cls, value):
        if value is not None and (not gateways.PROVIDER_ID.fullmatch(value)
                                  or value.startswith(('sk_', 'pk_', 'rk_', 'whsec_'))):
            raise ValueError('Use a provider identifier, not a URL or credential')
        return value


class PreparationBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    accounts: dict[str, PlannedAccount] = Field(max_length=50)
    revision: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=3, max_length=300)

    @field_validator('reason')
    @classmethod
    def nonempty_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError('A preparation reason is required')
        return value.strip()


def _source(conn, brand_id, lock=False):
    row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s' +
                       (' FOR UPDATE' if lock else ''), (brand_id,)).fetchone()
    if not row:
        raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير موجود.')
    settings = row['settings']
    profiles = deepcopy(settings.get('invoice_profiles') or invoice_profiles.defaults(brand_id))
    saved = settings.get('online_payment_preparation') or {}
    return settings, profiles, saved


def _view(brand_id, profiles, saved):
    result = []
    known_names = {p['id']: p['name'] for p in gateways.PLANNED_PROVIDERS}
    for entity_id, entity in sorted(profiles.get('entities', {}).items()):
        account = saved.get('accounts', {}).get(entity_id, {})
        provider = account.get('provider')
        has_adapter = gateways.verification_prepared(provider)
        result.append({'entity_id': entity_id, 'entity_name': entity.get('legal_name', entity_id),
                       'provider': provider, 'provider_name': known_names.get(provider, provider),
                       'branch_count': sum(v == entity_id for v in profiles.get('assignments', {}).values()),
                       'enabled': False,
                       'adapter_state': 'verification_prepared' if has_adapter else 'adapter_required',
                       'sandbox_credentials_configured': bool(provider and
                           gateways.sandbox_key_configured(provider, brand_id, entity_id))})
    # The revision includes the canonical seller/branch mapping: changing that
    # mapping makes an open preparation form stale instead of cross-binding funds.
    revision = db.digest({'brand_id': brand_id, 'profiles': profiles, 'saved': saved})
    return {'state': 'prepared_disabled', 'enabled': False, 'live_payments_available': False,
            'customer_payment_methods': [], 'accounts': result, 'revision': revision,
            'providers': list(gateways.PLANNED_PROVIDERS),
            'requires_before_activation': ['provider_account_approval', 'provider_checkout_adapter',
                'durable_payment_attempts_and_reconciliation', 'verified_callbacks',
                'refund_acceptance', 'exact_release_tests', 'owner_activation']}


@router.get('')
def preparation(user=Depends(owner)):
    with db.tx() as conn:
        _, profiles, saved = _source(conn, user['brand_id'])
        return _view(user['brand_id'], profiles, saved)


@router.put('')
def save_preparation(body: PreparationBody, user=Depends(owner),
                     idem: str | None = Header(None, alias='Idempotency-Key')):
    with db.tx() as conn:
        def perform():
            settings, profiles, saved = _source(conn, user['brand_id'], lock=True)
            before = _view(user['brand_id'], profiles, saved)
            if body.revision != before['revision']:
                raise db.error(409, 'PAYMENT_PREPARATION_CHANGED',
                               'تغيرت إعدادات المنشآت أو الدفع. أعد فتح الشاشة.')
            if set(body.accounts) - set(profiles.get('entities', {})):
                raise db.error(422, 'PAYMENT_SELLER_INVALID',
                               'اختر منشأة موجودة ضمن البراند.')
            # No activation, endpoints or credentials can be supplied in this schema.
            value = {'accounts': {k: v.model_dump() for k, v in body.accounts.items()}}
            settings = settings | {'online_payment_preparation': value}
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',
                         (db.json(settings), user['brand_id']))
            db.audit(conn, user['id'], 'payment.preparation.updated', 'brand', user['brand_id'],
                     {'before': saved, 'after': value, 'reason': body.reason, 'enabled': False})
            return _view(user['brand_id'], profiles, value)
        return db.idempotent(conn, user['id'], idem, 'payment.preparation.update',
                             body.model_dump(), perform)

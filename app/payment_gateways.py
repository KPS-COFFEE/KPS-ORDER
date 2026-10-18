"""Provider-neutral payment preparation. Customer charging is not enabled.

An adapter is explicit code, never a URL or module name supplied by a browser.
The first adapter prepares read-only Moyasar sandbox verification; it does not
create charges, update orders, or write driver collection/custody records.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Protocol
from uuid import UUID

import httpx

from . import db

IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
PROVIDER_ID = re.compile(r'[a-z][a-z0-9_-]{0,39}')
PLANNED_PROVIDERS = (
    {'id': 'moyasar', 'name': 'Moyasar'},
    {'id': 'paytabs', 'name': 'PayTabs'},
    {'id': 'hyperpay', 'name': 'HyperPay'},
    {'id': 'tap', 'name': 'Tap Payments'},
)


@dataclass(frozen=True)
class PaymentContext:
    """Trusted server-side expectation, scoped to one seller and one attempt."""
    brand_id: str
    entity_id: str
    order_id: str
    attempt_id: str
    amount_halalas: int
    currency: str = 'SAR'

    def __post_init__(self):
        identifiers = (self.brand_id, self.entity_id, self.order_id, self.attempt_id)
        if (any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in identifiers)
                or type(self.amount_halalas) is not int
                or not 100 <= self.amount_halalas <= 1_000_000_000
                or self.currency != 'SAR'):
            raise ValueError('Invalid server payment context')

    @property
    def metadata(self):
        return {key: getattr(self, key) for key in
                ('brand_id', 'entity_id', 'order_id', 'attempt_id')}

    @property
    def idempotency_reference(self):
        # Future create/refund adapters can reuse this durable attempt identity.
        # The caller must persist the attempt before contacting its provider.
        return db.digest({**self.metadata, 'amount': self.amount_halalas,
                          'currency': self.currency})


@dataclass(frozen=True)
class VerifiedPayment:
    provider: str
    payment_id: str
    context: PaymentContext
    mode: str


class VerificationAdapter(Protocol):
    provider_id: str

    def verify_sandbox_payment(self, context: PaymentContext,
                               payment_id: str) -> VerifiedPayment: ...


_ADAPTERS: dict[str, VerificationAdapter] = {}


def register_adapter(adapter: VerificationAdapter):
    """Called by trusted application code when a provider adapter is added."""
    provider = adapter.provider_id
    if not isinstance(provider, str) or not PROVIDER_ID.fullmatch(provider):
        raise ValueError('Invalid payment provider identifier')
    if provider in _ADAPTERS:
        raise ValueError('Payment provider already registered')
    _ADAPTERS[provider] = adapter


def adapter_for(provider: str) -> VerificationAdapter:
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise db.error(409, 'PAYMENT_ADAPTER_REQUIRED',
                       'هذا المزود يحتاج ربطًا واختبارًا قبل التفعيل.')
    return adapter


def verification_prepared(provider: str | None) -> bool:
    return provider in _ADAPTERS


def credential_prefix(provider: str, brand_id: str, entity_id: str) -> str:
    if (not isinstance(provider, str) or not PROVIDER_ID.fullmatch(provider)
            or any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v)
                   for v in (brand_id, entity_id))):
        raise ValueError('Invalid merchant credential scope')
    # Hash the exact pair instead of normalizing hyphens/underscores into a
    # colliding environment name. One brand/seller cannot inherit another key.
    scope = hashlib.sha256((provider + '\0' + brand_id + '\0' + entity_id).encode()).hexdigest().upper()
    return f'KPS_PAYMENTS_{provider.upper().replace("-", "_")}_{scope}'


def sandbox_key_configured(provider: str, brand_id: str, entity_id: str) -> bool:
    if provider != 'moyasar':
        return False  # Other adapters define their own credential contracts.
    key = os.getenv(credential_prefix(provider, brand_id, entity_id) + '_TEST_SECRET_KEY', '')
    return bool(re.fullmatch(r'sk_test_[A-Za-z0-9_-]{8,256}', key))


class MoyasarVerificationAdapter:
    provider_id = 'moyasar'

    def verify_sandbox_payment(self, context: PaymentContext,
                               payment_id: str) -> VerifiedPayment:
        # This release has no live switch, checkout endpoint or charge request.
        # Merely supplying credentials must never cause any provider traffic.
        if os.getenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED') != 'true':
            raise db.error(409, 'PAYMENT_SANDBOX_DISABLED', 'اختبار بوابة الدفع غير مفعل.')
        if not sandbox_key_configured(self.provider_id, context.brand_id, context.entity_id):
            raise db.error(503, 'PAYMENT_TEST_CREDENTIALS_REQUIRED',
                           'لم تُضبط مفاتيح اختبار مستقلة لهذه المنشأة.')
        try:
            identifier = str(UUID(payment_id))
        except (ValueError, TypeError, AttributeError):
            raise db.error(422, 'PAYMENT_REFERENCE_INVALID', 'مرجع الدفع غير صالح.') from None
        key = os.environ[credential_prefix(self.provider_id, context.brand_id,
                                           context.entity_id) + '_TEST_SECRET_KEY']
        try:
            with httpx.Client(timeout=httpx.Timeout(10, connect=3),
                              follow_redirects=False, trust_env=False) as client:
                response = client.get('https://api.moyasar.com/v1/payments/' + identifier,
                                      auth=(key, ''), headers={'Accept': 'application/json'})
                if response.status_code != 200:
                    raise ValueError('Provider response rejected')
                payment = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise db.error(503, 'PAYMENT_PROVIDER_UNAVAILABLE',
                           'تعذر التحقق من مزود الدفع. حاول لاحقًا.') from None
        return self.validate(payment, identifier, context)

    def validate(self, payment, payment_id, context):
        # Redirect/browser/webhook claims alone never constitute payment proof.
        # Return only normalized evidence, excluding PANs/tokens/raw responses.
        if (not isinstance(payment, dict)
                or payment.get('id') != payment_id
                or payment.get('status') != 'paid'
                or type(payment.get('amount')) is not int
                or payment['amount'] != context.amount_halalas
                or payment.get('currency') != context.currency
                or type(payment.get('refunded')) is not int
                or payment['refunded'] != 0
                or payment.get('live', False) is not False
                or not isinstance(payment.get('metadata'), dict)
                or any(payment['metadata'].get(k) != v for k, v in context.metadata.items())):
            raise db.error(409, 'PAYMENT_EVIDENCE_MISMATCH',
                           'لم يتطابق إثبات الدفع مع الطلب والمنشأة والمبلغ.')
        return VerifiedPayment(self.provider_id, payment_id, context, 'test')


register_adapter(MoyasarVerificationAdapter())

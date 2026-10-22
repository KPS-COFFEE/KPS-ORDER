"""Once per verified Saudi mobile, per brand, in the order transaction.

Advertising and offer eligibility are independent. A quotation never claims a
gift; order creation serializes the phone and inserts the claim atomically.
"""
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator
from . import auth, catalog, db

router = APIRouter(prefix='/api/gifts', tags=['first-order-gift'])


class GiftItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str = Field(min_length=1, max_length=80)
    options: dict[str, str | list[str]] = Field(default_factory=dict, max_length=15)


class GiftPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    items: list[GiftItem] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def complete_when_enabled(self):
        if self.starts_at is not None and self.starts_at.tzinfo is None:
            raise ValueError('Choose a start time with an explicit time zone.')
        if self.ends_at is not None:
            if self.ends_at.tzinfo is None:
                raise ValueError('Choose an end time with an explicit time zone.')
            if not self.starts_at or self.ends_at <= self.starts_at:
                raise ValueError('The end time must follow the start time.')
        if self.enabled and (not self.starts_at or len(self.items) != 2):
            raise ValueError('An active gift requires a start time and two approved items.')
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError('Choose two different gift products.')
        return self


class PolicyChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy: GiftPolicy
    expected_version: StrictInt = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


def defaults():
    return {'version': 0, 'enabled': False, 'starts_at': None, 'ends_at': None, 'items': []}


def configured(settings):
    return deepcopy((settings or {}).get('first_order_gift') or defaults())


def _item(product, selection, branch):
    if not product or not catalog.source_approved(product) or product['settings'].get('unified_khaliya'):
        raise db.error(409, 'GIFT_ITEM_UNAVAILABLE', 'أحد أصناف الهدية غير معتمد أو غير متاح.')
    if branch is not None and not catalog.product_availability(product, branch, service='delivery')[0]:
        raise db.error(409, 'GIFT_ITEM_UNAVAILABLE', 'أحد أصناف الهدية غير متاح في فرع التجهيز.')
    item = catalog.OrderItem(product_id=product['id'], quantity=1,
                             units=[catalog.Unit(options=selection['options'])])
    original, units = catalog._price_units(product, item, branch)
    for unit in units:
        unit['original_price_halalas'] = unit['price_halalas']
        unit['price_halalas'] = 0
        for group in unit['option_snapshot'].values():
            for choice in group['choices']:
                choice['price_delta_halalas'] = 0
    return {'product_id': product['id'], 'source_id': product['settings'].get('source_id', product['id']),
            'name': product['name'], 'category': product['category'], 'quantity': 1,
            'unit_price_halalas': 0, 'line_total_halalas': 0, 'original_line_total_halalas': original,
            'units': units, 'note': '', 'is_gift': True}


@router.get('/policy')
def read_policy(user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (user['brand_id'],)).fetchone()
        return {'policy': configured((row or {}).get('settings'))}


@router.put('/policy')
def save_policy(body: PolicyChange, user=Depends(auth.require_roles('owner')),
                idem: str | None = Header(None, alias='Idempotency-Key')):
    if len(body.reason.strip()) < 3:
        raise db.error(422, 'REASON_REQUIRED', 'أدخل سبب التعديل.')
    with db.tx() as conn:
        # An authenticated owner confirms this operational change in the UI.
        def perform():
            candidate = body.policy.model_dump(mode='json')
            for selection in candidate['items']:
                product = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s FOR SHARE',
                                       (selection['product_id'], user['brand_id'])).fetchone()
                if not product:
                    raise db.error(409, 'GIFT_ITEM_UNAVAILABLE', 'صنف الهدية غير متاح ضمن هذا البراند.')
                if candidate['enabled']:
                    _item(product, selection, None)
            row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            if not row:
                raise db.error(404, 'BRAND_NOT_FOUND', 'إعدادات البراند غير متاحة.')
            previous = configured(row['settings'])
            if previous['version'] != body.expected_version:
                raise db.error(409, 'POLICY_CHANGED', 'تغير إعداد العرض من جلسة أخرى؛ افتحه مجددًا.')
            # Already-open older owner forms must not silently remove the
            # expiry. Explicit null from the current form can clear it.
            if 'ends_at' not in body.policy.model_fields_set:
                candidate['ends_at'] = previous.get('ends_at')
                try:
                    candidate = GiftPolicy.model_validate(candidate).model_dump(mode='json')
                except ValidationError as error:
                    raise db.error(422, 'GIFT_POLICY_INVALID', 'أعد فتح إعدادات الهدية وحدد موعد انتهاء بعد البداية.') from error
            saved = candidate | {'version': previous['version'] + 1}
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',
                         (db.json(row['settings'] | {'first_order_gift': saved}), user['brand_id']))
            db.audit(conn, user['id'], 'gift.policy.updated', 'brand', user['brand_id'],
                     {'before': previous, 'after': saved, 'reason': body.reason.strip()})
            return {'policy': saved, 'advertising_unchanged': True}
        return db.idempotent(conn, user['id'], idem, 'gift.policy.update', body.model_dump(mode='json'), perform)


def eligibility(p, *, verified, claimed, now=None):
    now = now or datetime.now(timezone.utc)
    if not p['enabled']:
        return 'inactive'
    if not p.get('starts_at') or datetime.fromisoformat(p['starts_at'].replace('Z','+00:00')) > now:
        return 'scheduled'
    if p.get('ends_at') and datetime.fromisoformat(p['ends_at'].replace('Z','+00:00')) <= now:
        return 'expired'
    if not verified:
        return 'verification_required'
    return 'claimed' if claimed else 'eligible'


def prepare(conn, body, quote, user, settings, *, for_order=False):
    """Returns the canonical claim phone only to the trusted creation path."""
    p = configured(settings)
    if for_order and p['enabled']:
        # Coordinate owner stop/change with in-flight gift orders. No extra
        # settings lock/query is taken when the feature is disabled.
        row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR SHARE', (body.brand_id,)).fetchone()
        p = configured((row or {}).get('settings'))
    if not p['enabled']:
        if for_order and body.expected_gift is True:
            raise db.error(409, 'GIFT_CHANGED', 'توقف العرض. راجع السلة قبل تأكيد الطلب.')
        return None
    verified = False
    phone = None
    if p['enabled'] and body.service == 'delivery' and user['brand_id'] == body.brand_id:
        try:
            auth.assert_customer_order_eligible(conn, user, phone_required=True)
            phone = auth.normalize_phone(user.get('phone') or '')
            if not (phone.startswith('+9665') and len(phone) == 13):
                phone = None
            contact = auth.normalize_phone(body.customer.phone) if body.customer.phone else phone
            verified = bool(phone and phone == contact)
        except HTTPException:
            verified = False
    if for_order and verified:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('first-order-gift:'+body.brand_id+':'+phone,))
    claimed = bool(verified and conn.execute('SELECT 1 FROM first_order_gift_claims WHERE brand_id=%s AND phone=%s',
                                             (body.brand_id, phone)).fetchone())
    status = eligibility(p, verified=verified, claimed=claimed)
    if body.service != 'delivery':
        status = 'not_applicable'
    gift_items = []
    if status == 'eligible':
        branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s', (quote['branch_id'], body.brand_id)).fetchone()
        selected = branch if body.branch_id == quote['branch_id'] else conn.execute(
            'SELECT * FROM branches WHERE id=%s AND brand_id=%s', (body.branch_id, body.brand_id)).fetchone()
        minimum = (selected or {}).get('settings', {}).get('minimum_order_halalas')
        if (not branch or type(minimum) is not int or minimum < 0 or
                quote['subtotal_halalas'] <= 0 or quote['subtotal_halalas'] < minimum):
            status = 'minimum_unconfigured' if type(minimum) is not int else 'minimum_not_met'
        try:
            for selection in p['items'] if status == 'eligible' else []:
                product = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s FOR SHARE',
                                       (selection['product_id'], body.brand_id)).fetchone()
                gift_items.append(_item(product, selection, branch))
        except HTTPException as error:
            if error.status_code not in (409, 422):
                raise
            status = 'unavailable'
            gift_items = []
    if for_order and body.expected_gift is True and (status != 'eligible' or body.expected_gift_version != p['version']):
        raise db.error(409, 'GIFT_CHANGED', 'تغير استحقاق الهدية أو توفرها. راجع السلة قبل تأكيد الطلب.')
    quote['first_order_gift'] = {'status': status, 'version': p['version'], 'starts_at': p['starts_at'],
                                'ends_at': p.get('ends_at'), 'once_per_verified_mobile': True, 'items': deepcopy(gift_items)}
    if gift_items:
        quote['items'].extend(gift_items)
    return phone if status == 'eligible' and gift_items else None


@router.post('/quote')
def gift_quote(body: catalog.QuoteBody, user=Depends(auth.require_roles('customer'))):
    if body.brand_id != user['brand_id']:
        raise db.error(403, 'BRAND_FORBIDDEN', 'الطلب لا يتبع حسابك.')
    with db.tx() as conn:
        auth.assert_customer_order_eligible(conn, user, phone_required=True)
        settings = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (body.brand_id,)).fetchone()
        quote = catalog.quote_order(conn, body)
        prepare(conn, body, quote, user, (settings or {}).get('settings'))
        return {'quote': catalog.customer_quote_view(quote)}


def claim_cte(phone, version):
    if not phone:
        return '', ()
    return ("created_gift_claim AS (INSERT INTO first_order_gift_claims(brand_id,phone,order_id,customer_id,policy_version) "
            "SELECT brand_id,%s,id,customer_id,%s FROM created_order RETURNING order_id), ", (phone, version))

"""Brand-scoped promotion and promotional-credit ledger, never cash custody.

Rates, limits, terms and the discount accounting treatment require an explicit
owner-authored version. Nothing is seeded or activated by registration/startup.
"""
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from . import activation, auth, db
from .engagement_validation import EngagementModel

router = APIRouter(tags=['customer-benefits'])


class Program(EngagementModel):
    model_config = ConfigDict(extra='forbid')
    terms_version: str = Field(min_length=1, max_length=100)
    cashback_earn_bps: StrictInt = Field(ge=0, le=10000)
    cashback_redemption_max_bps: StrictInt = Field(ge=0, le=9999)
    redemption_treatment: Literal['promotional_discount']
    # Only this documented contract is implemented, never a guessed expiry.
    expiration_policy: Literal['no_expiration']
    refund_policy: Literal['proportional_confirmed_refund']
    minimum_items_halalas: StrictInt = Field(ge=0, le=10000000)


class Rule(EngagementModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(pattern=r'^[A-Z0-9_-]{3,50}$')
    label: str = Field(min_length=1, max_length=150)
    discount_bps: StrictInt = Field(ge=1, le=9999)
    branch_ids: list[str] = Field(default_factory=list, max_length=100)
    services: list[Literal['delivery','pickup','car']] = Field(min_length=1, max_length=3)
    product_ids: list[str] = Field(default_factory=list, max_length=200)
    segment: Literal['all','new','repeat','selected']
    customer_ids: list[str] = Field(default_factory=list, max_length=1000)
    customer_limit: StrictInt | None = Field(..., ge=1, le=1000000)
    total_limit: StrictInt | None = Field(..., ge=1, le=10000000)
    minimum_items_halalas: StrictInt = Field(ge=0, le=10000000)
    allow_cashback_redemption: bool
    automatic: bool
    starts_at: datetime
    ends_at: datetime
    terms_version: str = Field(min_length=1, max_length=100)

    @field_validator('starts_at','ends_at')
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError('Timezone required')
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def valid(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('End must follow start')
        if (self.segment == 'selected') != bool(self.customer_ids):
            raise ValueError('Selected segment requires explicit customers only')
        return self


class ActiveRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool


@router.get('/api/crm/benefits/setup')
def setup(response:Response,user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control']='no-store'
    with db.tx() as c:
        rules=c.execute('SELECT id,code,definition,active FROM benefit_rules WHERE brand_id=%s ORDER BY created_at DESC,id DESC LIMIT 100',(user['brand_id'],)).fetchall()
        programs=c.execute('SELECT id,definition,created_at FROM benefit_programs WHERE brand_id=%s ORDER BY created_at DESC,id DESC LIMIT 20',(user['brand_id'],)).fetchall()
        branches=c.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY branch_no,id',(user['brand_id'],)).fetchall()
        products=c.execute('SELECT id,name FROM products WHERE brand_id=%s ORDER BY name,id',(user['brand_id'],)).fetchall()
    return {'brand_id':user['brand_id'],'rules':rules,'programs':programs,'branches':branches,'products':products,
            'release_enabled':activation.enabled('customer_benefits',user['brand_id'])}


def _customer(c, user):
    if user.get('role') != 'customer':
        raise db.error(403, 'CUSTOMER_REQUIRED', 'يلزم حساب عميل موثق. / Verified customer required.')
    auth.assert_customer_order_eligible(c, user, phone_required=True)


def calculate(items, rule, redemption, program):
    """Integer halalas; item-base-only discounts never discount modifiers/fees."""
    subtotal = sum(item['line_total_halalas'] for item in items)
    eligible = sum(min(item['line_total_halalas'], item['unit_price_halalas']*item['quantity'])
                   for item in items if not rule or not rule['product_ids'] or item['product_id'] in rule['product_ids'])
    discount = eligible*rule['discount_bps']//10000 if rule else 0
    maximum = (subtotal-discount)*program['cashback_redemption_max_bps']//10000 if program else 0
    if redemption and (not program or redemption > maximum or subtotal < program['minimum_items_halalas']
                       or rule and not rule['allow_cashback_redemption']):
        raise db.error(409, 'CASHBACK_REDEMPTION_NOT_ELIGIBLE', 'الرصيد المطلوب لا يطابق السياسة. / Credit does not match the approved policy.')
    return discount, redemption


def _eligible(c, row, user, body, subtotal, now):
    rule = Rule(**row['definition']).model_dump(mode='json')
    if not (datetime.fromisoformat(rule['starts_at']) <= now < datetime.fromisoformat(rule['ends_at'])):
        return None
    if (body.service not in rule['services'] or rule['branch_ids'] and body.branch_id not in rule['branch_ids']
            or subtotal < rule['minimum_items_halalas'] or rule['segment']=='selected' and user['id'] not in rule['customer_ids']):
        return None
    purchases = c.execute("SELECT count(*) AS n FROM orders WHERE brand_id=%s AND customer_id=ANY(%s) AND payment_status IN ('paid','partially_refunded') AND (preparation_status='completed' OR delivery_status='delivered')", (user['brand_id'],auth.customer_ids(user))).fetchone()['n']
    if rule['segment']=='new' and purchases or rule['segment']=='repeat' and not purchases:
        return None
    counts = c.execute('SELECT count(*) AS total,count(*) FILTER(WHERE customer_id=%s) AS customer FROM benefit_order_states WHERE brand_id=%s AND rule_id=%s', (user['id'],user['brand_id'],row['id'])).fetchone()
    if (rule['customer_limit'] is not None and counts['customer'] >= rule['customer_limit']
            or rule['total_limit'] is not None and counts['total'] >= rule['total_limit']):
        return None
    return rule


def apply(c, body, quote, user=None, *, reserve_order_id=None):
    requested = bool(body.benefit_code or body.cashback_halalas)
    if not activation.enabled('customer_benefits',body.brand_id):
        if requested:
            activation.require('customer_benefits',body.brand_id)
        return quote
    if not user or user.get('role')!='customer':
        if requested:
            raise db.error(401,'CUSTOMER_REQUIRED','سجّل دخول العميل أولًا. / Sign in first.')
        return quote
    _customer(c,user)
    if body.brand_id != user['brand_id']:
        raise db.error(403,'BRAND_FORBIDDEN','البراند خارج نطاق العميل. / Customer brand mismatch.')
    if quote.get('branch_payment_mode')=='test':
        if requested:
            raise db.error(409,'BENEFIT_SANDBOX_ISOLATION','طلبات تجربة الدفع لا تستخدم رصيد العميل أو عروضه الفعلية. / Payment tests cannot consume real credit or offer eligibility.')
        return quote
    # All mutations lock the wallet before the rule(s). Quote reads never reserve.
    if reserve_order_id:
        c.execute('INSERT INTO customer_wallets(brand_id,customer_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(user['brand_id'],user['id']))
    wallet = c.execute('SELECT balance_halalas FROM customer_wallets WHERE brand_id=%s AND customer_id=%s'+(' FOR UPDATE' if reserve_order_id else ''),(user['brand_id'],user['id'])).fetchone()
    available = max(0,(wallet or {}).get('balance_halalas',0))
    if body.cashback_halalas > available:
        raise db.error(409,'CASHBACK_INSUFFICIENT','الرصيد المتاح غير كافٍ. / Insufficient available credit.')
    program_row = c.execute("SELECT p.* FROM benefit_programs p JOIN app_settings a ON a.brand_id=p.brand_id WHERE p.brand_id=%s AND p.id=a.settings->>'customer_benefit_program_id'",(user['brand_id'],)).fetchone()
    program = Program(**program_row['definition']).model_dump() if program_row else None
    rows = c.execute('SELECT * FROM benefit_rules WHERE brand_id=%s AND active ORDER BY id'+(' FOR UPDATE' if reserve_order_id else ''),(user['brand_id'],)).fetchall()
    candidates=[]
    for row in rows:
        if body.benefit_code and row['code']!=body.benefit_code:
            continue
        if not body.benefit_code and not row['definition'].get('automatic'):
            continue
        definition=_eligible(c,row,user,body,quote['subtotal_halalas'],datetime.now(timezone.utc))
        if definition:
            discount,_=calculate(quote['items'],definition,0,program)
            if discount:
                candidates.append((discount,row['id'],definition))
    if body.benefit_code and not candidates:
        raise db.error(409,'BENEFIT_NOT_ELIGIBLE','العرض غير متاح لهذا الطلب. / Offer unavailable for this order.')
    selected=sorted(candidates,key=lambda x:(-x[0],x[1]))[0] if candidates else None
    discount,redeemed=calculate(quote['items'],selected[2] if selected else None,body.cashback_halalas,program)
    if not selected and not redeemed and not program:
        return quote
    earn_bps=program['cashback_earn_bps'] if program and quote['subtotal_halalas']>=program['minimum_items_halalas'] else 0
    earn_base=quote['subtotal_halalas']-discount-redeemed
    quote.update(discount_halalas=discount+redeemed,total_halalas=quote['subtotal_halalas']-discount-redeemed+quote['delivery_fee_halalas'])
    quote['benefits']={'rule_id':selected[1] if selected else None,'offer_discount_halalas':discount,
                       'cashback_redeemed_halalas':redeemed,'earn_bps':earn_bps,'earn_base_halalas':earn_base,
                       'program_id':program_row['id'] if program_row else None,'redemption_treatment':'promotional_discount',
                       'terms_version':program['terms_version'] if program else selected[2]['terms_version'],
                       'rule_terms_version':selected[2]['terms_version'] if selected else None}
    if reserve_order_id:
        c.execute('INSERT INTO benefit_order_states(order_id,brand_id,customer_id,rule_id,discount_halalas,redeemed_halalas,earn_bps,earn_base_halalas) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(reserve_order_id,user['brand_id'],user['id'],selected[1] if selected else None,discount,redeemed,earn_bps,earn_base))
        if redeemed:
            c.execute("INSERT INTO customer_wallet_entries(id,brand_id,customer_id,order_id,amount_halalas,kind,source_key) VALUES(%s,%s,%s,%s,%s,'redemption',%s)",(db.new_id('wallet_'),user['brand_id'],user['id'],reserve_order_id,-redeemed,reserve_order_id+':redemption'))
        db.audit(c,user['id'],'benefit.reserved','order',reserve_order_id,quote['benefits'])
    return quote


@router.get('/api/customer/benefits')
def account(response:Response, after:str=Query(default='',max_length=150), user=Depends(auth.require_roles('customer'))):
    response.headers['Cache-Control']='no-store'
    with db.tx() as c:
        _customer(c,user)
        wallet=c.execute('SELECT balance_halalas FROM customer_wallets WHERE brand_id=%s AND customer_id=%s',(user['brand_id'],user['id'])).fetchone()
        entries=c.execute('SELECT id,order_id,amount_halalas,kind,created_at FROM customer_wallet_entries WHERE brand_id=%s AND customer_id=%s AND id>%s ORDER BY id LIMIT 51',(user['brand_id'],user['id'],after)).fetchall()
    return {'brand_id':user['brand_id'],'balance_halalas':(wallet or {}).get('balance_halalas',0),
            'entries':entries[:50],'next_after':entries[49]['id'] if len(entries)>50 else None}


def _scope_ids(c, brand, body):
    for table, values in (('branches',body.branch_ids),('products',body.product_ids),('users',body.customer_ids)):
        if values:
            # Table names are constants above, never request text.
            rows=c.execute('SELECT id FROM '+table+' WHERE brand_id=%s AND id=ANY(%s)'+(" AND role='customer'" if table=='users' else ''),(brand,values)).fetchall()
            if {r['id'] for r in rows} != set(values):
                raise db.error(422,'BENEFIT_SCOPE_INVALID','أحد المحددات لا يتبع البراند. / Rule scope belongs to another brand.')


@router.post('/api/crm/benefit-programs')
def program(body:Program, user=Depends(auth.require_roles('owner')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        def save():
            identifier=db.new_id('program_')
            c.execute('INSERT INTO benefit_programs(id,brand_id,actor_id,definition) VALUES(%s,%s,%s,%s)',(identifier,user['brand_id'],user['id'],db.json(body.model_dump())))
            # Selection is a saved policy; the release feature gate remains OFF.
            c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{customer_benefit_program_id}',to_jsonb(%s::text)) WHERE brand_id=%s",(identifier,user['brand_id']))
            db.audit(c,user['id'],'benefit.program.versioned','benefit_program',identifier)
            return {'program_id':identifier,'release_enabled':activation.enabled('customer_benefits',user['brand_id'])}
        return db.idempotent(c,user['id'],idempotency_key,'crm.benefit.program:'+user['brand_id'],body.model_dump(),save)


@router.post('/api/crm/benefit-rules')
def create_rule(body:Rule,user=Depends(auth.require_roles('owner')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        _scope_ids(c,user['brand_id'],body)
        def save():
            identifier=db.new_id('benefit_')
            row=c.execute('INSERT INTO benefit_rules(id,brand_id,code,actor_id,definition) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(brand_id,code) DO NOTHING RETURNING id',(identifier,user['brand_id'],body.code,user['id'],db.json(body.model_dump(mode='json')))).fetchone()
            if not row:
                raise db.error(409,'BENEFIT_CODE_EXISTS','رمز العرض مستخدم. / Offer code already exists.')
            db.audit(c,user['id'],'benefit.rule.created','benefit_rule',identifier)
            return {'rule_id':identifier,'active':False}
        return db.idempotent(c,user['id'],idempotency_key,'crm.benefit.rule:'+user['brand_id'],body.model_dump(mode='json'),save)


@router.put('/api/crm/benefit-rules/{identifier}/state')
def rule_state(identifier:str,body:ActiveRule,user=Depends(auth.require_roles('owner')),idempotency_key:str|None=Header(default=None)):
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        if body.active:
            activation.require('customer_benefits',user['brand_id'])
        if not c.execute('SELECT id FROM benefit_rules WHERE id=%s AND brand_id=%s FOR UPDATE',(identifier,user['brand_id'])).fetchone():
            raise db.error(404,'BENEFIT_NOT_FOUND','العرض غير موجود. / Offer not found.')
        def save():
            c.execute('UPDATE benefit_rules SET active=%s WHERE id=%s AND brand_id=%s',(body.active,identifier,user['brand_id']))
            db.audit(c,user['id'],'benefit.rule.state','benefit_rule',identifier,body.model_dump())
            return {'active':body.active}
        return db.idempotent(c,user['id'],idempotency_key,'crm.benefit.state:'+identifier,body.model_dump(),save)

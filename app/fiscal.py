"""Local fiscal preparation; no official issuance or payment-provider dependency."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, Header
from . import auth, db, invoice_profiles

router = APIRouter(prefix='/api/fiscal', tags=['fiscal-preparation'])
owner = auth.require_roles('owner')
SELLER = {'entity_id': 'reem', 'legal_name': 'مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات',
          'vat_number': '311887526600003',
          'address': 'حفر الباطن,حفر الباطن,8437 - حي الصفاء,39912',
          'postal_code': '39912', 'address_source': 'ZATCA VAT certificate 100241103016858'}


def readiness():
    # No flag can accidentally enable the uncertified production path.
    return {'official_issuance_enabled': False, 'status': 'local_preview_only',
            'remaining_requirements': ['phase_applicability_confirmation', 'phase2_xml_and_hash_chain',
                'phase2_onboarded_signing_certificate', 'phase2_reporting_and_recovery',
                'production_activation_authorization']}


def money(value):
    if type(value) is not int or value < 0:
        raise ValueError('Amounts must be non-negative integer halalas')
    return format(Decimal(value) / 100, '.2f')


def inclusive_tax(gross):
    money(gross)
    return int((Decimal(gross)*15/115).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def totals(subtotal, discount, delivery):
    for v in (subtotal, discount, delivery): money(v)
    if discount > subtotal: raise ValueError('Discount exceeds items subtotal')
    gross = subtotal-discount+delivery
    tax = inclusive_tax(gross)
    return {'subtotal_halalas': subtotal, 'discount_halalas': discount, 'delivery_fee_halalas': delivery,
            'total_halalas': gross, 'tax_halalas': tax, 'net_halalas': gross-tax, 'vat_rate': '15.00'}


def encode_qr(doc):
    """Base64 UTF-8 TLV tags 1–5. Never accept invented Phase 2 signatures."""
    if doc.get('phase') != 1: raise ValueError('Phase 2 signed XML is not configured')
    if datetime.fromisoformat(doc['issued_at'].replace('Z', '+00:00')).utcoffset() is None:
        raise ValueError('Timezone required')
    values = [doc['seller']['legal_name'], doc['seller']['vat_number'], doc['issued_at'],
              money(doc['total_halalas']), money(doc['tax_halalas'])]
    payload = bytearray()
    for tag, value in enumerate(values, 1):
        raw = value.encode('utf-8')
        if not 1 <= len(raw) <= 255: raise ValueError('TLV field length out of range')
        payload.extend(bytes((tag, len(raw)))+raw)
    return base64.b64encode(payload).decode('ascii')


def qr(doc):
    text = encode_qr(doc)
    matrix = invoice_profiles.qr_matrix(text)
    return {'text': text, 'matrix': matrix, 'image': 'data:image/svg+xml;base64,'+
            base64.b64encode(invoice_profiles.qr_svg(matrix).encode()).decode()}


def build(order, number, issued_at):
    values = totals(order['subtotal_halalas'], order.get('discount_halalas', 0), order['delivery_fee_halalas'])
    if values['total_halalas'] != order['total_halalas']: raise ValueError('Order totals do not reconcile')
    items = deepcopy(order['snapshot'].get('items', []))
    if not items or sum(x['line_total_halalas'] for x in items) != values['subtotal_halalas']:
        raise ValueError('Items do not reconcile')
    for item in items:
        money(item['line_total_halalas'])
        if type(item['quantity']) is not int or item['quantity'] <= 0: raise ValueError('Invalid quantity')
    if issued_at.utcoffset() is None: raise ValueError('Timezone required')
    doc = {**values, 'schema_version': 1, 'phase': 1, 'kind': 'invoice', 'status': 'local_preview_only',
           'number': number, 'issued_at': issued_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
           'seller': deepcopy(SELLER), 'order_id': order['id'], 'items': items,
           'service': order.get('service','delivery'), 'brand_id': order.get('brand_id','kps'),
           'branch_id': order.get('branch_id'), 'branch_name': order['snapshot'].get('branch_name',''),
           'notes': order.get('notes') or order['snapshot'].get('notes',''),
           'print_labels_en': deepcopy(order.get('print_labels_en',{})),
           'payment_method': order['payment_method'], 'source_revision': order.get('amendment_revision', 0),
           'receipt_occasion_style': deepcopy(order['snapshot'].get('receipt_occasion_style', {})),
           'prices_include_vat': True, 'receipt_occasion_text': order['snapshot'].get('receipt_occasion_text', '')}
    doc['qr_text'] = encode_qr(doc)
    return doc


def credit(original, amount, prior_gross, number, issued_at, refund_id, reason):
    """Cumulative rounding reverses exactly the original VAT on a full refund."""
    for value in (amount, prior_gross): money(value)
    gross = original['total_halalas']
    if not amount or prior_gross+amount > gross: raise ValueError('Credit exceeds remaining original')
    if issued_at.utcoffset() is None: raise ValueError('Timezone required')
    def allocated(v):
        return int((Decimal(original['tax_halalas'])*v/gross).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    tax = allocated(prior_gross+amount)-allocated(prior_gross)
    doc = {**deepcopy(original), 'kind': 'credit_note', 'number': number, 'original_number': original['number'],
           'refund_id': refund_id, 'reason': reason, 'payment_status': 'refunded' if refund_id else 'unpaid',
           'issued_at': issued_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
           'total_halalas': amount, 'tax_halalas': tax, 'net_halalas': amount-tax,
           'subtotal_halalas': amount, 'discount_halalas': 0, 'delivery_fee_halalas': 0,
           'items': [{'name': reason, 'quantity': 1, 'line_total_halalas': amount, 'units': []}]}
    doc['qr_text'] = encode_qr(doc)
    return doc


def print_order(doc):
    return {**deepcopy(doc), 'id': doc['order_id'], 'order_no': doc['number'],
            'created_at': datetime.fromisoformat(doc['issued_at'].replace('Z', '+00:00')),
            'snapshot': {**deepcopy(doc), 'order_reference': doc['number']},
            'order_reference': doc['number'], 'fiscal_document': deepcopy(doc),
            'payment_status': doc.get('payment_status', 'refunded' if doc['kind']=='credit_note' else 'paid'), 'receipt_qr': qr(doc)}


def save_preview(conn, order, actor):
    existing = conn.execute("SELECT document FROM fiscal_previews WHERE order_id=%s AND kind='invoice'", (order['id'],)).fetchone()
    if existing: return existing['document']
    if (order['brand_id'] != 'kps' or order['service'] != 'delivery' or order['payment_status'] != 'paid'
            or order['delivery_status'] != 'delivered'):
        raise db.error(409, 'FISCAL_PREVIEW_SALE_NOT_COMPLETE', 'المعاينة تتطلب طلب توصيل كبز مكتملًا ومحصلًا.')
    number = 'PREVIEW-KPS-'+str(conn.execute("SELECT nextval('fiscal_preview_number_seq') n").fetchone()['n'])
    try: doc = build(order, number, datetime.now(timezone.utc))
    except ValueError as exc:
        raise db.error(409, 'FISCAL_TOTAL_MISMATCH', 'مبالغ الطلب المحفوظة غير متطابقة.') from exc
    conn.execute("INSERT INTO fiscal_previews(order_id,kind,number,document) VALUES(%s,'invoice',%s,%s)",
                 (order['id'], number, db.json(doc)))
    db.audit(conn, actor['id'], 'fiscal.preview_saved', 'order', order['id'], {'number': number, **readiness()})
    return doc


@router.get('/readiness')
def get_readiness(user=Depends(owner)):
    from .fiscal_issuance import status
    with db.tx() as conn:
        return status(conn,user['brand_id'])


@router.post('/orders/{order_id}/preview')
def prepare(order_id: str, user=Depends(owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    from .operations import order_access
    with db.tx() as conn:
        def perform():
            order = conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
            order_access(conn, user, order)
            doc = save_preview(conn, order, user)
            return {'document': doc, 'order': print_order(doc), **readiness()}
        return db.idempotent(conn, user['id'], idem, 'fiscal.preview', {'order_id': order_id}, perform)


@router.post('/orders/{order_id}/credit-previews')
def prepare_credits(order_id: str, user=Depends(owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    from .operations import order_access
    with db.tx() as conn:
        def perform():
            order = conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
            order_access(conn, user, order)
            row = conn.execute("SELECT document FROM fiscal_previews WHERE order_id=%s AND kind='invoice'", (order_id,)).fetchone()
            if not row: raise db.error(409, 'FISCAL_ORIGINAL_REQUIRED', 'يلزم أصل محفوظ قبل معاينة الإشعار الدائن.')
            documents, prior = [], 0
            for refund in conn.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY created_at,id', (order_id,)).fetchall():
                existing = conn.execute('SELECT document FROM fiscal_previews WHERE refund_id=%s', (refund['id'],)).fetchone()
                if existing: doc = existing['document']
                else:
                    number = 'PREVIEW-CN-'+str(conn.execute("SELECT nextval('fiscal_preview_number_seq') n").fetchone()['n'])
                    try: doc = credit(row['document'], refund['amount_halalas'], prior, number, datetime.now(timezone.utc), refund['id'], refund['reason'])
                    except ValueError as exc:
                        raise db.error(409, 'FISCAL_CREDIT_REVIEW_REQUIRED', 'راجع الاستردادات مقابل الأصل المحفوظ.') from exc
                    conn.execute("INSERT INTO fiscal_previews(order_id,kind,number,refund_id,document) VALUES(%s,'credit_note',%s,%s,%s)",
                                 (order_id, number, refund['id'], db.json(doc)))
                    db.audit(conn, user['id'], 'fiscal.credit_preview_saved', 'order', order_id, {'number': number, 'refund_id': refund['id']})
                prior += doc['total_halalas']
                documents.append(doc)
            return {'documents': documents, **readiness()}
        return db.idempotent(conn, user['id'], idem, 'fiscal.credit_previews', {'order_id': order_id}, perform)


@router.post('/orders/{order_id}/issue')
def issue(order_id: str, user=Depends(owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    from .operations import order_access
    from .fiscal_issuance import issue_sale
    with db.tx() as conn:
        order_access(conn, user, conn.execute('SELECT * FROM orders WHERE id=%s', (order_id,)).fetchone())
        def perform():
            doc = issue_sale(conn,order_id,user,required=True)
            return {'document':doc,'order':print_order(doc)}
        return db.idempotent(conn,user['id'],idem,'fiscal.issue',{'order_id':order_id},perform)

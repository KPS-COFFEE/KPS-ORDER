"""KPS ORDER channel identity and internal, gross settlement basis.

Only newly created orders receive this versioned snapshot. Historical records
are never inferred/backfilled. This is not a fiscal issuer or a payout engine.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from . import db

REEM = {'entity_id': 'reem', 'legal_name': 'مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات',
        'registration_number': '7037181620', 'vat_number': '311887526600003',
        'short_national_address': 'EDPC7048',
        'address': '7048 شارع أمير المؤمنين عمر بن الخطاب\n5323 حي أحد\nالدمام 32263\nالمملكة العربية السعودية'}
SAMI_NAME = 'مؤسسة سامي فهاد لافي الحربي لتقديم الوجبات'
SAMI_CR = '7049120772'
MODEL = 'kps_order_reem_seller_v1'


def seller_profile(config):
    entity = config.get('entities', {}).get('reem', {})
    # Never silently replace a conflicting persisted registration or VAT status.
    if (entity.get('legal_name') != REEM['legal_name'] or entity.get('vat_enabled') is not True
            or entity.get('vat_number') != REEM['vat_number']):
        raise db.error(409, 'CHANNEL_SELLER_REVIEW_REQUIRED', 'راجع بيانات بائع قناة الطلبات المعتمدة.')
    return {**deepcopy(entity), **REEM, 'issuance_status': 'not_enabled', 'prices_include_vat': True}


def capture(config, branch_id, quote, actor):
    seller_profile(config)
    beneficiary = config.get('assignments', {}).get(branch_id)
    if beneficiary not in ('reem', 'sami') or beneficiary not in config.get('entities', {}):
        raise db.error(409, 'SETTLEMENT_OWNER_REQUIRED', 'حدد مالك الفرع والمستفيد من التسوية أولًا.')
    return amounts({'model': MODEL, 'customer_facing_seller_legal_entity': 'reem',
        'fulfilment_branch': branch_id, 'branch_owner': beneficiary,
        'settlement_beneficiary': beneficiary, 'delivery_operator': 'reem',
        'tax_invoice_issuer': 'reem', 'delivery_revenue_beneficiary': 'reem',
        'branch_owner_profile': deepcopy(config['entities'][beneficiary]),
        'settlement_basis': 'items_gross_excluding_delivery',
        'settlement_status': 'review_required', 'refund_allocation': 'separate_authorized_ledger',
        'adjustment_reason': 'initial_order', 'audit_actor': actor}, quote)


def amounts(snapshot, quote):
    """Recalculate only within an already authorized order amendment/new order."""
    result = deepcopy(snapshot)
    subtotal, delivery, total = (int(quote[k]) for k in ('subtotal_halalas','delivery_fee_halalas','total_halalas'))
    if min(subtotal, delivery, total) < 0 or subtotal + delivery != total:
        raise db.error(409, 'SETTLEMENT_TOTAL_MISMATCH', 'راجع مبالغ الطلب قبل التسوية.')
    vat = int((Decimal(total)*15/115).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    result.update(settlement_amount_halalas=subtotal, delivery_fee_halalas=delivery,
                  tax_halalas=vat, tax_basis='inclusive_15_percent_channel_sales',
                  tax_document_issued=False, total_halalas=total)
    return result

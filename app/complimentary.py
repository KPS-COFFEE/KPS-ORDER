"""Explicit staff-created deliveries with no customer collection."""
from copy import deepcopy

KINDS = {'hospitality': ('ضيافة','Hospitality'), 'advertising': ('ضيافة إعلانية','Advertising hospitality'),
         'compensation': ('تعويض','Compensation'), 'completion': ('استكمال طلب','Order completion')}


def operational_view(value):
    """Copy an outward operational response; accounting evidence stays intact."""
    if isinstance(value, list):
        return [operational_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    free=value.get('complimentary')
    result={key:operational_view(item) for key,item in value.items() if key!='complimentary'}
    if isinstance(free,dict):
        result['complimentary']={'kind':free.get('kind')}
        if free.get('reason') and result.get('notes')==free['reason']:
            result.pop('notes',None)
    elif 'complimentary' in value:
        result['complimentary']=free
    return result


def contains_complimentary(value):
    if isinstance(value, dict):
        return isinstance(value.get('complimentary'),dict) or any(contains_complimentary(item) for item in value.values())
    return isinstance(value,list) and any(contains_complimentary(item) for item in value)


def is_free(order):
    return (order.get('payment_status') == 'not_required' and order.get('total_halalas') == 0
            and (order.get('snapshot') or {}).get('complimentary', {}).get('kind') in KINDS)


def accounting_values(order, lang='ar'):
    """Historical reference values only; never reprice from today's menu."""
    snap = order.get('snapshot') or {}
    free = snap.get('complimentary') or {}
    en = lang == 'en'
    cancelled = (order.get('preparation_status') in ('cancelled', 'rejected')
                 or order.get('delivery_status') == 'cancelled')
    kind = KINDS.get(free.get('kind'))
    amount = free.get('catalog_value_halalas')
    delivery = free.get('delivery_value_halalas')
    if not free:
        items = snap.get('items') or []
        values = [item.get('original_line_total_halalas', item.get('line_total_halalas')) for item in items]
        amount = sum(values) if values and all(type(value) is int for value in values) else snap.get('subtotal_halalas')
        delivery = snap.get('delivery_fee_halalas')
    missing_reason = ('سبب غير مسجل — يحتاج مراجعة', 'Reason not recorded — review required')[en]
    reason = free.get('reason') or (order.get('cancel_details') or {}).get('reason')
    if kind:
        label = kind[en]
    elif cancelled:
        label = ('طلب ملغى', 'Cancelled order')[en]
    else:
        label = ('طلب بقيمة صفر — يحتاج مراجعة', 'Zero-value order — review required')[en]
    # An unknown historical price is unknown, not a fabricated zero or current price.
    unknown_value = ('غير موثقة', 'Not recorded')[en]
    return {'kind': label, 'amount': amount if type(amount) is int else unknown_value,
            'delivery_value': delivery if type(delivery) is int else unknown_value,
            'payable': 0 if cancelled else order['total_halalas'],
            'treatment': (('على المنشأة — لا مديونية على العميل أو السائق أو مدير التوصيل',
                           'Business-funded — no customer, driver or delivery-manager receivable')[en]
                          if kind else ('تراجع حسب حالة الطلب','Review according to order status')[en]),
            'reason': reason or missing_reason,
            'authorized_by':free.get('authorized_by'), 'authorized_at':free.get('authorized_at'),
            'original_order_id':free.get('original_order_id'),
            'original_invoice_number':free.get('original_invoice_number'),
            'authorization_id':free.get('authorization_id')}


def prepare(quote, kind, reason, actor_id, authorization=None):
    result=deepcopy(quote)
    result['complimentary']={'kind':kind,'reason':reason,'created_by':actor_id,
        'reference_items':deepcopy(quote['items']),
        'catalog_value_halalas':quote['subtotal_halalas'], 'delivery_value_halalas':quote['delivery_fee_halalas']}
    result['complimentary'].update(authorization or {})
    for key in ('subtotal_halalas','delivery_fee_halalas','total_halalas','discount_halalas'):result[key]=0
    for item in result['items']:
        item['unit_price_halalas']=item['line_total_halalas']=0
        for unit in item.get('units') or []:
            if isinstance(unit,dict):unit['price_halalas']=0
    result['tax']={'status':'not_a_sale','amount_halalas':0}
    result['delivery_workflow']='branch_fulfillment'
    return result

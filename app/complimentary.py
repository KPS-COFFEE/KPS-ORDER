"""Explicit staff-created deliveries with no customer collection."""
from copy import deepcopy

KINDS = {'hospitality': ('ضيافة','Hospitality'), 'advertising': ('ضيافة إعلانية','Advertising hospitality'),
         'compensation': ('تعويض','Compensation')}


def is_free(order):
    return (order.get('payment_status') == 'not_required' and order.get('total_halalas') == 0
            and (order.get('snapshot') or {}).get('complimentary', {}).get('kind') in KINDS)


def prepare(quote, kind, reason, actor_id):
    result=deepcopy(quote)
    result['complimentary']={'kind':kind,'reason':reason,'created_by':actor_id,
        'catalog_value_halalas':quote['subtotal_halalas'], 'delivery_value_halalas':quote['delivery_fee_halalas']}
    for key in ('subtotal_halalas','delivery_fee_halalas','total_halalas','discount_halalas'):result[key]=0
    for item in result['items']:
        item['unit_price_halalas']=item['line_total_halalas']=0
        for unit in item.get('units') or []:
            if isinstance(unit,dict):unit['price_halalas']=0
    result['tax']={'status':'not_a_sale','amount_halalas':0}
    result['delivery_workflow']='branch_fulfillment'
    return result

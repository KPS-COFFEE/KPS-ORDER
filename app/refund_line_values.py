"""Conservative selected-quantity ceiling from the immutable order snapshot.

The ceiling never grants a refund or invents a reduction. Unallocated monetary
refunds and ambiguous same-product prices require accounting review without
asserting specific returned quantities. Delivery charges are not item value.
"""
from . import db


def ceiling(order, quantities, previous):
    rows=order['snapshot'].get('items',[])
    gross=sum(int(x['line_total_halalas']) for x in rows)
    net=max(0,min(gross,int(order['total_halalas'])-int(order.get('delivery_fee_halalas') or 0)))
    products={}
    for row in rows:
        pid=row['product_id'];qty=int(row['quantity']);amount=int(row['line_total_halalas'])
        if qty<=0 or amount<0:raise db.error(409,'REFUND_LINE_SNAPSHOT','بيانات الصنف لا تسمح بتقييم مرتجع؛ راجع المحاسبة.')
        item=products.setdefault(pid,{'quantity':0,'gross':0,'prices':set()})
        item['quantity']+=qty;item['gross']+=amount;item['prices'].add((amount,qty))
    value=0
    for pid,q in quantities.items():
        item=products.get(pid)
        prior=previous.get(pid,0)
        if not item or type(q) is not int or q<=0 or prior+q>item['quantity']:
            raise db.error(422,'REFUND_LINE_QUANTITY','اختر كمية مرتجعة فعلية موجبة ضمن المتبقي.')
        # Product-only references cannot distinguish variants with different prices.
        prices=list(item['prices'])
        if any(a*b0!=a0*b for a,b in prices for a0,b0 in prices) and q+prior<item['quantity']:
            raise db.error(409,'REFUND_LINE_AMBIGUOUS','للصنف أسعار مختلفة؛ يلزم تحديد الوحدة الفعلية لدى المحاسبة قبل تسجيل كمية مرتجعة.')
        if gross:
            denominator=gross*item['quantity']
            value+=(item['gross']*net*(prior+q)//denominator)-(item['gross']*net*prior//denominator)
    return value

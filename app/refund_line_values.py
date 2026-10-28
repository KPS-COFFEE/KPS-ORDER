"""Conservative selected-quantity ceiling from the immutable order snapshot.

The ceiling never grants a refund or invents a reduction. Unallocated monetary
refunds and ambiguous same-product prices require accounting review without
asserting specific returned quantities. Delivery charges are not item value.
"""
from . import db


def item_allocations(order):
    """Allocate every net item halalah once in frozen snapshot order.

    Product budgets use cumulative integer differences, then quantities within
    a product telescope against that fixed budget. Zero-price lines never take
    a rounding remainder; delivery fees never enter the item net.
    """
    snapshot=order.get('snapshot')
    rows=snapshot.get('items') if isinstance(snapshot,dict) else None
    if (not isinstance(rows,list) or not rows or type(order.get('total_halalas')) is not int
            or order['total_halalas']<0 or type(order.get('delivery_fee_halalas')) is not int
            or order['delivery_fee_halalas']<0
            or any(not isinstance(x,dict) or not isinstance(x.get('product_id'),str)
                   or type(x.get('quantity')) is not int or x['quantity']<=0
                   or type(x.get('line_total_halalas')) is not int or x['line_total_halalas']<0 for x in rows)):
        raise db.error(409,'REFUND_LINE_SNAPSHOT','بيانات قيمة الصنف غير موثقة؛ راجع المحاسبة قبل تقييم المرتجع.')
    gross=sum(x['line_total_halalas'] for x in rows)
    net=max(0,min(gross,order['total_halalas']-order['delivery_fee_halalas']))
    products={}
    for index,row in enumerate(rows):
        pid=row['product_id'];qty=int(row['quantity']);amount=int(row['line_total_halalas'])
        if qty<=0 or amount<0:raise db.error(409,'REFUND_LINE_SNAPSHOT','بيانات الصنف لا تسمح بتقييم مرتجع؛ راجع المحاسبة.')
        item=products.setdefault(pid,{'quantity':0,'gross':0,'prices':set(),'line_indices':[]})
        item['quantity']+=qty;item['gross']+=amount;item['prices'].add((amount,qty))
        item['line_indices'].append(index)
    cumulative=0
    for item in products.values():
        end=cumulative+item['gross']
        item['net_halalas']=(end*net//gross-cumulative*net//gross) if gross else 0
        cumulative=end
    return rows,products


def quantity_value(budget,quantity,prior,selected):
    """A fixed budget's cumulative differences preserve split-return totals."""
    return budget*(prior+selected)//quantity-budget*prior//quantity


def ceiling(order, quantities, previous):
    _,products=item_allocations(order)
    value=0
    for pid,q in quantities.items():
        item=products.get(pid)
        prior=previous.get(pid,0)
        if (not item or type(q) is not int or q<=0 or type(prior) is not int
                or prior<0 or prior+q>item['quantity']):
            raise db.error(422,'REFUND_LINE_QUANTITY','اختر كمية مرتجعة فعلية موجبة ضمن المتبقي.')
        # Product-only references cannot distinguish variants with different prices.
        prices=list(item['prices'])
        if any(a*b0!=a0*b for a,b in prices for a0,b0 in prices) and (q<item['quantity'] or prior):
            raise db.error(409,'REFUND_LINE_AMBIGUOUS','للصنف أسعار مختلفة؛ يلزم تحديد الوحدة الفعلية لدى المحاسبة قبل تسجيل كمية مرتجعة.')
        value+=quantity_value(item['net_halalas'],item['quantity'],prior,q)
    return value


def unallocated_prior_ids(refunds, events):
    """Find monetary returns whose item coverage cannot be proved.

    One refund can consume several collection rows: only the first row carries
    quantities. Its immutable order event records all row IDs, the exact total,
    and the selected quantities, so subsequent empty rows are not guessed to be
    an unrelated refund. Amendment credit and test voids are separate records.
    """
    actual={r['id']:r for r in refunds if not r.get('amendment_id') and r.get('record_kind')!='test_void'}
    documented={r['id'] for r in actual.values() if any(type(q) is int and q>0 for q in (r.get('line_quantities') or {}).values())}
    for event in events:
        details=event.get('details') or {}
        ids=details.get('refund_ids')
        quantities=details.get('line_quantities')
        if (not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids)
                or len(ids)!=len(set(ids)) or any(i not in actual for i in ids)
                or not isinstance(quantities,dict) or not quantities
                or any(type(q) is not int or q<=0 for q in quantities.values())
                or type(details.get('amount_halalas')) is not int):
            continue
        rows=[actual[i] for i in ids]
        if (sum(r['amount_halalas'] for r in rows)==details['amount_halalas']
                and rows[0].get('line_quantities')==quantities
                and all(not r.get('line_quantities') for r in rows[1:])):
            documented.update(ids)
    return sorted(set(actual)-documented)


def require_documented_prior(c, order_id):
    rows=c.execute('SELECT id,amount_halalas,line_quantities,amendment_id,record_kind FROM refunds WHERE order_id=%s ORDER BY created_at,id',(order_id,)).fetchall()
    events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='order.refund' ORDER BY created_at,id",(order_id,)).fetchall()
    covered={rid for record in approved_allocations(c,order_id) for rid in record['refund_ids']}
    missing=set(unallocated_prior_ids(rows,events))-covered
    if missing:
        raise db.error(409,'REFUND_PRIOR_ITEM_ALLOCATION_REQUIRED',
            'يوجد استرداد مالي سابق دون كميات موثقة؛ يلزم ربطه بالأصناف لدى المحاسبة قبل تقييم مرتجع جديد، دون تكرار الرد.')


def approved_allocations(c,order_id):
    return [r['details'] for r in c.execute("SELECT details FROM audit_events WHERE entity_type='order' AND entity_id=%s AND action='accounting.refund_item_allocation.approved' ORDER BY created_at,id",(order_id,)).fetchall()]


def prior_quantities(c,order_id,products):
    previous={pid:int(c.execute("SELECT coalesce(sum((line_quantities->>%s)::int),0) n FROM refunds WHERE order_id=%s AND amendment_id IS NULL AND record_kind<>'test_void'",(pid,order_id)).fetchone()['n']) for pid in products}
    for record in approved_allocations(c,order_id):
        for pid,quantity in record['line_quantities'].items():
            if pid in previous:previous[pid]+=quantity
    from .delivery_partial_returns import approved
    order=c.execute('SELECT snapshot FROM orders WHERE id=%s',(order_id,)).fetchone()
    for record in approved(c,order_id):
        for line in record['lines']:
            pid=order['snapshot']['items'][line['line_index']]['product_id']
            if pid in previous:previous[pid]+=line['quantity']
    return previous


def overlay_item_quantities(refunds,allocations):
    """Read projection only: one approved item allocation has one quantity effect."""
    result=[dict(r,line_quantities=dict(r.get('line_quantities') or {})) for r in refunds]
    by_id={r['id']:r for r in result}
    for allocation in allocations:
        identifiers=allocation['refund_ids']
        if not identifiers or identifiers[0] not in by_id:continue
        row=by_id[identifiers[0]]
        for product,quantity in allocation['line_quantities'].items():
            row['line_quantities'][product]=row['line_quantities'].get(product,0)+quantity
    return result


def with_allocated_quantities(c,refunds):
    if not refunds:return []
    orders=list({r['order_id'] for r in refunds})
    records=c.execute("SELECT details FROM audit_events WHERE entity_type='order' AND entity_id=ANY(%s) AND action='accounting.refund_item_allocation.approved' ORDER BY created_at,id",(orders,)).fetchall()
    return overlay_item_quantities(refunds,[r['details'] for r in records])

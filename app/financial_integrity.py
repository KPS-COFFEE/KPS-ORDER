"""Independent checks of immutable closure evidence; never repair money silently."""


def inspect_snapshot(snapshot):
    issues = []
    required = ('orders', 'collections', 'refunds', 'summary')
    if not isinstance(snapshot, dict) or any(key not in snapshot for key in required):
        return {'verified': False, 'issues': [{'code': 'EVIDENCE_MISSING'}]}
    summary = snapshot['summary']
    if not isinstance(summary, dict) or any(not isinstance(snapshot[key], list) for key in required[:-1]):
        return {'verified': False, 'issues': [{'code': 'INVALID_EVIDENCE'}]}
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
           for key in required[:-1] for row in snapshot[key]):
        return {'verified': False, 'issues': [{'code': 'INVALID_RECORD'}]}
    if any(type(row.get('total_halalas')) is not int or row['total_halalas'] < 0 for row in snapshot['orders']):
        return {'verified': False, 'issues': [{'code': 'INVALID_ORDER_MONEY'}]}
    external = snapshot.get('external_enbishan', [])
    if (not isinstance(external, list) or not isinstance(snapshot.get('external_enbishan_totals', {}), dict)
            or any(not isinstance(row, dict) or not isinstance(row.get('shift_id'), str)
                   or any(type(row.get(key)) is not int or row[key] < 0
                          for key in ('order_count', 'cash_halalas', 'card_halalas', 'total_halalas')) for row in external)):
        return {'verified': False, 'issues': [{'code': 'INVALID_EXTERNAL_EVIDENCE'}]}

    def check(key, actual, expected):
        if actual != expected:
            issues.append({'code': key, 'recorded': actual, 'calculated': expected})

    for name in required[:-1]:
        rows = snapshot[name]
        ids = [row.get('id') for row in rows]
        if None in ids or len(ids) != len(set(ids)):
            issues.append({'code': 'DUPLICATE_OR_MISSING_ID', 'source': name})
    collections, refunds = snapshot['collections'], snapshot['refunds']
    for name, rows in (('collections', collections), ('refunds', refunds)):
        for row in rows:
            if type(row.get('amount_halalas')) is not int or row['amount_halalas'] < 0 or row.get('method') not in ('cash', 'card'):
                issues.append({'code': 'INVALID_MONEY', 'source': name, 'id': row.get('id')})
    if any(issue['code'] == 'INVALID_MONEY' for issue in issues):
        return {'verified': False, 'issues': issues}
    void = sum(r['amount_halalas'] for r in refunds if r.get('record_kind') == 'test_void')
    cash = sum(r['amount_halalas'] for r in collections if r['method'] == 'cash') - void
    card = sum(r['amount_halalas'] for r in collections if r['method'] == 'card')
    cash_refunds = sum(r['amount_halalas'] for r in refunds if r['method'] == 'cash' and r.get('record_kind') != 'test_void')
    card_refunds = sum(r['amount_halalas'] for r in refunds if r['method'] == 'card' and r.get('record_kind') != 'test_void')
    for key, expected in dict(cash_collected_halalas=cash, card_collected_halalas=card,
                             cash_refunded_halalas=cash_refunds, card_refunded_halalas=card_refunds,
                             net_collections_halalas=cash+card-cash_refunds-card_refunds).items():
        check(key, summary.get(key), expected)
    orders = snapshot['orders']
    completed = [o for o in orders if o.get('fulfilled_in_report') and not o.get('test_void')]
    check('unique_orders', summary.get('unique_orders'), len(orders))
    check('completed_orders', summary.get('completed_orders'), len(completed))
    check('gross_completed_halalas', summary.get('gross_completed_halalas'), sum(o['total_halalas'] for o in completed))
    shift_ids = [r.get('shift_id') for r in external]
    if len(shift_ids) != len(set(shift_ids)):
        issues.append({'code': 'DUPLICATE_EXTERNAL_SHIFT'})
    for row in external:
        check('external_payment_total', row.get('total_halalas'), row.get('cash_halalas', 0)+row.get('card_halalas', 0))
    for key in ('order_count', 'cash_halalas', 'card_halalas', 'total_halalas'):
        check('external_'+key, snapshot.get('external_enbishan_totals', {}).get(key, 0), sum(r.get(key, 0) for r in external))
    return {'verified': not issues, 'issues': issues}


def require_verified(snapshot):
    from fastapi import HTTPException
    result = inspect_snapshot(snapshot)
    if not result['verified']:
        raise HTTPException(409, {'code': 'FINANCIAL_INTEGRITY_BLOCKED',
            'message': 'تعذر اعتماد الحساب: تفاصيل التقفيلة لا تطابق الإجماليات أو أدلتها ناقصة. راجع الفروقات دون تعديل الأصل.',
            'integrity': result})
    return result

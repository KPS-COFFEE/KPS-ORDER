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
        # bool is an int subclass: False must never document an actual zero.
        if type(actual) is not int or actual != expected:
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
    issues.extend(inspect_custody(snapshot))
    return {'verified': not issues, 'issues': issues}


def require_verified(snapshot):
    from fastapi import HTTPException
    result = inspect_snapshot(snapshot)
    if not result['verified']:
        raise HTTPException(409, {'code': 'FINANCIAL_INTEGRITY_BLOCKED',
            'message': 'تعذر اعتماد الحساب: تفاصيل التقفيلة لا تطابق الإجماليات أو أدلتها ناقصة. راجع الفروقات دون تعديل الأصل.',
            'integrity': result})
    return result


def inspect_custody(snapshot):
    """Rebuild the report's own shift ledger from immutable source rows.

    Manager reports also contain child events. Their opening balances are not
    present in historical snapshots, so this does not certify child ledgers or
    a bank deposit. Declarations and witnessed receipts remain separate facts.
    """
    shift = snapshot.get('shift')
    events, transfers = snapshot.get('custody_events'), snapshot.get('transfers')
    if (not isinstance(shift, dict) or not isinstance(shift.get('id'), str)
            or type(shift.get('opening_halalas')) is not int or shift['opening_halalas'] < 0
            or not isinstance(shift.get('submission'), dict)
            or not isinstance(events, list) or not isinstance(transfers, list)):
        return [{'code': 'CUSTODY_EVIDENCE_MISSING'}]
    if any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
           or not isinstance(r.get('shift_id'), str) or not isinstance(r.get('reference_id'), str)
           or type(r.get('amount_halalas')) is not int
           or r.get('kind') not in ('collection','refund','transfer_in','transfer_out','expense','direct_receipt','adjustment')
           for r in events):
        return [{'code': 'INVALID_CUSTODY_EVENT'}]
    if any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
           or not isinstance(r.get('from_shift_id'), str)
           or type(r.get('amount_halalas')) is not int or r['amount_halalas'] < 0
           or type(r.get('closure')) is not bool or r.get('status') not in ('pending','confirmed','rejected')
           for r in transfers):
        return [{'code': 'INVALID_CUSTODY_TRANSFER'}]
    issues = []
    for name, rows in (('custody_events',events),('transfers',transfers)):
        if len({r['id'] for r in rows}) != len(rows):
            issues.append({'code':'DUPLICATE_OR_MISSING_ID','source':name})
    keys = [(r['shift_id'],r['kind'],r['reference_id']) for r in events]
    if len(keys) != len(set(keys)):
        issues.append({'code':'DUPLICATE_CUSTODY_EFFECT'})
    indexed = {(r['shift_id'],r['kind'],r['reference_id']):r for r in events}
    shifts = {r['shift_id'] for r in events} | {shift['id']}
    for transfer in transfers:
        if transfer['status'] != 'confirmed':
            if any(r['reference_id']==transfer['id'] and r['kind'].startswith('transfer_') for r in events):
                issues.append({'code':'UNCONFIRMED_TRANSFER_EFFECT','id':transfer['id']})
            continue
        if (not transfer.get('confirmed_by') or not transfer.get('confirmed_at')
                or transfer.get('confirmed_by') != transfer.get('recipient_user_id')):
            issues.append({'code':'TRANSFER_RECEIPT_UNDOCUMENTED','id':transfer['id']})
        for sid,kind,amount in ((transfer['from_shift_id'],'transfer_out',-transfer['amount_halalas']),
                                (transfer.get('to_shift_id'),'transfer_in',transfer['amount_halalas'])):
            if sid not in shifts:
                continue
            row=indexed.get((sid,kind,transfer['id']))
            if not row or row['amount_halalas'] != amount:
                issues.append({'code':'TRANSFER_EFFECT_MISMATCH','id':transfer['id'],'shift_id':sid})
    transfer_ids = {r['id'] for r in transfers}
    for row in events:
        if row['kind'] in ('transfer_in','transfer_out') and row['reference_id'] not in transfer_ids:
            issues.append({'code':'TRANSFER_SOURCE_MISSING','id':row['id']})
    corrections=snapshot.get('driver_attribution_corrections',[])
    if not isinstance(corrections,list) or any(not isinstance(r,dict) for r in corrections):
        issues.append({'code':'ATTRIBUTION_EVIDENCE_INVALID'})
        corrections=[]
    corrected_orders={}
    for correction in corrections:
        if (not isinstance(correction.get('id'),str) or not isinstance(correction.get('order_id'),str)
                or not isinstance(correction.get('target_shift_id'),str)
                or type(correction.get('cash_halalas')) is not int or correction['cash_halalas']<0):
            issues.append({'code':'ATTRIBUTION_EVIDENCE_INVALID'})
            continue
        corrected_orders[correction['order_id']]=correction
        target=correction['target_shift_id'];amount=correction['cash_halalas']
        source_rows=[r for r in snapshot.get('collections',[]) if r.get('order_id')==correction['order_id'] and r.get('method')=='cash']
        if target in shifts and amount:
            effect=indexed.get((target,'adjustment',correction['id']))
            if (not effect or effect['amount_halalas']!=amount or sum(r['amount_halalas'] for r in source_rows)!=amount
                    or any(r.get('shift_id')!=target for r in source_rows)):
                issues.append({'code':'ATTRIBUTION_CASH_EFFECT_MISMATCH','id':correction['id']})
    for name, kind in (('collections','collection'),('refunds','refund')):
        for source in snapshot.get(name,[]):
            if source.get('method') != 'cash' or not source.get('shift_id'):
                continue
            if name=='collections' and source.get('order_id') in corrected_orders:
                continue
            # Test voids reverse cash through a linked adjustment, without a payout.
            effect='adjustment' if source.get('record_kind')=='test_void' else kind
            row=indexed.get((source['shift_id'],effect,source['id']))
            amount=source['amount_halalas']*(1 if name=='collections' else -1)
            if not row or row['amount_halalas'] != amount:
                issues.append({'code':'CASH_SOURCE_EFFECT_MISMATCH','source':name,'id':source['id']})
    sid=shift['id'];sub=shift['submission']
    closing={r['id'] for r in transfers if r['from_shift_id']==sid and r['closure'] and r['status']=='confirmed'}
    own=[r for r in events if r['shift_id']==sid]
    ledger=shift['opening_halalas']+sum(r['amount_halalas'] for r in own
        if not (r['kind']=='adjustment' and r['reference_id']=='closure-difference:'+sid)
        and not (r['kind']=='transfer_out' and r['reference_id'] in closing))
    if type(sub.get('expected_halalas')) is not int or sub['expected_halalas'] != ledger:
        issues.append({'code':'CUSTODY_EXPECTED_MISMATCH','recorded':sub.get('expected_halalas'),'calculated':ledger})
    if type(sub.get('counted_halalas')) is not int or sub['counted_halalas'] < 0:
        issues.append({'code':'CUSTODY_DECLARATION_UNDOCUMENTED'})
    elif type(sub.get('difference_halalas')) is not int or sub['difference_halalas'] != sub['counted_halalas']-ledger:
        issues.append({'code':'CUSTODY_DECLARATION_DIFFERENCE_MISMATCH'})
    # A pending final transfer documents custody still with its sender. It is
    # not a witnessed receipt; absence of receipt never becomes a recorded zero.
    if 'actual_handover_halalas' in sub:
        received=sum(r['amount_halalas'] for r in transfers if r['id'] in closing)
        if type(sub['actual_handover_halalas']) is not int or not closing or sub['actual_handover_halalas'] != received:
            issues.append({'code':'CUSTODY_ACTUAL_RECEIPT_MISMATCH'})
    return issues

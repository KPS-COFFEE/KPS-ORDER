"""Owner-attested reversal of fictitious cash on an unclosed morning shift.

Original collections and documents are retained. No payment provider, cash
disbursement, inventory return, or customer notification is executed.
"""
from datetime import datetime,timezone
from . import db


def perform(c,user,oid,body):
    from .operations import order_access,lock_shift,event,available_balance,uid,fail
    order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
    order_access(c,user,order)
    if order.get('test_void'):fail(409,'الفاتورة ملغاة تجريبيًا مسبقًا.')
    if not body.confirmed_no_cash_received:fail(422,'أكد أن المبلغ لم يُستلم ولم يُصرف فعليًا.')
    if (order['version']!=body.expected_version or order['total_halalas']!=body.expected_total_halalas
        or order['snapshot'].get('order_reference')!=body.order_reference):fail(409,'بيانات الفاتورة تغيرت؛ راجعها أولًا.')
    if (order['preparation_status']=='cancelled' and order['delivery_status']=='cancelled'
        and order['payment_status']=='unpaid'):
        return mark_unpaid_cancelled_test(c,user,order,body)
    if (order['service']!='delivery' or order['snapshot'].get('delivery_period')!='morning'
        or order['payment_method']!='cash' or order['payment_status']!='paid'
        or order['delivery_status']!='delivered'):fail(409,'الإجراء لتجربة صباحية مكتملة ومسجلة كاش فقط.')
    from .branch_payments import online_order
    from .order_amendments import active
    if online_order(order) or active(c,oid) or c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(oid,)).fetchone():
        fail(409,'الفاتورة عليها دفع إلكتروني أو تعديل أو استرداد سابق؛ يلزم مراجعتها.')
    if c.execute('SELECT 1 FROM channel_settlement_allocations WHERE order_id=%s',(oid,)).fetchone():
        fail(409,'اعتمدت تسوية داخلية لهذه الفاتورة؛ يلزم مراجعة المحاسب.')
    tasks=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s ORDER BY created_at,id FOR UPDATE',(oid,)).fetchall()
    delivered=[t for t in tasks if t['status']=='delivered']
    if len(delivered)!=1:fail(409,'تعذر تحديد مهمة التحصيل الأصلية بشكل فريد.')
    task=delivered[0];shift=lock_shift(c,task['shift_id'])
    manager=lock_shift(c,shift['manager_shift_id']) if shift else None
    if (not shift or not manager or shift['status']!='open' or manager['status']!='open'
        or shift['brand_id']!=order['brand_id'] or manager['brand_id']!=order['brand_id']
        or (shift.get('delivery_window_snapshot') or {}).get('period')!='morning'
        or (manager.get('delivery_window_snapshot') or {}).get('period')!='morning'
        or c.execute('SELECT 1 FROM closure_reports WHERE shift_id=ANY(%s)',([shift['id'],manager['id']],)).fetchone()):
        fail(409,'لا يمكن عكس تجربة بعد تقديم أو إقفال الشفت أو خلط الفترات.')
    collections=c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
    if (len(collections)!=1 or collections[0]['method']!='cash'
        or collections[0]['shift_id']!=shift['id'] or collections[0]['collector_id']!=task['driver_id']
        or collections[0]['amount_halalas']!=order['total_halalas']):fail(409,'قيد التحصيل الأصلي غير مطابق للفحص.')
    col=collections[0];amount=col['amount_halalas']
    if not c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='collection' AND reference_id=%s AND amount_halalas=%s",(shift['id'],col['id'],amount)).fetchone():
        fail(409,'قيد العهدة الأصلي غير مطابق؛ لم يجرِ أي عكس.')
    if c.execute("SELECT 1 FROM custody_transfers WHERE from_shift_id=%s AND status IN ('pending','confirmed')",(shift['id'],)).fetchone():
        fail(409,'سُجل تسليم نقد من هذا الشفت؛ يلزم مراجعة المحاسب بدل الإلغاء التجريبي.')
    if available_balance(c,shift)<amount:fail(409,'لا يكفي الرصيد المسجل لعكس التجربة بأمان.')
    rid=uid();reason='إلغاء تجريبي بأمر المالك — لا صرف نقدي: '+body.reason
    from .fiscal_issuance import credit_before_refund
    credit=credit_before_refund(c,order,user,rid,amount,reason)
    c.execute("""INSERT INTO refunds(id,collection_id,order_id,shift_id,method,amount_halalas,line_quantities,reason,authorized_by,record_kind)
      VALUES(%s,%s,%s,%s,'cash',%s,'{}',%s,%s,'test_void')""",(rid,col['id'],oid,shift['id'],amount,reason,user['id']))
    event(c,shift['id'],-amount,'adjustment',rid,user['id'],reason)
    marker=dict(id=rid,reason=body.reason,actor_id=user['id'],at=datetime.now(timezone.utc).isoformat(),
        amount_halalas=amount,no_cash_disbursed=True,no_cash_received=True,original_collection_id=col['id'],
        original_shift_id=shift['id'],original_delivery_status=order['delivery_status'],original_payment_status=order['payment_status'])
    c.execute("UPDATE orders SET test_void=%s,preparation_status='cancelled',delivery_status='cancelled',payment_status='refunded',version=version+1 WHERE id=%s",(db.json(marker),oid))
    # Delivered task evidence is immutable; cancellation belongs to the order
    # and its additive reversal ledger, not a rewrite of the delivery history.
    c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'owner_test_void',%s)",(uid(),oid,user['id'],db.json(marker)))
    db.audit(c,user['id'],'order.test_void','order',oid,{'before':{'payment_status':order['payment_status'],'delivery_status':order['delivery_status'],'task_status':task['status']},**marker})
    return {'order_id':oid,'order_reference':body.order_reference,'reversal_id':rid,'record_kind':'test_void',
        'amount_halalas':amount,'no_cash_disbursed':True,'credit_note':credit['number'] if credit else None,'shift_id':shift['id']}


def mark_unpaid_cancelled_test(c,user,order,body):
    """Exclude an unpaid cancelled test without inventing a refund or receipt.

    A submitted driver shift may still be corrected before its approval. Closed
    shifts and issued reports remain immutable. Original task/cancellation
    evidence, all monetary ledgers and the unpaid payment status are retained.
    """
    from .operations import lock_shift,uid,fail
    from .branch_payments import online_order
    from .order_amendments import active
    oid=order['id']
    if (user['role']!='owner' or order['service']!='delivery'
        or order['snapshot'].get('delivery_period')!='morning'
        or order['payment_method']!='cash'):
        fail(409,'التصنيف لتجربة توصيل صباحية ملغاة وغير محصلة فقط.')
    if (online_order(order) or active(c,oid)
        or c.execute('SELECT 1 FROM collections WHERE order_id=%s',(oid,)).fetchone()
        or c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(oid,)).fetchone()
        or c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()
        or c.execute('SELECT 1 FROM channel_settlement_allocations WHERE order_id=%s',(oid,)).fetchone()):
        fail(409,'الطلب مرتبط بتحصيل أو مستند أو تعديل؛ يلزم مراجعته دون تغيير مالي.')
    tasks=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s ORDER BY created_at,id FOR UPDATE',(oid,)).fetchall()
    if not tasks or any(t['status'] not in {'cancelled','transferred','reassigned'} for t in tasks):
        fail(409,'أنهِ مهام الطلب قبل تصنيف التجربة.')
    # Lock every historical task shift as well: the marker affects entitlement
    # projections for the whole order, so no closed report may be rewritten.
    for sid in sorted({t['shift_id'] for t in tasks}):
        shift=lock_shift(c,sid)
        manager=lock_shift(c,shift['manager_shift_id']) if shift and shift.get('manager_shift_id') else None
        if (not shift or not manager or shift['kind']!='driver' or manager['kind']!='manager'
            or shift['status'] not in {'open','closing','submitted'}
            or manager['status'] not in {'open','closing'}
            or shift['brand_id']!=order['brand_id'] or manager['brand_id']!=order['brand_id']
            or order['branch_id'] not in shift['branch_ids'] or order['branch_id'] not in manager['branch_ids']
            or (shift.get('delivery_window_snapshot') or {}).get('period')!='morning'
            or (manager.get('delivery_window_snapshot') or {}).get('period')!='morning'
            or c.execute('SELECT 1 FROM closure_reports WHERE shift_id=ANY(%s)',([sid,manager['id']],)).fetchone()):
            fail(409,'لا تصنف التجربة بعد اعتماد الشفت أو خارج مناوبتها الأصلية.')
    marker=dict(id=uid(),kind='unpaid_cancelled_test',reason=body.reason,actor_id=user['id'],
                at=datetime.now(timezone.utc).isoformat(),amount_halalas=0,
                no_cash_disbursed=True,no_cash_received=True,
                original_delivery_status=order['delivery_status'],original_payment_status=order['payment_status'])
    c.execute('UPDATE orders SET test_void=%s,version=version+1 WHERE id=%s',(db.json(marker),oid))
    c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'owner_test_void',%s)",(uid(),oid,user['id'],db.json(marker)))
    db.audit(c,user['id'],'order.test_void','order',oid,marker)
    return {'order_id':oid,'order_reference':body.order_reference,'record_kind':'test_void',
            'amount_halalas':0,'no_cash_disbursed':True,'no_cash_received':True,'credit_note':None}

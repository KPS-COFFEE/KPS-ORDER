"""Owner-attested correction of completed delivery attribution, before closure.

Original operational and financial rows remain immutable evidence. Effective
read views change attribution; two cash adjustments keep custody balanced.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import ConfigDict, Field, StrictBool, StrictInt
from . import auth, db, operations as ops, service_hours

router = APIRouter(prefix='/api/orders', tags=['driver attribution'])


class Correction(ops.OperationBody):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    source_task_id: str = Field(min_length=1, max_length=200)
    source_shift_id: str = Field(min_length=1, max_length=200)
    target_shift_id: str = Field(min_length=1, max_length=200)
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=5, max_length=1000)
    confirmed: StrictBool


def fail(message):
    raise db.error(409, 'DRIVER_ATTRIBUTION_BLOCKED', message)


def access(user=Depends(auth.current_user)):
    ops.roles(user, 'owner')
    return user


def period(s):
    return (s.get('delivery_window_snapshot') or {}).get('period', 'evening')


def day(s):
    return s.get('delivery_operational_date') or service_hours.delivery_operational_date(s['opened_at'])


def principal(c, sid, brand, branch, roles):
    from .multibrand import project
    raw = c.execute('SELECT * FROM users WHERE id=%s AND active', (sid,)).fetchone()
    p = project(c, raw, brand) if raw else None
    if (not p or p['role'] not in roles or
            (not ops.unrestricted_owner(p) and branch not in p.get('branch_ids', []))):
        fail('صلاحية السائق أو المدير غير سارية في فرع الطلب.')


def shift(c, user, sid, lock=False):
    s = c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id '
                  'WHERE s.id=%s' + (' FOR UPDATE OF s' if lock else ''), (sid,)).fetchone()
    ops.shift_access(c, user, s)
    return s


def untouched(c, sids):
    if c.execute('SELECT 1 FROM closure_reports WHERE shift_id=ANY(%s)', (sids,)).fetchone():
        fail('صدرت تقفيلة لأحد الشفتات؛ يلزم تصحيح محاسبي مستقل.')
    if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) AND status<>'rejected'", (sids, sids)).fetchone():
        fail('توجد حركة تسليم عهدة مرتبطة بالشفت؛ يلزم مراجعة المحاسب.')
    if c.execute('SELECT 1 FROM retained_carryovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)', (sids, sids)).fetchone():
        fail('توجد عهدة مرحلة مرتبطة بالشفت؛ يلزم مراجعة المحاسب.')
    if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) AND status<>'rejected'", (sids, sids)).fetchone():
        fail('توجد عملية تسليم مدير مرتبطة بالمناوبة.')


def source(c, user, o, lock=False):
    if c.execute('SELECT 1 FROM order_driver_attribution_corrections WHERE order_id=%s', (o['id'],)).fetchone():
        fail('تم تصحيح نسبة هذا الطلب مسبقًا؛ راجع سجل التصحيح.')
    if (o['service'] != 'delivery' or o['delivery_status'] != 'delivered' or
            o['preparation_status'] != 'completed' or o['payment_status'] != 'paid' or
            not o.get('completed_at') or o.get('test_void') or o['snapshot'].get('complimentary')):
        fail('التصحيح مخصص لطلب توصيل مكتمل ومحصّل بالكامل.')
    from .branch_payments import online_order
    from .order_amendments import active
    if (online_order(o) or active(c, o['id']) or
            c.execute('SELECT 1 FROM refunds WHERE order_id=%s', (o['id'],)).fetchone() or
            c.execute('SELECT 1 FROM channel_settlement_allocations WHERE order_id=%s', (o['id'],)).fetchone()):
        fail('الطلب مرتبط باسترداد أو تعديل معلق أو تسوية؛ لا يمكن نقل نسبته.')
    if c.execute('SELECT 1 FROM driver_incentive_decisions WHERE order_id=%s', (o['id'],)).fetchone():
        fail('تم احتساب حافز لهذا الطلب؛ يلزم مراجعة المحاسب.')
    tasks = c.execute('SELECT * FROM driver_tasks WHERE order_id=%s ORDER BY id' + (' FOR UPDATE' if lock else ''), (o['id'],)).fetchall()
    delivered = [t for t in tasks if t['status'] == 'delivered']
    if len(delivered) != 1 or any(t['status'] not in ops.TERMINAL or t.get('transfer_shift_id') for t in tasks):
        fail('تعذر تحديد مهمة مكتملة واحدة دون تحويل معلق.')
    t = delivered[0]
    s = shift(c, user, t['shift_id'], lock)
    m = shift(c, user, s['manager_shift_id'], lock)
    if (s['kind'] != 'driver' or m['kind'] != 'manager' or s['status'] != 'open' or m['status'] != 'open'
            or s.get('submission') or s.get('supplement_of_shift_id') or s['user_id'] != t['driver_id']
            or s['brand_id'] != o['brand_id'] or m['brand_id'] != o['brand_id']
            or o['branch_id'] not in s['branch_ids'] or o['branch_id'] not in m['branch_ids']
            or period(s) != period(m) or period(s) != o['snapshot'].get('delivery_period', 'evening')
            or (o.get('delivery_operational_date') and day(s) != o['delivery_operational_date'])):
        fail('يلزم شفت سائق ومدير مفتوحان لنفس فرع وفترة ويوم الطلب، قبل تقديم التقفيلة.')
    principal(c, s['user_id'], o['brand_id'], o['branch_id'], ('driver',))
    principal(c, m['user_id'], o['brand_id'], o['branch_id'], ('owner', 'delivery_manager'))
    untouched(c, [s['id'], m['id']])
    cols = c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id', (o['id'],)).fetchall()
    if (not cols or sum(p['amount_halalas'] for p in cols) != o['total_halalas'] or
            any(p['shift_id'] != s['id'] or p['collector_id'] != s['user_id'] or p['method'] not in ('cash', 'card') for p in cols)):
        fail('قيود التحصيل الأصلية لا تطابق السائق والمبلغ بالكامل.')
    for p in cols:
        if p['method'] == 'cash' and not c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='collection' AND reference_id=%s AND amount_halalas=%s", (s['id'], p['id'], p['amount_halalas'])).fetchone():
            fail('قيد العهدة النقدية الأصلي غير مطابق.')
    return t, s, m, cols


def target(c, user, o, s, m, sid, lock=False):
    to = shift(c, user, sid, lock)
    if (to['id'] == s['id'] or to['user_id'] == s['user_id'] or to['kind'] != 'driver'
            or to['status'] != 'open' or to.get('submission') or to.get('supplement_of_shift_id')
            or to['manager_shift_id'] != m['id'] or to['brand_id'] != o['brand_id']
            or o['branch_id'] not in to['branch_ids'] or period(to) != period(s) or day(to) != day(s)):
        fail('اختر شفت سائق آخر مفتوحًا تحت المدير نفسه وللفرع والفترة ويوم التشغيل نفسيهما.')
    principal(c, to['user_id'], o['brand_id'], o['branch_id'], ('driver',))
    untouched(c, [to['id']])
    return to


@router.get('/{oid}/driver-attribution-correction')
def preview(oid: str, user=Depends(access)):
    with db.tx() as c:
        o = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        ops.order_access(c, user, o)
        result = {'order': o, 'source_task': None, 'source_shift': None, 'collections': [],
                  'target_shifts': [], 'existing_correction': c.execute('SELECT * FROM order_driver_attribution_corrections WHERE order_id=%s', (oid,)).fetchone(),
                  'blocked_reason': None}
        try:
            t, s, m, cols = source(c, user, o)
            result.update(source_task=t, source_shift=s, collections=cols)
            candidates = c.execute("SELECT id FROM shifts WHERE manager_shift_id=%s AND kind='driver' AND status='open' AND id<>%s ORDER BY opened_at,id", (m['id'], s['id'])).fetchall()
            for candidate in candidates:
                try:
                    result['target_shifts'].append(target(c, user, o, s, m, candidate['id']))
                except HTTPException as exc:
                    if exc.status_code not in (403, 404, 409): raise
            if not result['target_shifts']:
                result['blocked_reason'] = 'لا يوجد شفت سائق آخر مؤهل للتصحيح تحت المدير نفسه.'
        except HTTPException as exc:
            if exc.status_code != 409: raise
            result['blocked_reason'] = exc.detail.get('message', str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail)
        return ops.safe(result)


@router.post('/{oid}/driver-attribution-correction')
def correct(oid: str, body: Correction, request: Request, user=Depends(access)):
    if not body.confirmed:
        raise db.error(422, 'DRIVER_ATTRIBUTION_CONFIRM', 'أكد أن السائق المختار هو المنفذ والمحصّل الفعلي وأنها عملية تصحيح فقط.')
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        # Current scope is checked before replay; a saved result grants no access.
        o = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        ops.order_access(c, user, o)
        for sid in (body.source_shift_id, body.target_shift_id):
            shift(c, user, sid)
        def perform():
            o = c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (oid,)).fetchone()
            ops.order_access(c, user, o)
            if o['version'] != body.expected_version:
                fail('تغيرت بيانات الطلب؛ حدّث المعاينة قبل التصحيح.')
            t, s, m, cols = source(c, user, o, True)
            if t['id'] != body.source_task_id or s['id'] != body.source_shift_id:
                fail('تغيرت مهمة أو وردية المصدر؛ حدّث المعاينة.')
            to = target(c, user, o, s, m, body.target_shift_id, True)
            cash = sum(p['amount_halalas'] for p in cols if p['method'] == 'cash')
            card = sum(p['amount_halalas'] for p in cols if p['method'] == 'card')
            if ops.available_balance(c, s) < cash:
                fail('العهدة المتاحة لا تكفي لتصحيح كامل المبلغ النقدي.')
            ops.valid_cash_amount(ops.balance(c, to) + cash)
            cid = db.new_id('attribution_')
            details = {'source_task': ops.safe(t), 'collection_ids': [p['id'] for p in cols],
                       'original_collections': ops.safe(cols), 'actual_driver_attested_by_owner': True,
                       'physical_work_evidence_unchanged': True, 'invoice_unchanged': True,
                       'total_halalas': o['total_halalas'], 'resulting_version': o['version'] + 1}
            c.execute('INSERT INTO order_driver_attribution_corrections(id,order_id,brand_id,branch_id,source_task_id,source_shift_id,source_driver_id,target_shift_id,target_driver_id,manager_shift_id,expected_version,cash_halalas,card_halalas,actor_id,reason,details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (cid,oid,o['brand_id'],o['branch_id'],t['id'],s['id'],s['user_id'],to['id'],to['user_id'],m['id'],o['version'],cash,card,user['id'],body.reason,db.json(details)))
            if cash:
                ops.event(c, s['id'], -cash, 'adjustment', cid, user['id'], body.reason)
                ops.event(c, to['id'], cash, 'adjustment', cid, user['id'], body.reason)
            c.execute('UPDATE orders SET version=version+1 WHERE id=%s', (oid,))
            correction = c.execute('SELECT * FROM order_driver_attribution_corrections WHERE id=%s', (cid,)).fetchone()
            db.event(c, oid, user['id'], 'order.driver_attribution.corrected', ops.safe(correction))
            db.audit(c, user['id'], 'order.driver_attribution.corrected', 'order', oid, ops.safe(correction))
            return {'correction': correction, 'order_id': oid, 'version': o['version'] + 1}
        return ops.mutate(c, user, request, 'order.driver_attribution.correct:'+oid, body.model_dump(), perform)

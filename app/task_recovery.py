"""Scoped intervention desk; administrative review never impersonates a driver."""
from .complimentary import operational_view
from fastapi import APIRouter, Depends, Query, Request
from pydantic import ConfigDict, Field, StrictBool, StrictInt
from . import auth, db, operations, order_amendments, morning_manager_context

router = APIRouter(prefix='/api/task-recovery', tags=['operations'])


def authorize(user):
    operations.roles(user, 'owner', 'delivery_manager')
    if not all(auth.has_permission(user, p) for p in ('tasks_resolve_delivery', 'tasks_view', 'orders_view')):
        raise db.error(403, 'RECOVERY_PERMISSION', 'صلاحية معالجة الطلبات المتعثرة غير متاحة.')


def scoped(c, user, tid, *, lock=False):
    task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
    if not task:
        operations.fail(404, 'المهمة غير موجودة')
    order = c.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''), (task['order_id'],)).fetchone()
    operations.order_access(c, user, order)
    if lock:
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE', (tid,)).fetchone()
    shift = operations.lock_shift(c, task['shift_id']) if lock else c.execute('SELECT * FROM shifts WHERE id=%s', (task['shift_id'],)).fetchone()
    operations.shift_access(c, user, shift)
    return task, order, shift


@router.get('')
def listing(user=Depends(auth.current_user), search: str = Query('', max_length=100),
            offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    authorize(user)
    with db.tx() as c:
        clause, args = morning_manager_context.order_filter(user, 'o')
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        rows = c.execute("""SELECT t.id,t.order_id,t.status,t.created_at,t.driver_id,
            o.order_no,o.snapshot->>'order_reference' AS order_reference,o.branch_id,o.amendment_revision,u.display_name AS driver_name
            FROM driver_tasks t JOIN orders o ON o.id=t.order_id
            JOIN users u ON u.id=t.driver_id JOIN shifts s ON s.id=t.shift_id
            WHERE o.brand_id=%s AND (%s OR o.branch_id=ANY(%s)) AND o.test_void IS NULL
            AND o.service='delivery' AND s.kind='driver' AND s.brand_id=o.brand_id
            AND (%s OR s.branch_ids <@ %s::jsonb)
            AND t.status IN ('assigned','accepted','picked_up','problem')
            AND (%s OR EXISTS(SELECT 1 FROM shifts m WHERE m.id=s.manager_shift_id AND m.user_id=%s))
            AND (o.order_no::text ILIKE %s OR (o.snapshot->>'order_reference') ILIKE %s OR u.display_name ILIKE %s OR o.id=%s)
            AND """ + clause + " ORDER BY t.created_at,t.id LIMIT %s OFFSET %s",
            [user['brand_id'], user['role']=='owner', user.get('branch_ids', []), user['role']=='owner', db.json(user.get('branch_ids', [])), user['role']=='owner',
             user['id'], pattern, pattern, pattern, search, *args, limit+1, offset]).fetchall()
        return operations.safe({'tasks': rows[:limit], 'next_offset': offset+limit if len(rows)>limit else None})


@router.get('/{tid}')
def detail(tid: str, user=Depends(auth.current_user)):
    authorize(user)
    with db.tx() as c:
        task, order, shift = scoped(c, user, tid)
        revision = order.get('amendment_revision', 0)
        branch_received = not revision or bool(c.execute("SELECT 1 FROM order_revision_receipts WHERE order_id=%s AND revision=%s AND kind='branch'", (order['id'], revision)).fetchone())
        driver_received = not revision or order_amendments.driver_reviewed(c, order, task['driver_id'])
        pending = bool(order_amendments.active(c, order['id']))
        blockers = []
        if task['status'] in operations.TERMINAL or order.get('completed_at') or order['preparation_status'] in ('cancelled','rejected','completed'):
            blockers.append('closed')
        if shift['status'] != 'open': blockers.append('shift_closed')
        if pending: blockers.append('amendment_pending')
        if not branch_received: blockers.append('branch_revision')
        if not driver_received: blockers.append('driver_revision')
        if not task.get('picked_up_at'): blockers.append('pickup_missing')
        if order['payment_status'] != 'paid':
            from .complimentary import is_free
            from .order_financial_cases import documented_shortfall
            if not is_free(order) and not documented_shortfall(c,order): blockers.append('payment_missing')
        reviews = c.execute("""SELECT e.created_at,e.details,u.display_name AS actor_name
            FROM order_events e LEFT JOIN users u ON u.id=e.actor_id
            WHERE e.order_id=%s AND e.event_type='amendment.administrative_review'
            ORDER BY e.created_at DESC,e.id DESC LIMIT 20""", (order['id'],)).fetchall()
        return operations.safe({'task': task, 'order': {'id': order['id'], 'order_no': order['order_no'], 'order_reference': order['snapshot'].get('order_reference'),
            'version': order['version'], 'revision': revision, 'customer': order['customer'],
            'snapshot': operational_view(order['snapshot']), 'payment_status': order['payment_status'],
            'total_halalas': order['total_halalas'], 'preparation_status': order['preparation_status']},
            'blockers': blockers, 'can_review_revision': bool(revision and not driver_received and
                not any(x in blockers for x in ('closed','shift_closed','amendment_pending')) and auth.has_permission(user, 'orders_amend')),
            'can_resolve_delivery': not blockers and task['status'] in ('picked_up','problem'), 'reviews': reviews})


class RevisionReview(operations.OperationBody):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: StrictInt = Field(gt=0)
    version: StrictInt = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2000)
    evidence: str = Field(min_length=3, max_length=2000)
    confirmed: StrictBool


@router.post('/{tid}/review-revision')
def review_revision(tid: str, body: RevisionReview, request: Request, user=Depends(auth.current_user)):
    authorize(user)
    if not auth.has_permission(user, 'orders_amend'):
        raise db.error(403, 'AMENDMENT_PERMISSION', 'صلاحية مراجعة التعديل غير متاحة.')
    with db.tx() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('operations:'+user['brand_id'],))
        task, order, shift = scoped(c, user, tid, lock=True)
        def perform():
            if not body.confirmed:
                raise db.error(422, 'RECOVERY_CONFIRMATION', 'أكد مراجعة النسخة الحالية وإبلاغ السائق بمحتواها.')
            if body.revision != order['amendment_revision'] or body.version != order['version']:
                raise db.error(409, 'AMENDMENT_STALE_RECEIPT', 'تغير الطلب؛ راجع آخر نسخة قبل التدخل.')
            if (shift['status']!='open' or task['status'] in operations.TERMINAL or order.get('completed_at')
                    or order['preparation_status'] in ('completed','cancelled','rejected') or order.get('test_void')):
                raise db.error(409, 'RECOVERY_CLOSED', 'لا يمكن معالجة مهمة أو طلب أو شفت منتهٍ.')
            if order_amendments.active(c, order['id']):
                raise db.error(409, 'AMENDMENT_PENDING', 'احسم طلب التعديل المعلق أولًا.')
            if not order_amendments.driver_reviewed(c, order, task['driver_id']):
                details = {**body.model_dump(), 'task_id': tid, 'driver_id': task['driver_id'],
                           'administrative_review': True, 'actor_name': user.get('display_name', '')}
                db.event(c, order['id'], user['id'], 'amendment.administrative_review', details)
                db.audit(c, user['id'], 'amendment.administrative_review', 'order', order['id'], details)
            return {'reviewed': True, 'order_id': order['id'], 'revision': body.revision}
        return operations.mutate(c, user, request, 'task.recovery.review:'+tid, body.model_dump(), perform)

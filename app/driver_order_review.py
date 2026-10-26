"""Read-only driver orders, bound to real shifts and fresh canonical grants."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from . import auth, db, multibrand, operations as ops

router = APIRouter(prefix='/api/driver-order-review', tags=['driver-order-review'])


def principals(c, user):
    from .central_operations import enabled
    if enabled() and not user.get('_morning_manager_context'):
        return [p for b in multibrand.available(c, user)
                if (p := multibrand.project(c, user, b['id']))
                and all(auth.has_permission(p, key) for key in ('orders_view', 'shifts_view'))]
    return [user]


def shift_legs(c, user, shift, scopes):
    """Never treat a shared identity as permission to read another ledger."""
    from .central_workdays import association, authorized
    if not shift or shift['kind'] != 'driver':
        raise db.error(404, 'DRIVER_REVIEW_UNAVAILABLE', 'شفت السائق غير متاح.')
    day = association(c, shift['id'])
    if day:
        if user.get('_morning_manager_context'):
            raise db.error(404, 'DRIVER_REVIEW_UNAVAILABLE', 'شفت السائق غير متاح.')
        _, legs = authorized(c, user, shift['id'], capability='shifts_view', lock=False)
        if any(p['brand_id'] not in scopes for p, _ in legs):
            raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية عرض الطلبات غير متاحة.')
        return legs, day['anchor_shift_id']
    principal = scopes.get(shift['brand_id'])
    if not principal:
        raise db.error(404, 'DRIVER_REVIEW_UNAVAILABLE', 'شفت السائق غير متاح.')
    ops.shift_access(c, principal, shift, capability='shifts_view')
    return [(principal, shift)], shift['id']


def history_bounds(start, end):
    if start is None or end is None or end < start or (end-start).days > 30:
        raise db.error(422, 'DRIVER_REVIEW_PERIOD', 'اختر فترة صحيحة لا تتجاوز 31 يومًا.')
    zone = ZoneInfo('Asia/Riyadh')
    return (datetime.combine(start, time.min, zone),
            datetime.combine(end+timedelta(days=1), time.min, zone))


@router.get('')
def listing(driver_id: str = Query(default='', max_length=200),
            shift_id: str = Query(default='', max_length=200),
            view: Literal['current', 'history'] = 'current',
            start: date | None = None, end: date | None = None,
            brand_id: Literal['kps', 'enbishan'] | None = None,
            limit: int = Query(default=50, ge=1, le=100),
            offset: int = Query(default=0, ge=0, le=100000),
            user=Depends(auth.current_user)):
    ops.roles(user, 'owner', 'delivery_manager')
    if not all(auth.has_permission(user, key) for key in ('orders_view', 'shifts_view')):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية عرض الطلبات غير متاحة.')
    if not driver_id and not shift_id:
        raise db.error(422, 'DRIVER_REVIEW_REQUIRED', 'اختر السائق أو التقفيلة.')
    if shift_id and (view != 'current' or start is not None or end is not None):
        raise db.error(422, 'DRIVER_REVIEW_EXACT_SHIFT', 'طلبات التقفيلة تخص الشفت المحدد فقط.')
    bounds = history_bounds(start, end) if view == 'history' else None
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        scopes = {p['brand_id']: p for p in principals(c, user)}
        selected = c.execute('SELECT * FROM shifts WHERE id=%s', (shift_id,)).fetchone() if shift_id else None
        if shift_id:
            legs, anchor = shift_legs(c, user, selected, scopes)
            if driver_id and selected['user_id'] != driver_id:
                raise db.error(404, 'DRIVER_REVIEW_UNAVAILABLE', 'شفت السائق غير متاح.')
            driver_id = selected['user_id']
        target = c.execute("SELECT * FROM users WHERE id=%s AND role='driver'", (driver_id,)).fetchone()
        visible = False
        for brand, principal in scopes.items():
            subject = multibrand.project(c, target, brand) if target and multibrand.enabled() else target
            if (subject and subject['brand_id'] == brand
                    and (ops.unrestricted_owner(principal)
                         or set(subject.get('branch_ids') or []) & set(principal.get('branch_ids') or []))):
                visible = True
        if not visible:
            raise db.error(404, 'DRIVER_REVIEW_UNAVAILABLE', 'السائق غير متاح ضمن صلاحياتك.')
        selected_legs = legs if shift_id else []
        selected_shift = selected if shift_id else None
        if not shift_id:
            physical = ' AND staffing_ended_at IS NULL' if multibrand.enabled() and view == 'current' else ''
            current = " AND status<>'closed'" if view == 'current' else ''
            candidates = c.execute("SELECT * FROM shifts WHERE user_id=%s AND kind='driver' "
                'AND brand_id=ANY(%s)'+current+physical+' ORDER BY opened_at DESC,id DESC',
                (driver_id, list(scopes))).fetchall()
            seen = set()
            for candidate in candidates:
                if candidate['id'] in seen:
                    continue
                try:
                    legs, anchor = shift_legs(c, user, candidate, scopes)
                except HTTPException as exc:
                    if exc.status_code in (403, 404):
                        continue
                    raise
                selected_legs.extend(legs)
                seen.update(s['id'] for _, s in legs)
                if view == 'current':
                    selected_shift = next(s for _, s in legs if s['id'] == anchor)
                    break
        # The morning read follows the existing historical provenance checks;
        # it does not grant access to another morning duty or brand.
        scoped = {p['brand_id']: dict(p, _morning_history_read=True)
                  if p.get('_morning_manager_context') else p for p, _ in selected_legs}
        ids = list(dict.fromkeys(s['id'] for _, s in selected_legs))
        rows = []
        if ids:
            conditions = ["t.shift_id=ANY(%s)", "t.status NOT IN ('transferred','reassigned')"]
            params = [ids]
            if bounds:
                conditions.append('o.created_at>=%s AND o.created_at<%s')
                params.extend(bounds)
            candidates = c.execute('SELECT DISTINCT ON (o.id) o.*,t.status AS task_status '
                'FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id WHERE '
                +' AND '.join(conditions)+' ORDER BY o.id,t.created_at DESC,t.id DESC', tuple(params)).fetchall()
            rows = [r for r in candidates if r['brand_id'] in scoped
                    and auth.can_access_order(scoped[r['brand_id']], r, c)]
        rows.sort(key=lambda r: (r['created_at'], r['id']), reverse=True)
        summaries = []
        for brand in ('kps', 'enbishan'):
            if brand not in {s['brand_id'] for _, s in selected_legs}:
                continue
            entries = [r for r in rows if r['brand_id'] == brand]
            summaries.append({'id': brand, 'name': 'كبز' if brand == 'kps' else 'عنبيشن',
                              'count': len(entries), 'total_halalas': sum(r['total_halalas'] for r in entries)})
        filtered = [r for r in rows if brand_id is None or r['brand_id'] == brand_id]
        fields = ('id', 'order_no', 'brand_id', 'total_halalas', 'payment_method', 'payment_status',
                  'preparation_status', 'delivery_status', 'task_status', 'created_at')
        orders = [dict({k: r.get(k) for k in fields},
                       snapshot={'order_reference': (r.get('snapshot') or {}).get('order_reference')})
                  for r in filtered[offset:offset+limit]]
        return ops.safe({'orders': orders, 'brands': summaries, 'total_count': len(filtered),
                         'next_offset': offset+limit if offset+limit < len(filtered) else None,
                         'driver': {'id': target['id'], 'display_name': target['display_name']},
                         'shift': {k: selected_shift.get(k) for k in ('id', 'opened_at', 'status')}
                                  if selected_shift else None,
                         'view': 'closure' if shift_id else view})

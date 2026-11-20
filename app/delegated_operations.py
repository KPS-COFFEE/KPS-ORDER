"""Explicit, read-only operational delegation; never expands order ownership.

This projection deliberately omits customer contacts, money and arbitrary event
payloads. The grant and current branch scope are read again on every request.
"""
from fastapi import APIRouter, Depends, Query
from . import auth, db

router = APIRouter(prefix='/api/staff/scoped/orders', tags=['scoped operations'])


def authorized(user=Depends(auth.current_user)):
    if not auth.has_permission(user, 'orders_operational_view'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'هذه الصلاحية لم يمنحها المالك لحسابك.')
    return user


def scope(user, branch_id=None):
    clauses = ['o.brand_id=%s']
    params = [user['brand_id']]
    if user['role'] != 'owner':
        clauses.append('o.branch_id=ANY(%s)')
        params.append(user.get('branch_ids') or [])
    if branch_id:
        clauses.append('o.branch_id=%s')
        params.append(branch_id)
    return ' AND '.join(clauses), params


_FIELDS = 'o.id,o.branch_id,o.service,o.preparation_status,o.delivery_status,o.created_at,o.completed_at'


@router.get('')
def listing(branch_id: str | None = None, limit: int = Query(50, ge=1, le=100),
            offset: int = Query(0, ge=0, le=10000), user=Depends(authorized)):
    where, params = scope(user, branch_id)
    with db.tx() as conn:
        if branch_id and not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s', (branch_id,user['brand_id'])).fetchone():
            raise db.error(404,'BRANCH_NOT_FOUND','الفرع غير متاح.')
        if branch_id and user['role'] != 'owner' and branch_id not in (user.get('branch_ids') or []):
            raise db.error(404,'BRANCH_NOT_FOUND','الفرع غير متاح.')
        rows=conn.execute('SELECT '+_FIELDS+' FROM orders o WHERE '+where+' ORDER BY o.created_at DESC,o.id LIMIT %s OFFSET %s',(*params,limit+1,offset)).fetchall()
        return {'orders':rows[:limit], 'next_offset':offset+limit if len(rows)>limit else None}


@router.get('/{order_id}/history')
def history(order_id: str, user=Depends(authorized)):
    where, params=scope(user)
    with db.tx() as conn:
        order=conn.execute('SELECT '+_FIELDS+' FROM orders o WHERE o.id=%s AND '+where,(order_id,*params)).fetchone()
        if not order:
            raise db.error(404,'ORDER_NOT_FOUND','الطلب غير متاح.')
        # Only documented operational transition fields are exposed. Never return
        # raw event details that could contain money, addresses or credentials.
        rows=conn.execute('SELECT e.id,e.event_type,e.actor_id,u.display_name actor_name,e.created_at,e.details FROM order_events e LEFT JOIN users u ON u.id=e.actor_id AND u.brand_id=%s WHERE e.order_id=%s ORDER BY e.created_at,e.id',(user['brand_id'],order_id)).fetchall()
        events=[]
        for row in rows:
            details=row.pop('details') or {}
            row['transition']={key:details[key] for key in ('from','to','previous','previous_status','status','branch_id','reason') if key in details}
            events.append(row)
        return {'order':order,'events':events}

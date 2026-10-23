"""Central work screens; canonical per-record authorization remains mandatory.

The rollout is deliberately independent of the existing multibrand release.
Never enable it until the shared shift/closure journeys are certified too.
"""
import os
from typing import Literal

from fastapi import APIRouter, Depends, Query

from . import auth, db, multibrand

router = APIRouter(prefix='/api/central-operations', tags=['central-operations'])


def enabled():
    return os.getenv('KPS_CENTRAL_OPERATIONS_ENABLED') == 'true' and multibrand.enabled()


def context(conn, user):
    """Publish current branch grants, never a synthetic widened principal."""
    if not enabled() or user['role'] == 'customer':
        return None
    scopes = []
    for brand in multibrand.available(conn, user):
        principal = multibrand.project(conn, user, brand['id'])
        if not principal:
            continue
        branches = conn.execute(
            'SELECT id,brand_id,branch_no,name,city FROM branches WHERE brand_id=%s '
            'AND (%s OR id=ANY(%s)) ORDER BY branch_no,id',
            (brand['id'], principal['role'] == 'owner', principal.get('branch_ids', [])),
        ).fetchall()
        scopes.append(dict(brand, branch_ids=[b['id'] for b in branches], branches=branches))
    return {'brands': scopes}


@router.get('/resource-context')
def resource_context(kind: Literal['order', 'task', 'shift', 'conversation', 'branch'],
                     resource_id: str = Query(min_length=1, max_length=200),
                     user=Depends(auth.current_user)):
    """Resolve a clicked record without asking staff to choose its brand.

    A browser cache is only a routing convenience. Each canonical destination
    still reloads its own permissions, branch grant, task ownership and CSRF.
    """
    if not enabled() or user['role'] == 'customer':
        raise db.error(404, 'CENTRAL_RECORD_UNAVAILABLE', 'السجل غير متاح.')
    with db.tx() as conn:
        # Table identifiers are fixed here, never interpolated from a request.
        statements = {
            'order': 'SELECT * FROM orders WHERE id=%s',
            'task': 'SELECT o.*,t.driver_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.id=%s',
            'shift': 'SELECT * FROM shifts WHERE id=%s',
            'conversation': 'SELECT * FROM conversations WHERE id=%s',
            'branch': 'SELECT * FROM branches WHERE id=%s',
        }
        row = conn.execute(statements[kind], (resource_id,)).fetchone()
        principal = multibrand.project(conn, user, row['brand_id']) if row else None
        allowed = False
        if principal:
            if kind in {'order', 'task'}:
                allowed = auth.has_permission(principal, 'tasks_view' if principal['role'] == 'driver' else 'orders_view') and auth.can_access_order(principal, row, conn)
                if kind == 'task' and principal['role'] == 'driver':
                    allowed = allowed and row['driver_id'] == principal['id']
            elif kind == 'conversation':
                from .chat import allowed as chat_allowed
                allowed = auth.has_permission(principal, 'chat_view') and chat_allowed(principal, row, conn)
            elif kind == 'shift':
                from .operations import shift_access
                from fastapi import HTTPException
                try:
                    shift_access(conn, principal, row)
                    allowed = auth.has_permission(principal, 'shifts_view')
                except HTTPException:
                    allowed = False
            elif kind == 'branch':
                allowed = principal['role'] == 'owner' or row['id'] in principal.get('branch_ids', [])
        if not allowed:
            raise db.error(404, 'CENTRAL_RECORD_UNAVAILABLE', 'السجل غير متاح.')
        return {'brand_id': row['brand_id']}

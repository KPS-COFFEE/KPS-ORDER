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


# Imported after context/enabled are defined to avoid initialization cycles.
from .central_workdays import router as workdays_router
router.include_router(workdays_router)

from .central_staff import router as staff_router
router.include_router(staff_router)


def project_read(conn, request, user):
    """Route protected links/images to their owning brand, without an authority grant.

    Downloads cannot attach an operations header. Destination routes still apply
    their normal role, record, branch and task checks; explicit headers win.
    Restricted morning requests keep their existing bounded principal.
    """
    if (not enabled() or user['role']=='customer' or request.method not in {'GET','HEAD'}
            or request.headers.getlist('x-operations-brand') or request.headers.get('x-delivery-period')):
        return user
    query_brand=request.query_params.get('operations_brand')
    if query_brand and request.url.path.startswith(('/api/accounting/','/api/admin/')):
        target=multibrand.project(conn,user,query_brand)
        if not target:raise db.error(403,'OPERATIONS_BRAND_FORBIDDEN','البراند غير مخول لحسابك.')
        return target
    endpoint=request.scope.get('endpoint')
    if endpoint is None:return user
    module=endpoint.__module__.rsplit('.',1)[-1]
    params=request.path_params
    row=None
    if params.get('oid') and module in {'orders','operations','order_support','order_amendments','fiscal','fiscal_issuance','invoice_profiles','branch_printing','branch_refunds','driver_evidence'}:
        row=conn.execute('SELECT brand_id FROM orders WHERE id=%s',(params['oid'],)).fetchone()
    elif module=='chat' and params.get('aid'):
        row=conn.execute('SELECT c.brand_id FROM chat_attachments a JOIN conversations c ON c.id=a.conversation_id WHERE a.id=%s',(params['aid'],)).fetchone()
    elif module=='chat' and params.get('cid'):
        row=conn.execute('SELECT brand_id FROM conversations WHERE id=%s',(params['cid'],)).fetchone()
    elif module=='chat' and params.get('tid'):
        row=conn.execute('SELECT o.brand_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.id=%s',(params['tid'],)).fetchone()
    elif module=='reporting' and params.get('rid'):
        row=conn.execute('SELECT brand_id FROM closure_reports WHERE id=%s',(params['rid'],)).fetchone()
    elif module=='report_notifications' and params.get('identifier'):
        row=conn.execute('SELECT brand_id FROM report_notifications WHERE id=%s',(params['identifier'],)).fetchone()
    elif module=='branch_printing' and params.get('bid'):
        row=conn.execute('SELECT brand_id FROM branches WHERE id=%s',(params['bid'],)).fetchone()
    if row:
        return multibrand.project(conn,user,row['brand_id']) or user
    return user

"""Central work screens; canonical per-record authorization remains mandatory.

The rollout is deliberately independent of the existing multibrand release.
Never enable it until the shared shift/closure journeys are certified too.
"""
import os
from typing import Literal

from fastapi import APIRouter, Depends, Query

from . import auth, db, multibrand, catalog

router = APIRouter(prefix='/api/central-operations', tags=['central-operations'])


def enabled():
    return os.getenv('KPS_CENTRAL_OPERATIONS_ENABLED') == 'true' and multibrand.enabled()


def context(conn, user, *, available_brands=None):
    """Publish current branch grants, never a synthetic widened principal."""
    if not enabled() or user['role'] == 'customer':
        return None
    scopes = []
    for brand in (multibrand.available(conn, user) if available_brands is None else available_brands):
        principal = multibrand.project(conn, user, brand['id'])
        if not principal:
            continue
        branches = conn.execute(
            'SELECT id,brand_id,branch_no,name,city,settings FROM branches WHERE brand_id=%s '
            'AND (%s OR id=ANY(%s)) ORDER BY branch_no,id',
            (brand['id'], principal['role'] == 'owner' and not principal.get('home_brand_id'), principal.get('branch_ids', [])),
        ).fetchall()
        # Central UI consumes this projection instead of /api/branches. Preserve
        # verified map pins while never publishing the full operational settings.
        visible=[]
        for row in branches:
            branch={key:row[key] for key in ('id','brand_id','branch_no','name','city')}
            verified=catalog.location_verified(row)
            location=row['settings'].get('location') if verified else None
            branch.update(location={'lat':location['lat'],'lng':location['lng']} if location else None,
                          location_verified=bool(verified))
            visible.append(branch)
        scopes.append(dict(brand, branch_ids=[b['id'] for b in visible], branches=visible))
    return {'brands': scopes}


@router.get('/resource-context')
def resource_context(kind: Literal['order', 'order_intervention', 'task', 'shift', 'conversation', 'branch', 'printer', 'report', 'staff', 'custody', 'fuel', 'break', 'offer', 'maintenance', 'notification', 'print_job', 'ticket', 'trip', 'vehicle'],
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
            'order_intervention': 'SELECT * FROM orders WHERE id=%s',
            'task': 'SELECT o.*,t.driver_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.id=%s',
            'shift': 'SELECT * FROM shifts WHERE id=%s',
            'conversation': 'SELECT * FROM conversations WHERE id=%s',
            'branch': 'SELECT * FROM branches WHERE id=%s',
            'vehicle': 'SELECT * FROM fleet_vehicles WHERE id=%s',
            'fuel': 'SELECT * FROM fuel_exceptions WHERE id=%s',
            'break': 'SELECT b.*,s.branch_ids FROM driver_breaks b JOIN shifts s ON s.id=b.shift_id WHERE b.id=%s',
            'offer': 'SELECT f.*,o.branch_id FROM dispatch_offers f JOIN orders o ON o.id=f.order_id WHERE f.id=%s',
            'maintenance': 'SELECT * FROM vehicle_reports WHERE id=%s',
            'notification': 'SELECT * FROM report_notifications WHERE id=%s',
            'print_job': 'SELECT * FROM branch_print_jobs WHERE id=%s',
            'ticket': 'SELECT t.*,o.brand_id,o.branch_id FROM support_tickets t JOIN orders o ON o.id=t.order_id WHERE t.id=%s',
            'trip': 'SELECT t.*,s.branch_ids FROM driver_work_trips t JOIN shifts s ON s.id=t.shift_id WHERE t.id=%s',
            'printer': 'SELECT * FROM branch_print_devices WHERE id=%s',
            'report': 'SELECT * FROM closure_reports WHERE id=%s',
            'staff': "SELECT * FROM users WHERE id=%s AND role<>'customer'",
            'custody': 'SELECT t.*,s.brand_id,s.branch_ids,s.user_id FROM custody_transfers t JOIN shifts s ON s.id=t.from_shift_id WHERE t.id=%s',
        }
        row = conn.execute(statements[kind], (resource_id,)).fetchone()
        principal = multibrand.project(conn, user, row['brand_id']) if row else None
        allowed = False
        if principal:
            if kind in {'order', 'task'}:
                allowed = auth.has_permission(principal, 'tasks_view' if principal['role'] == 'driver' else 'orders_view') and auth.can_access_order(principal, row, conn)
                if kind == 'task' and principal['role'] == 'driver':
                    allowed = allowed and row['driver_id'] == principal['id']
            elif kind == 'order_intervention':
                # Cancellation delegates do not acquire general order access.
                # Resolve this workflow using its canonical read authorization.
                from fastapi import HTTPException
                from .order_interventions import require_access, scoped
                if principal['role'] in {'owner', 'delivery_manager', 'admin', 'driver'}:
                    try:
                        require_access(principal)
                        scoped(conn, resource_id, principal)
                        allowed = True
                    except HTTPException:
                        allowed = False
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
            elif kind in {'fuel','break','trip'}:
                from fastapi import HTTPException
                from .operations import shift_access
                shift=conn.execute('SELECT * FROM shifts WHERE id=%s',(row.get('shift_id'),)).fetchone()
                try:
                    if kind=='fuel':
                        from .fuel_exceptions import scope as fuel_scope
                        fuel_scope(conn,principal,row['driver_id'],row['manager_shift_id'],row.get('shift_id'))
                    else:
                        shift_access(conn,principal,shift)
                        if principal['role']=='driver' and row['driver_id']!=principal['id']:
                            raise db.error(404,'CENTRAL_RECORD_UNAVAILABLE','السجل غير متاح.')
                    allowed=True
                except HTTPException:
                    allowed=False
            elif kind == 'offer':
                allowed=principal['role']=='driver' and row['driver_id']==principal['id'] and row['branch_id'] in principal.get('branch_ids',[]) and auth.has_permission(principal,'tasks_view')
            elif kind == 'maintenance':
                from .maintenance import report as maintenance_report
                from fastapi import HTTPException
                try:
                    maintenance_report(conn,resource_id,principal)
                    allowed=any(auth.has_permission(principal,p) for p in ('vehicle_maintenance_manage','support_manage','vehicle_faults_report'))
                except HTTPException:allowed=False
            elif kind == 'notification':
                allowed=principal['role']=='owner' and row['recipient_user_id']==principal['id']
            elif kind == 'print_job':
                from .branch_printing import can_access_branch
                allowed=(auth.has_permission(principal,'branch_printers_manage') or auth.has_permission(principal,'orders_print')) and can_access_branch(principal,row['branch_id'])
            elif kind == 'ticket':
                order=conn.execute('SELECT * FROM orders WHERE id=%s',(row['order_id'],)).fetchone()
                allowed=auth.has_permission(principal,'support_manage') and auth.can_access_order(principal,order,conn)
            elif kind == 'vehicle':
                from .fleet import vehicles_in_tx,PERMISSION
                allowed=auth.has_permission(principal,PERMISSION) and any(v['id']==resource_id for v in vehicles_in_tx(conn,principal)['vehicles'])
            elif kind == 'printer':
                from .branch_printing import can_access_branch
                allowed = auth.has_permission(principal,'branch_printers_manage') and can_access_branch(principal,row['branch_id'])
            elif kind == 'report':
                from .reporting import can_read
                allowed = auth.has_permission(principal,'reports_view') and can_read(principal,row)
            elif kind == 'staff':
                allowed = principal['role']=='owner'
            elif kind == 'custody':
                allowed = (row['recipient_user_id']==principal['id'] or row['user_id']==principal['id']) and (principal['role']=='owner' or set(row['branch_ids'])<=set(principal.get('branch_ids',[])))
            elif kind == 'branch':
                allowed = (principal['role'] == 'owner' and not principal.get('home_brand_id')) or row['id'] in principal.get('branch_ids', [])
        if principal and principal['role']=='owner' and principal.get('home_brand_id'):
            if row.get('branch_id') is not None:
                allowed=allowed and row['branch_id'] in principal.get('branch_ids',[])
            if row.get('branch_ids') is not None:
                allowed=allowed and set(row['branch_ids'])<=set(principal.get('branch_ids',[]))
        if not allowed:
            raise db.error(404, 'CENTRAL_RECORD_UNAVAILABLE', 'السجل غير متاح.')
        result={'brand_id': row['brand_id']}
        if kind=='shift':
            from .central_workdays import annotate
            annotate(conn,[row])
            result.update({key:row[key] for key in ('central_workday_id','central_anchor_id') if key in row})
        return result


# Imported after context/enabled are defined to avoid initialization cycles.
from .central_workdays import router as workdays_router
router.include_router(workdays_router)

from .central_handovers import router as handovers_router
router.include_router(handovers_router)

from .central_dashboard import router as dashboard_router
router.include_router(dashboard_router)

from .central_driver_overview import router as driver_overview_router
router.include_router(driver_overview_router)

from .central_fleet import router as fleet_router
router.include_router(fleet_router)

from .central_shift_history import router as history_router
router.include_router(history_router)

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
    if params.get('oid') and module in {'orders','operations','order_support','order_amendments','fiscal','fiscal_issuance','invoice_profiles','branch_printing','branch_refunds','driver_evidence','driver_attribution'}:
        row=conn.execute('SELECT brand_id FROM orders WHERE id=%s',(params['oid'],)).fetchone()
    elif module=='chat' and params.get('aid'):
        row=conn.execute('SELECT c.brand_id FROM chat_attachments a JOIN conversations c ON c.id=a.conversation_id WHERE a.id=%s',(params['aid'],)).fetchone()
    elif module=='chat' and params.get('cid'):
        row=conn.execute('SELECT brand_id FROM conversations WHERE id=%s',(params['cid'],)).fetchone()
    elif module=='chat' and params.get('tid'):
        row=conn.execute('SELECT o.brand_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.id=%s',(params['tid'],)).fetchone()
    elif module=='driver_evidence' and params.get('eid'):
        row=conn.execute('SELECT brand_id FROM driver_evidence WHERE id=%s',(params['eid'],)).fetchone()
    elif module=='order_support' and params.get('aid'):
        row=conn.execute('SELECT o.brand_id FROM delivery_photos p JOIN orders o ON o.id=p.order_id WHERE p.id=%s',(params['aid'],)).fetchone()
    elif module=='admin' and params.get('record_id') and request.url.path.startswith('/api/admin/assets/'):
        row=conn.execute('SELECT brand_id FROM assets WHERE id=%s',(params['record_id'],)).fetchone()
    elif module=='reporting' and params.get('rid'):
        row=conn.execute('SELECT brand_id FROM closure_reports WHERE id=%s',(params['rid'],)).fetchone()
    elif module=='report_notifications' and params.get('nid'):
        row=conn.execute('SELECT brand_id FROM report_notifications WHERE id=%s',(params['nid'],)).fetchone()
    elif module=='branch_printing' and params.get('order_id'):
        row=conn.execute('SELECT brand_id FROM orders WHERE id=%s',(params['order_id'],)).fetchone()
    elif module=='branch_printing' and params.get('bid'):
        row=conn.execute('SELECT brand_id FROM branches WHERE id=%s',(params['bid'],)).fetchone()
    if row:
        return multibrand.project(conn,user,row['brand_id']) or user
    return user


@router.get('/driver-locations')
def driver_locations(user=Depends(auth.current_user)):
    from .central_workdays import require_enabled, association
    from . import driver_destination
    from .operations import safe
    require_enabled(user,'drivers_view')
    if user['role'] not in {'owner','delivery_manager','delivery_staff'}:
        raise db.error(403,'ROLE_FORBIDDEN','عرض مواقع السائقين غير متاح لحسابك.')
    with db.tx() as c:
        scopes={b['id']:b for b in context(c,user)['brands']}
        principals={brand:multibrand.project(c,user,brand) for brand in scopes}
        points=c.execute("SELECT g.*,u.display_name FROM driver_latest_locations g JOIN users u ON u.id=g.driver_id JOIN shifts s ON s.id=g.shift_id AND s.user_id=u.id AND s.brand_id=g.brand_id WHERE u.role='driver' AND u.active AND s.status='open' AND g.updated_at>now()-interval '24 hours'").fetchall()
        output=[]
        for point in points:
            target=c.execute('SELECT id,role,active,brand_id,branch_ids FROM users WHERE id=%s',(point['driver_id'],)).fetchone()
            group=association(c,point['shift_id'])
            legs=c.execute("SELECT s.* FROM shifts s WHERE s.id IN (SELECT shift_id FROM central_workday_legs WHERE workday_id=%s) AND s.status='open'",(group['id'],)).fetchall() if group else c.execute("SELECT * FROM shifts WHERE id=%s AND status='open'",(point['shift_id'],)).fetchall()
            branches=[];states=[]
            for leg in legs:
                brand=leg['brand_id'];scope=scopes.get(brand);actor=principals.get(brand)
                driver=multibrand.project(c,target,brand) if scope and actor else None
                permitted=[b for b in leg['branch_ids'] if driver and b in driver['branch_ids'] and b in scope['branch_ids']]
                if not permitted:continue
                row={'id':target['id'],'shift_id':leg['id'],'branch_ids':permitted,'shift_branches':leg['branch_ids'],'next_destination':leg.get('next_destination')}
                states.append(driver_destination.metadata(c,actor,[row])[target['id']]);branches.extend(permitted)
            if not branches:continue
            active=[r for r in states if r['visible_active_task_count']]
            brands={r['active_order_brand']['id'] for r in active if r.get('active_order_brand')}
            name=next(iter(brands)) if len(brands)==1 else 'mixed' if brands else None
            destination=next((r.get('next_destination') for r in states if r.get('next_destination')),None) if not active else None
            movement=next((kind for kind in ('delivering','awaiting_pickup','returning') if any(r['movement_state']==kind for r in states)),'at_branch')
            output.append({'id':target['id'],'display_name':point['display_name'],'branch_ids':sorted(set(branches)),
                'shift_id':group['anchor_shift_id'] if group else point['shift_id'],
                'location':{'lat':point['lat'],'lng':point['lng']},'accuracy_m':point['accuracy_m'],'updated_at':point['updated_at'],
                'stale':(c.execute('SELECT now() n').fetchone()['n']-point['updated_at']).total_seconds()>120,
                'active_order_brand':{'id':name,'name':{'kps':'KPS','enbishan':'Enbishan','mixed':'Mixed'}[name],'source':'active_task'} if name else None,
                'visible_active_task_count':sum(r['visible_active_task_count'] for r in states),
                'next_destination':destination,'movement_state':movement,
                'destination_choice_required':not active and any(r['destination_choice_required'] for r in states)})
        return safe({'drivers':output,'stale_after_seconds':120})


@router.get('/driver-route-context')
def driver_route_context(driver_id:str=Query(min_length=1,max_length=100),user=Depends(auth.current_user)):
    from .dispatch_map import central_route_context
    from .operations import safe
    with db.tx() as c:
        return safe(central_route_context(c, user, driver_id))


from .dispatch_map import DriverRouteRequest


@router.post('/driver-route-context')
def permit_driver_route(body: DriverRouteRequest, user=Depends(auth.current_user)):
    from .dispatch_map import central_route_context, consume_route_budget
    from .operations import safe
    with db.tx() as c:
        value = central_route_context(c, user, body.driver_id)
        choices = value['tasks'] if body.task_id else value['return_branches']
        selected = body.task_id or body.return_branch_id
        destination = next((row for row in choices if row['id'] == selected), None)
        if destination is None:
            raise db.error(409, 'CENTRAL_ROUTE_DESTINATION_UNAVAILABLE', 'الوجهة غير متاحة ضمن صلاحياتك.')
        issued = consume_route_budget(c, body.driver_id, user['id'])
        return safe({'driver_id': body.driver_id, 'origin': value['origin'], 'destination': destination['location'],
                     'brand_id': destination['brand_id'], 'kind': 'customer' if body.task_id else 'return_branch',
                     'target_id': selected, 'issued_at': issued})


@router.get('/fleet/vehicles')
def fleet_vehicles(user=Depends(auth.current_user)):
    from .central_workdays import require_enabled
    from .fleet import vehicles_in_tx,PERMISSION
    require_enabled(user,PERMISSION)
    with db.tx() as c:
        output=[]
        for scope in context(c,user)['brands']:
            principal=multibrand.project(c,user,scope['id'])
            if not principal or not scope['branch_ids']:continue
            for vehicle in vehicles_in_tx(c,principal)['vehicles']:
                item=dict(vehicle,brand_id=scope['id'])
                physical=c.execute("SELECT s.id,s.branch_ids,w.id central_workday_id,w.anchor_shift_id central_anchor_id FROM fleet_operating_segments l JOIN shifts s ON s.id=l.shift_id LEFT JOIN central_workdays w ON w.anchor_shift_id=s.id WHERE l.vehicle_id=%s AND s.brand_id=%s AND s.status='open' AND l.returned_at IS NULL AND NOT (s.vehicle_log ? 'central_vehicle_shift_id')",(vehicle['id'],scope['id'])).fetchone()
                if physical and set(physical['branch_ids'])<=set(scope['branch_ids']):
                    item['active_shift_id']=physical['id']
                    item.update({key:physical[key] for key in ('central_workday_id','central_anchor_id') if physical[key]})
                output.append(item)
        # A card belongs to its canonical brand ledger; only anchor shifts create
        # physical operating segments, so shared workday legs add no duplicate.
        return {'vehicles':sorted(output,key=lambda v:(v['plate'],v['brand_id'],v['id']))}


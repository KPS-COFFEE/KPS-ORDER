"""Revocable per-order tracking capabilities. Tokens travel in a fragment/body, never a URL query."""
import hashlib
import secrets
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, dispatch_map
from .operations import order_access, safe
from .map_services import quality, require, STAFF

router=APIRouter(tags=['delivery-tracking'])


@router.post('/api/operations/orders/{order_id}/tracking-link')
def create_link(order_id:str,user=Depends(auth.require_roles(*STAFF))):
    require(user,'orders_view')
    require(user,'drivers_view')
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(order_id,)).fetchone()
        order_access(c,user,order)
        if order['service']!='delivery' or order.get('completed_at') or order['delivery_status'] in ('delivered','cancelled') or order['preparation_status'] in ('cancelled','rejected','completed'):
            raise HTTPException(409,'التتبع متاح للطلبات الجارية فقط.')
        if not db.rate_limit(c,'tracking-create:'+user['id'],30,600):
            raise HTTPException(429,'انتظر قليلًا قبل إنشاء روابط إضافية.')
        token=secrets.token_urlsafe(32)
        digest=hashlib.sha256(token.encode()).hexdigest()
        c.execute('''INSERT INTO delivery_tracking_links(order_id,brand_id,token_hash,expires_at)
          VALUES(%s,%s,%s,now()+interval '4 hours') ON CONFLICT(order_id) DO UPDATE SET
          token_hash=EXCLUDED.token_hash,expires_at=EXCLUDED.expires_at''',(order_id,user['brand_id'],digest))
        db.audit(c,user['id'],'tracking.link_created','order',order_id,{'expires_hours':4})
        return {'path':'/delivery-track#'+token,'expires_hours':4,'replaces_previous_link':True}


@router.delete('/api/operations/orders/{order_id}/tracking-link')
def revoke_link(order_id:str,user=Depends(auth.require_roles(*STAFF))):
    require(user,'orders_view')
    require(user,'drivers_view')
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(order_id,)).fetchone()
        order_access(c,user,order)
        c.execute('DELETE FROM delivery_tracking_links WHERE order_id=%s AND brand_id=%s',(order_id,user['brand_id']))
        db.audit(c,user['id'],'tracking.link_revoked','order',order_id,{})
    return {'revoked':True}


class TrackingToken(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    token:str=Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    route:bool=False


@router.post('/api/delivery-tracking/view')
def view(body:TrackingToken,request:Request):
    digest=hashlib.sha256(body.token.encode()).hexdigest()
    ip=request.client.host if request.client else 'unknown'
    with db.tx() as c:
        allowed=db.rate_limit(c,'tracking-view:'+ip,120,60)
    if not allowed:
        raise HTTPException(429,'انتظر قليلًا ثم حاول مرة أخرى.')
    with db.tx() as c:
        order=c.execute('''SELECT o.* FROM delivery_tracking_links l JOIN orders o ON o.id=l.order_id AND o.brand_id=l.brand_id
          WHERE l.token_hash=%s AND l.expires_at>now()''',(digest,)).fetchone()
        if not order:
            raise HTTPException(404,'الرابط غير متاح أو انتهت صلاحيته.')
        ended=bool(order.get('completed_at') or order['delivery_status'] in ('delivered','cancelled') or order['preparation_status'] in ('completed','cancelled','rejected'))
        # No staff/customer identity, phone, other-order IDs, branch choices or trip history.
        result={'status':'ended' if ended else 'in_progress','location':None,'gps':{'state':'missing'}}
        if ended:
            return result
        rows=c.execute('''SELECT t.driver_id,t.shift_id,g.lat,g.lng,g.accuracy_m,g.updated_at
          FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id AND s.brand_id=%s AND s.status='open'
          JOIN users u ON u.id=t.driver_id AND u.active AND u.role='driver' AND u.brand_id=s.brand_id
          JOIN driver_latest_locations g ON g.driver_id=u.id AND g.shift_id=s.id AND g.brand_id=s.brand_id
          WHERE t.order_id=%s AND t.status='picked_up' AND s.branch_ids ? %s AND u.branch_ids ? %s
          AND NOT EXISTS (SELECT 1 FROM driver_tasks other WHERE other.shift_id=t.shift_id AND other.id<>t.id
            AND other.status NOT IN ('delivered','cancelled','transferred','reassigned')) LIMIT 1''',
          (order['brand_id'],order['id'],order['branch_id'],order['branch_id'])).fetchall()
        if rows:
            gps=quality(rows[0]);result['gps']=safe(gps)
            if gps['state'] in ('live','approximate'):
                result['status']='on_the_way'
                result['location']={'lat':rows[0]['lat'],'lng':rows[0]['lng']}
        # Use an already configured public map key, never a secret or a separate backend.
        config=dispatch_map.map_config({'brand_id':order['brand_id']})
        result['map']=config if config.get('enabled') else {'enabled':False}
        if body.route and rows and result['gps']['state']=='live' and result['location'] and config.get('enabled'):
            destination=dispatch_map.route_point((order['customer'] or {}).get('location'))
            if destination:
                # Share the existing global monthly and per-driver route permit budget.
                try:
                    dispatch_map.consume_route_budget(c,rows[0]['driver_id'],'tracking:'+digest)
                    result['route_destination']=destination
                except HTTPException as exc:
                    if exc.status_code not in (429,503):
                        raise
                    result['route_unavailable']=True
        return safe(result)


@router.get('/delivery-track')
def page():
    return FileResponse(Path(__file__).resolve().parents[1]/'public/delivery-track.html',
                        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

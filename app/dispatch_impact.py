"""Prospective road dispatch quotes, immutable evidence and assignment revalidation.

No network I/O occurs under assignment locks. A missing provider/GPS result is
explicit, with a named operational override; it never becomes a green estimate.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import auth, catalog, db, dispatch_map, route_eta

router = APIRouter(prefix='/api/operations', tags=['operations'])
TTL_SECONDS = 45
REASONS = {'all_drivers_busy':'جميع السائقين مشغولون', 'branch_preparation':'انتظار تجهيز الفرع',
           'same_route_batch':'تجميع طلبات على نفس المسار', 'vehicle_or_driver_issue':'مشكلة بالسائق أو المركبة',
           'reduce_delay':'إعادة توزيع لتقليل التأخير', 'customer_location_change':'تغيير موقع العميل',
           'estimate_unavailable':'تقدير المسار غير متاح'}
CONTACTS = {'agreed':'تم التواصل ووافق', 'disagreed':'تم التواصل ولم يوافق',
            'no_answer':'لا يرد', 'not_contacted':'لم يتم التواصل'}


def enabled():
    return os.getenv('DISPATCH_ROAD_IMPACT_ENABLED', 'false').lower() == 'true'


def policy(order):
    saved=(order.get('snapshot') or {}).get('delivery_journey_policy')
    if saved and saved.get('version')==1:
        return saved, 'order_snapshot'
    # Historical orders retain their snapshots. Any prospective comparison is
    # explicitly marked as a current-policy assessment, not a historical rating.
    from .journey_timing import capture
    snap=order.get('snapshot') or {}
    return capture(dict(snap,service=order.get('service'),branch_city=order.get('branch_city') or snap.get('branch_city'))), 'current_policy_assessment'


def stamp(value):
    if isinstance(value,str):
        try:value=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:return None
    return value if isinstance(value,datetime) and value.tzinfo is not None else None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def optimize(stops, matrix, now, included=None):
    """Exact bounded route search: protect existing deadlines before new work.

    Pickup precedes its deliveries. Ready-at and stop allowance are explicit
    estimates. Directed road times never use straight-line speed fallbacks.
    """
    route_eta.validate_matrix(matrix,len(stops)+1)
    allowed=set(included) if included is not None else {s['id'] for s in stops}
    chosen=[(i+1,s) for i,s in enumerate(stops) if s['id'] in allowed]
    if len(chosen)>8 or len({s['id'] for _,s in chosen})!=len(chosen):raise ValueError('stop_limit_or_duplicate')
    index={s['id']:i for i,(_,s) in enumerate(chosen)}
    prerequisites=[]
    for _,s in chosen:
        if any(p not in index for p in s.get('prerequisite_ids',[])):raise ValueError('missing_prerequisite')
        prerequisites.append(sum(1<<index[p] for p in s.get('prerequisite_ids',[])))
    winner=None
    def walk(mask, previous, elapsed, totals, rows):
        nonlocal winner
        if mask==(1<<len(chosen))-1:
            key=(*totals,elapsed,tuple(row['id'] for row in rows))
            if winner is None or key<winner[0]:winner=(key,rows)
            return
        for i,(matrix_index,s) in enumerate(chosen):
            if mask&(1<<i) or prerequisites[i]&mask!=prerequisites[i]:continue
            seconds=matrix[previous][matrix_index]
            if seconds is None:continue
            arrive=now+timedelta(seconds=elapsed+math.ceil(seconds))
            ready=stamp(s.get('ready_at'))
            begin=max(arrive,ready) if ready else arrive
            dwell=s.get('dwell_seconds',{'pickup':180,'return':0}.get(s['kind'],120))
            if type(dwell) is not int or not 0<=dwell<=3600:raise ValueError('invalid_dwell')
            depart=begin+timedelta(seconds=dwell)
            deadline=stamp(s.get('deadline_at'))
            late=max(0,math.ceil((depart-deadline).total_seconds())) if deadline else 0
            score=list(totals)
            if s['kind'] in ('dropoff','delivery'):
                offset=2 if s.get('candidate') else 0
                score[offset]+=int(late>0);score[offset+1]+=late
            row=dict(s,arrival_at=arrive,completion_at=depart,drive_seconds=math.ceil(seconds),
                     wait_seconds=math.ceil((begin-arrive).total_seconds()),dwell_seconds=dwell,
                     late_seconds=late if deadline else None)
            walk(mask|(1<<i),matrix_index,math.ceil((depart-now).total_seconds()),tuple(score),rows+[row])
    walk(0,0,0,(0,0,0,0),[])
    if winner is None:raise ValueError('no_road_route')
    return winner[1]


def impact(stops, matrix, now):
    original=[s['id'] for s in stops if not s.get('candidate')]
    before=optimize([dict(s,ready_at=s.get('baseline_ready_at',s.get('ready_at')),prerequisite_ids=[i for i in s.get('prerequisite_ids',[]) if i in original]) for s in stops],matrix,now,original)
    after=optimize(stops,matrix,now)
    baseline={r['order_id']:r for r in before if r['kind'] in ('dropoff','delivery')}
    deliveries=[]
    for r in after:
        if r['kind'] not in ('dropoff','delivery'):continue
        old=baseline.get(r['order_id'])
        increase=max(0,r['late_seconds']-old['late_seconds']) if old and r['late_seconds'] is not None and old['late_seconds'] is not None else 0
        deliveries.append(dict(r,baseline_completion_at=old['completion_at'] if old else None,
                               added_lateness_seconds=increase))
    unknown=any(r.get('deadline_at') is None for r in deliveries)
    late=any((r['late_seconds'] or 0)>0 or r['added_lateness_seconds']>0 for r in deliveries)
    return {'status':'unverified' if unknown else 'late' if late else 'within_target',
            'basis':'road_time_plus_explicit_preparation_and_stop_estimates',
            'preparation_basis':'policy_allowance_not_observed_readiness',
            'orders':deliveries, 'stops':after,
            'newly_late_existing_orders':sum(r['added_lateness_seconds']>0 for r in deliveries if not r.get('candidate'))}


def absent(reason):
    return {'status':'unavailable','reason':reason,'orders':[], 'stops':[],
            'basis':'no_estimate','newly_late_existing_orders':None}


def context(c,user,order,shift):
    from .operations import eligible_driver_shift, shift_access
    shift_access(c,user,shift)
    eligible_driver_shift(c,shift,order)
    from . import multibrand
    target=c.execute('SELECT * FROM users WHERE id=%s',(shift['user_id'],)).fetchone()
    driver=multibrand.project(c,target,order['brand_id']) if multibrand.enabled() and target else target
    if not driver or not driver['active'] or order['branch_id'] not in driver.get('branch_ids',[]):raise HTTPException(409,'السائق غير مؤهل.')
    if user['role']=='delivery_manager':
        parent=c.execute('SELECT user_id,status FROM shifts WHERE id=%s',(shift['manager_shift_id'],)).fetchone()
        if not parent or parent['status']!='open' or parent['user_id']!=user['id']:raise HTTPException(403,'شفت مدير آخر.')
    rows=c.execute("""SELECT t.id task_id,t.status task_status,t.shift_id,o.id,o.version,o.brand_id,o.branch_id,
        o.service,o.customer,o.snapshot,o.created_at,o.preparation_status,b.city branch_city
        FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id JOIN orders o ON o.id=t.order_id
        JOIN branches b ON b.id=o.branch_id WHERE t.driver_id=%s AND s.status='open'
        AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')
        AND o.completed_at IS NULL ORDER BY t.id""",(shift['user_id'],)).fetchall()
    # Lock-free read is serialized by all live shift row locks at assignment.
    # Include all brands physically carried; any hidden/problem order makes a
    # full prospective plan unavailable rather than leaking or omitting it.
    visible=[]
    for r in rows:
        principal=multibrand.project(c,user,r['brand_id']) if multibrand.enabled() else user
        if not principal or not auth.can_access_order(principal,r,c):return None,absent('itinerary_scope_incomplete')
        if r['task_status']=='problem':return None,absent('active_problem_requires_review')
        visible.append(r)
    candidate_tasks=c.execute("SELECT id,status,shift_id,picked_up_at,transfer_shift_id FROM driver_tasks WHERE order_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned') ORDER BY id",(order['id'],)).fetchall()
    signature=fingerprint({'candidate_tasks':candidate_tasks,'order':{k:order.get(k) for k in ('id','version','customer','snapshot','preparation_status')},
                           'shift':shift['id'],'tasks':visible})
    if any(t.get('picked_up_at') for t in candidate_tasks):return signature,absent('handover_location_unverified')
    try:ctx=route_eta.context_for(c,user,shift['user_id'])
    except HTTPException as exc:
        if exc.status_code==409:return signature,absent('location_or_map_unavailable')
        raise
    if not route_eta.complete_itinerary(c,user,ctx):return signature,absent('itinerary_scope_incomplete')
    if not route_eta.location_fresh(ctx.get('updated_at')):return signature,absent('location_stale')
    branch=c.execute('SELECT * FROM branches WHERE id=%s',(order['branch_id'],)).fetchone()
    point=(branch.get('settings') or {}).get('location') if branch and catalog.location_verified(branch) else None
    if not point or not dispatch_map.route_point((order.get('customer') or {}).get('location')):return signature,absent('location_missing')
    now=datetime.now(timezone.utc)
    p,_=policy(dict(order,branch_city=branch['city']))
    if not p:return signature,absent('city_policy_unconfigured')
    specs={r['id']:r for r in visible}
    specs[order['id']]=dict(order,branch_city=branch['city'])
    common={'task_id':'candidate:'+order['id'],'order_id':order['id'],'order_no':order['order_no'],
            'brand_id':order['brand_id'],'branch_id':order['branch_id'],'shift_id':shift['id'],'candidate':True}
    pickup='pickup:candidate:'+order['id']
    stops=list(ctx['stops'])+[
        dict(common,id=pickup,kind='pickup',location=point,prerequisite_ids=[]),
        dict(common,id='dropoff:candidate:'+order['id'],kind='dropoff',location=order['customer']['location'],prerequisite_ids=[pickup])]
    for s in stops:
        spec=specs.get(s['order_id']);rules,basis=policy(spec)
        created=stamp(spec.get('created_at'))
        s['deadline_at']=created+timedelta(seconds=rules['total_seconds']) if rules and created else None
        s['deadline_basis']=basis
        if s['kind']=='pickup':
            # The allowance is disclosed; never report this as observed readiness.
            ready=spec['preparation_status'] in ('ready','handed_over')
            s['ready_at']=now if ready else created+timedelta(seconds=max(0,rules['pickup_cutoff_seconds']-180)) if rules and created else None
            s['readiness_basis']='recorded_ready' if ready else 'policy_allowance'
    pickup_groups={}
    for s in stops:
        if s['kind']!='pickup':continue
        key=(s.get('brand_id'),s.get('branch_id'),s.get('shift_id'))
        pickup_groups.setdefault(key,[]).append(s)
    saved=c.execute('SELECT return_branch_id FROM driver_route_eta_state WHERE driver_id=%s AND manager_id=%s',
                    (shift['user_id'],user['id'])).fetchone()
    intent=ctx.get('next_destination') or {}
    return_id=(saved or {}).get('return_branch_id') or (intent.get('branch_id') if intent.get('kind')=='branch' else None)
    signature=fingerprint({'itinerary':signature,'return_branch_id':return_id})
    try:stops=route_eta.prepare_stops({'stops':stops,'return_branches':ctx['return_branches']},return_id)
    except HTTPException:return signature,absent('stop_limit_or_location_missing')
    for s in stops:
        if s['kind']!='pickup':continue
        group=pickup_groups[(s.get('brand_id'),s.get('branch_id'),s.get('shift_id'))]
        ready=[stamp(x.get('ready_at')) for x in group if stamp(x.get('ready_at'))]
        baseline=[stamp(x.get('ready_at')) for x in group if not x.get('candidate') and stamp(x.get('ready_at'))]
        s['ready_at']=max(ready) if ready else None
        s['baseline_ready_at']=max(baseline) if baseline else None
        s['candidate']=all(x.get('candidate') for x in group)
        s['readiness_basis']='policy_allowance' if any(x.get('readiness_basis')=='policy_allowance' for x in group) else 'recorded_ready'
    # Shared branch pickup occurs once; its readiness is the latest required
    # order, while the baseline excludes the candidate's preparation allowance.
    return signature, {'origin':ctx['origin'],'origin_updated_at':ctx['updated_at'],'stops':stops,'now':now,'return_branch_id':return_id}


class PreviewBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:str|None=Field(None,min_length=1,max_length=100)
    task_id:str|None=Field(None,min_length=1,max_length=100)
    driver_shift_id:str=Field(min_length=1,max_length=100)


@router.get('/dispatch-impact-policy')
def read_policy(user=Depends(auth.require_roles('owner','delivery_manager','delivery_staff'))):
    if not auth.has_permission(user,'delivery_assign'):raise HTTPException(403,'صلاحية الإسناد غير متاحة.')
    return {'impact_enabled':enabled(),'reason_options':REASONS,'contact_options':CONTACTS}


@router.post('/dispatch-impact')
def preview(body:PreviewBody,user=Depends(auth.require_roles('owner','delivery_manager','delivery_staff'))):
    if not auth.has_permission(user,'delivery_assign'):raise HTTPException(403,'صلاحية الإسناد غير متاحة.')
    from .operations import order_access
    with db.tx() as c:
        oid=body.order_id
        if body.task_id:
            task=c.execute('SELECT order_id FROM driver_tasks WHERE id=%s',(body.task_id,)).fetchone()
            if not task or oid and oid!=task['order_id']:raise HTTPException(404,'المهمة غير متاحة.')
            oid=task['order_id']
        if not oid:raise HTTPException(422,'حدد الطلب أو المهمة.')
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone();order_access(c,user,order)
        if order['service']!='delivery' or order.get('completed_at') or order['preparation_status'] in ('cancelled','rejected'):raise HTTPException(409,'الطلب غير متاح.')
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(body.driver_shift_id,)).fetchone()
        if not shift or shift['kind']!='driver' or shift['status']!='open' or shift['brand_id']!=order['brand_id'] or order['branch_id'] not in shift['branch_ids']:raise HTTPException(409,'السائق غير مؤهل.')
        if (order.get('snapshot') or {}).get('delivery_period')=='morning':return {'enabled':False,**absent('morning_unchanged')}
        signature,plan=context(c,user,order,shift)
        if not db.rate_limit(c,'dispatch-impact:'+user['id'],12,60):return {'enabled':enabled(),**absent('preview_rate_limit')}
        result=plan if 'status' in plan else None
        if result is None:
            token=os.getenv('MAPBOX_ROUTING_TOKEN','')
            if not re.fullmatch(r'(?:pk|sk)\.[A-Za-z0-9_.-]{20,2048}',token):result=absent('routing_token_unconfigured')
            else:
                try:dispatch_map.consume_route_budget(c,shift['user_id'],user['id'],units=(len(plan['stops'])+1)**2)
                except HTTPException as exc:
                    if exc.status_code!=429:raise
                    result=absent('routing_budget_or_cooldown')
    matrix=None
    if result is None:
        try:
            matrix=route_eta.fetch_matrix([plan['origin']]+[s['location'] for s in plan['stops']],token)
            result=impact(plan['stops'],matrix,plan['now'])
        except (httpx.HTTPError,ValueError,TypeError):result=absent('routing_provider_unavailable')
    with db.tx() as c:
        user=route_eta.refreshed_caller(c,user,assignment=True)
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone();order_access(c,user,order)
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(body.driver_shift_id,)).fetchone()
        if not shift or shift['status']!='open':raise HTTPException(409,'شفت السائق تغير أثناء الحساب.')
        current,current_plan=context(c,user,order,shift)
        if current!=signature:result=absent('itinerary_changed_during_calculation')
        elif 'status' in current_plan:result=current_plan
        c.execute("DELETE FROM dispatch_impact_quotes WHERE id IN (SELECT id FROM dispatch_impact_quotes WHERE expires_at<now()-interval '1 day' ORDER BY expires_at LIMIT 200)")
        qid=uuid4().hex
        stored=dict(result,origin=plan.get('origin'),origin_updated_at=plan.get('origin_updated_at'),matrix=matrix,calculated_at=plan.get('now'))
        c.execute('INSERT INTO dispatch_impact_quotes(id,actor_id,order_id,driver_id,shift_id,fingerprint,result,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now()+interval \'45 seconds\')',
                  (qid,user['id'],order['id'],shift['user_id'],shift['id'],signature or '',db.json(stored)))
        return {**route_eta.safe(result),'quote_id':qid,'enabled':enabled(),'expires_in_seconds':TTL_SECONDS,
                'reason_options':REASONS,'contact_options':CONTACTS}


def lock_physical_shifts(c,shift_ids):
    rows=c.execute('SELECT DISTINCT user_id FROM shifts WHERE id=ANY(%s)',(list(shift_ids),)).fetchall()
    if rows:
        c.execute("SELECT id FROM shifts WHERE user_id=ANY(%s) AND status='open' ORDER BY id FOR UPDATE",
                  ([r['user_id'] for r in rows],)).fetchall()


def validate_assignment(c,user,order,shift,body):
    if not enabled() or (order.get('snapshot') or {}).get('delivery_period')=='morning':return None
    # Caller holds all physical live shift rows in canonical order before
    # checking the itinerary. No provider HTTP is allowed inside these locks.
    signature,plan=context(c,user,order,shift)
    quote=c.execute('SELECT *,expires_at>clock_timestamp() fresh FROM dispatch_impact_quotes WHERE id=%s AND actor_id=%s AND order_id=%s AND shift_id=%s FOR UPDATE',
                    (body.impact_quote_id,user['id'],order['id'],shift['id'])).fetchone() if body.impact_quote_id else None
    result=absent('no_current_quote')
    if quote and quote['fresh'] and quote['fingerprint']==signature:
        saved=quote['result']
        if 'status' in plan:
            result=plan
        elif saved.get('origin') and saved.get('matrix') is not None:
            distance=catalog.direct_distance(saved['origin'],plan['origin'])
            if distance is not None and distance<=200:
                # Rebase the quoted directed roads on current time and current
                # readiness. Never subtract quote age as fictitious progress.
                try:result=impact(plan['stops'],saved['matrix'],datetime.now(timezone.utc))
                except (ValueError,TypeError):result=absent('quote_matrix_unavailable')
                result.update(origin_distance_m=round(distance),road_data_at=saved.get('calculated_at'))
            else:result=absent('driver_moved_refresh_required')
        else:result={k:v for k,v in saved.items() if k not in ('matrix','origin','origin_updated_at')}
    reason=body.impact_reason;contact=body.impact_contact
    if reason is not None and reason not in REASONS or contact is not None and contact not in CONTACTS:
        raise db.error(422,'DISPATCH_IMPACT_CHOICE','اختر السبب وحالة التواصل من الخيارات الموجودة.')
    if result['status']!='within_target' and (reason not in REASONS or contact not in CONTACTS):
        raise db.error(409,'DISPATCH_IMPACT_ACK_REQUIRED','راجع التقدير أو اختر سبب الإسناد وحالة التواصل؛ لا يوجد تقدير مؤكد.')
    evidence={**result,'quote_id':quote['id'] if quote else None,'reason':reason,'contact':contact,
              'reason_label':REASONS.get(reason),'contact_label':CONTACTS.get(contact),
              'driver_id':shift['user_id'],'shift_id':shift['id'],'actor_id':user['id'],
              'recorded_at':datetime.now(timezone.utc),'deadline_preserved':True}
    db.event(c,order['id'],user['id'],'dispatch.impact_accepted',evidence)
    db.audit(c,user['id'],'dispatch.impact_accepted','order',order['id'],evidence)
    if quote and result['status'] in ('within_target','late','unverified') and 'status' not in plan and quote['result'].get('matrix') is not None:
        evidence['_route_bridge']={'matrix':quote['result']['matrix'],
            'stop_ids':[s['id'] for s in plan['stops']], 'return_branch_id':plan.get('return_branch_id'),
            'origin_updated_at':plan.get('origin_updated_at')}
    return evidence


def remap_plan(canonical, evidence, task_id, order_id):
    """Map a candidate quote to actual task identities, preserving every road."""
    bridge=evidence['_route_bridge']
    def actual(identifier):
        return 'dropoff:'+task_id if identifier=='dropoff:candidate:'+order_id else identifier
    source_ids=['origin']+[actual(i) for i in bridge['stop_ids']]
    destination_ids=['origin']+[s['id'] for s in canonical]
    if len(set(source_ids))!=len(source_ids) or set(source_ids)!=set(destination_ids):
        raise ValueError('publication_itinerary_changed')
    matrix=route_eta.validate_matrix(bridge['matrix'],len(source_ids))
    indices=[source_ids.index(i) for i in destination_ids]
    remapped=[[matrix[a][b] for b in indices] for a in indices]
    proposed=[actual(s['id']) for s in evidence['stops']]
    ordered=route_eta.validate_order(canonical,[i for i in proposed if not i.startswith('return:')])
    if [s['id'] for s in ordered]!=proposed:raise ValueError('publication_order_changed')
    timing={actual(s['id']):s for s in evidence['stops']}
    augmented=[dict(s,dispatch_ready_at=timing[s['id']].get('ready_at'),
                    dispatch_dwell_seconds=timing[s['id']]['dwell_seconds']) for s in canonical]
    return augmented,proposed,remapped


def apply_assignment_plan(c,user,order,shift,task_id,evidence):
    """Assignment confirmation also publishes its actual canonical itinerary.

    Executed after task insertion under the same physical shift locks. Uses the
    validated road matrix only, with no external I/O. Any mismatch aborts this
    ready-ETA assignment instead of claiming a route the driver cannot follow.
    """
    if not evidence:return
    bridge=evidence.pop('_route_bridge',None)
    if bridge is None:return
    actor=route_eta.refreshed_caller(c,user,assignment=True)
    ctx=route_eta.context_for(c,actor,shift['user_id'])
    if not route_eta.complete_itinerary(c,actor,ctx):
        raise db.error(409,'DISPATCH_PLAN_CHANGED','تغير مسار السائق. حدّث التقدير قبل الإسناد.')
    canonical=route_eta.prepare_stops(ctx,bridge.get('return_branch_id'))
    try:
        augmented,ordered,matrix=remap_plan(canonical,{**evidence,'_route_bridge':bridge},task_id,order['id'])
    except (ValueError,HTTPException,KeyError,TypeError):
        raise db.error(409,'DISPATCH_PLAN_CHANGED','تغيرت محطات الرحلة. حدّث التقدير قبل الإسناد.')
    calculated=datetime.now(timezone.utc)
    signature=route_eta.fingerprint(augmented)
    shifts=sorted({s['shift_id'] for s in canonical if s.get('shift_id')})
    c.execute('''INSERT INTO driver_route_eta_state(driver_id,manager_id,fingerprint,matrix,calculated_at,
        origin_updated_at,ordered_stop_ids,return_branch_id,published_at,published_stop_ids,published_stops,
        published_matrix,published_calculated_at,published_shift_ids,source_session_id,manager_brand_id,central_scope)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(driver_id,manager_id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,matrix=EXCLUDED.matrix,
        calculated_at=EXCLUDED.calculated_at,origin_updated_at=EXCLUDED.origin_updated_at,
        ordered_stop_ids=EXCLUDED.ordered_stop_ids,return_branch_id=EXCLUDED.return_branch_id,
        published_at=EXCLUDED.published_at,published_stop_ids=EXCLUDED.published_stop_ids,
        published_stops=EXCLUDED.published_stops,published_matrix=EXCLUDED.published_matrix,
        published_calculated_at=EXCLUDED.published_calculated_at,published_shift_ids=EXCLUDED.published_shift_ids,
        source_session_id=EXCLUDED.source_session_id,manager_brand_id=EXCLUDED.manager_brand_id,
        central_scope=EXCLUDED.central_scope,morning_shift_id=NULL,road_corridor=NULL''',
        (shift['user_id'],user['id'],signature,db.json(matrix),calculated,ctx['updated_at'],
         db.json([i for i in ordered if not i.startswith('return:')]),bridge.get('return_branch_id'),
         db.json(ordered),db.json(augmented),db.json(matrix),calculated,db.json(shifts),actor['session_id'],
         user['brand_id'],bool(user.get('central_operations'))))
    db.audit(c,user['id'],'driver.route_published','driver',shift['user_id'],
             {'source':'dispatch_impact_assignment','order_id':order['id'],'task_id':task_id,
              'quote_id':evidence.get('quote_id'),'stop_ids':ordered,'shift_ids':shifts})
    evidence['route_published']=True

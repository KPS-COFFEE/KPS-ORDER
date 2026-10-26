from datetime import datetime, timezone, timedelta
import httpx
import pytest
from fastapi import HTTPException
from app import route_eta as eta

NOW=datetime(2026,10,26,10,tzinfo=timezone.utc)


def stop(identifier,kind='dropoff',required=()):
    return {'id':identifier,'kind':kind,'location':{'lat':28.4,'lng':45.9},
            'prerequisite_ids':list(required),'brand_id':'kps'}


def test_exact_road_order_differs_from_geographic_neighbour_and_is_directed():
    stops=[stop('a'),stop('b'),stop('c')]
    roads=[[0,1,2,3],[100,0,100,100],[100,1,0,1],[100,1,100,0]]
    # Geographic ordering is irrelevant. Best road path is b -> c -> a.
    assert [s['id'] for s in eta.road_order(stops,roads)] == ['b','c','a']


def test_pickups_before_deliveries_and_return_last_across_two_brands():
    stops=[stop('pickup:a','pickup'),stop('a',required=['pickup:a']),
           {**stop('b'),'brand_id':'enbishan'},stop('return:x','return',['pickup:a','a','b'])]
    roads=[[0 if i==j else 10 for j in range(5)] for i in range(5)]
    roads[0][2]=1  # Delivery appears attractive but must not precede pickup.
    ordered=eta.road_order(stops,roads)
    ids=[s['id'] for s in ordered]
    assert ids.index('pickup:a')<ids.index('a') and ids[-1]=='return:x'
    assert {s['brand_id'] for s in ordered}=={'kps','enbishan'}


def test_cumulative_eta_counts_each_road_and_dwell_once_and_separates_return():
    stops=[stop('p','pickup'),stop('a',required=['p']),stop('b'),stop('r','return',['p','a','b'])]
    roads=[[0 if i==j else 60 for j in range(5)] for i in range(5)]
    view=eta.timeline(stops,stops,roads,NOW,NOW)
    assert [r['eta_seconds'] for r in view['stops']]==[60,300,480,660]
    assert view['delivery_complete_seconds']==600
    assert view['return_seconds']==660
    assert sum(r['drive_seconds'] for r in view['stops'])==240
    assert sum(r['dwell_seconds'] for r in view['stops'])==420
    assert eta.timeline(stops,stops,roads,NOW,NOW+timedelta(seconds=20))['return_seconds']==640


@pytest.mark.parametrize('ordered',[['a','a'],['a'],['a','foreign'],['a','p']])
def test_manual_reorder_cannot_omit_duplicate_inject_or_deliver_before_pickup(ordered):
    stops=[stop('p','pickup'),stop('a',required=['p'])]
    with pytest.raises(HTTPException):eta.validate_order(stops,ordered)


def test_manual_order_and_return_are_preserved():
    stops=[stop('a'),stop('b'),stop('r','return',['a','b'])]
    assert [s['id'] for s in eta.validate_order(stops,['b','a'])]==['b','a','r']


def test_unreachable_road_has_no_straight_line_speed_fallback():
    with pytest.raises(ValueError):eta.road_order([stop('a')],[[0,None],[None,0]])
    with pytest.raises(ValueError):eta.timeline([stop('a')],[stop('a')],[[0,None],[None,0]],NOW)


@pytest.mark.parametrize('matrix',[[[0]],[[0,True],[0,0]],[[0,float('nan')],[0,0]],[[0,-1],[0,0]],[[0,'60'],[0,0]]])
def test_malformed_matrix_cannot_become_eta(matrix):
    with pytest.raises(ValueError):eta.validate_matrix(matrix,2)


def test_missing_station_location_is_explicit_instead_of_dropped():
    context={'stops':[dict(stop('a'),location=None)],'return_branches':[]}
    with pytest.raises(HTTPException):eta.prepare_stops(context)


def test_foreign_return_cannot_be_requested():
    context={'stops':[stop('a')],'return_branches':[]}
    with pytest.raises(HTTPException):eta.prepare_stops(context,'foreign-branch')


def test_http_matrix_uses_one_fixed_host_no_fallback_and_directed_durations():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'code':'Ok','durations':[[0,10],[25,0]]})
    assert eta.fetch_matrix([{'lat':28.,'lng':45.},{'lat':29.,'lng':46.}], 'pk.test',httpx.MockTransport(handler))==[[0,10],[25,0]]
    assert len(requests)==1
    url=requests[0].url
    assert url.host=='api.mapbox.com'
    assert url.path=='/directions-matrix/v1/mapbox/driving/45.0,28.0;46.0,29.0'
    assert url.params.get('annotations')=='duration'
    assert 'fallback_speed' not in url.params


@pytest.mark.parametrize('status,payload',[(403,{}),(200,{'code':'NoRoute'}),(200,{'code':'Ok','durations':[[0]]}),(302,{})])
def test_provider_rejections_redirect_and_bad_shape_fail_closed(status,payload):
    transport=httpx.MockTransport(lambda _:httpx.Response(status,json=payload))
    with pytest.raises(ValueError):eta.fetch_matrix([{'lat':0,'lng':0},{'lat':1,'lng':1}],'pk.test',transport)


def test_missing_prerequisite_cannot_be_ignored():
    with pytest.raises(ValueError):eta.road_order([stop('a',required=['foreign'])],[[0,5],[5,0]])


def test_two_orders_at_same_branch_share_one_pickup_and_wait_estimate():
    source=[]
    for i in ('a','b'):
        source += [{**stop('pickup:'+i,'pickup'),'branch_id':'branch-1','task_id':i,'order_id':'order-'+i,'shift_id':'shift-1'},
                   stop('dropoff:'+i,required=['pickup:'+i])]
    stops=eta.prepare_stops({'stops':source,'return_branches':[]})
    assert len(stops)==3
    branch=[s for s in stops if s['kind']=='pickup'][0]
    assert branch['task_ids']==['a','b']
    assert all(s['prerequisite_ids']==[branch['id']] for s in stops if s['kind']=='dropoff')
    roads=[[0 if i==j else 60 for j in range(4)] for i in range(4)]
    view=eta.timeline(stops,eta.road_order(stops,roads),roads,NOW,NOW)
    assert sum(r['dwell_seconds'] for r in view['stops'])==420  # 180 once + 120 per delivery.


def test_hidden_active_task_prevents_partial_trip_completion_claim():
    class Connection:
        def execute(self,sql,args):return self
        def fetchall(self):return [{'id':'visible-task'},{'id':'hidden-task'}]
    user={'id':'m','brand_id':'kps'}
    context={'driver_id':'d','stops':[dict(stop('a'),task_id='visible-task')],'tasks':[]}
    assert eta.complete_itinerary(Connection(),user,context) is False
    context['stops'].append(dict(stop('b'),task_id='hidden-task'))
    assert eta.complete_itinerary(Connection(),user,context) is True


class LifeConnection:
    def __init__(self,tasks):self.tasks=tasks
    def execute(self,sql,args):return self
    def fetchall(self):return self.tasks


def publication(stops):
    return {'published_stops':stops,'published_stop_ids':[s['id'] for s in stops]}


def life_task(identifier,status='picked_up',driver='d',shift='s'):
    return {'id':identifier,'status':status,'driver_id':driver,'shift_id':shift}


def task_stop(identifier,kind='dropoff',required=()):
    return dict(stop(identifier,kind,required),task_id=identifier.split(':')[-1],shift_id='s')


def test_published_pickup_then_delivery_advance_without_reapproval():
    pickup=dict(task_stop('pickup:a','pickup'),task_ids=['a'],branch_id='b')
    a=task_stop('dropoff:a',required=['pickup:a'])
    b=task_stop('dropoff:b')
    original=[pickup,a,b]
    picked=[dict(a,prerequisite_ids=[]),b]
    c=LifeConnection([life_task('a'),life_task('b')])
    result=eta.remaining_publication(c,publication(original),picked,'d')
    assert [s['id'] for s in result[1]]==['dropoff:a','dropoff:b']
    c=LifeConnection([life_task('a','delivered'),life_task('b')])
    result=eta.remaining_publication(c,publication(original),[b],'d')
    assert [s['id'] for s in result[1]]==['dropoff:b']


@pytest.mark.parametrize('status,driver,shift',[('cancelled','d','s'),('reassigned','other','s'),('delivered','other','s'),('delivered','d','new-shift')])
def test_missing_station_requires_proven_own_completed_task(status,driver,shift):
    original=[task_stop('dropoff:a'),task_stop('dropoff:b')]
    c=LifeConnection([life_task('a',status,driver,shift),life_task('b')])
    assert eta.remaining_publication(c,publication(original),[original[1]],'d') is None


def test_new_work_and_changed_coordinates_invalidate_published_order():
    original=[task_stop('dropoff:a')]
    c=LifeConnection([life_task('a')])
    assert eta.remaining_publication(c,publication(original),original+[task_stop('dropoff:new')],'d') is None
    assert eta.remaining_publication(c,publication(original),[dict(original[0],location={'lat':29.,'lng':46.})],'d') is None


def test_remaining_matrix_keeps_original_indices_instead_of_renumbering():
    original=[task_stop('dropoff:a'),task_stop('dropoff:b')]
    roads=[[0,10,70],[10,0,20],[70,20,0]]
    c=LifeConnection([life_task('a','delivered'),life_task('b')])
    full,remaining=eta.remaining_publication(c,publication(original),[original[1]],'d')
    assert eta.timeline(full,remaining,roads,NOW,NOW)['next_stop']['drive_seconds']==70


def test_morning_published_source_rechecks_policy_instead_of_reusing_capabilities(monkeypatch):
    from app import multibrand,morning_manager_context
    manager={'id':'m','brand_id':'kps','role':'delivery_manager','active':True,
             'branch_ids':['b'],'permission_denials':['delivery_assign']}
    class Connection:
        def execute(self,sql,args):return self
        def fetchone(self):return manager
    monkeypatch.setattr(multibrand,'project',lambda c,u,b:u)
    calls=[]
    def allowed(c,u,shift_id):
        calls.append(shift_id)
        return dict(u,_morning_manager_context={'period':'morning','shift_id':shift_id,'branch_ids':['b']})
    monkeypatch.setattr(morning_manager_context,'resolve',allowed)
    saved={'manager_id':'m','manager_brand_id':'kps','central_scope':False,'morning_shift_id':'morning-duty'}
    assert eta.published_manager(Connection(),saved)['_morning_manager_context']['shift_id']=='morning-duty'
    assert calls==['morning-duty']
    def revoked(*args,**kwargs):raise HTTPException(403,'revoked')
    monkeypatch.setattr(morning_manager_context,'resolve',revoked)
    assert eta.published_manager(Connection(),saved) is None


def test_new_map_endpoints_have_bounded_morning_manager_allowlist():
    from app import morning_manager_context
    assert morning_manager_context.ENDPOINTS['route_eta']=={'route_eta'}
    assert morning_manager_context.ENDPOINTS['tracking_ingest']=={'history','resolve_alert'}
    assert 'driver_route_plan' not in morning_manager_context.ENDPOINTS['route_eta']


def test_own_route_denied_tasks_capability_before_database_or_history_read():
    user={'id':'d','role':'driver','brand_id':'kps','permission_denials':['tasks_view']}
    with pytest.raises(HTTPException) as exc:eta.driver_route_plan(user)
    assert exc.value.status_code==403

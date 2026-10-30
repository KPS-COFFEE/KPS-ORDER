from datetime import datetime,timedelta,timezone
import copy
import pytest
from fastapi import HTTPException
from app import dispatch_impact as d

NOW=datetime(2026,10,30,10,tzinfo=timezone.utc)


def stop(identifier,*,candidate=False,kind='dropoff',due=600,required=()):
    return {'id':identifier,'order_id':identifier,'order_no':identifier,'candidate':candidate,'kind':kind,
            'deadline_at':NOW+timedelta(seconds=due) if due is not None else None,
            'prerequisite_ids':list(required)}


def test_existing_customer_deadline_wins_over_nearest_new_dropoff():
    stops=[stop('existing',due=200),stop('new',candidate=True,due=2000)]
    roads=[[0,10,1],[1,0,1],[1,200,0]]
    result=d.impact(stops,roads,NOW)
    assert [x['id'] for x in result['stops']]==['existing','new']
    assert result['status']=='within_target'
    assert result['orders'][0]['added_lateness_seconds']==0


def test_new_order_can_be_late_even_when_old_customer_is_protected():
    stops=[stop('existing',due=200),stop('new',candidate=True,due=150)]
    result=d.impact(stops,[[0,10,1],[1,0,1],[1,200,0]],NOW)
    assert result['status']=='late'
    assert result['orders'][0]['late_seconds']==0
    assert result['orders'][1]['late_seconds']==101


def test_candidate_preparation_at_shared_pickup_is_not_in_baseline():
    stops=[{**stop('pickup',kind='pickup',due=None),'candidate':False,
            'ready_at':NOW+timedelta(seconds=300),'baseline_ready_at':NOW},
           stop('existing',due=400,required=['pickup']),stop('new',candidate=True,due=1800,required=['pickup'])]
    roads=[[0 if i==j else 5 for j in range(4)] for i in range(4)]
    result=d.impact(stops,roads,NOW)
    old=next(x for x in result['orders'] if x['id']=='existing')
    assert old['baseline_completion_at']==NOW+timedelta(seconds=310)
    assert old['completion_at']==NOW+timedelta(seconds=605)
    assert old['added_lateness_seconds']==205
    assert result['newly_late_existing_orders']==1
    assert sum(x['kind']=='pickup' for x in result['stops'])==1


def test_pickup_must_precede_delivery_and_does_not_mutate_source():
    stops=[stop('p',kind='pickup'),stop('new',candidate=True,required=['p'])]
    original=copy.deepcopy(stops)
    result=d.impact(stops,[[0,10,1],[10,0,1],[10,1,0]],NOW)
    assert [x['id'] for x in result['stops']]==['p','new']
    assert stops==original


def test_missing_deadline_is_never_green():
    result=d.impact([stop('unknown',candidate=True,due=None)],[[0,10],[10,0]],NOW)
    assert result['status']=='unverified' and result['orders'][0]['late_seconds'] is None


@pytest.mark.parametrize('roads',[[[0,None],[None,0]],[[0,-1],[1,0]],[[0,float('nan')],[1,0]],[[0,True],[1,0]]])
def test_bad_or_missing_roads_never_use_distance_speed_fallback(roads):
    with pytest.raises(ValueError):d.impact([stop('a',candidate=True)],roads,NOW)


def test_all_original_orders_are_returned_in_the_comparison():
    stops=[stop('a'),stop('b'),stop('new',candidate=True)]
    result=d.impact(stops,[[0 if i==j else 1 for j in range(4)] for i in range(4)],NOW)
    assert {r['order_id'] for r in result['orders']}=={'a','b','new'}
    assert all(r['baseline_completion_at'] for r in result['orders'] if not r['candidate'])


def test_city_deadline_always_starts_at_order_creation():
    for city,minutes in [('حفر الباطن',45),('الخفجي',32),('رفحاء',32),('الذيبية',27),('قبه',27),('النعيرية',27)]:
        p,basis=d.policy({'service':'delivery','branch_city':city,'snapshot':{}})
        assert p['total_seconds']==minutes*60 and basis=='current_policy_assessment'
    assert d.policy({'service':'delivery','branch_city':'حفر الباطن','snapshot':{'delivery_period':'morning'}})[0] is None


def test_frozen_order_policy_is_not_changed_by_new_configuration():
    saved={'version':1,'total_seconds':2700,'pickup_cutoff_seconds':1200}
    assert d.policy({'snapshot':{'delivery_journey_policy':saved},'branch_city':'الخفجي'})==(saved,'order_snapshot')


def test_route_search_is_bounded_and_duplicate_stops_fail():
    with pytest.raises(ValueError):d.optimize([stop('a'),stop('a')],[[0]*3 for _ in range(3)],NOW)
    with pytest.raises(ValueError):d.optimize([stop(str(i)) for i in range(9)],[[0]*10 for _ in range(10)],NOW)

class QuoteCursor:
    def __init__(self,row):self.row=row
    def execute(self,*args):return self
    def fetchone(self):return self.row


def test_assignment_rebases_eta_and_rejects_unsupported_free_text(monkeypatch):
    from types import SimpleNamespace
    now=datetime.now(timezone.utc)
    stops=[{**stop('new',candidate=True),'deadline_at':now-timedelta(seconds=1)}]
    quote={'id':'q','fresh':True,'fingerprint':'current','result':{'status':'within_target',
        'origin':{'lat':28.,'lng':45.},'matrix':[[0,10],[10,0]],'calculated_at':now-timedelta(seconds=30)}}
    monkeypatch.setenv('DISPATCH_ROAD_IMPACT_ENABLED','true')
    monkeypatch.setattr(d,'context',lambda *args:('current',{'stops':stops,'origin':{'lat':28.,'lng':45.}}))
    events=[]
    def record_event(*args):
        # Exercise the real psycopg JSON serializer, even without a database.
        from psycopg.types.json import Jsonb, JsonbDumper
        JsonbDumper(Jsonb).dump(d.db.json(args[-1]))
        events.append(args[-1])
    monkeypatch.setattr(d.db,'event',record_event)
    monkeypatch.setattr(d.db,'audit',lambda *args:None)
    user={'id':'manager'};order={'id':'order','snapshot':{}};shift={'id':'shift','user_id':'driver'}
    body=SimpleNamespace(impact_quote_id='q',impact_reason=None,impact_contact=None)
    # The earlier "within_target" status is never trusted as current evidence.
    with pytest.raises(HTTPException) as err:d.validate_assignment(QuoteCursor(quote),user,order,shift,body)
    assert err.value.status_code==409 and events==[]
    body.impact_reason='a made-up reason';body.impact_contact='agreed'
    with pytest.raises(HTTPException) as err:d.validate_assignment(QuoteCursor(quote),user,order,shift,body)
    assert err.value.status_code==422
    body.impact_reason='all_drivers_busy';body.impact_contact='disagreed'
    result=d.validate_assignment(QuoteCursor(quote),user,order,shift,body)
    assert result['status']=='late' and result['contact']=='disagreed'
    assert result['orders'][0]['late_seconds']>=130 and len(events)==1


def test_current_gps_failure_cannot_reuse_previously_green_quote(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('DISPATCH_ROAD_IMPACT_ENABLED','true')
    monkeypatch.setattr(d,'context',lambda *args:('same',d.absent('location_stale')))
    quote={'id':'q','fresh':True,'fingerprint':'same','result':{'status':'within_target','orders':[]}}
    body=SimpleNamespace(impact_quote_id='q',impact_reason=None,impact_contact=None)
    with pytest.raises(HTTPException) as err:d.validate_assignment(QuoteCursor(quote),{'id':'manager'},{'id':'o','snapshot':{}},{'id':'s'},body)
    assert err.value.status_code==409


def test_canonical_publication_remaps_candidate_and_preserves_road_indices():
    from app import route_eta
    pickup={'id':'pickup:b:x','kind':'pickup','task_ids':['t'],'location':{'lat':1,'lng':2},'brand_id':'b','shift_id':'s','branch_id':'x'}
    drop={'id':'dropoff:t','kind':'dropoff','task_id':'t','location':{'lat':3,'lng':4},'pickup_id':pickup['id']}
    canonical=[pickup,drop]
    timing=[dict(drop,id='dropoff:candidate:o',dwell_seconds=120),dict(pickup,dwell_seconds=180,ready_at=NOW+timedelta(seconds=300))]
    # Provider indices deliberately differ from the canonical database order.
    evidence={'stops':list(reversed(timing)),'_route_bridge':{'stop_ids':['dropoff:candidate:o',pickup['id']], 'matrix':[[0,11,12],[21,0,23],[31,32,0]]}}
    augmented,ordered,matrix=d.remap_plan(canonical,evidence,'t','o')
    assert ordered==[pickup['id'],drop['id']]
    assert matrix==[[0,12,11],[31,0,32],[21,23,0]]
    assert augmented[0]['dispatch_ready_at']==NOW+timedelta(seconds=300)
    assert route_eta.fingerprint(augmented)==route_eta.fingerprint(canonical)
    view=route_eta.timeline(augmented,augmented,matrix,NOW,NOW)
    assert view['stops'][0]['wait_seconds']==288
    assert view['delivery_complete_at']==NOW+timedelta(seconds=632)
    with pytest.raises(ValueError):d.remap_plan(canonical+[{'id':'new'}],evidence,'t','o')


def test_frozen_allowances_survive_refresh_only_for_same_task_station():
    from app import route_eta
    original={'id':'p','kind':'pickup','task_ids':['t'],'location':{'lat':1,'lng':2},'dispatch_ready_at':NOW.isoformat(),'dispatch_dwell_seconds':180}
    live={k:v for k,v in original.items() if not k.startswith('dispatch_')}
    carried=route_eta.inherit_dispatch_allowances([live],[original])[0]
    assert carried['dispatch_dwell_seconds']==180
    assert 'dispatch_ready_at' not in route_eta.inherit_dispatch_allowances([dict(live,task_ids=['new'])],[original])[0]
    assert 'dispatch_ready_at' not in route_eta.inherit_dispatch_allowances([dict(live,location={'lat':2,'lng':3})],[original])[0]
    assert route_eta.fingerprint(live if isinstance(live,list) else [live])!=route_eta.fingerprint([dict(live,task_ids=['new'])])


@pytest.mark.parametrize('available',[True,False])
def test_quote_and_canonical_publication_use_real_jsonb_serialization(available):
    import json
    from psycopg.types.json import Jsonb, JsonbDumper
    result=d.impact([stop('new',candidate=True)],[[0,10],[10,0]],NOW) if available else d.absent('routing_provider_unavailable')
    stored=dict(result,origin={'lat':28.,'lng':45.},origin_updated_at=NOW,
                calculated_at=NOW,matrix=[[0,10],[10,0]] if available else None)
    encoded=JsonbDumper(Jsonb).dump(d.json_parameter(stored))
    decoded=json.loads(bytes(encoded))
    assert decoded['status']==('within_target' if available else 'unavailable')
    assert d.stamp(decoded['origin_updated_at'])==NOW
    if available:
        assert d.stamp(decoded['orders'][0]['deadline_at'])==NOW+timedelta(seconds=600)
        assert d.stamp(decoded['orders'][0]['completion_at'])==NOW+timedelta(seconds=130)
    else:
        assert decoded['orders']==[] and decoded['matrix'] is None
    canonical=[{'id':'p','dispatch_ready_at':NOW,'dispatch_dwell_seconds':180}]
    publication=json.loads(bytes(JsonbDumper(Jsonb).dump(d.json_parameter(canonical))))
    assert d.stamp(publication[0]['dispatch_ready_at'])==NOW
    assert publication[0]['dispatch_dwell_seconds']==180

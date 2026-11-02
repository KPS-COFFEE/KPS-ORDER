from datetime import datetime,timedelta,timezone
import pytest
from app.journey_timing import capture,evaluate
T=datetime(2026,10,30,16,tzinfo=timezone.utc)

@pytest.mark.parametrize('city,total,pickup',[('حفر الباطن',45,20),('الخفجي',32,15),('رفحاء',32,15),('الذيبية',27,12),('قبة',27,12),('قبه',27,12),('النعيرية',27,12)])
def test_owner_city_budgets_sum_exactly(city,total,pickup):
    p=capture({'branch_city':city,'service':'delivery'})
    assert p['total_minutes']==total and p['pickup_cutoff_minutes']==pickup
    assert p['pickup_cutoff_seconds']+p['delivery_seconds']==p['total_seconds']

@pytest.mark.parametrize('data',[{'branch_city':'unknown'},{'branch_city':'الخفجي','service':'pickup'},{'branch_city':'الخفجي','delivery_period':'morning'}])
def test_no_inferred_policy_for_other_scopes(data):assert capture(data) is None

def order():return {'id':'o','service':'delivery','created_at':T,'preparation_status':'new','delivery_status':'pending','snapshot':{'delivery_journey_policy':capture({'branch_city':'حفر الباطن'})}}

def test_two_minutes_manager_limit_and_no_reassignment_reset():
    o=order();j=evaluate(o,{},[],T+timedelta(seconds=121))
    assert j['current_stage']=='manager_accept' and j['late']
    tasks=[{'created_at':T+timedelta(minutes=29),'status':'accepted'}]
    j=evaluate(o,{'preparation.accepted':T+timedelta(minutes=1)},tasks,T+timedelta(minutes=30))
    assert j['current_stage']=='dispatch_print' and j['late']
    assert j['deadline_at']==(T+timedelta(minutes=45)).isoformat()

def test_print_and_assignment_both_required_and_pickup_not_invented():
    j=evaluate(order(),{'preparation.accepted':T,'order.print_requested':T},[],T+timedelta(minutes=4))
    assert j['current_stage']=='dispatch_print'
    assert j['stages'][2]['completed_at'] is None

def test_completed_missing_stage_does_not_run_forever_or_blame_driver():
    o=order();o['completed_at']=T+timedelta(minutes=40);o['delivery_status']='delivered'
    j=evaluate(o,{},[],T+timedelta(hours=3))
    assert j['current_stage'] is None and j['elapsed_seconds']==2400
    assert j['stages'][0]['state']=='missing' and j['stages'][0]['late_seconds'] is None

def test_historical_orders_and_cancelled_orders_are_not_retroactively_rated():
    o=order();o['snapshot']={};assert evaluate(o,{},[],T) is None
    o=order();o['delivery_status']='cancelled'
    j=evaluate(o,{},[],T+timedelta(hours=3));assert not j['late'] and j['cancelled']


def test_later_pickup_state_never_reports_waiting_for_missing_acceptance_or_print():
    o=order();o['preparation_status']='handed_over'
    j=evaluate(o,{},[{'status':'picked_up','created_at':T,'picked_up_at':T+timedelta(minutes=10)}],T+timedelta(minutes=11))
    assert j['current_stage']=='delivery'
    assert j['stages'][0]['state']=='missing' and j['stages'][0]['late_seconds'] is None


def test_reassignment_preserves_original_dispatch_completion():
    o=order();points={'preparation.accepted':T,'order.print_requested':T+timedelta(minutes=3)}
    tasks=[{'status':'reassigned','created_at':T+timedelta(minutes=2)}, {'status':'accepted','created_at':T+timedelta(minutes=25)}]
    j=evaluate(o,points,tasks,T+timedelta(minutes=26))
    assert j['stages'][1]['completed_at']==(T+timedelta(minutes=3)).isoformat()
    assert j['stages'][1]['late_seconds']==0


def test_proven_pickup_status_advances_work_without_inventing_timestamp():
    o=order();o['preparation_status']='handed_over';o['delivery_status']='picked_up'
    j=evaluate(o,{},[{'status':'picked_up'}],T+timedelta(minutes=10))
    assert j['current_stage']=='delivery' and j['action_at']=='السائق'
    assert j['stages'][2]['state']=='missing' and j['stages'][2]['completed_at'] is None


def test_printed_journey_explains_missing_clocks_in_arabic_without_grading_cancelled():
    from app.reporting import v5_tables
    o=order();o['delivery_status']='cancelled'
    o['journey_timing']=evaluate(o,{},[],T+timedelta(hours=2))
    for view in ('compact','owner'):
        tables=v5_tables({'shift':{},'summary':{},'orders':[o]},view)
        rows=next(t['rows'] for t in tables if t['key']=='delivery_journey')
        assert len(rows)==4
        assert all(r[4]=='ملغي — لا تقييم' and r[5]=='—' for r in rows)


def test_printed_dispatch_decision_uses_recorded_arabic_choices():
    from app.reporting import v5_tables
    snap={'shift':{},'summary':{},'orders':[order()], 'journey_decisions':[{
        'order_id':'o','event_type':'dispatch.impact_accepted','created_at':T,
        'actor_name':'مدير التوصيل','details':{'reason':'all_drivers_busy',
        'reason_label':'جميع السائقين مشغولون','contact':'not_contacted',
        'contact_label':'لم يتم التواصل'}}]}
    for view in ('compact','owner'):
        rows=next(t['rows'] for t in v5_tables(snap,view) if t['key']=='journey_decisions')
        assert rows[0][3:6]==['اعتماد تقييم الإسناد','جميع السائقين مشغولون','لم يتم التواصل']


def test_report_retains_forecast_impact_as_estimates_in_both_print_views():
    from app.reporting import v5_tables
    snap={'shift':{},'summary':{},'orders':[order()], 'journey_decisions':[{
        'order_id':'o','event_type':'dispatch.impact_accepted','created_at':T,'details':{
        'status':'late','orders':[{'order_id':'existing','order_no':123,
        'deadline_at':(T+timedelta(minutes=45)).isoformat(),
        'baseline_completion_at':(T+timedelta(minutes=44)).isoformat(),
        'completion_at':(T+timedelta(minutes=48)).isoformat(),'added_lateness_seconds':180}]}}]}
    for view in ('compact','owner'):
        tables=v5_tables(snap,view)
        decisions=next(t for t in tables if t['key']=='journey_decisions')
        assert decisions['rows'][0][6]=='تأخر متوقع'
        forecast=next(t for t in tables if t['key']=='dispatch_forecasts')
        assert 'ليست أوقات تسليم فعلية' in forecast['title']
        assert len(forecast['rows'])==1 and forecast['rows'][0][-1]==3
        assert '123' in forecast['rows'][0][1]


def test_native_report_excludes_foreign_route_evidence_without_mutating_audit():
    from copy import deepcopy
    from app.reporting import scoped_journey_decisions,v5_tables
    estimates=[{'order_id':key,'order_no':key,'brand_id':brand,'branch_id':branch,
        'completion_at':T,'location':{'lat':1,'lng':2}} for key,brand,branch in
        [('allowed','a','one'),('other-brand','b','one'),('other-branch','a','two')]]
    raw=[{'order_id':'o','event_type':'dispatch.impact_accepted','created_at':T,
          'details':{'status':'late','orders':estimates,'matrix':[[0]],
          'origin':{'lat':1,'lng':2},'stops':estimates,'reason_label':'سبب موثق'}}]
    before=deepcopy(raw)
    copied=scoped_journey_decisions(raw,'a',['one'])
    assert raw==before
    assert [r['order_id'] for r in copied[0]['details']['orders']]==['allowed']
    assert set(copied[0]['details'])=={'status','orders','reason_label'}
    assert 'location' not in copied[0]['details']['orders'][0]
    # Rendering is also defensive for a previously supplied unsanitized snapshot.
    snap={'shift':{'brand_id':'a','branch_ids':['one']},'summary':{},'orders':[order()], 'journey_decisions':raw}
    for view in ('compact','owner'):
        table=next(t for t in v5_tables(snap,view) if t['key']=='dispatch_forecasts')
        assert len(table['rows'])==1 and 'allowed' in table['rows'][0][1]

"""Synthetic PostgreSQL station journey over one physical mixed-brand trip."""
import pytest
from app import db
from test_enbishan_dual_brand import dual,shared_dual


@pytest.mark.postgres
def test_second_pickup_mixed_customers_and_other_brand_return(shared_dual):
    (client,actor,post,_,order),_,driver,by_brand=shared_dual
    kps=order('kps');enbishan=order('enbishan')
    with db.tx() as c:
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES('station-k',%s,'dual-driver',%s,'picked_up','dual-manager',now())",(kps,by_brand['kps'][1]))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('station-e',%s,'dual-driver',%s,'assigned','dual-manager')",(enbishan,by_brand['enbishan'][1]))
    trip=post('driver','/api/driver-work-hours/depart',{'shift_id':by_brand['kps'][1],'branch_id':'kps-branch-1','order_ids':[kps]})['trip']
    base='/api/operations/map-services/trip-stops'
    arrival={'trip_id':trip['id'],'kind':'pickup','branch_id':'enbishan-test-1','task_ids':['station-e']}
    second=post('driver',base,arrival,'enbishan')['stop']
    assert post('driver',base,arrival,'enbishan')['stop']['id']==second['id']
    assert second['shift_id']==by_brand['enbishan'][1]
    post('driver',base+'/'+second['id']+'/depart',{},'enbishan')
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='picked_up',picked_up_at=now() WHERE id='station-e'")
        assert c.execute('SELECT count(*) n FROM driver_work_trip_orders WHERE trip_id=%s',(trip['id'],)).fetchone()['n']==2
    for brand,task in [('kps','station-k'),('enbishan','station-e')]:
        stop=post('driver',base,{'trip_id':trip['id'],'kind':'delivery','task_ids':[task]},brand)['stop']
        with db.tx() as c:c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE id=%s",(task,))
        # Even completed orders cannot close a trip with an open customer stop.
        post('driver','/api/driver-work-hours/'+trip['id']+'/return',{'branch_id':'enbishan-test-2'},status=409)
        post('driver',base+'/'+stop['id']+'/depart',{},brand)
    returned_stop=post('driver',base,{'trip_id':trip['id'],'kind':'return','branch_id':'enbishan-test-2'},'enbishan')['stop']
    returned=post('driver','/api/driver-work-hours/'+trip['id']+'/return',{'branch_id':'enbishan-test-2'})['trip']
    assert returned['return_branch_id']=='enbishan-test-2'
    assert returned['return_evidence']['return_brand_id']=='enbishan'
    with db.tx() as c:
        assert c.execute('SELECT departed_at FROM driver_trip_stops WHERE id=%s',(returned_stop['id'],)).fetchone()['departed_at'] is not None
        assert c.execute('SELECT count(*) n FROM driver_work_trips').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM driver_trip_stops WHERE trip_id=%s',(trip['id'],)).fetchone()['n']==4
    history=actor('manager','enbishan').get('/api/operations/map-services/history',params={'shift_id':by_brand['enbishan'][1]})
    assert history.status_code==200,history.text
    assert len(history.json()['trip_stops'])==4
    assert set(history.json()['physical_shift_ids'])=={by_brand['kps'][1],by_brand['enbishan'][1]}


@pytest.mark.postgres
def test_projected_owner_zone_inventory_keeps_current_membership_branch_scope(shared_dual):
    (client,actor,post,_,order),_,_,_=shared_dual
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]'::jsonb WHERE user_id='dual-owner' AND brand_id='enbishan'")
    response=actor('owner','enbishan').get('/api/operations/map-services/zones')
    assert response.status_code==200,response.text
    branches={row['branch_id'] for row in response.json()['zones']}
    assert 'enbishan-test-2' in branches
    assert 'enbishan-test-1' not in branches

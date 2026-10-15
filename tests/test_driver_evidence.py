import base64,io
from PIL import Image
import pytest
from app import db
from test_operations import ops
pytestmark=pytest.mark.postgres


def test_fuel_receipt_and_report_share_scoped_durable_evidence(ops):
    actor,post,order,shifts=ops;manager,sid=shifts()
    image=io.BytesIO();Image.new('RGB',(12,12),'white').save(image,'PNG')
    eid=post('driver','/api/driver-evidence/shifts/'+sid,{'content_type':'image/png','data_base64':base64.b64encode(image.getvalue()).decode()})['id']
    for who in ('driver','manager','owner'):assert actor(who).get('/api/driver-evidence/'+eid).status_code==200
    assert actor('driver2').get('/api/driver-evidence/'+eid).status_code==403
    assert actor('other').get('/api/driver-evidence/'+eid).status_code==404
    payload={'liters':'10','odometer_km':1010,'amount_halalas':2000,'station':'TEST station','receipt_id':eid,'fueled_at':'2026-10-15T15:00:00Z'}
    fuel=post('driver','/api/shifts/'+sid+'/refuel',payload,key='receipt-once')
    assert post('driver','/api/shifts/'+sid+'/refuel',payload,key='receipt-once')==fuel
    report=post('driver','/api/vehicle-maintenance/reports',{'branch_id':'ops-branch','category':'delay','vehicle_description':'TEST car','description':'TEST delay','shift_id':sid,'evidence_id':eid})['report']
    assert report['department']=='delivery'
    assert actor('manager').get('/api/vehicle-maintenance/reports/'+report['id']).status_code==200
    post('driver','/api/vehicle-maintenance/reports/'+report['id']+'/status',{'status':'resolved','version':1,'note':'not allowed'},status=403)
    post('manager','/api/vehicle-maintenance/reports/'+report['id']+'/status',{'status':'in_progress','version':1,'note':'reviewing'})
    with db.tx() as c:
        log=c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()['vehicle_log']
        assert len(log['refuels'])==1 and log['refuels'][0]['receipt_id']==eid
        assert c.execute('SELECT count(*) n FROM driver_evidence').fetchone()['n']==1


def test_new_driver_records_survive_full_restore(ops,monkeypatch):
    import os
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from app.exports import build_archive,restore_archive
    from test_driver_breaks import configure
    from test_smart_dispatch import enable
    actor,post,order,shifts=ops;manager,sid=shifts();configure(actor)
    bid=post('driver','/api/driver-breaks',{'shift_id':sid,'reason':'prayer','minutes':5})['break_id']
    post('driver','/api/driver-breaks/'+bid,{'action':'end'})
    image=io.BytesIO();Image.new('RGB',(12,12),'white').save(image,'PNG')
    eid=post('driver','/api/driver-evidence/shifts/'+sid,{'content_type':'image/png','data_base64':base64.b64encode(image.getvalue()).decode()})['id']
    saved_image=actor('driver').get('/api/driver-evidence/'+eid).content
    report=post('driver','/api/vehicle-maintenance/reports',{'branch_id':'ops-branch','category':'delay','vehicle_description':'TEST car','description':'TEST report','shift_id':sid,'evidence_id':eid})['report']
    enable(monkeypatch);oid=order()
    fid=post('manager','/api/dispatch/offers',{'order_id':oid,'driver_shift_id':sid})['offer_id']
    post('driver','/api/dispatch/offers/'+fid+'/decision',{'decision':'accept'})
    with db.tx() as c:contents,manifest=build_archive(c,full=True)
    target='kps_restore_'+uuid4().hex;url=os.environ['TEST_DATABASE_URL']
    try:
        restore_archive(contents,target_url=url,target_schema=target)
        with psycopg.connect(url) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert bytes(c.execute('SELECT data FROM driver_evidence WHERE id=%s',(eid,)).fetchone()[0])==saved_image
            assert c.execute('SELECT evidence_id FROM vehicle_reports WHERE id=%s',(report['id'],)).fetchone()[0]==eid
            assert c.execute('SELECT count(*) FROM driver_break_events WHERE break_id=%s',(bid,)).fetchone()[0]==2
            assert c.execute('SELECT count(*) FROM driver_break_policies').fetchone()[0]==1
            assert c.execute('SELECT status FROM dispatch_offers WHERE id=%s',(fid,)).fetchone()[0]=='accepted'
            assert c.execute('SELECT count(*) FROM dispatch_offer_events WHERE offer_id=%s',(fid,)).fetchone()[0]==2
            assert c.execute('SELECT count(*) FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()[0]==1
    finally:
        with psycopg.connect(url,autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))

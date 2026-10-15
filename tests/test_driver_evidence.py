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

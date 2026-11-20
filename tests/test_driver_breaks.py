import pytest
from app import db,driver_breaks
from test_operations import ops


def config(approval=False):
    return {'reasons':{r:{'default_minutes':5,'max_minutes':15,'approval_required':approval} for r in driver_breaks.REASONS},'warning_minutes':2,'driver_max_minutes':{}}


def test_break_policy_requires_explicit_bounded_durations():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):driver_breaks.Policy.model_validate({})
    p=config();p['reasons']['prayer']['default_minutes']=20
    with pytest.raises(ValidationError):driver_breaks.Policy.model_validate(p)


def configure(actor,approval=False):
    r=actor('manager').put('/api/driver-breaks/policy',json=config(approval),headers={'Idempotency-Key':'policy-'+str(approval)})
    assert r.status_code==200,r.text


@pytest.mark.postgres
def test_break_blocks_manual_assignment_overlap_and_auto_resume(ops):
    actor,post,order,shifts=ops;manager,sid=shifts();configure(actor)
    body={'shift_id':sid,'reason':'prayer','minutes':5}
    b=post('driver','/api/driver-breaks',body,key='break-once')
    assert post('driver','/api/driver-breaks',body,key='break-once')==b
    post('driver','/api/driver-breaks',body,status=409)
    oid=order();post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    post('manager','/api/shifts/'+sid+'/availability',{'available':True},status=409)
    post('driver','/api/shifts/'+sid+'/closing',status=409)
    post('driver2','/api/driver-breaks/'+b['break_id'],{'action':'end'},status=403)
    with db.tx() as c:c.execute("UPDATE driver_breaks SET ends_at=now()-interval '1 minute' WHERE id=%s",(b['break_id'],))
    assert actor('manager').get('/api/driver-breaks').json()['breaks'][0]['overdue']
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    post('driver','/api/driver-breaks/'+b['break_id'],{'action':'end'})
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid})
    post('driver','/api/driver-breaks',body,status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
        assert c.execute('SELECT count(*) n FROM driver_break_events').fetchone()['n']==2


@pytest.mark.postgres
def test_approval_limits_extension_and_scope(ops):
    actor,post,order,shifts=ops;manager,sid=shifts();configure(actor,True)
    body={'shift_id':sid,'reason':'short','minutes':16}
    post('driver','/api/driver-breaks',body,status=422)
    body['minutes']=5;b=post('driver','/api/driver-breaks',body)
    assert b['status']=='requested'
    post('driver','/api/driver-breaks/'+b['break_id'],{'action':'approve'},status=403)
    post('other','/api/driver-breaks/'+b['break_id'],{'action':'approve'},status=404)
    post('manager','/api/driver-breaks/'+b['break_id'],{'action':'approve','minutes':10})
    post('manager','/api/driver-breaks/'+b['break_id'],{'action':'extend','minutes':16},status=422)
    post('manager','/api/driver-breaks/'+b['break_id'],{'action':'extend','minutes':15,'note':'authorized'})
    post('manager','/api/driver-breaks/'+b['break_id'],{'action':'end'})
    assert actor('driver').get('/api/driver-breaks').json()['breaks']==[]

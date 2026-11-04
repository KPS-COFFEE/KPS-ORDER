import pytest
from app import db
from test_operations import ops

pytestmark=pytest.mark.postgres

def test_driver_choices_follow_order_scope_and_live_eligibility(ops):
    actor,post,order,shifts=ops
    shifts()
    oid=order('delivery')
    def choices():
        result=actor('manager').get('/api/shifts',params={'order_id':oid})
        assert result.status_code==200,result.text
        return result.json()['shifts']
    rows=choices()
    assert rows and all(r['kind']=='driver' and r['status']=='open' for r in rows)
    sid=rows[0]['id']
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('scope-kfj',2,'scope-kfj','ops-brand','TEST KFJ','الخفجي','{}')")
        c.execute("UPDATE shifts SET branch_ids='[\"scope-kfj\"]' WHERE id=%s",(sid,))
    assert sid not in [r['id'] for r in choices()]
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\"]',available=false WHERE id=%s",(sid,))
    assert sid not in [r['id'] for r in choices()]
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    with db.tx() as c:c.execute('UPDATE shifts SET available=true WHERE id=%s',(sid,))
    assert sid in [r['id'] for r in choices()]
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid})['task']
    result=actor('manager').get('/api/shifts',params={'task_id':task['id']})
    assert result.status_code==200,result.text
    assert sid not in [r['id'] for r in result.json()['shifts']]
    assert actor('other').get('/api/shifts',params={'order_id':oid}).status_code==404

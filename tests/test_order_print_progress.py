"""Operator paper confirmation is separate from Windows spool acknowledgement."""
from uuid import uuid4

import pytest

from app import db
from test_operations import ops
from test_branch_printing import approve_test_device


def setup_job(actor,post,order):
    oid=order()
    pair=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Synthetic branch output'})
    response=actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Test printer'})
    token=response.json()['token'];device=pair['id']
    approve_test_device(actor,post,device,token)
    job=post('manager','/api/printing/orders/'+oid,{'device_id':device,'kind':'order'})['id']
    return oid,job,{'Authorization':'Bearer '+token}


@pytest.mark.postgres
def test_paper_output_requires_actual_operator_confirmation_and_keeps_order_state(ops):
    actor,post,order,_=ops;oid,job,headers=setup_job(actor,post,order)
    path='/api/printing/orders/'+oid+'/output'
    payload={'job_id':job,'version':1,'paper_confirmed':True}
    post('manager',path,payload,status=409)
    claim=actor('customer').post('/api/printing/bridge/next',json={},headers=headers)
    assert claim.json()['job']['id']==job
    post('manager',path,payload,status=409)
    assert actor('customer').post('/api/printing/bridge/jobs/'+job+'/ack',json={'status':'submitted'},headers=headers).status_code==200
    view=actor('manager').get(path)
    assert view.status_code==200 and view.json()['jobs'][0]['confirmed_at'] is None
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'",(oid,)).fetchone()
    post('manager',path,{**payload,'paper_confirmed':False},status=422)
    post('driver',path,payload,status=403);post('customer',path,payload,status=403)
    post('other',path,payload,status=404)
    key=uuid4().hex
    result=post('manager',path,payload,key=key)
    assert post('manager',path,payload,key=key)==result
    post('manager',path,payload)
    assert actor('manager').get(path).json()['jobs'][0]['confirmed_at']
    with db.tx() as c:
        events=c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'",(oid,)).fetchall()
        assert len(events)==1 and events[0]['actor_id']=='ops-manager'
        assert events[0]['details']=={'job_id':job,'branch_id':'ops-branch','revision':0,'source':'operator_paper_confirmation'}
        assert c.execute('SELECT preparation_status,version FROM orders WHERE id=%s',(oid,)).fetchone()=={'preparation_status':'ready','version':1}


@pytest.mark.postgres
@pytest.mark.parametrize('invalid', ['other_branch','preparation','uncertain','old_revision','stale_version','closed'])
def test_invalid_print_evidence_cannot_move_customer_progress(ops,invalid):
    actor,post,order,_=ops;oid,job,headers=setup_job(actor,post,order)
    path='/api/printing/orders/'+oid+'/output'
    payload={'job_id':job,'version':1,'paper_confirmed':True};status=409
    claim=actor('customer').post('/api/printing/bridge/next',json={},headers=headers)
    assert claim.status_code==200 and claim.json()['job']['id']==job
    assert actor('customer').post('/api/printing/bridge/jobs/'+job+'/ack',json={'status':'submitted'},headers=headers).status_code==200
    with db.tx() as c:
        if invalid=='other_branch':
            c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('output-second',2,'output-second','ops-brand','Other','حفر الباطن','{}')")
            c.execute("UPDATE branch_print_jobs SET branch_id='output-second' WHERE id=%s",(job,));status=404
        elif invalid=='preparation':c.execute("UPDATE branch_print_jobs SET kind='preparation' WHERE id=%s",(job,))
        elif invalid=='uncertain':c.execute("UPDATE branch_print_jobs SET status='uncertain' WHERE id=%s",(job,))
        elif invalid=='old_revision':c.execute("UPDATE branch_print_jobs SET payload=payload||'{\"order_revision\":-1}'::jsonb WHERE id=%s",(job,))
        elif invalid=='stale_version':payload['version']=2
        elif invalid=='closed':c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status='cancelled' WHERE id=%s",(oid,))
    post('manager',path,payload,status=status)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'",(oid,)).fetchone()

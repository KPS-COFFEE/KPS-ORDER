"""Manager alert decisions are scoped, audited and independent of order progress."""
import pytest
from uuid import uuid4
from app import db
from test_operations import ops
from test_order_print_progress import setup_job


def prepare(ops):
    actor, post, order, _ = ops
    oid, job, headers = setup_job(actor, post, order)
    assert actor('customer').post('/api/printing/bridge/next',json={},headers=headers).json()['job']['id']==job
    assert actor('customer').post('/api/printing/bridge/jobs/'+job+'/ack',json={'status':'uncertain'},headers=headers).status_code==200
    return actor, post, oid, job, headers


@pytest.mark.postgres
def test_confirm_scope_audit_replay_no_order_change(ops):
    actor, post, oid, job, _ = prepare(ops)
    path=f'/api/printing/jobs/{job}/resolve-alert'
    body={'action':'confirmed','reason':'Branch checked paper','paper_checked':True}
    for who, status in [('driver',403),('customer',403),('branch',403),('other',404)]:
        post(who,path,body,status=status)
    post('manager',path,{**body,'paper_checked':False},status=422)
    post('manager',path,{**body,'reason':' '},status=422)
    key=uuid4().hex
    result=post('manager',path,body,key=key)
    assert post('manager',path,body,key=key)==result
    post('manager',path,body,status=409)
    assert job not in [r['id'] for r in actor('manager').get('/api/operations/alerts').json()['printing']]
    with db.tx() as c:
        assert c.execute('SELECT preparation_status,version FROM orders WHERE id=%s',(oid,)).fetchone()=={'preparation_status':'ready','version':1}
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='printer.alert.confirmed'",(oid,)).fetchone()['n']==1
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'",(oid,)).fetchone()


@pytest.mark.postgres
def test_escalation_visible_to_owner_then_confirm(ops):
    actor,post,oid,job,_=prepare(ops)
    path=f'/api/printing/jobs/{job}/resolve-alert'
    result=post('manager',path,{'action':'escalated','reason':'Printer offline'})
    assert result['actor_id']=='ops-manager'
    for who in ('owner','manager'):
        alert=next(r for r in actor(who).get('/api/operations/alerts').json()['printing'] if r['id']==job)
        assert alert['alert_resolution']['reason']=='Printer offline'
    post('owner',path,{'action':'confirmed','reason':'Resolved with branch','paper_checked':True})


@pytest.mark.postgres
def test_reprint_atomic_replay_marked_and_stays_visible(ops):
    actor,post,oid,job,_=prepare(ops)
    path=f'/api/printing/jobs/{job}/resolve-alert'
    body={'action':'reprint','reason':'No paper at branch','paper_checked':True}
    key=uuid4().hex
    result=post('manager',path,body,key=key)
    assert post('manager',path,body,key=key)==result
    post('manager',path,body,status=409)
    with db.tx() as c:
        rows=c.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchall()
        assert len(rows)==2
        replacement=next(r for r in rows if r['id']==result['replacement_id'])
        assert replacement['payload']['lines'][0]['text']=='REPRINT'
        assert replacement['status']=='queued'
    assert result['replacement_id'] in [r['id'] for r in actor('manager').get('/api/operations/alerts').json()['printing']]


@pytest.mark.postgres
@pytest.mark.parametrize('invalid',['claimed','closed','revision','revoked','scope','permission'])
def test_reprint_rejects_unsafe_jobs(ops,invalid):
    actor,post,oid,job,_=prepare(ops)
    status=409
    with db.tx() as c:
        if invalid=='claimed': c.execute("UPDATE branch_print_jobs SET status='claimed' WHERE id=%s",(job,))
        if invalid=='closed': c.execute("UPDATE orders SET delivery_status='delivered' WHERE id=%s",(oid,))
        if invalid=='revision': c.execute("UPDATE branch_print_jobs SET payload=payload||'{\"order_revision\":-1}'::jsonb WHERE id=%s",(job,))
        if invalid=='revoked': c.execute('UPDATE branch_print_devices SET revoked_at=now() WHERE id=(SELECT device_id FROM branch_print_jobs WHERE id=%s)',(job,))
        if invalid=='scope':
            c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'");status=404
        if invalid=='permission':
            c.execute("UPDATE users SET permission_denials='[\"orders_print\"]'::jsonb WHERE id='ops-manager'");status=403
    post('manager',f'/api/printing/jobs/{job}/resolve-alert',{'action':'reprint','reason':'Checked','paper_checked':True},status=status)

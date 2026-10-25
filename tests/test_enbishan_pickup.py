"""Actual PostgreSQL pickup: no branch employee, real driver/photo still required."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app import db
from test_operations import ops


@pytest.mark.postgres
def test_legacy_enbishan_pickup_requires_actual_driver_photo_and_preserves_snapshot(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = order()
    task = post('manager', '/api/orders/'+oid+'/assign', {'driver_shift_id': driver})['task']['id']
    post('driver', '/api/tasks/'+task+'/transition', {'status': 'accepted'})
    with db.tx() as c:
        # Move the isolated fixture's complete, consistent scope into Enbishan.
        c.execute("INSERT INTO brands(id,name) VALUES('enbishan','Enbishan test') ON CONFLICT(id) DO NOTHING")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{}') ON CONFLICT(brand_id) DO UPDATE SET settings='{}'")
        c.execute("UPDATE branches SET brand_id='enbishan',branch_no=901 WHERE id='ops-branch'")
        c.execute("UPDATE users SET brand_id='enbishan' WHERE brand_id='ops-brand'")
        c.execute("UPDATE shifts SET brand_id='enbishan' WHERE brand_id='ops-brand'")
        c.execute("UPDATE orders SET brand_id='enbishan',preparation_status='accepted',snapshot=snapshot||%s WHERE id=%s",
                  (db.json({'delivery_workflow':'branch_fulfillment'}),oid))
        original = deepcopy(c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot'])

    path = '/api/tasks/'+task+'/transition'
    # A missing photo cannot record either a pickup or a simulated handover.
    post('driver', path, {'status':'picked_up'}, status=422)
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='accepted'
        assert c.execute('SELECT picked_up_at FROM driver_tasks WHERE id=%s',(task,)).fetchone()['picked_up_at'] is None
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.handed_over'",(oid,)).fetchone()['n']==0

    # Restoring an explicit branch policy immediately revokes the bridge.
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=%s WHERE brand_id='enbishan'",(db.json({'delivery_workflow':'branch_fulfillment'}),))
    denied=post('driver',path,{'status':'picked_up'},status=409)
    assert denied['detail']['code']=='BRANCH_HANDOVER_REQUIRED'
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings='{}' WHERE brand_id='enbishan'")

    post('driver2',path,{'status':'picked_up'},status=403)
    post('owner',path,{'status':'picked_up'},status=409)
    post('manager',path,{'status':'picked_up'},status=409)

    # Fixture uploads an actual test image for this driver's physical pickup.
    key=uuid4().hex
    picked=post('driver',path,{'status':'picked_up'},key=key)
    assert picked['task']['status']=='picked_up'
    with db.tx() as c:
        stored=c.execute('SELECT preparation_status,snapshot FROM orders WHERE id=%s',(oid,)).fetchone()
        assert stored['preparation_status']=='handed_over'
        assert stored['snapshot']==original
        assert c.execute('SELECT picked_up_at FROM driver_tasks WHERE id=%s',(task,)).fetchone()['picked_up_at'] is not None
        assert c.execute("SELECT count(*) n FROM delivery_photos WHERE task_id=%s AND stage='picked_up' AND used_at IS NOT NULL",(task,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='preparation.handed_over'",(oid,)).fetchall()
        assert len(events)==1
        assert events[0]['details']['policy_scope']=='enbishan_unpicked_delivery'
        assert events[0]['details']['frozen_workflow']=='branch_fulfillment'

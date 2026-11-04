"""Photo reuse is an administrative observation, never a delivery blocker."""
import base64
import io

import pytest
from PIL import Image
from app import db
from test_operations import ops


def image_body(stage, color='white'):
    data=io.BytesIO()
    Image.new('RGB',(24,24),color).save(data,'PNG')
    return {'stage':stage,'content_type':'image/png','data_base64':base64.b64encode(data.getvalue()).decode()}


def journey(ops, delivery_color='white'):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    pickup=post('driver',f'/api/tasks/{tid}/photos',image_body('picked_up'))['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up','photo_id':pickup})
    # An abandoned same-image upload must not itself create an observation.
    post('driver',f'/api/tasks/{tid}/photos',image_body('delivered'))
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM order_events WHERE event_type='driver_evidence.observation'").fetchone()
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    delivery=post('driver',f'/api/tasks/{tid}/photos',image_body('delivered',delivery_color))['id']
    done={'status':'delivered','photo_id':delivery}
    first=post('driver',f'/api/tasks/{tid}/transition',done,key='photo-delivery-once')
    assert post('driver',f'/api/tasks/{tid}/transition',done,key='photo-delivery-once')==first
    return oid,tid


@pytest.mark.postgres
def test_same_used_image_records_one_nonblocking_open_observation_and_read(ops):
    actor,post,order,shifts=ops
    oid,tid=journey(ops)
    data=actor('manager').get(f'/api/orders/{oid}/delivery-photos').json()
    assert len(data['photos'])==2 and len(data['observations'])==1
    note=data['observations'][0]
    assert note['read_at'] is None and note['details']['status']=='open'
    assert note['details']['driver_id']=='ops-driver'
    assert note['details']['kind']=='same_image_across_stages'
    with db.tx() as c:
        assert c.execute('SELECT status FROM driver_tasks WHERE id=%s',(tid,)).fetchone()['status']=='delivered'
        assert c.execute('SELECT delivery_status,payment_status FROM orders WHERE id=%s',(oid,)).fetchone()=={'delivery_status':'delivered','payment_status':'paid'}
        assert c.execute("SELECT count(*) n FROM order_events WHERE event_type='driver_evidence.observation'").fetchone()['n']==1
    url=f"/api/orders/{oid}/evidence-observations/{note['id']}/read"
    a=post('manager',url,key='read-note')
    assert post('manager',url,key='read-note')==a
    b=post('owner',url,key='read-again-different-actor')
    assert b['observation']['read_by']=='ops-manager'
    assert b['observation']['read_at'] and b['observation']['read_by_name']=='manager'
    assert b['observation']['details']['status']=='open'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE event_type='driver_evidence.observation_read'").fetchone()['n']==1
        assert c.execute("SELECT details FROM order_events WHERE id=%s",(note['id'],)).fetchone()['details']==note['details']
    assert not any(e['event_type'].startswith('driver_evidence.') for e in actor('customer').get(f'/api/orders/{oid}').json()['events'])


@pytest.mark.postgres
def test_different_used_image_and_unused_same_image_do_not_raise_observation(ops):
    actor,post,order,shifts=ops
    oid,tid=journey(ops,'black')
    data=actor('owner').get(f'/api/orders/{oid}/delivery-photos').json()
    assert data['observations']==[] and len(data['photos'])==2


@pytest.mark.postgres
def test_observation_read_is_manager_only_brand_branch_and_order_scoped(ops):
    actor,post,order,shifts=ops
    oid,tid=journey(ops)
    note=actor('manager').get(f'/api/orders/{oid}/delivery-photos').json()['observations'][0]
    url=f"/api/orders/{oid}/evidence-observations/{note['id']}/read"
    for who in ['customer','driver','branch']:
        post(who,url,status=403)
    post('other',url,status=404)
    assert actor('other').get(f'/api/orders/{oid}/delivery-photos').status_code==404
    assert actor('customer').get(f'/api/orders/{oid}/delivery-photos').status_code==403
    another=order()
    post('owner',f"/api/orders/{another}/evidence-observations/{note['id']}/read",status=404)
    post('manager',url,key='read-before-branch-revoked')
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',url,key='read-before-branch-revoked',status=404)
    post('manager',url,status=404)
    assert actor('manager').get(f'/api/orders/{oid}/delivery-photos').status_code==404

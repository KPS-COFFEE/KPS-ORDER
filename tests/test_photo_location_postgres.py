from datetime import datetime, timezone
from uuid import uuid4
import pytest
from app import db
from test_operations import ops
from test_order_support import image_body

pytestmark=pytest.mark.postgres


def test_capture_metadata_is_persisted_scoped_and_does_not_block_missing_gps(ops):
    actor,post,order,shifts=ops
    _,sid=shifts();oid=order()
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    now=datetime.now(timezone.utc).isoformat()
    body={**image_body('picked_up'),'capture_location':{'captured_at':now,'source':'browser_camera',
        'lat':28.4,'lng':45.9,'accuracy_m':3000,'location_at':now}}
    pid=post('driver',f'/api/tasks/{tid}/photos',body,key='capture-once')['id']
    assert post('driver',f'/api/tasks/{tid}/photos',body,key='capture-once')['id']==pid
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up','photo_id':pid})
    route=f'/api/orders/{oid}/delivery-photos'
    for who in ('manager','owner'):
        metadata=actor(who).get(route).json()['photos'][0]['capture_location']
        assert metadata['status']=='weak' and metadata['accuracy_m']==3000
    assert 'capture_location' not in actor('driver').get(route).json()['photos'][0]
    assert actor('other').get(route).status_code==404
    # Missing GPS is explicitly persisted and never prevents uploading evidence.
    missing=post('driver',f'/api/tasks/{tid}/photos',image_body('delivered'))['id']
    with db.tx() as c:
        assert c.execute('SELECT capture_location FROM delivery_photos WHERE id=%s',(missing,)).fetchone()['capture_location']['status']=='missing'
        c.execute('UPDATE delivery_photos SET data=NULL,deleted_at=now() WHERE id=%s',(pid,))
    assert actor('manager').get(route).json()['photos'][0]['capture_location']['status']=='weak'
    # Changing location under the same retry key cannot silently rewrite proof.
    body['capture_location']['accuracy_m']=10
    response=actor('driver').post(f'/api/tasks/{tid}/photos',json=body,headers={'Idempotency-Key':'capture-once'})
    assert response.status_code==409

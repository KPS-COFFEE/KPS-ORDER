import base64
import pytest
from app import db
from test_enbishan_dual_brand import dual
from tests.test_driver_order_sounds import replacement

pytestmark=pytest.mark.postgres


def test_shared_driver_gets_the_task_brands_distinct_ring_and_revocation_is_live(dual):
    _,actor,_,_,_=dual
    driver=actor('driver')
    kps=driver.get('/api/operations/driver-order-sound?brand_id=kps').json()['sound']
    en=driver.get('/api/operations/driver-order-sound?brand_id=enbishan').json()['sound']
    assert kps['brand_id']=='kps' and en['brand_id']=='enbishan' and kps['sha256']!=en['sha256']
    assert driver.get(kps['path']).status_code==200 and driver.get(en['path']).status_code==200
    assert driver.get('/api/operations/driver-order-sound?brand_id=foreign').status_code==404
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    assert driver.get(en['path']).status_code==404
    assert driver.get('/api/operations/driver-order-sound?brand_id=enbishan').status_code==404
    assert driver.get(kps['path']).status_code==200


def test_owner_can_change_each_brand_independently_and_staff_cannot(dual):
    _,actor,_,_,_=dual
    owner=actor('owner')
    kps=owner.get('/api/operations/driver-order-sound?brand_id=kps').json()['sound']
    en=owner.get('/api/operations/driver-order-sound?brand_id=enbishan').json()['sound']
    body={'name':'Synthetic Enbishan sound','data_base64':base64.b64encode(replacement()).decode()}
    response=owner.patch('/api/admin/driver-order-sound?brand_id=enbishan',json=body,headers={'Idempotency-Key':'same-sound-key'})
    assert response.status_code==200,response.text
    assert response.json()['sound']['brand_id']=='enbishan'
    assert owner.get('/api/operations/driver-order-sound?brand_id=kps').json()['sound']['sha256']==kps['sha256']
    assert owner.get('/api/operations/driver-order-sound?brand_id=enbishan').json()['sound']['sha256']!=en['sha256']
    # An identical owner/key in another brand must not replay the first brand response.
    response=owner.patch('/api/admin/driver-order-sound?brand_id=kps',json={'reset':True},headers={'Idempotency-Key':'same-sound-key'})
    assert response.status_code==200 and response.json()['sound']['brand_id']=='kps'
    driver=actor('driver')
    assert driver.patch('/api/admin/driver-order-sound?brand_id=enbishan',json={'reset':True},headers={'Idempotency-Key':'driver-forbidden'}).status_code==403
    owner=actor('owner')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    assert owner.patch('/api/admin/driver-order-sound?brand_id=enbishan',json={'reset':True},headers={'Idempotency-Key':'revoked-owner'}).status_code==404

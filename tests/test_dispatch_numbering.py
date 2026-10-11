"""Real database coverage for temporary dispatch and immutable daily references."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from app import db, order_numbering
from test_v5_operations import v5

pytestmark = pytest.mark.postgres


def shifts(call):
    manager=call('manager','POST','/api/shifts',{'kind':'manager','branch_ids':['v5-a']}).json()['shift']['id']
    return call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a'],'manager_shift_id':manager}).json()['shift']['id']


def test_temporary_delivery_without_branch_actions_and_full_mode_preserved(v5):
    actor,call,body,_=v5
    sid=shifts(call)
    assert call('manager','PATCH','/api/admin/settings',{'delivery_workflow':'manager_dispatch'}).status_code==403
    assert call('owner','PATCH','/api/admin/settings',{'delivery_workflow':'invalid'}).status_code==422
    assert call('owner','PATCH','/api/admin/settings',{'delivery_workflow':'manager_dispatch'}).status_code==200
    key=uuid4().hex
    created=call('customer','POST','/api/orders',body('delivery'),key)
    assert created.status_code==200,created.text
    order=created.json()['order'];oid=order['id']
    assert order['snapshot']['delivery_workflow']=='manager_dispatch'
    ref=order['snapshot']['order_reference']
    assert ref.startswith('HFR1-') and ref.endswith('-0001')
    assert call('customer','POST','/api/orders',body('delivery'),key).json()['order']['snapshot']['order_reference']==ref
    # Switching affects only new orders, not an in-flight customer promise.
    assert call('owner','PATCH','/api/admin/settings',{'delivery_workflow':'branch_fulfillment'}).status_code==200
    assert actor('manager').get('/api/orders',params={'search':ref}).json()['orders'][0]['id']==oid
    tid=call('manager','POST',f'/api/orders/{oid}/assign',{'driver_shift_id':sid}).json()['task']['id']
    assert call('driver','POST',f'/api/tasks/{tid}/transition',{'status':'accepted'}).status_code==200
    assert call('owner','POST',f'/api/tasks/{tid}/transition',{'status':'picked_up'}).status_code==403
    r=call('driver','POST',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    assert r.status_code==200,r.text
    assert call('driver','POST',f'/api/tasks/{tid}/transition',{'status':'delivered','proof':'TEST'}).status_code==409
    assert call('driver','POST',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1200,'tendered_halalas':1200}).status_code==200
    assert call('driver','POST',f'/api/tasks/{tid}/transition',{'status':'delivered','proof':'TEST ONLY'}).status_code==200
    full=call('customer','POST','/api/orders',body('delivery')).json()['order']
    assert full['snapshot']['delivery_workflow']=='branch_fulfillment'
    task=call('manager','POST',f"/api/orders/{full['id']}/assign",{'driver_shift_id':sid}).json()['task']['id']
    assert call('driver','POST',f'/api/tasks/{task}/transition',{'status':'accepted'}).status_code==200
    assert call('driver','POST',f'/api/tasks/{task}/transition',{'status':'picked_up'}).status_code==409
    with db.tx() as c:
        events=c.execute('SELECT event_type FROM order_events WHERE order_id=%s',(oid,)).fetchall()
        assert not any(e['event_type'] in ('preparation.accepted','preparation.ready') for e in events)
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']==order['snapshot']


def test_references_riyadh_midnight_branch_independence_and_concurrency(v5):
    def allocate(branch,stamp):
        with db.tx() as c:return order_numbering.allocate(c,branch,stamp)
    before=datetime(2026,10,11,20,59,59,tzinfo=timezone.utc)
    after=datetime(2026,10,11,21,0,0,tzinfo=timezone.utc)
    assert allocate('v5-a',before)=='HFR1-261011-0001'
    assert allocate('v5-b',before)=='HFR9-261011-0001'
    assert allocate('v5-a',after)=='HFR1-261012-0001'
    with ThreadPoolExecutor(max_workers=6) as pool:
        numbers=list(pool.map(lambda _:allocate('v5-a',after),range(12)))
    assert len(set(numbers))==12
    assert {int(n.rsplit('-',1)[1]) for n in numbers}==set(range(2,14))


def test_reference_rollback_and_no_four_digit_truncation(v5):
    stamp=datetime(2026,10,11,12,tzinfo=timezone.utc)
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            assert order_numbering.allocate(c,'v5-a',stamp).endswith('-0001')
            raise RuntimeError('TEST rollback')
    with db.tx() as c:
        assert order_numbering.allocate(c,'v5-a',stamp).endswith('-0001')
        c.execute("UPDATE order_daily_counters SET last_number=9999 WHERE branch_id='v5-a'")
        assert order_numbering.allocate(c,'v5-a',stamp)=='HFR1-261011-10000'

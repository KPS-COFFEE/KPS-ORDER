import pytest
from app import db
from test_operations import ops

pytestmark=pytest.mark.postgres

def test_manager_current_and_history_keep_pending_work_visible(ops):
    actor,post,order,shifts=ops
    pending=order('delivery')
    def rows(view,who='manager',**extra):
        r=actor(who).get('/api/orders',params={'manager_view':view,**extra})
        assert r.status_code==200,r.text
        return {o['id'] for o in r.json()['orders']}
    assert rows('current')==set()
    manager,driver=shifts()
    assert pending in rows('current',period='custom')
    assigned=order('delivery')
    post('manager','/api/orders/'+assigned+'/assign',{'driver_shift_id':driver})
    assert {pending,assigned}<=rows('current')
    cancelled=order('delivery')
    post('manager','/api/orders/'+cancelled+'/preparation',{'status':'cancelled','version':1,'reason':'TEST rejected'})
    assert cancelled in rows('current')
    assert cancelled not in rows('history')
    assert actor('other').get('/api/orders',params={'manager_view':'current'}).status_code==403
    # Synthetic closed records model the canonical final-closure result only.
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered' WHERE order_id=%s",(assigned,))
        c.execute("UPDATE orders SET delivery_status='delivered',preparation_status='completed',completed_at=now() WHERE id=%s",(assigned,))
        c.execute("UPDATE shifts SET status='closed',closed_at=now() WHERE id=ANY(%s)",([driver,manager],))
    assert rows('current')==set()
    assert {assigned,cancelled}<=rows('history')
    assert pending not in rows('history')
    new_manager,new_driver=shifts()
    assert new_manager!=manager
    assert rows('current')=={pending}
    assert {assigned,cancelled}<=rows('history')

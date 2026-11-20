"""Real scoped API responses, private evidence and order-specific pickup."""
import pytest
from app import db
from app.customer_progress import enrich_orders
from test_operations import ops


@pytest.mark.postgres
def test_customer_progress_print_trip_and_private_history(ops):
    actor, post, order, shifts = ops
    _, sid = shifts()
    first, second = order(), order()
    with db.tx() as c:
        # Fixture snapshots are immutable: insert manager workflow at creation
        # through a separate synthetic row, not a production mutation bypass.
        source=c.execute('SELECT * FROM orders WHERE id=%s',(first,)).fetchone()
        first='progress-manager-order'
        snapshot={**source['snapshot'],'delivery_workflow':'manager_dispatch'}
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','handed_over','picked_up','cash',1000,0,1000,%s,'{}')",(first,db.json(snapshot)))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES('progress-task',%s,'ops-driver',%s,'accepted','ops-manager',now())",(first,sid))
        db.event(c,first,'ops-manager','driver_evidence.observation',{'private':'driver observation'})
        db.event(c,first,'ops-manager','driver_evidence.observation_read',{'private':'read by employee'})
        db.event(c,first,'ops-manager','order.print_output_confirmed',{'branch_id':'ops-branch','revision':99})
        rows=c.execute('SELECT * FROM orders WHERE id=ANY(%s)',([first,second],)).fetchall()
        enrich_orders(c,rows)
        assert next(r for r in rows if r['id']==first)['customer_progress']['stage']=='received'
        db.event(c,first,'ops-manager','order.print_output_confirmed',{'branch_id':'ops-branch','revision':0})
    detail=actor('customer').get('/api/orders/'+first)
    assert detail.status_code==200,detail.text
    assert detail.json()['order']['customer_progress']['stage']=='preparing'
    assert not any(e['event_type'].startswith('driver_evidence.') for e in detail.json()['events'])
    # A trip containing only another order must never advance this one.
    with db.tx() as c:
        c.execute("INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,departure_branch_id,departure_evidence) VALUES('progress-trip','ops-brand',%s,'ops-driver','ops-branch','{}')",(sid,))
        c.execute("INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES('progress-trip',%s)",(second,))
    assert actor('customer').get('/api/orders/'+first).json()['order']['customer_progress']['stage']=='preparing'
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='picked_up',picked_up_at=now() WHERE id='progress-task'")
    detail=actor('customer').get('/api/orders/'+first).json()['order']
    assert detail['customer_progress']['stage']=='on_the_way'
    listing=actor('customer').get('/api/orders').json()['orders']
    assert next(r for r in listing if r['id']==first)['customer_progress']==detail['customer_progress']
    assert 'driver_id' not in detail['customer_progress']
    assert actor('other').get('/api/orders/'+first).status_code==404
    with db.tx() as c:
        c.execute("UPDATE orders SET delivery_status='delivered' WHERE id=%s",(first,))
    assert actor('customer').get('/api/orders/'+first).json()['order']['customer_progress']['stage']=='delivered'

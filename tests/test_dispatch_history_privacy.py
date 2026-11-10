"""Real HTTP order histories project nested road evidence without rewriting it."""
import pytest
from app import db
from test_operations import ops

pytestmark = pytest.mark.postgres


def test_order_history_and_financial_journey_hide_peer_orders_and_route_coordinates(ops):
    actor, post, order, shifts = ops
    _, sid = shifts()
    oid = order()
    post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': sid})
    original = {'status': 'within_target', 'reason_label': 'سبب موثق',
        'origin': {'lat': 28.123456, 'lng': 45.654321}, 'matrix': [[0, 10], [10, 0]],
        'stops': [{'order_id': 'foreign-private-order', 'location': {'lat': 28, 'lng': 45}}],
        'orders': [{'order_id': oid, 'order_no': 123, 'completion_at': '2026-10-30T10:00:00+00:00',
                    'location': {'lat': 28, 'lng': 45}, 'driver_id': 'ops-driver'},
                   {'order_id': 'foreign-private-order', 'order_no': 999,
                    'completion_at': '2026-10-30T11:00:00+00:00'}]}
    with db.tx() as c:
        db.event(c, oid, 'ops-manager', 'dispatch.impact_accepted', original)
        db.audit(c, 'ops-manager', 'dispatch.impact_accepted', 'order', oid, original)
    for role in ('owner', 'manager'):
        for path, key in ((f'/api/orders/{oid}', 'events'),
                          (f'/api/order-financial-cases/orders/{oid}/journey', 'timeline')):
            response = actor(role).get(path)
            assert response.status_code == 200, response.text
            event = next(e for e in response.json()[key] if e['event_type'] == 'dispatch.impact_accepted')
            assert event['details']['status'] == 'within_target'
            assert event['details']['reason_label'] == 'سبب موثق'
            assert [r['order_id'] for r in event['details']['orders']] == [oid]
            assert set(event['details']['orders'][0]) == {'order_id', 'order_no', 'completion_at'}
            assert not {'origin', 'matrix', 'stops'} & set(event['details'])
    for role in ('customer', 'driver'):
        response = actor(role).get(f'/api/orders/{oid}')
        assert response.status_code == 200, response.text
        assert all(not e['event_type'].startswith('dispatch.impact_') for e in response.json()['events'])
    response = actor('driver').get(f'/api/order-financial-cases/orders/{oid}/journey')
    assert response.status_code == 200, response.text
    assert all(not e['event_type'].startswith('dispatch.impact_') for e in response.json()['timeline'])
    with db.tx() as c:
        assert c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='dispatch.impact_accepted'", (oid,)).fetchone()['details'] == original
        assert c.execute("SELECT details FROM audit_events WHERE entity_id=%s AND action='dispatch.impact_accepted'", (oid,)).fetchone()['details'] == original

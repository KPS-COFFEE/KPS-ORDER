"""Customer milestones derived from recorded evidence, never inferred timestamps."""
from __future__ import annotations


def progress(order, *, printed=False, departed=False):
    preparation = order.get('preparation_status')
    delivery = order.get('delivery_status') or order.get('fulfillment_status')
    status = order.get('status')
    is_delivery = order.get('service') == 'delivery'
    manager = is_delivery and order.get('snapshot', {}).get('delivery_workflow', 'branch_fulfillment') == 'manager_dispatch'
    stages = ['received', 'preparing']
    if not manager:
        stages.append('ready')
    if is_delivery:
        stages.append('on_the_way')
    stages.append('delivered')
    current = 'received'
    if (manager and printed) or (not manager and preparation in {'preparing', 'ready', 'handed_over', 'completed'}):
        current = 'preparing'
    if not manager and preparation in {'ready', 'handed_over'}:
        current = 'ready'
    if is_delivery and departed:
        current = 'on_the_way'
    if delivery == 'delivered' or status == 'delivered' or preparation == 'completed' or status == 'completed':
        current = 'delivered'
    # Exceptions are shown separately; branch handover never means customer delivery.
    exception = None
    if 'cancelled' in {preparation, delivery, status} or 'rejected' in {preparation, delivery, status}:
        exception = 'cancelled'
    elif 'problem' in {preparation, delivery, status}:
        exception = 'problem'
    return {'stage': current, 'stages': stages, 'exception': exception}


def enrich_orders(conn, orders):
    """Two bounded queries for an already authorized page, not one per order."""
    if not orders:
        return
    ids = [order['id'] for order in orders]
    printed = {row['order_id'] for row in conn.execute(
        "SELECT DISTINCT e.order_id FROM order_events e JOIN orders o ON o.id=e.order_id "
        "WHERE e.order_id=ANY(%s) AND e.event_type='order.print_output_confirmed' "
        "AND e.details->>'branch_id'=o.branch_id "
        "AND COALESCE(e.details->>'revision','0')=COALESCE(o.amendment_revision,0)::text", (ids,)).fetchall()}
    departed = {row['order_id'] for row in conn.execute(
        "SELECT DISTINCT t.order_id FROM driver_tasks t "
        "JOIN orders o ON o.id=t.order_id "
        "JOIN shifts s ON s.id=t.shift_id AND s.brand_id=o.brand_id AND s.user_id=t.driver_id "
        "WHERE t.order_id=ANY(%s) AND t.picked_up_at IS NOT NULL "
        "AND t.status IN ('picked_up','delivered','problem')", (ids,)).fetchall()}
    for order in orders:
        order['customer_progress'] = progress(order, printed=order['id'] in printed, departed=order['id'] in departed)


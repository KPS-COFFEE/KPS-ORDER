"""Delivery policy defaults and bounded adoption of an owner-approved pickup mode.

Historical order snapshots remain immutable.  The legacy exception authorizes
only a real pickup by the currently assigned driver; the transition caller must
still verify the photo, amendment guards and transaction scope.
"""
from collections.abc import Mapping


VALID_WORKFLOWS = ('manager_dispatch', 'branch_fulfillment', 'branch_without_manager')


def configured_workflow(brand_id, settings):
    """Honor a saved mode; Enbishan operates without a branch employee by default."""
    configured = settings.get('delivery_workflow') if isinstance(settings, Mapping) else None
    if configured in VALID_WORKFLOWS:
        return configured
    return 'manager_dispatch' if brand_id == 'enbishan' else 'branch_fulfillment'


def is_legacy_manager_pickup(order, task, shift, user, settings):
    """Whether live Enbishan policy permits this assigned driver's first pickup.

    This predicate never updates status, snapshots, timestamps, evidence or money.
    Explicit branch workflows, morning duty and other brands remain unchanged.
    """
    snapshot = order.get('snapshot') or {}
    window = shift.get('delivery_window_snapshot') or {}
    if not isinstance(snapshot, Mapping) or not isinstance(window, Mapping):
        return False
    branch = order.get('branch_id')
    return bool(
        order.get('brand_id') == 'enbishan'
        and order.get('service') == 'delivery'
        and snapshot.get('delivery_workflow') in (None, 'branch_fulfillment')
        and snapshot.get('delivery_period') != 'morning'
        and window.get('period') != 'morning'
        and configured_workflow('enbishan', settings) == 'manager_dispatch'
        and order.get('preparation_status') in ('accepted', 'preparing', 'ready')
        and order.get('completed_at') is None
        and not order.get('test_void')
        and task.get('status') == 'accepted'
        and task.get('picked_up_at') is None
        and task.get('delivered_at') is None
        and shift.get('status') == 'open'
        and shift.get('kind') == 'driver'
        and shift.get('brand_id') == 'enbishan'
        and user.get('role') == 'driver'
        and user.get('brand_id') == 'enbishan'
        and bool(user.get('id'))
        and task.get('driver_id') == shift.get('user_id') == user['id']
        and bool(order.get('id'))
        and task.get('order_id') == order['id']
        and bool(shift.get('id'))
        and task.get('shift_id') == shift['id']
        and bool(branch)
        and branch in (shift.get('branch_ids') or [])
        and branch in (user.get('branch_ids') or [])
    )

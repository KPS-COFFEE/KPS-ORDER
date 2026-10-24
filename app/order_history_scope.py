"""Read-only morning history requires a recorded connection to that duty."""
from . import db


def allowed(c,user,order):
    context=user.get('_morning_manager_context') or {}
    sid=context.get('shift_id')
    historical_read=user.get('_morning_history_read')
    cancelled_replay=(user.get('_morning_cancellation_replay') or user.get('_morning_cancelled_incentive_review')) and order and order.get('preparation_status')=='cancelled'
    if not ((historical_read or cancelled_replay) and sid and order
            and order['brand_id']==user['brand_id'] and order['branch_id'] in user['branch_ids']
            and order['service']=='delivery' and order['snapshot'].get('delivery_period')=='morning'):
        return False
    return bool(c.execute("""SELECT 1 WHERE
        EXISTS(SELECT 1 FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id
          WHERE t.order_id=%s AND s.manager_shift_id=%s AND s.brand_id=%s
          AND s.kind='driver' AND s.branch_ids <@ %s::jsonb)
        OR EXISTS(SELECT 1 FROM order_events e WHERE e.order_id=%s
          AND e.event_type IN ('preparation.accepted','order.operationally_cancelled')
          AND e.details->>'manager_shift_id'=%s)
        OR EXISTS(SELECT 1 FROM manager_handovers h WHERE h.status='accepted'
          AND (h.from_shift_id=%s OR h.to_shift_id=%s)
          AND h.operational_snapshot->'order_ids' ? %s)
        OR EXISTS(SELECT 1 FROM driver_support_requests r WHERE r.status='accepted'
          AND (r.from_shift_id=%s OR r.to_shift_id=%s)
          AND r.operational_snapshot->'order_ids' ? %s)
        """,(order['id'],sid,user['brand_id'],db.json(user['branch_ids']),
              order['id'],sid,sid,sid,order['id'],sid,sid,order['id'])).fetchone())

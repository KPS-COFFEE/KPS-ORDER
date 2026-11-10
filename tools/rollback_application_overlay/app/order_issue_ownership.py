"""Current operational duty for unresolved issues, never a permission grant.

The immutable request and accepted handover history stay intact. Read projections
follow the live task's manager after an accepted duty or custody transfer.
"""


_CURRENT_MANAGER_SQL = """SELECT candidates.shift_id,duty.user_id manager_user_id FROM (
            SELECT ds.manager_shift_id shift_id,0 priority,t.created_at observed
              FROM effective_driver_tasks t JOIN shifts ds ON ds.id=t.shift_id
              WHERE t.order_id=o.id AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')
            UNION ALL
            SELECT h.to_shift_id,1,h.accepted_at FROM manager_handovers h
              WHERE h.status='accepted' AND h.operational_snapshot->'order_ids' ? o.id
            UNION ALL
            SELECT e.details->>'manager_shift_id',2,e.created_at FROM order_events e
              WHERE e.order_id=o.id AND e.event_type='preparation.accepted'
            UNION ALL
            SELECT o.snapshot->>'manager_shift_id',3,o.created_at
          ) candidates JOIN shifts duty ON duty.id=candidates.shift_id
          WHERE duty.kind='manager' AND duty.status IN ('open','closing') AND duty.brand_id=o.brand_id
            AND duty.branch_ids ? o.branch_id
          ORDER BY candidates.priority,candidates.observed DESC,candidates.shift_id LIMIT 1"""


def current_manager_candidate_sql():
    """Trusted lateral SQL shared with the pre-pagination scoped inbox filter."""
    return _CURRENT_MANAGER_SQL


def current_managers(c, order_ids):
    if not order_ids:
        return {}
    rows = c.execute(f"""SELECT o.id order_id,o.brand_id,o.branch_id,s.*,
            s.id manager_shift_id,s.user_id manager_user_id,u.display_name manager_name,
            ARRAY(WITH RECURSIVE prior(shift_id) AS (
              SELECT h.from_shift_id FROM manager_handovers h
              WHERE h.to_shift_id=s.id AND h.status='accepted'
                AND h.operational_snapshot->'order_ids' ? o.id
              UNION
              SELECT h.from_shift_id FROM manager_handovers h JOIN prior p ON h.to_shift_id=p.shift_id
              WHERE h.status='accepted' AND h.operational_snapshot->'order_ids' ? o.id
            ) SELECT DISTINCT previous.user_id FROM prior p JOIN shifts previous ON previous.id=p.shift_id
            ) handover_predecessor_user_ids
        FROM orders o
        JOIN LATERAL (
          {_CURRENT_MANAGER_SQL}
        ) live ON true
        JOIN shifts s ON s.id=live.shift_id JOIN users u ON u.id=s.user_id
        WHERE o.id=ANY(%s) AND u.active AND u.role IN ('owner','delivery_manager')""", (list(order_ids),)).fetchall()
    # Reuse canonical live membership and morning-policy validation; metadata
    # must not name a revoked or out-of-scope manager. Cache by shift/branch.
    from .manager_handover_scope import authorized
    checked={};result={}
    for row in rows:
        key=row['manager_shift_id']
        if key not in checked:checked[key]=authorized(c,row,row['branch_ids'])
        if checked[key]:
            result[row['order_id']]={k:row[k] for k in ('manager_shift_id','manager_user_id','manager_name','handover_predecessor_user_ids')}
    return result


def with_current_manager(flow, manager, *, user=None, kind=None):
    """A returned target changes only along its accepted, order-bound handover."""
    if not manager or flow.get('current_responsible_role') != 'delivery_manager':
        return flow
    previous=flow.get('current_responsible_user_id')
    if previous and previous!=manager['manager_user_id'] and previous not in manager.get('handover_predecessor_user_ids',[]):
        return flow
    projected={**flow, 'current_responsible_user_id':manager['manager_user_id'],
            'current_responsible_name':manager['manager_name'],
            'current_responsible_shift_id':manager['manager_shift_id']}
    if previous and previous!=manager['manager_user_id']:
        projected['handover_from_user_id']=previous
        if flow.get('status')=='returned':
            projected['preferred_return_target_id']=manager['manager_user_id']
            actions=[a for a in flow.get('available_actions',[]) if a!='resubmit']
            from . import auth
            if user and (user.get('role')=='owner' or user.get('role')=='delivery_manager'
                    and user.get('id')==manager['manager_user_id']
                    and auth.has_permission(user,'orders_cancel' if kind=='cancel' else 'orders_view')):
                actions.append('resubmit')
            projected['available_actions']=actions
    return projected

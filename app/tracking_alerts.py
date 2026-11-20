"""Evidence-based staff alerts with a validated road reference corridor."""
from datetime import timedelta
from . import catalog


def movement_alerts(points, target, expected_at, now):
    recent = sorted((p for p in points if p['accuracy_m'] <= 100 and
                     now-timedelta(minutes=10) <= p['captured_at'] <= now),
                    key=lambda p: p['captured_at'])
    if len(recent) < 2 or (now-recent[-1]['captured_at']).total_seconds() > 120:
        return []
    alerts = []
    window = [p for p in recent if p['captured_at'] >= now-timedelta(minutes=5)]
    # Require sustained observations, not two points spanning a GPS outage.
    continuous = (len(window) >= 4 and
                  (window[-1]['captured_at']-window[0]['captured_at']).total_seconds() >= 240 and
                  all((b['captured_at']-a['captured_at']).total_seconds() <= 120
                      for a,b in zip(window,window[1:])))
    if continuous and all(catalog.direct_distance(window[0],p) <= 100 for p in window):
        alerts.append({'kind':'long_stop', 'observed_seconds':round(
            (window[-1]['captured_at']-window[0]['captured_at']).total_seconds()),
            'basis':'continuous_gps_within_100m', 'suggested_action':'contact_driver_and_record_reason'})
    if target:
        distances = [(p,catalog.direct_distance(p,target)) for p in recent]
        baseline = min((d for p,d in distances if
                        (recent[-1]['captured_at']-p['captured_at']).total_seconds() >= 120), default=None)
        latest = distances[-1][1]
        if baseline is not None and latest is not None and latest-baseline >= 2000:
            alerts.append({'kind':'route_progress_deviation', 'increase_m':round(latest-baseline),
                           'basis':'distance_to_next_stop_increasing_not_road_corridor',
                           'suggested_action':'review_route_with_driver'})
    if expected_at and (now-expected_at).total_seconds() > 300:
        alerts.append({'kind':'arrival_delayed', 'delay_seconds':round((now-expected_at).total_seconds()),
                       'basis':'first_recorded_expected_arrival',
                       'suggested_action':'contact_driver_and_review_remaining_stops'})
    return alerts


def for_scope(c, user):
    """All resource access remains checked against the projected brand principal."""
    from fastapi import HTTPException
    from .operations import shift_access
    from .central_workdays import association
    now = c.execute('SELECT now() n').fetchone()['n']
    shifts = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id "
                       "WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND u.active",
                       (user['brand_id'],)).fetchall()
    result = []
    for shift in shifts:
        try:
            shift_access(c,user,shift)
        except HTTPException:
            continue
        source_ids = [shift['id']]
        day = association(c,shift['id'])
        if day:
            source_ids = [r['shift_id'] for r in c.execute(
                'SELECT shift_id FROM central_workday_legs WHERE workday_id=%s', (day['id'],)).fetchall()]
        points = c.execute('SELECT lat,lng,accuracy_m,captured_at FROM driver_tracking_points '
                           "WHERE shift_id=ANY(%s) AND captured_at>=now()-interval '10 minutes' "
                           'ORDER BY captured_at DESC LIMIT 100', (source_ids,)).fetchall()
        # Only the principal's live tasks may supply a target or a deadline.
        tasks = c.execute("SELECT t.id FROM driver_tasks t JOIN orders o ON o.id=t.order_id "
                          "WHERE t.shift_id=%s AND t.status='picked_up' AND o.brand_id=%s "
                          "AND (%s OR o.branch_id=ANY(%s)) AND o.completed_at IS NULL "
                          "AND o.delivery_status NOT IN ('delivered','cancelled')",
                          (shift['id'],user['brand_id'],user['role']=='owner',user.get('branch_ids',[]))).fetchall()
        tids = {t['id'] for t in tasks}
        forecasts = c.execute('SELECT stops,calculated_at FROM driver_route_eta_expectations '
                              'WHERE driver_id=%s AND calculated_at>=%s ORDER BY calculated_at LIMIT 2000',
                              (shift['user_id'],shift['opened_at'])).fetchall()
        expected = None
        target = None
        for forecast in forecasts:
            for stop in forecast['stops']:
                if stop.get('task_id') not in tids or stop.get('kind') not in ('delivery','dropoff'):
                    continue
                from datetime import datetime
                try:
                    arrival = datetime.fromisoformat(stop['arrival_at'])
                except (ValueError,TypeError,KeyError):
                    continue
                if expected is None or arrival < expected:
                    expected = arrival
        from .route_eta import published_plan_for
        published = published_plan_for(c,user.get('_map_route_viewer') or user,shift['user_id'])
        next_stop = (published or {}).get('next_stop')
        if next_stop and next_stop.get('brand_id') == user['brand_id']:
            target = next_stop.get('location')
        if not tasks and not target:
            continue
        alerts = movement_alerts(points,None,expected,now)
        corridor = (published or {}).get('road_corridor')
        if target and corridor:
            from .route_corridor import corridor_alert
            from datetime import datetime
            alert = corridor_alert(points,corridor['geometry'],
                                   datetime.fromisoformat(corridor['calculated_at']),now)
            if alert:
                alerts.append(alert)
        for alert in alerts:
            result.append(dict(alert,driver_id=shift['user_id'],display_name=shift['display_name'],
                               shift_id=shift['id'],brand_id=user['brand_id']))
    return result

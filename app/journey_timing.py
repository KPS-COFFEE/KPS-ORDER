"""Owner-approved evening budgets captured prospectively with each order."""
from datetime import datetime, timedelta, timezone

CITY_LIMITS = {
    'حفرالباطن': (45, 20), 'الخفجي': (32, 15), 'رفحاء': (32, 15),
    'الذيبية': (27, 12), 'قبه': (27, 12), 'قبة': (27, 12), 'النعيرية': (27, 12),
}


def capture(quote):
    if quote.get('service') not in (None, 'delivery') or quote.get('delivery_period') == 'morning':
        return None
    city = ''.join(str(quote.get('branch_city') or '').split())
    if city not in CITY_LIMITS:
        return None
    total, pickup = CITY_LIMITS[city]
    return {'version': 1, 'city_key': city, 'total_minutes': total,
            'pickup_cutoff_minutes': pickup, 'total_seconds': total*60,
            'pickup_cutoff_seconds': pickup*60, 'manager_accept_seconds': 120,
            'dispatch_seconds': 180, 'delivery_seconds': (total-pickup)*60}


def stamp(value):
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    return value if isinstance(value, datetime) and value.tzinfo is not None else None


def evaluate(order, points, tasks, now):
    policy = (order.get('snapshot') or {}).get('delivery_journey_policy')
    if (order.get('service') != 'delivery' or not policy or
            (order.get('snapshot') or {}).get('delivery_period') == 'morning'):
        return None
    created, now = stamp(order.get('created_at')), stamp(now)
    if not created or not now or now < created: return None
    cancelled = (order.get('preparation_status') in ('cancelled','rejected') or
                 order.get('delivery_status') == 'cancelled')
    delivered = stamp(points.get('task.delivered') or order.get('completed_at'))
    terminal = bool(cancelled or delivered or order.get('delivery_status')=='delivered')
    active = [t for t in tasks if t.get('status') not in ('cancelled','reassigned','transferred')]
    task = active[-1] if active else {}
    accepted = stamp(points.get('preparation.accepted'))
    assignments = [stamp(t.get('created_at')) for t in tasks if t.get('status') != 'cancelled']
    assignments = [x for x in assignments if x is not None]
    assigned = stamp(points.get('task.assigned')) or (min(assignments) if assignments else None)
    printed = stamp(points.get('order.print_requested'))
    pickup = stamp(task.get('picked_up_at') or points.get('task.picked_up'))
    # Every cutoff is anchored to order creation; reassignment never resets it.
    deadlines = {
        'manager_accept': created+timedelta(seconds=policy['manager_accept_seconds']),
        'dispatch_print': created+timedelta(seconds=policy['manager_accept_seconds']+policy['dispatch_seconds']),
        'pickup': created+timedelta(seconds=policy['pickup_cutoff_seconds']),
        'delivery': created+timedelta(seconds=policy['total_seconds']),
    }
    dispatch = max(assigned, printed) if assigned and printed else None
    completions = {'manager_accept':accepted,'dispatch_print':dispatch,'pickup':pickup,'delivery':delivered}
    owners = {'manager_accept':'مدير التوصيل','dispatch_print':'مدير التوصيل',
              'pickup':'السائق والفرع — السبب يحتاج توثيقًا','delivery':'السائق'}
    pickup_state = bool(pickup or task.get('status')=='picked_up' or order.get('delivery_status')=='picked_up' or order.get('preparation_status')=='handed_over')
    accepted_state = bool(accepted or assigned or pickup_state or task.get('status') in ('assigned','accepted','picked_up') or order.get('delivery_status') in ('assigned','accepted','picked_up') or order.get('preparation_status') not in ('new',None))
    current = ('delivery' if pickup_state else 'pickup' if dispatch else
               'dispatch_print' if accepted_state else 'manager_accept') if not terminal else None
    order_of_stages=list(deadlines)
    current_index=order_of_stages.index(current) if current else len(order_of_stages)
    rows=[]
    for name,deadline in deadlines.items():
        completed=completions[name]
        invalid=bool(completed and not created<=completed<=now)
        ended = terminal or order_of_stages.index(name)<current_index
        state=('invalid_time' if invalid else 'completed' if completed else
               'missing' if ended else 'pending')
        stop=completed if completed and not invalid else now if not ended else None
        late=max(0,(stop-deadline).total_seconds()) if stop else None
        rows.append({'stage':name,'deadline_at':deadline.isoformat(),
                     'completed_at':completed.isoformat() if completed else None,
                     'state':state,'late_seconds':late,'responsible_role':owners[name]})
    end=delivered if delivered and created<=delivered<=now else now if not terminal else None
    return {'version':policy['version'],'deadline_at':deadlines['delivery'].isoformat(),
            'limit_seconds':policy['total_seconds'], 'elapsed_seconds':(end-created).total_seconds() if end else None,
            'current_stage':current,'action_at':owners.get(current), 'stages':rows,
            'cancelled':cancelled,'terminal':terminal,
            'late':not cancelled and any(r['late_seconds'] and r['late_seconds']>0 for r in rows if r['stage']==current or r['stage']=='delivery')}

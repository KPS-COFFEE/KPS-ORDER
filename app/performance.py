"""Versioned province thresholds and event-derived phase durations, without a second order engine."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db

router = APIRouter(prefix='/api/performance', tags=['performance'])
BASIS = {'delivery': 'driver_pickup_to_customer_delivery', 'total': 'order_confirmation_to_completion'}
LABELS = {'excellent': 'ممتاز', 'good': 'جيد', 'late': 'متأخر', 'unconfigured': 'المعيار غير محدد',
          'not_started': 'لم تبدأ المرحلة', 'excluded': 'مستبعد', 'missing': 'بيانات الزمن غير مكتملة'}


def classify(seconds, policy, state='completed'):
    if state == 'cancelled':
        code = 'excluded'
    elif seconds is None:
        code = 'not_started' if state == 'not_started' else 'missing'
    elif not policy or policy.get('t1_seconds') is None or policy.get('t2_seconds') is None:
        code = 'unconfigured'
    elif seconds <= policy['t1_seconds']:
        code = 'excellent'
    elif seconds <= policy['t2_seconds']:
        code = 'good'
    else:
        code = 'late'
    return {'classification': code, 'label': LABELS[code], 'seconds': seconds,
            'color': {'excellent': 'green', 'good': 'orange', 'late': 'red'}.get(code, 'neutral'),
            'basis': (policy or {}).get('basis', BASIS['delivery']),
            'policy_version': (policy or {}).get('version'), 'policy_id': (policy or {}).get('id'),
            'province_id': (policy or {}).get('province_id'), 'state': state}


def capture_policy(conn, brand_id, branch_id):
    province = conn.execute('SELECT p.id,p.name FROM performance_provinces p JOIN performance_province_branches b '
                            'ON p.brand_id=b.brand_id AND p.id=b.province_id WHERE b.brand_id=%s AND b.branch_id=%s',
                            (brand_id, branch_id)).fetchone()
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'policies': {},
              'province_id': province['id'] if province else None, 'province_name': province['name'] if province else None}
    if province:
        rows = conn.execute('SELECT DISTINCT ON(metric) * FROM province_performance_policies '
                            'WHERE brand_id=%s AND province_id=%s AND effective_at<=now() '
                            'ORDER BY metric,effective_at DESC,version DESC', (brand_id, province['id'])).fetchall()
        for row in rows:
            result['policies'][row['metric']] = {key: row[key] for key in ('id', 'province_id', 'metric', 'version', 't1_seconds', 't2_seconds')}
            result['policies'][row['metric']].update(basis=BASIS[row['metric']], effective_at=row['effective_at'].isoformat())
    return result


def _date(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _phase(start, end, now, terminal=False, cancelled=False):
    start, end = _date(start), _date(end)
    if cancelled:
        return {'seconds': None, 'state': 'cancelled'}
    if start is None:
        return {'seconds': None, 'state': 'missing' if terminal else 'not_started'}
    stop = end if end is not None else (None if terminal else now)
    if stop is None or stop < start:
        return {'seconds': None, 'state': 'missing'}
    return {'seconds': (stop-start).total_seconds(), 'state': 'completed' if end is not None else 'running'}


def enrich_orders(conn, rows, now=None):
    """One bounded batch; numbers classify exact seconds, never rounded display minutes."""
    if not rows:
        return rows
    now = _date(now) if now else datetime.now(timezone.utc)
    ids = [row['id'] for row in rows]
    events = conn.execute('SELECT order_id,event_type,created_at FROM order_events WHERE order_id=ANY(%s) '
                          'ORDER BY created_at,id', (ids,)).fetchall()
    tasks = conn.execute('SELECT t.order_id,t.driver_id,t.created_at,t.picked_up_at,t.delivered_at,t.status,t.reason,u.display_name '
                         'FROM driver_tasks t JOIN users u ON u.id=t.driver_id WHERE t.order_id=ANY(%s) '
                         'ORDER BY t.created_at,t.id', (ids,)).fetchall()
    points, names = {}, {}
    for event in events:
        points.setdefault(event['order_id'], {}).setdefault(event['event_type'], event['created_at'])
    for task in tasks:
        point = points.setdefault(task['order_id'], {})
        if task['picked_up_at']:
            point['task.picked_up'] = min(point.get('task.picked_up', task['picked_up_at']), task['picked_up_at'])
        if task['delivered_at']:
            point['task.delivered'] = task['delivered_at']
        names[task['order_id']] = {'driver_id': task['driver_id'], 'driver_name': task['display_name'],
                                 'delivery_problem_reason': task.get('reason') if task['status']=='problem' else None}
    for row in rows:
        point = points.get(row['id'], {})
        done = row.get('completed_at') or point.get('task.delivered')
        cancelled = row['preparation_status'] in {'cancelled', 'rejected'} or row['delivery_status'] == 'cancelled'
        terminal = bool(done or cancelled or row['delivery_status'] == 'delivered' or row['preparation_status'] == 'completed')
        accepted, ready, pickup = point.get('preparation.accepted'), point.get('preparation.ready'), point.get('task.picked_up')
        # A later recorded stage proves the previous stage has ended, but does
        # not supply its missing timestamp. Never run two successive clocks or
        # invent a ready/acceptance time from a pickup.
        handed_over = point.get('preparation.handed_over') or pickup
        times = {'acceptance_wait': _phase(row['created_at'], accepted, now, terminal or bool(ready or handed_over), cancelled),
                 'preparation': _phase(accepted, ready, now, terminal or bool(handed_over), cancelled),
                 'driver_wait': _phase(ready, pickup, now, terminal or bool(pickup), cancelled) if row['service'] == 'delivery' else {'seconds': None, 'state': 'not_applicable'},
                 'delivery': _phase(pickup, point.get('task.delivered') or done, now, terminal, cancelled) if row['service'] == 'delivery' else {'seconds': None, 'state': 'not_applicable'},
                 'total': _phase(row['created_at'], done, now, terminal, cancelled)}
        snap = row.get('performance_snapshot') or {}
        metric = 'delivery' if row['service'] == 'delivery' else 'total'
        policy = (snap.get('policies') or {}).get(metric)
        row['timings'] = times
        row['performance'] = classify(times[metric]['seconds'], policy, times[metric]['state'])
        row['performance']['basis'] = BASIS[metric]
        row['performance']['province_id'] = snap.get('province_id')
        row['performance_by_metric'] = {name: classify(times[name]['seconds'], (snap.get('policies') or {}).get(name), times[name]['state'])
                                        for name in ('delivery', 'total') if times[name]['state'] != 'not_applicable'}
        row.update(names.get(row['id'], {'driver_id': None, 'driver_name': None}))
        row['branch_name'] = row.get('branch_name') or row.get('snapshot', {}).get('branch_name')
        row['neighborhood'] = row.get('customer', {}).get('neighborhood', '')
        row['server_now'] = now.isoformat()
    return rows


def delivery_summary(conn, where, params, now=None):
    """Aggregate the same frozen classifications shown in order lists/reports.

    Read in bounded batches instead of loading a whole operational period into
    memory. Live order age is deliberately not this road-duration metric.
    """
    now = _date(now) if now else datetime.now(timezone.utc)
    codes = tuple(LABELS)
    result = {'basis': BASIS['delivery'], 'policy_source': 'order_snapshot',
              'total_orders': 0, 'classifications': dict.fromkeys(codes, 0),
              'live': dict.fromkeys(codes, 0), 'completed': dict.fromkeys(codes, 0),
              'valid': 0, 'excluded': 0, 'average_seconds': None,
              'live_late': 0, 'completed_late': 0}
    after, total_seconds = '', 0
    while True:
        rows = conn.execute("SELECT o.* FROM orders o WHERE o.service='delivery' AND " + where +
                            ' AND o.id>%s ORDER BY o.id LIMIT 500', [*params, after]).fetchall()
        if not rows:
            break
        enrich_orders(conn, rows, now=now)
        for row in rows:
            result['total_orders'] += 1
            point = row['performance']
            code, state = point['classification'], point['state']
            result['classifications'][code] += 1
            complete = state == 'completed'
            terminal = complete or state == 'cancelled' or bool(row.get('completed_at'))
            result['completed' if terminal else 'live'][code] += 1
            if complete and point['seconds'] is not None:
                result['valid'] += 1
                total_seconds += point['seconds']
                result['completed_late'] += code == 'late'
            elif terminal:
                result['excluded'] += 1
            elif state == 'running':
                result['live_late'] += code == 'late'
        after = rows[-1]['id']
        if len(rows) < 500:
            break
    if result['valid']:
        result['average_seconds'] = total_seconds / result['valid']
    return result


def policy_scope(conn, user, province_id, branch_ids):
    if not branch_ids or len(branch_ids) != len(set(branch_ids)):
        raise db.error(422, 'PROVINCE_BRANCH_SCOPE_REQUIRED', 'حدد فروع المحافظة دون تكرار.')
    for bid in branch_ids:
        if not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s', (bid, user['brand_id'])).fetchone() or (
                user['role'] != 'owner' and bid not in user['branch_ids']):
            raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود ضمن صلاحياتك.')
        assigned = conn.execute('SELECT province_id FROM performance_province_branches WHERE branch_id=%s', (bid,)).fetchone()
        if assigned and assigned['province_id'] != province_id:
            raise db.error(409, 'PROVINCE_ASSIGNMENT_CONFLICT', 'الفرع مرتبط بمحافظة مختلفة. راجع المطابقة المعتمدة.')
    existing = conn.execute('SELECT branch_id FROM performance_province_branches WHERE brand_id=%s AND province_id=%s',
                            (user['brand_id'], province_id)).fetchall()
    if user['role'] != 'owner' and any(row['branch_id'] not in user['branch_ids'] for row in existing):
        raise db.error(403, 'PROVINCE_OUTSIDE_SCOPE', 'المحافظة تضم فروعًا خارج صلاحيات المدير.')


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    province_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    province_name: str = Field(min_length=1, max_length=120)
    branch_ids: list[str] = Field(min_length=1, max_length=500)
    metric: str = Field(pattern='^(delivery|total)$')
    t1_seconds: StrictInt = Field(gt=0, le=86399)
    t2_seconds: StrictInt = Field(gt=0, le=86400)
    effective_at: datetime


@router.post('/policies')
def create_policy(body: PolicyBody, user=Depends(auth.require_roles('owner', 'delivery_manager')),
                  idem: str | None = Header(None, alias='Idempotency-Key')):
    if not body.province_name.strip() or body.t1_seconds >= body.t2_seconds or body.effective_at.tzinfo is None:
        raise db.error(422, 'PERFORMANCE_LIMITS_INVALID', 'يلزم 0 < T1 < T2 وتاريخ نفاذ بمنطقة زمنية.')
    with db.tx() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('province-policy:'+user['brand_id']+':'+body.province_id,))
        # Authorization precedes replay so withdrawn scopes do not return saved responses.
        policy_scope(conn, user, body.province_id, body.branch_ids)
        def perform():
            conn.execute('INSERT INTO performance_provinces(brand_id,id,name,created_by) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (user['brand_id'], body.province_id, body.province_name.strip(), user['id']))
            for bid in body.branch_ids:
                conn.execute('INSERT INTO performance_province_branches(brand_id,province_id,branch_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                             (user['brand_id'], body.province_id, bid))
            version = conn.execute('SELECT coalesce(max(version),0)+1 n FROM province_performance_policies WHERE brand_id=%s AND province_id=%s AND metric=%s',
                                   (user['brand_id'], body.province_id, body.metric)).fetchone()['n']
            row = conn.execute('INSERT INTO province_performance_policies(id,brand_id,province_id,metric,version,t1_seconds,t2_seconds,effective_at,created_by) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                               (db.new_id('policy_'), user['brand_id'], body.province_id, body.metric, version, body.t1_seconds, body.t2_seconds, body.effective_at, user['id'])).fetchone()
            db.audit(conn, user['id'], 'performance.policy.created', 'performance_policy', row['id'],
                     {'province_id': body.province_id, 'metric': body.metric, 'version': version, 'branch_ids': body.branch_ids})
            return {'policy': row, 'basis': BASIS[body.metric]}
        return db.idempotent(conn, user['id'], idem, 'performance.policy.create', body.model_dump(mode='json'), perform)


@router.get('/policies')
def policies(user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff', 'branch_manager'))):
    with db.tx() as conn:
        rows = conn.execute('SELECT p.*,v.name province_name FROM province_performance_policies p '
                            'JOIN performance_provinces v ON v.brand_id=p.brand_id AND v.id=p.province_id '
                            'WHERE p.brand_id=%s ORDER BY p.province_id,p.metric,p.version DESC', (user['brand_id'],)).fetchall()
        bindings = conn.execute('SELECT province_id,branch_id FROM performance_province_branches WHERE brand_id=%s', (user['brand_id'],)).fetchall()
        by_province = {}
        for binding in bindings:
            by_province.setdefault(binding['province_id'], []).append(binding['branch_id'])
        rows = [dict(row, branch_ids=by_province.get(row['province_id'], []), basis=BASIS[row['metric']]) for row in rows
                if user['role'] == 'owner' or set(by_province.get(row['province_id'], [])) <= set(user['branch_ids'])]
    return {'policies': rows, 'metrics': BASIS}

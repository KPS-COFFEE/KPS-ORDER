"""Explicit owner correction of unallocated, mixed-day manager vault duties.

No startup execution. No new receipts, money, invoice changes or approvals.
The caller owns the transaction and must compare the reviewed plan fingerprint.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json

from . import db, operations as ops, service_hours


def digest(value):
    return hashlib.sha256(json.dumps(ops.safe(value), sort_keys=True,
                                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def shifted_window(window, old_day, new_day):
    result = deepcopy(window or {})
    result['operational_date'] = new_day.isoformat()
    delta = new_day - old_day
    for branch in (result.get('branches') or {}).values():
        for field in ('window_start', 'window_end', 'preparation_start'):
            if branch.get(field):
                branch[field] = (datetime.fromisoformat(branch[field]) + delta).isoformat()
    return result


def vault_closure(c, workday_id, *, allow_inherited=True):
    """Project current bag membership; preserve the original vault declaration."""
    row = c.execute('SELECT * FROM manager_vault_closures WHERE workday_id=%s', (workday_id,)).fetchone()
    split = None
    if not row and allow_inherited:
        split = c.execute('SELECT correction.* FROM manager_day_splits correction '
            'JOIN central_workday_legs target ON target.shift_id=correction.target_shift_id '
            'WHERE target.workday_id=%s ORDER BY correction.id LIMIT 1', (workday_id,)).fetchone()
        if not split:
            return None
        row = c.execute('SELECT vault.* FROM manager_vault_closures vault '
            'JOIN central_workday_legs source ON source.workday_id=vault.workday_id '
            'WHERE source.shift_id=%s', (split['source_shift_id'],)).fetchone()
    if not row:
        return None
    effective = c.execute('SELECT DISTINCT bag.id FROM driver_cash_bags bag '
        'JOIN central_workday_legs child_leg ON child_leg.workday_id=bag.workday_id '
        'JOIN shifts child ON child.id=child_leg.shift_id '
        'JOIN central_workday_legs manager_leg ON manager_leg.shift_id=child.manager_shift_id '
        "WHERE manager_leg.workday_id=%s AND bag.destination='manager_vault' "
        'AND bag.id=ANY(%s) ORDER BY bag.id', (workday_id, row['bag_ids'])).fetchall()
    return dict(row, original_bag_ids=row['bag_ids'], bag_ids=[b['id'] for b in effective],
                attribution_correction_id=split['id'] if split else None,
                physical_receipt_unchanged=True)


def allocation_manager_id(c, shift, frozen_parent_id):
    """Only an audited correction may redirect later allocation of an old receipt."""
    effective = shift['manager_shift_id']
    if effective != frozen_parent_id and c.execute(
            'SELECT 1 FROM manager_day_splits WHERE source_shift_id=%s AND target_shift_id=%s '
            "AND brand_id=%s AND original_state->'drivers' @> %s::jsonb",
            (frozen_parent_id, effective, shift['brand_id'], db.json([{'id': shift['id']}]))).fetchone():
        return effective
    return frozen_parent_id



def measure(c, child_ids, bags):
    """Integer halalas from actual collections, separate from declared bag values."""
    channel_sql = 'SELECT method,count(*) count,coalesce(sum(amount_halalas),0)::bigint amount_halalas '
    collections = c.execute(channel_sql + 'FROM effective_collections WHERE shift_id=ANY(%s) '
                            'GROUP BY method ORDER BY method', (child_ids,)).fetchall()
    refunds = c.execute(channel_sql + 'FROM refunds WHERE shift_id=ANY(%s) '
                        'GROUP BY method ORDER BY method', (child_ids,)).fetchall()
    orders = c.execute("""SELECT count(*) count FROM (
        SELECT DISTINCT ON (order_id) order_id,shift_id FROM effective_driver_tasks
        WHERE order_id IN (SELECT order_id FROM effective_driver_tasks WHERE shift_id=ANY(%s))
        ORDER BY order_id,(status='delivered') DESC,created_at DESC,id DESC
      ) canonical WHERE shift_id=ANY(%s)""", (child_ids, child_ids)).fetchone()
    return {'collections': collections, 'refunds': refunds, 'order_count': orders['count'],
            'bag_count': len(bags), 'bag_cash_halalas': sum(b['declared_cash_halalas'] for b in bags),
            'bag_card_halalas': sum(b['declared_card_halalas'] for b in bags)}


def current_bags(c, workday_ids):
    return c.execute('SELECT b.* FROM driver_cash_bags b WHERE b.workday_id=ANY(%s) '
                     'AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns r '
                     'WHERE r.declaration_id=b.declaration_id) ORDER BY b.id', (workday_ids,)).fetchall()


def plan(c, owner, source_sid):
    from . import central_workdays as days
    from .manager_admission import stored_day
    ops.roles(owner, 'owner')
    day, legs = days.authorized(c, owner, source_sid)
    if day['kind'] != 'manager' or source_sid != day['anchor_shift_id']:
        raise ValueError('Use the original manager workday anchor.')
    if c.execute('SELECT 1 FROM manager_day_splits WHERE source_shift_id=ANY(%s)',
                 ([s['id'] for _, s in legs],)).fetchone():
        raise ValueError('This workday already has a documented split; review the existing correction.')
    parents = [s for _, s in legs]
    ids = [s['id'] for s in parents]
    children = c.execute("SELECT s.* FROM shifts s WHERE s.manager_shift_id=ANY(%s) "
                         "ORDER BY s.opened_at,s.id FOR UPDATE", (ids,)).fetchall()
    if not children or any(s['kind'] != 'driver' or s['status'] != 'submitted'
                           or not s.get('staffing_ended_at') for s in children):
        raise ValueError('Only fully ended, submitted driver duties before allocation can be split.')
    vault = c.execute('SELECT * FROM manager_vault_closures WHERE workday_id=%s', (day['id'],)).fetchone()
    if not vault or any(s['status'] != 'submitted' or s['staffing_ended_at'] != vault['created_at']
                        or s['opening_halalas'] or ops.balance(c, s) for s in parents):
        raise ValueError('The original zero-balance manager vault closure is required.')
    for table, predicate, params in (
        ('closure_reports', 'shift_id=ANY(%s)', (ids + [s['id'] for s in children],)),
        ('collections', 'shift_id=ANY(%s)', (ids,)),
        ('refunds', 'shift_id=ANY(%s)', (ids,)),
        ('custody_events', 'shift_id=ANY(%s)', (ids,)),
        ('custody_transfers', 'from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)', (ids, ids)),
        ('manager_handovers', 'from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)', (ids, ids)),
        ('shared_manager_closure_declarations', 'workday_id=%s', (day['id'],)),
        ('accounting_daily_closures', 'manager_shift_id=ANY(%s)', (ids,)),
    ):
        if c.execute('SELECT 1 FROM ' + table + ' WHERE ' + predicate + ' LIMIT 1', params).fetchone():
            raise ValueError('Existing allocated financial evidence requires a separate reviewed correction: ' + table)
    groups = {}
    parent_by_id = {s['id']: s for s in parents}
    for child in children:
        actual_day = service_hours.delivery_operational_date(child['opened_at'])
        parent = parent_by_id[child['manager_shift_id']]
        if ((child.get('delivery_window_snapshot') or {}).get('period', 'evening') != 'evening'
                or (parent.get('delivery_window_snapshot') or {}).get('period', 'evening') != 'evening'
                or actual_day < stored_day(parent)):
            raise ValueError('This correction supports evidenced evening duty admission days only.')
        groups.setdefault(actual_day.isoformat(), []).append(child)
    if len(groups) < 2:
        raise ValueError('No mixed admission days were found.')
    child_ids = [s['id'] for s in children]
    links = {r['shift_id']: r['workday_id'] for r in c.execute(
        'SELECT shift_id,workday_id FROM central_workday_legs WHERE shift_id=ANY(%s)', (child_ids,)).fetchall()}
    if set(links) != set(child_ids):
        raise ValueError('A driver duty is missing its physical workday evidence.')
    workday_ids = sorted(set(links.values()))
    # A physical duty must move atomically across all of its brand ledgers.
    outside = c.execute('SELECT 1 FROM central_workday_legs WHERE workday_id=ANY(%s) '
                        'AND NOT (shift_id=ANY(%s)) LIMIT 1', (workday_ids, child_ids)).fetchone()
    day_membership = {key: {links[s['id']] for s in rows} for key, rows in groups.items()}
    if outside or sum(map(len, day_membership.values())) != len(workday_ids):
        raise ValueError('A physical driver duty spans different managers or dates; review the whole duty.')
    bags = current_bags(c, workday_ids)
    validation = measure(c, child_ids, bags)
    validation['days'] = {key: measure(c, [s['id'] for s in rows],
                                    [b for b in bags if b['workday_id'] in day_membership[key]])
                          for key, rows in sorted(groups.items())}
    if any(sum(day[key] for day in validation['days'].values()) != validation[key]
           for key in ('order_count', 'bag_count', 'bag_cash_halalas', 'bag_card_halalas')):
        raise ValueError('The proposed split does not conserve orders or declared bag values.')
    state = {'parents': parents, 'children': children, 'bags': bags, 'vault': vault, 'validation': validation}
    return dict(state, fingerprint=digest(state), source_workday=day, groups=groups, day_membership=day_membership)


def apply(c, owner, source_sid, fingerprint, reason):
    """All changes and the post-check commit together, or none of them do."""
    from . import central_workdays as days
    from .manager_admission import stored_day
    days.lock_lanes(c)
    original = plan(c, owner, source_sid)
    if not reason.strip() or len(reason.strip()) < 5 or original['fingerprint'] != fingerprint:
        raise ValueError('Review the fresh plan and provide its exact fingerprint and correction reason.')
    baseline = db.preservation_snapshot(c, lock_tables=False)
    excluded = {'shifts', 'central_workdays', 'central_workday_legs', 'manager_day_splits'}
    baseline = {name: value for name, value in baseline.items() if name not in excluded}
    all_children = {s['id']: s for s in original['children']}
    results = []
    for value, group in sorted(original['groups'].items()):
        corrected = date.fromisoformat(value)
        affected = [s for s in original['parents'] if stored_day(s) != corrected
                    and any(child['manager_shift_id'] == s['id'] for child in group)]
        if not affected:
            continue
        new_day = ops.uid()
        new_shifts = []
        for parent in affected:
            moved = [s for s in group if s['manager_shift_id'] == parent['id']]
            target = ops.uid()
            opened = min(s['opened_at'] for s in moved)
            window = shifted_window(parent['delivery_window_snapshot'], stored_day(parent), corrected)
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,submitted_at,"
                "delivery_operational_date,delivery_window_snapshot,manager_admission_ended_at,submission) "
                "VALUES(%s,'manager',%s,%s,%s,'submitted',%s,%s,%s,%s,%s,%s)",
                (target, parent['user_id'], parent['brand_id'], db.json(parent['branch_ids']), opened,
                 parent['submitted_at'], corrected, db.json(window), parent['staffing_ended_at'],
                 db.json({'financial_state': 'awaiting_accounting_bag_counts', 'physical_state': 'bags_in_vault',
                          'source_manager_shift_id': parent['id'], 'attribution_correction': True})))
            new_shifts.append((parent, target, moved))
        anchor = next((target for p, target, _ in new_shifts if p['id'] == source_sid), new_shifts[0][1])
        c.execute('INSERT INTO central_workdays(id,user_id,kind,anchor_shift_id,created_by) VALUES(%s,%s,%s,%s,%s)',
                  (new_day, original['source_workday']['user_id'], 'manager', anchor, owner['id']))
        for parent, target, moved in new_shifts:
            c.execute('INSERT INTO central_workday_legs(workday_id,brand_id,shift_id) VALUES(%s,%s,%s)',
                      (new_day, parent['brand_id'], target))
            correction = ops.uid()
            c.execute('INSERT INTO manager_day_splits(id,source_shift_id,target_shift_id,brand_id,corrected_day,'
                'original_state,validation,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (correction, parent['id'], target, parent['brand_id'], corrected,
                 db.json(ops.safe({'parent': parent, 'drivers': moved, 'vault': original['vault']})),
                 db.json(ops.safe(original['validation'])), owner['id'], reason))
            c.execute('UPDATE shifts SET staffing_ended_at=%s WHERE id=%s', (parent['staffing_ended_at'], target))
            for child in moved:
                window = shifted_window(child['delivery_window_snapshot'], stored_day(parent), corrected)
                c.execute('UPDATE shifts SET manager_shift_id=%s,delivery_operational_date=%s,'
                          'delivery_window_snapshot=%s WHERE id=%s', (target, corrected, db.json(window), child['id']))
            results.append({'correction_id': correction, 'source_shift_id': parent['id'], 'target_shift_id': target,
                            'day': value, 'driver_shift_ids': [s['id'] for s in moved]})
    after = db.preservation_snapshot(c, baseline, lock_tables=False)
    if baseline != after:
        raise RuntimeError('Financial/original evidence changed; correction transaction must roll back.')
    parents_after = c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY id',
                             ([s['id'] for s in original['parents']],)).fetchall()
    if digest(sorted(original['parents'], key=lambda s: s['id'])) != digest(parents_after):
        raise RuntimeError('Original manager records changed.')
    changed_ids = {sid for result in results for sid in result['driver_shift_ids']}
    mapping = {sid: result for result in results for sid in result['driver_shift_ids']}
    for row in c.execute('SELECT * FROM shifts WHERE id=ANY(%s)', (list(all_children),)).fetchall():
        before = all_children[row['id']]
        allowed = {'manager_shift_id', 'delivery_operational_date', 'delivery_window_snapshot',
                   'reporting_operational_date'} if row['id'] in changed_ids else set()
        if any(row[key] != value for key, value in before.items() if key not in allowed):
            raise RuntimeError('An original driver field outside attribution changed.')
        if row['id'] in mapping and (row['manager_shift_id'] != mapping[row['id']]['target_shift_id']
                or row['reporting_operational_date'].isoformat() != mapping[row['id']]['day']):
            raise RuntimeError('The corrected driver attribution does not match the reviewed plan.')
    all_bags = current_bags(c, sorted(set().union(*original['day_membership'].values())))
    verified_totals = measure(c, list(all_children), all_bags)
    verified_totals['days'] = {key: measure(c, [s['id'] for s in rows],
                                         [b for b in all_bags if b['workday_id'] in original['day_membership'][key]])
                               for key, rows in sorted(original['groups'].items())}
    if verified_totals != original['validation']:
        raise RuntimeError('Order, cash or card totals changed; correction transaction must roll back.')
    db.audit(c, owner['id'], 'manager.days_split', 'central_workday', original['source_workday']['id'],
             {'reason': reason, 'fingerprint': fingerprint, 'corrections': results,
              'validation_before': original['validation'], 'validation_after': verified_totals,
              'financial_bytes_verified': True, 'original_bags_preserved': True, 'no_receipt_or_approval_posted': True})
    return {'corrections': results, 'validation': verified_totals, 'financial_bytes_verified': True}

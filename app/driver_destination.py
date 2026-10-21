"""Current-shift destination intent, separate from financial and arrival evidence."""
from datetime import datetime
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import catalog, db
from .operations import shift_access, external_enbishan_eligible


class DestinationChoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['branch', 'external_enbishan']
    branch_id: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_choice(self):
        if (self.kind == 'branch') != bool(self.branch_id):
            raise ValueError('A branch destination requires exactly one branch ID.')
        return self


def own_shift(c, user, lock=False):
    row = c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='driver' AND status='open'" +
                    (' FOR UPDATE' if lock else ''), (user['id'], user['brand_id'])).fetchone()
    if not row:
        raise HTTPException(409, 'يلزم شفت سائق مفتوح.')
    shift_access(c, user, row)
    return row


def metadata(c, user, rows):
    """Three batch reads regardless of roster size; hidden tasks never leak."""
    if not rows:
        return {}
    ids, shift_ids = [r['id'] for r in rows], [r['shift_id'] for r in rows]
    branch_ids = list({b for r in rows for b in r['branch_ids'] if b in r['shift_branches'] and
                       (user['role'] == 'owner' or b in user.get('branch_ids', []))})
    branches = {b['id']: b for b in c.execute('SELECT id,name,city,brand_id,settings FROM branches WHERE brand_id=%s AND id=ANY(%s)',
                                             (user['brand_id'], branch_ids)).fetchall()}
    # Include terminal tasks only to ensure an old declaration cannot reappear
    # after a newer task finishes. No customer data is read here.
    tasks = c.execute("SELECT t.driver_id,t.shift_id,t.created_at,t.status,o.completed_at,o.delivery_status,"
                      "o.preparation_status,o.brand_id,o.branch_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id "
                      "WHERE t.driver_id=ANY(%s) AND t.shift_id=ANY(%s) AND o.brand_id=%s",
                      (ids, shift_ids, user['brand_id'])).fetchall()
    trips = {t['shift_id']: t for t in c.execute('SELECT shift_id,(array_agg(departure_branch_id) FILTER (WHERE returned_at IS NULL))[1] departure_branch_id,'
                                               'max(returned_at) latest_returned_at FROM driver_work_trips WHERE shift_id=ANY(%s) GROUP BY shift_id',
                                               (shift_ids,)).fetchall()}
    result = {}
    for row in rows:
        scoped = [branches[b] for b in row['branch_ids'] if b in row['shift_branches'] and b in branches]
        allowed = {b['id'] for b in scoped}
        cities = {catalog.normalize_city(b['city']) for b in scoped}
        complete_scope = bool(scoped) and allowed == set(row['shift_branches'])
        hafar = complete_scope and cities == {'حفر الباطن'}
        driver_tasks = [t for t in tasks if t['driver_id'] == row['id'] and t['shift_id'] == row['shift_id']]
        active = [t for t in driver_tasks if t['status'] not in ('delivered','cancelled','transferred','reassigned')
                  and not t['completed_at'] and t['delivery_status'] not in ('delivered','cancelled')
                  and t['preparation_status'] not in ('cancelled','rejected')]
        brands = {t['brand_id'] for t in active if t['branch_id'] in allowed}
        saved = row.get('next_destination') or {}
        try:
            declared = datetime.fromisoformat(saved['declared_at'])
            returned = (trips.get(row['shift_id']) or {}).get('latest_returned_at')
            if (returned is not None and returned >= declared) or any(t['created_at'] > declared for t in driver_tasks):
                saved = {}
        except (ValueError, KeyError, TypeError):
            saved = {}
        destination = None
        if not active:
            if hafar and saved.get('kind') == 'external_enbishan' and user['brand_id'] == 'kps':
                brands.add('enbishan')
                destination = {'kind':'external_enbishan', 'source':'driver_declared'}
            else:
                bid = saved.get('branch_id') if hafar and saved.get('kind') == 'branch' else None
                source = 'driver_declared'
                if complete_scope and cities and 'حفر الباطن' not in cities:
                    bid = (trips.get(row['shift_id']) or {}).get('departure_branch_id')
                    source = 'departure_branch'
                if bid in allowed:
                    branch = branches[bid]
                    destination = {'kind':'branch', 'branch_id':bid, 'name':branch['name'], 'source':source,
                                   'location':branch['settings'].get('location') if catalog.location_verified(branch) else None}
        brand = next(iter(brands)) if len(brands) == 1 else 'mixed' if brands else None
        result[row['id']] = {
            'active_order_brand': ({'id':brand, 'name':{'kps':'KPS','enbishan':'Enbishan','mixed':'Mixed'}.get(brand,brand),
                                    'source':'active_task' if active else 'driver_declared'} if brand else None),
            'next_destination':destination, 'destination_choice_required':hafar and not active and destination is None,
            'hafar':hafar, 'external_enbishan_allowed':hafar and user['brand_id']=='kps',
            'branches':[{'id':b['id'],'name':b['name']} for b in scoped]}
    return result


def own_view(c, user, shift):
    row = {'id':user['id'], 'shift_id':shift['id'], 'branch_ids':user.get('branch_ids',[]),
           'shift_branches':shift['branch_ids'], 'next_destination':shift.get('next_destination')}
    return metadata(c, user, [row])[user['id']]


def save_choice(c, user, body):
    shift = own_shift(c, user, lock=True)
    if not own_view(c, user, shift)['hafar']:
        raise HTTPException(403, 'اختيار الوجهة متاح لسائق حفر الباطن؛ خارجها العودة إلى فرع الانطلاق.')
    if body.kind == 'external_enbishan' and not external_enbishan_eligible(c, shift):
        raise HTTPException(403, 'تصريح عنبيشان الخارجي متاح لسائق KPS في حفر الباطن فقط.')
    if body.branch_id and (body.branch_id not in shift['branch_ids'] or body.branch_id not in user.get('branch_ids', [])):
        raise HTTPException(403, 'الفرع خارج نطاق الشفت.')
    if body.branch_id and not c.execute('SELECT id FROM branches WHERE id=%s AND brand_id=%s',
                                        (body.branch_id, user['brand_id'])).fetchone():
        raise HTTPException(403, 'الفرع غير متاح.')
    if c.execute("SELECT 1 FROM driver_tasks WHERE shift_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned') LIMIT 1",
                 (shift['id'],)).fetchone():
        raise HTTPException(409, 'أكمل الطلبات الحالية قبل اختيار الوجهة التالية.')
    value = body.model_dump(exclude_none=True)
    value['declared_at'] = c.execute('SELECT now() n').fetchone()['n'].isoformat()
    c.execute('UPDATE shifts SET next_destination=%s WHERE id=%s', (db.json(value), shift['id']))
    db.audit(c, user['id'], 'driver.next_destination', 'shift', shift['id'], value)
    shift['next_destination'] = value
    return own_view(c, user, shift)

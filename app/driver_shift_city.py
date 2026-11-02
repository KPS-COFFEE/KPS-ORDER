"""Select one physical city without widening staff or manager permissions."""
from . import catalog, db


def assigned(c, driver_id, requested=None):
    row=c.execute('SELECT city FROM driver_duty_cities WHERE driver_id=%s',(driver_id,)).fetchone()
    if not row:return requested
    if requested and catalog.normalize_city(requested)!=row['city']:
        raise db.error(409,'DRIVER_DUTY_CITY_ASSIGNED','مدينة دوام السائق محددة بأمر المالك.')
    return row['city']


def remember(c, user, driver_id, city, reason):
    if user['role']!='owner':return
    c.execute('INSERT INTO driver_duty_cities(driver_id,city,approved_by,reason) VALUES(%s,%s,%s,%s) '
              'ON CONFLICT(driver_id) DO UPDATE SET city=excluded.city,approved_by=excluded.approved_by,reason=excluded.reason,updated_at=now()',
              (driver_id,catalog.normalize_city(city),user['id'],reason))


def select(branches, city=None):
    cities = {catalog.normalize_city(b.get('city') or '') for b in branches}
    chosen = catalog.normalize_city(city or '')
    if not chosen and len(cities) == 1:
        chosen = next(iter(cities))
    if not chosen or chosen not in cities:
        raise db.error(409, 'DRIVER_SHIFT_CITY_REQUIRED',
                       'حدد مدينة الدوام من فروع السائق المعتمدة قبل فتح الشفت.')
    return [b for b in branches if catalog.normalize_city(b.get('city') or '') == chosen]


def correct_group(c, user, sid, body):
    """Narrow all empty ledgers atomically; retain their custody/closure access."""
    from . import operations as ops, multibrand
    from .central_workdays import authorized
    day, legs = authorized(c, user, sid)
    if user['role']!='owner':assigned(c,day['user_id'],body.city)
    identifiers = [s['id'] for _,s in legs]
    if day['kind'] != 'driver' or any(s['status'] != 'open' for _,s in legs):
        ops.fail(409, 'التصحيح يخص وردية سائق مفتوحة فقط.')
    if c.execute('SELECT 1 FROM driver_tasks WHERE shift_id=ANY(%s) LIMIT 1', (identifiers,)).fetchone():
        ops.fail(409, 'الوردية تحتوي سجل مهام؛ يلزم مسار تسليم معتمد.')
    if c.execute("SELECT 1 FROM custody_transfers WHERE status='pending' AND (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) LIMIT 1", (identifiers,identifiers)).fetchone():
        ops.fail(409, 'أكمل تسليم العهدة المعلق قبل تصحيح المدينة.')
    chosen = catalog.normalize_city(body.city)
    changes=[]
    for principal,s in legs:
        driver=multibrand.project(c,c.execute('SELECT * FROM users WHERE id=%s',(s['user_id'],)).fetchone(),s['brand_id'])
        manager=ops.lock_shift(c,s['manager_shift_id'])
        manager_user=multibrand.project(c,c.execute('SELECT * FROM users WHERE id=%s',(manager['user_id'],)).fetchone(),s['brand_id']) if manager else None
        if not driver or not driver['active'] or not manager or manager['kind']!='manager' or manager['brand_id']!=s['brand_id'] or manager['status']!='open' or not manager_user or not manager_user['active'] or manager_user['role'] not in ('owner','delivery_manager'):
            ops.fail(409, 'صلاحية السائق أو مناوبة المدير غير سارية.')
        allowed=set(s['branch_ids']) & set(driver['branch_ids']) & set(manager['branch_ids'])
        if not ops.unrestricted_owner(manager_user):allowed &= set(manager_user['branch_ids'])
        rows=c.execute('SELECT * FROM branches WHERE id=ANY(%s) AND brand_id=%s',(sorted(allowed),s['brand_id'])).fetchall()
        selected=[b for b in rows if catalog.normalize_city(b.get('city') or '')==chosen]
        if selected:ops.driver_shift_window(c,s['brand_id'],selected,manager,manager_user)
        changes.append((s,[b['id'] for b in selected]))
    # Foreign-city ledgers retain historical scope and money, but stop admitting
    # tasks. The physical vehicle anchor must serve the selected city.
    if not next(bids for s,bids in changes if s['id']==day['anchor_shift_id']):
        ops.fail(409, 'المدينة غير مخولة لشفت السيارة الأساسي.')
    for s,bids in changes:
        window=dict(s.get('delivery_window_snapshot') or {})
        retained=bids or s['branch_ids']
        if bids and 'branches' in window:window['branches']={k:v for k,v in window['branches'].items() if k in bids}
        c.execute('UPDATE shifts SET branch_ids=%s,delivery_window_snapshot=%s,available=%s WHERE id=%s',
                  (db.json(retained),db.json(window),s['available'] if bids else False,s['id']))
        db.audit(c,user['id'],'shift.duty_city.corrected','shift',s['id'],
                 {'before_branch_ids':s['branch_ids'],'branch_ids':retained,'accepting_tasks':bool(bids),'city':body.city,
                  'reason':body.reason,'financial_changes':False,'workday_id':day['id']})
    remember(c,user,day['user_id'],body.city,body.reason)
    return {'shift_id':sid,'city':body.city,'financial_changes':False}

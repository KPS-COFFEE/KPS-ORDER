"""Read-only driver fuel report over the canonical vehicle segment ledger."""
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, Query
from . import auth, catalog, db, fleet, multibrand, service_hours, employee_permissions

router = APIRouter(prefix='/api/fleet/driver-report', tags=['driver-fuel-report'])


def access(user=Depends(auth.current_user)):
    maintenance_reader = user['role'] in {'owner', 'admin'} and auth.has_permission(user, fleet.PERMISSION)
    if not maintenance_reader and not employee_permissions.reports_supervisor(user):
        raise db.error(403, 'ROLE_FORBIDDEN', 'تقرير البنزين والمسافات غير مخول لحسابك.')
    return user


def branches(c, user):
    return [dict(row, city=catalog.normalize_city(row['city'])) for row in c.execute(
        'SELECT id,name,branch_no,city FROM branches WHERE brand_id=%s ORDER BY branch_no,id',
        (user['brand_id'],)).fetchall() if fleet.unrestricted_owner(user) or row['id'] in user.get('branch_ids', [])]



def recheck(user, previous_branches):
    # Re-read grants after reporting; a repeatable-read report cannot observe
    # a concurrent revocation inside its own financial snapshot.
    with db.tx() as c:
        original = c.execute('SELECT * FROM users WHERE id=%s AND active', (user['id'],)).fetchone()
        fresh = multibrand.project(c, original, user['brand_id']) if original else None
        if not fresh:
            raise db.error(403, 'FUEL_REPORT_SCOPE_CHANGED', 'تغير نطاق الحساب؛ حدّث الشاشة.')
        access(fresh)
        if not {b['id'] for b in previous_branches} <= {b['id'] for b in branches(c, fresh)}:
            raise db.error(403, 'FUEL_REPORT_SCOPE_CHANGED', 'تغير نطاق الحساب؛ حدّث الشاشة.')


def segment_log(log, number):
    parts = [*(log.get('previous_segments') or []), {k: v for k, v in log.items() if k != 'previous_segments'}]
    return parts[number] if 0 <= number < len(parts) else {}


def fuel_values(log):
    refuels = log.get('refuels') or []
    known = sum((int(r['amount_halalas']) for r in refuels if r.get('amount_halalas') is not None), 0)
    missing = sum(r.get('amount_halalas') is None for r in refuels)
    return {'refuel_count': len(refuels), 'fuel_amount_known_halalas': known,
            'fuel_amount_halalas': known if refuels and not missing else None,
            'fuel_amount_missing_count': missing, 'fuel_record_missing': not bool(refuels)}


@router.get('/options')
def options(user=Depends(access)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        scope = branches(c, user)
        clauses, args = fleet.scoped(user)
        people = c.execute('SELECT DISTINCT u.id,u.display_name'+fleet.BASE+' WHERE '+' AND '.join(clauses)+' ORDER BY u.display_name,u.id', args).fetchall()
        cars = fleet.vehicles_in_tx(c, user)['vehicles']
        result = {'today': service_hours.delivery_operational_date(), 'drivers': people,
                'vehicles': [{'id': r['id'], 'plate': r['plate']} for r in cars],
                'cities': sorted({b['city'] for b in scope if b['city']}), 'branches': scope}
    recheck(user, scope)
    return result


@router.get('')
def report(start: date | None = None, end: date | None = None,
           driver_id: str | None = None, vehicle_id: str | None = None,
           city: str = Query('', max_length=120), limit: int = Query(50, ge=1, le=200),
           offset: int = Query(0, ge=0, le=1000000), user=Depends(access)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        scope = branches(c, user)
        city = catalog.normalize_city(city)
        lo, hi, statement, args = fleet.query_statement(user, start=start, end=end, driver_id=driver_id, vehicle_id=vehicle_id)
        statement = 'SELECT q.*,s.vehicle_log FROM ('+statement+') q JOIN shifts s ON s.id=q.shift_id'
        if city:
            ids = [b['id'] for b in scope if b['city'] == city]
            if not ids:
                raise db.error(422, 'CITY_INVALID', 'اختر مدينة ضمن نطاقك.')
            statement += ' WHERE q.branch_ids ?| %s'
            args.append(ids)
        # Full authorized filtered population, before paging; never total one page.
        # Bound period comes from the canonical fleet report (at most one year).
        raw = c.execute(statement+' ORDER BY q.operating_day DESC,q.received_at DESC,q.driver_id,q.shift_id,q.segment_no', args).fetchall()
        by_id = {b['id']: b for b in scope}
        rows = []
        for r in raw:
            log = segment_log(r.pop('vehicle_log') or {}, int(r['segment_no']))
            r.update(r.pop('metrics'))
            r.update(fuel_values(log))
            r['cities'] = sorted({by_id[b]['city'] for b in r['branch_ids'] if b in by_id})
            r['branches'] = ' / '.join(by_id[b]['name'] for b in r.pop('branch_ids') if b in by_id)
            # Null is an incomplete measurement, never zero or an inferred charge.
            if not r.get('distance_eligible', True):
                r['actual_distance'] = None
            rows.append(r)
        known_amount = sum(r['fuel_amount_known_halalas'] for r in rows)
        missing_amounts = sum(r['fuel_amount_missing_count'] for r in rows)
        missing_fuel = sum(r['fuel_record_missing'] for r in rows)
        totals = {'records': len(rows), 'drivers': len({r['driver_id'] for r in rows}),
                  'distance_km': sum((Decimal(str(r['actual_distance'])) for r in rows if r.get('actual_distance') is not None), Decimal(0)),
                  'distance_missing_records': sum(r.get('actual_distance') is None for r in rows),
                  'liters': sum((Decimal(str(r['liters'])) for r in rows if r.get('liters') is not None), Decimal(0)),
                  'fuel_record_missing': missing_fuel, 'fuel_amount_missing_count': missing_amounts,
                  'fuel_amount_known_halalas': known_amount,
                  'fuel_amount_halalas': known_amount if not missing_fuel and not missing_amounts else None}
        result = {'rows': rows[offset:offset+limit], 'total': len(rows), 'summary': totals,
                'period': {'start': lo, 'end': hi}, 'offset': offset, 'limit': limit,
                'note': 'كل صف فترة استلام سيارة داخل شفت السائق؛ تبديل السيارة لا يكرر التعبئة. المسافات واللترات من سجل التشغيل وتصحيحاته، وقيمة الوقود من مبالغ التعبئة المسجلة فقط. غياب التعبئة لا يثبت عدم التعبئة ولا يحمل السائق عجزا.'}

    recheck(user, scope)
    return result

"""Owner dashboard acceptance against isolated PostgreSQL, never runtime data.

Amounts are synthetic halalas. Assertions cover independent known totals as well
as parity with the canonical accounting report, including overlapping duties.
"""
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app import accounting, auth, db, service_hours
from test_accounting import accountant
from test_enbishan_dual_brand import dual
from test_operations import ops


TZ = ZoneInfo('Asia/Riyadh')
API = '/api/owner-dashboard'
DAY = '2026-10-28'


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def duty(c, label, day=DAY, *, period='evening', opened=None, closed=None,
         brand='ops-brand', branch='ops-branch', manager='ops-manager',
         driver='ops-driver', child_day=None):
    """Seed immutable historical duties without bypassing any live workflow."""
    manager_id, driver_id = 'od-manager-'+label, 'od-driver-'+label
    opened = opened or day+('T04:15:00' if period == 'morning' else 'T16:10:00')
    closed = closed or day+'T23:00:00'
    for sid, kind, user, parent in (
        (manager_id, 'manager', manager, None),
        (driver_id, 'driver', driver, manager_id),
    ):
        c.execute(
            "INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,"
            "status,opened_at,closed_at,delivery_operational_date,delivery_window_snapshot) "
            "VALUES(%s,%s,%s,%s,%s,%s,'closed',%s,%s,%s,%s)",
            (sid, kind, user, brand, db.json([branch]), parent, at(opened), at(closed),
             child_day if kind == 'driver' and child_day else day, db.json({'period': period})),
        )
    return driver_id


def sale(c, label, shift, amount, created, collected=None, *, complete=True,
         split=False, brand='ops-brand', branch='ops-branch', driver='ops-driver',
         customer='ops-customer', manager='ops-manager', snapshot=None,
         test_void=None, cancelled=False):
    identifier = 'od-order-'+label
    snap = {
        'order_reference': 'OD-'+label,
        'items': [{'product_id': 'ops-product', 'name': 'Synthetic coffee',
                   'quantity': 1, 'line_total_halalas': amount,
                   'unit_price_halalas': amount, 'units': [], 'note': ''}],
        **(snapshot or {}),
    }
    completion = at(collected or created) if complete and not cancelled else None
    c.execute(
        "INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,"
        "delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,"
        "snapshot,customer,created_at,completed_at,payment_status,test_void) "
        "VALUES(%s,%s,%s,%s,'delivery',%s,%s,'card',%s,0,%s,%s,'{}',%s,%s,%s,%s)",
        (identifier, brand, branch, customer,
         'cancelled' if cancelled else 'completed' if complete else 'ready',
         'cancelled' if cancelled else 'delivered' if complete else 'pending',
         amount, amount, db.json(snap), at(created), completion,
         'paid' if collected else 'unpaid', db.json(test_void) if test_void else None),
    )
    if shift:
        c.execute(
            "INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,"
            "created_at,delivered_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid4().hex, identifier, driver, shift,
             'cancelled' if cancelled else 'delivered' if complete else 'assigned',
             manager, at(created), completion),
        )
    if collected:
        assert shift, 'Synthetic collections must have a real historical shift'
        tenders = [('cash', amount//2), ('card', amount-amount//2)] if split else [('card', amount)]
        for method, value in tenders:
            c.execute(
                "INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (uuid4().hex, identifier, shift, driver, method, value, at(collected)),
            )
    return identifier


def dashboard(actor, day=DAY, *, who='owner', **filters):
    response = actor(who).get(API, params={
        'period': 'custom', 'start': day, 'end': day, **filters,
    })
    assert response.status_code == 200, response.text
    return response.json()


def ids(report):
    return {row['id'] for row in report['rows']}


def assert_financial_parity(actor, result, day=DAY, **filters):
    query = {'start': day, 'end': day, 'group': 'daily', **filters}
    reference = actor('owner').get('/api/accounting/reports', params=query)
    assert reference.status_code == 200, reference.text
    totals = reference.json()['totals']
    mapping = {
        'gross_completed_halalas': 'gross', 'net_sales_halalas': 'net_sales',
        'cash_halalas': 'cash', 'card_halalas': 'card',
        'net_collected_halalas': 'total',
    }
    for field, canonical in mapping.items():
        assert result['summary'][field] == totals[canonical], (field, result, totals)
    assert result['summary']['refunds_halalas'] == totals['refund_cash'] + totals['refund_card']


@pytest.mark.postgres
def test_owner_dashboard_requires_owner_even_with_reporting_permissions(ops):
    actor, _, _, _ = ops
    accountant()
    for role in ('manager', 'driver', 'branch', 'customer', 'accountant'):
        response = actor(role).get(API)
        assert response.status_code == 403, (role, response.text)
    result = dashboard(actor)
    assert result['summary']['orders'] == 0
    assert result['rows'] == []
    assert result['period']['basis'] == 'manager_shift'
    assert result['period']['fallback_start'] == '04:00'
    client = actor('owner')
    client.app.dependency_overrides.pop(auth.current_user)
    assert client.get(API).status_code == 401


@pytest.mark.postgres
def test_overlapping_manager_duties_and_late_money_match_accounting_without_writes(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        old = duty(c, 'old', '2026-10-27', child_day='2026-10-28', closed='2026-10-28T05:00:00')
        morning = duty(c, 'morning', period='morning', opened=DAY+'T04:15:00', closed=DAY+'T18:15:00')
        evening = duty(c, 'evening', opened=DAY+'T16:10:00', closed='2026-10-29T06:00:00', driver='ops-driver2')
        older = sale(c, 'old-31', old, 3100, '2026-10-28T03:55:00', '2026-10-28T04:27:00')
        am = sale(c, 'morning-overlap', morning, 21350, DAY+'T17:30:00', DAY+'T18:05:00')
        pm = sale(c, 'evening-overlap', evening, 231600, DAY+'T17:30:00', DAY+'T17:40:00', split=True, driver='ops-driver2')
        late = sale(c, 'evening-late', evening, 1650, '2026-10-29T03:48:00', '2026-10-29T04:19:00', driver='ops-driver2')
        pending = sale(c, 'paid-pending', evening, 1100, '2026-10-29T03:55:00', '2026-10-29T05:00:00', complete=False, driver='ops-driver2')
        collection = c.execute('SELECT id FROM collections WHERE order_id=%s', (older,)).fetchone()['id']
        c.execute(
            "INSERT INTO refunds(id,collection_id,order_id,shift_id,method,amount_halalas,reason,authorized_by,created_at) "
            "VALUES(%s,%s,%s,%s,'card',500,'SYNTHETIC later-duty refund','ops-owner','2026-10-29T05:10:00+03')",
            (uuid4().hex, collection, older, evening),
        )
        before = {name: c.execute('SELECT * FROM '+name+' ORDER BY id').fetchall()
                  for name in ('collections', 'refunds', 'orders', 'shifts')}
    result = dashboard(actor)
    s = result['summary']
    assert (s['orders'], s['completed'], s['active'], s['cancelled']) == (4, 3, 1, 0)
    assert s['gross_completed_halalas'] == 254600
    assert s['net_sales_halalas'] == 254100
    assert s['cash_halalas'] == 115800
    assert s['card_halalas'] == 139900
    assert s['collected_halalas'] == 255700
    assert s['refunds_halalas'] == 500
    assert s['net_collected_halalas'] == 255200
    assert {am, pm, late, pending} <= ids(result)
    old_row = next(row for row in result['rows'] if row['id'] == older)
    assert old_row['record_kind'] == 'movement'
    assert len(result['rows']) == len(ids(result))
    assert_financial_parity(actor, result)
    previous = dashboard(actor, '2026-10-27')
    assert previous['summary']['orders'] == 1
    assert previous['summary']['gross_completed_halalas'] == 3100
    assert previous['summary']['net_collected_halalas'] == 3100
    assert_financial_parity(actor, previous, '2026-10-27')
    following = dashboard(actor, '2026-10-29')
    assert following['summary']['orders'] == 0
    assert following['summary']['gross_completed_halalas'] == 0
    assert following['summary']['net_collected_halalas'] == 0
    assert_financial_parity(actor, following, '2026-10-29')
    with db.tx() as c:
        after = {name: c.execute('SELECT * FROM '+name+' ORDER BY id').fetchall()
                 for name in ('collections', 'refunds', 'orders', 'shifts')}
    assert after == before


@pytest.mark.postgres
def test_unassigned_orders_cross_four_am_once_and_never_split_at_four_pm(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        before = sale(c, 'before-four', None, 1000, DAY+'T03:59:59')
        at_four = sale(c, 'at-four', None, 2000, DAY+'T04:00:00')
        afternoon = sale(c, 'afternoon', None, 3000, DAY+'T15:59:59')
        evening = sale(c, 'at-sixteen', None, 4000, DAY+'T16:00:00')
    assert ids(dashboard(actor, '2026-10-27')) == {before}
    result = dashboard(actor)
    assert ids(result) == {at_four, afternoon, evening}
    assert result['summary']['orders'] == 3
    assert result['summary']['gross_completed_halalas'] == 9000
    assert_financial_parity(actor, result)


@pytest.mark.postgres
def test_default_operational_day_switches_at_four_am_not_midnight_or_four_pm(ops, monkeypatch):
    actor, _, _, _ = ops
    for clock, expected in (
        ('2026-10-31T00:00:00', '2026-10-30'),
        ('2026-10-31T03:59:59', '2026-10-30'),
        ('2026-10-31T04:00:00', '2026-10-31'),
        ('2026-10-31T16:00:00', '2026-10-31'),
    ):
        monkeypatch.setattr(service_hours, 'now_riyadh', lambda value=clock: at(value))
        response = actor('owner').get(API)
        assert response.status_code == 200, response.text
        period = response.json()['period']
        assert period['start'] == expected and period['end'] == expected


@pytest.mark.postgres
def test_city_branch_driver_intersections_preserve_historical_driver_moves(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES "
                  "('od-hafar-2',2,'od-source-2','ops-brand','Hafar 2','حفر الباطن'),"
                  "('od-rafha',3,'od-source-3','ops-brand','Rafha','رفحاء')")
        one = duty(c, 'one')
        two = duty(c, 'two', branch='od-hafar-2')
        three = duty(c, 'three', branch='od-rafha', driver='ops-driver2')
        four = duty(c, 'four', driver='ops-driver2')
        a = sale(c, 'filter-alpha', one, 1000, DAY+'T18:00:00', DAY+'T18:10:00')
        b = sale(c, 'filter-beta', two, 2000, DAY+'T19:00:00', DAY+'T19:10:00', branch='od-hafar-2')
        d = sale(c, 'filter-gamma', three, 3000, DAY+'T20:00:00', DAY+'T20:10:00', branch='od-rafha', driver='ops-driver2')
        e = sale(c, 'filter-delta', four, 4000, DAY+'T21:00:00', DAY+'T21:10:00', driver='ops-driver2')
        # Today's home branch must not erase a driver's historical Hafar work.
        c.execute("UPDATE users SET branch_ids='[\"od-rafha\"]' WHERE id='ops-driver'")
    assert ids(dashboard(actor)) == {a, b, d, e}
    city = dashboard(actor, city='حفر الباطن')
    assert ids(city) == {a, b, e}
    assert city['summary']['gross_completed_halalas'] == 7000
    branch = dashboard(actor, branch_id='ops-branch')
    assert ids(branch) == {a, e}
    assert branch['summary']['gross_completed_halalas'] == 5000
    driver = dashboard(actor, driver_id='ops-driver')
    assert ids(driver) == {a, b}
    assert driver['summary']['gross_completed_halalas'] == 3000
    both = dashboard(actor, city='حفر الباطن', branch_id='ops-branch', driver_id='ops-driver')
    assert ids(both) == {a}
    assert both['summary']['gross_completed_halalas'] == 1000
    assert 'ops-driver' in {item['id'] for item in branch['options']['drivers']}
    # The existing accountant endpoint keeps its current employee-scope rule;
    # compare its branch total, while the moved driver's historical amount is
    # independently asserted above against the known source order.
    assert_financial_parity(actor, branch, branch_ids='ops-branch')
    for invalid in ({'branch_id': 'ops-branch-other'}, {'driver_id': 'ops-owner'},
                    {'city': 'رفحاء', 'branch_id': 'ops-branch'}):
        response = actor('owner').get(API, params={'period': 'custom', 'start': DAY, 'end': DAY, **invalid})
        assert response.status_code in {403, 404, 422}, response.text


@pytest.mark.postgres
def test_pagination_preserves_whole_scope_totals_and_search_recomputes_scope(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        shift = duty(c, 'pagination')
        a = sale(c, 'search-alpha', shift, 1000, DAY+'T18:00:00', DAY+'T18:10:00')
        b = sale(c, 'search-beta', shift, 2000, DAY+'T19:00:00', DAY+'T19:10:00')
        d = sale(c, 'search-gamma', shift, 3000, DAY+'T20:00:00', DAY+'T20:10:00')
    result = dashboard(actor, limit=1, sort='value_desc')
    assert ids(result) == {d}
    assert result['pagination']['total'] == 3
    assert result['pagination']['next_offset'] == 1
    assert result['summary']['orders'] == 3
    assert result['summary']['gross_completed_halalas'] == 6000
    second = dashboard(actor, limit=1, offset=1, sort='value_desc')
    assert ids(second) == {b}
    assert second['summary'] == result['summary']
    selected = dashboard(actor, search='search-alpha')
    assert ids(selected) == {a}
    assert selected['summary']['orders'] == 1
    assert selected['summary']['gross_completed_halalas'] == 1000
    empty = dashboard(actor, search='not-an-order')
    assert empty['rows'] == [] and empty['summary']['orders'] == 0


@pytest.mark.postgres
def test_complimentary_and_test_void_orders_do_not_inflate_financial_cards(ops):
    actor, _, _, _ = ops
    with db.tx() as c:
        shift = duty(c, 'complimentary')
        paid = sale(c, 'paid', shift, 1000, DAY+'T18:00:00', DAY+'T18:10:00')
        free = sale(c, 'free', shift, 0, DAY+'T19:00:00', snapshot={'complimentary': {'kind': 'hospitality'}})
        void = sale(c, 'test-void', shift, 9000, DAY+'T20:00:00', test_void={'reason': 'SYNTHETIC test correction'})
    result = dashboard(actor)
    assert ids(result) == {paid, free}
    assert void not in ids(result)
    assert result['summary']['orders'] == 2
    assert result['summary']['complimentary_orders'] == 1
    assert result['summary']['gross_completed_halalas'] == 1000
    assert result['summary']['net_collected_halalas'] == 1000
    assert next(row for row in result['rows'] if row['id'] == free)['is_complimentary'] is True
    assert_financial_parity(actor, result)


@pytest.mark.postgres
def test_invalid_period_and_pagination_never_fall_back_to_unbounded_data(ops):
    actor, _, _, _ = ops
    for params in (
        {'period': 'unbounded'},
        {'period': 'custom', 'start': 'not-a-date', 'end': DAY},
        {'period': 'custom', 'start': '2026-10-29', 'end': DAY},
        {'period': 'custom', 'start': '2026-01-01', 'end': DAY},
        {'offset': -1}, {'limit': 101}, {'limit': 0}, {'sort': 'sql;delete'},
    ):
        response = actor('owner').get(API, params=params)
        assert response.status_code == 422, (params, response.text)


@pytest.mark.postgres
def test_both_brands_aggregate_only_current_owner_grants_including_projected_owner(dual, monkeypatch):
    _, actor, _, _, _ = dual
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    with db.tx() as c:
        ks = duty(c, 'kps', brand='kps', branch='kps-branch-1', manager='dual-manager', driver='dual-driver')
        es = duty(c, 'enbishan', brand='enbishan', branch='enbishan-test-1', manager='dual-manager', driver='dual-driver')
        hs = duty(c, 'enbishan-hidden', brand='enbishan', branch='enbishan-test-2', manager='dual-manager', driver='dual-driver')
        k = sale(c, 'brand-kps', ks, 1000, DAY+'T18:00:00', DAY+'T18:10:00', brand='kps', branch='kps-branch-1', driver='dual-driver', customer='dual-kps-customer', manager='dual-manager')
        e = sale(c, 'brand-enbishan', es, 2000, DAY+'T19:00:00', DAY+'T19:10:00', brand='enbishan', branch='enbishan-test-1', driver='dual-driver', customer='dual-enbishan-customer', manager='dual-manager')
        hidden = sale(c, 'brand-hidden', hs, 9000, DAY+'T20:00:00', DAY+'T20:10:00', brand='enbishan', branch='enbishan-test-2', driver='dual-driver', customer='dual-enbishan-customer', manager='dual-manager')
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-1\"]' WHERE user_id='dual-owner' AND brand_id='enbishan'")
    combined = dashboard(actor, brand_id='all')
    assert ids(combined) == {k, e}
    assert combined['summary']['gross_completed_halalas'] == 3000
    assert combined['summary']['net_collected_halalas'] == 3000
    assert {b['id'] for b in combined['options']['brands']} == {'kps', 'enbishan'}
    assert 'enbishan-test-2' not in {b['id'] for b in combined['options']['branches']}
    assert {r['brand_id']: r['gross_completed_halalas'] for r in combined['brand_breakdown']} == {'kps': 1000, 'enbishan': 2000}
    assert ids(dashboard(actor, brand_id='kps')) == {k}
    assert ids(dashboard(actor, brand_id='enbishan')) == {e}
    assert ids(dashboard(actor, brand_id='all', driver_id='dual-driver')) == {k, e}
    query = {'period': 'custom', 'start': DAY, 'end': DAY, 'brand_id': 'enbishan'}
    projected = actor('owner', 'enbishan').get(API, params=query)
    assert projected.status_code == 200, projected.text
    assert ids(projected.json()) == {e}
    denied = actor('owner', 'enbishan').get(API, params={**query, 'branch_id': 'enbishan-test-2'})
    assert denied.status_code == 403, denied.text
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    revoked = dashboard(actor, brand_id='all')
    assert ids(revoked) == {k}
    assert {b['id'] for b in revoked['options']['brands']} == {'kps'}
    assert actor('owner').get(API, params=query).status_code == 403
    assert actor('manager').get(API, params={'brand_id': 'all'}).status_code == 403
    with db.tx() as c:
        assert c.execute('SELECT id FROM orders WHERE id=%s', (hidden,)).fetchone()
        c.execute("UPDATE users SET role='admin',permissions='[\"accounting_reports\"]' WHERE id='dual-owner'")
    # The same already-authenticated session must recheck the live role.
    assert actor('owner').get(API, params={'brand_id': 'all'}).status_code == 403


@pytest.mark.postgres
def test_grant_narrowed_or_revoked_during_snapshot_is_rechecked_before_response(dual, monkeypatch):
    _, actor, _, _, _ = dual
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    with db.tx() as c:
        first = duty(c, 'race-visible', brand='enbishan', branch='enbishan-test-1', manager='dual-manager', driver='dual-driver')
        second = duty(c, 'race-hidden', brand='enbishan', branch='enbishan-test-2', manager='dual-manager', driver='dual-driver')
        visible = sale(c, 'race-visible', first, 2000, DAY+'T18:00:00', DAY+'T18:10:00', brand='enbishan', branch='enbishan-test-1', driver='dual-driver', customer='dual-enbishan-customer', manager='dual-manager')
        sale(c, 'race-hidden', second, 9000, DAY+'T19:00:00', DAY+'T19:10:00', brand='enbishan', branch='enbishan-test-2', driver='dual-driver', customer='dual-enbishan-customer', manager='dual-manager')
    original_build = accounting.build
    for change in ('narrow', 'revoke'):
        with db.tx() as c:
            c.execute("UPDATE staff_brand_memberships SET active=true,branch_ids='[\"enbishan-test-1\",\"enbishan-test-2\"]' WHERE user_id='dual-owner' AND brand_id='enbishan'")
        changed = []

        def build_then_change_grant(conn, principal, *args, **kwargs):
            result = original_build(conn, principal, *args, **kwargs)
            if principal['brand_id'] == 'enbishan' and not changed:
                # A second PostgreSQL connection commits while the first read
                # snapshot is still open: repeatable read alone cannot see it.
                with db.tx() as external:
                    assert external.info.backend_pid != conn.info.backend_pid
                    if change == 'narrow':
                        external.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-1\"]' WHERE user_id='dual-owner' AND brand_id='enbishan'")
                    else:
                        external.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
                changed.append(change)
            return result

        monkeypatch.setattr(accounting, 'build', build_then_change_grant)
        response = actor('owner').get(API, params={'brand_id': 'all', 'period': 'custom', 'start': DAY, 'end': DAY})
        assert changed == [change]
        assert response.status_code == 403, response.text
        assert 'rows' not in response.json()
        monkeypatch.setattr(accounting, 'build', original_build)
        current = dashboard(actor, brand_id='all')
        assert ids(current) == ({visible} if change == 'narrow' else set())

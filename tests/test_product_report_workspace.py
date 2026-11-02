"""Role-scoped product report: no owner dashboard privilege or write side effects."""
import pytest
from app import accounting, db
from test_accounting import accountant
from test_operations import ops
from test_owner_dashboard import DAY, duty, sale
from test_morning_manager_context_postgres import morning_manager

API = '/api/owner-dashboard/product-sales'
PARAMS = {'period': 'custom', 'start': DAY, 'end': DAY}


@pytest.mark.postgres
def test_manager_and_accountant_products_match_owner_without_owner_only_payload(ops):
    actor, _, _, _ = ops
    accountant()
    with db.tx() as c:
        shift = duty(c, 'product-workspace')
        sale(c, 'product-workspace', shift, 1500, DAY+'T18:00:00', DAY+'T18:10:00',
             snapshot={'items': [{'product_id': 'ops-product', 'name': 'Coffee', 'quantity': 3,
                                  'line_total_halalas': 1500, 'unit_price_halalas': 500}]})
        before = {name: c.execute('SELECT count(*) n FROM '+name).fetchone()['n']
                  for name in ('collections', 'refunds', 'accounting_reviews', 'closure_reports')}
    owner = actor('owner').get(API, params=PARAMS)
    assert owner.status_code == 200, owner.text
    for role in ('manager', 'accountant'):
        result = actor(role).get(API, params=PARAMS)
        assert result.status_code == 200, result.text
        assert result.json()['product_sales'] == owner.json()['product_sales']
        assert {'summary', 'rows', 'status_breakdown', 'financial_completion_blocked'}.isdisjoint(result.json())
        assert result.headers['cache-control'] == 'no-store'
        assert actor(role).get('/api/owner-dashboard').status_code == 403
    with db.tx() as c:
        assert before == {name: c.execute('SELECT count(*) n FROM '+name).fetchone()['n'] for name in before}


@pytest.mark.postgres
def test_product_report_rejects_other_roles_and_out_of_scope_filters(ops):
    actor, _, _, _ = ops
    accountant()
    for role in ('driver', 'customer', 'branch'):
        assert actor(role).get(API, params=PARAMS).status_code == 403
    for role in ('manager', 'accountant'):
        for filters in ({'brand_id': 'ops-other'}, {'city': 'UNAUTHORIZED'},
                        {'branch_id': 'ops-branch-other'}, {'driver_id': 'ops-other-driver'}):
            response = actor(role).get(API, params={**PARAMS, **filters})
            assert response.status_code == 403, response.text
        assert actor(role).get(API, params={**PARAMS, 'start': '2025-01-01'}).status_code == 422


@pytest.mark.postgres
@pytest.mark.parametrize('change', ['branch', 'permission', 'disabled'])
def test_product_report_rechecks_changes_during_projection(ops, monkeypatch, change):
    actor, _, _, _ = ops
    accountant()
    original = accounting.build
    changed = False
    def build(conn, user, *args, **kwargs):
        nonlocal changed
        result = original(conn, user, *args, **kwargs)
        if not changed:
            changed = True
            with db.tx() as other:
                if change == 'branch':
                    other.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-accountant'")
                elif change == 'permission':
                    other.execute("UPDATE users SET permissions='[]' WHERE id='ops-accountant'")
                else:
                    other.execute("UPDATE users SET active=false WHERE id='ops-accountant'")
        return result
    monkeypatch.setattr(accounting, 'build', build)
    response = actor('accountant').get(API, params=PARAMS)
    assert response.status_code == 403, response.text



@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_morning_product_report_excludes_evening_other_duty_and_other_branch(morning_manager):
    client, ids, before = morning_manager
    with db.tx() as c:
        dates = c.execute("SELECT min(reporting_operational_date) first,max(reporting_operational_date) last FROM shifts WHERE id LIKE 'ctx-%'").fetchone()
        first, last = str(dates['first']), str(dates['last'])
        for label, shift, branch, brand, period in (
                ('morning', 'ctx-driver', 'kps-branch-2', 'kps', 'morning'),
                ('bound_other', 'ctx-other-driver', 'kps-branch-2', 'kps', 'morning'),
                ('evening', 'ctx-evening-driver', 'kps-branch-2', 'kps', 'evening'),
                ('branch3', 'ctx-other-branch', 'kps-branch-3', 'kps', 'morning'),
                ('foreign', None, 'ops-branch-other', 'ops-other', 'morning')):
            sale(c, 'report-'+label, shift, 1000, first+'T08:00:00', first+'T08:30:00' if shift else None,
                 branch=branch, brand=brand, driver='ops-driver' if label=='morning' else 'ops-driver2',
                 snapshot={'delivery_period': period, 'items': [{'product_id': 'report-'+label,
                           'name': label, 'quantity': 1, 'line_total_halalas': 1000}]})
    response = client.get(API, params={'period': 'custom', 'start': first, 'end': last})
    assert response.status_code == 200, response.text
    sold = {row['product_id'] for row in response.json()['product_sales'] if row['quantity']}
    assert sold == {'report-morning'}
    assert client.get('/api/owner-dashboard').status_code == 403
    with db.tx() as c:
        after = c.execute("SELECT branch_ids,permission_denials FROM users WHERE id='ops-manager'").fetchone()
        assert after['branch_ids'] == before['branch_ids'] and after['permission_denials'] == before['permission_denials']

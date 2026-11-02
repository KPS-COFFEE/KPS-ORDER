"""Actual HTTP/auth/report wiring; isolated PostgreSQL and synthetic SMS only.

No API interceptions: the role navigation, scoped projection, filters and fuel
ledger execute the production handlers. Financial workflows stay read-only.
"""
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from app import db
from test_browser import ARTIFACTS, _staff, _context, _owner_login, chromium_browser, live_app, synthetic_mail
from test_fleet import seed
from test_owner_dashboard import DAY, duty, sale

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Explicit real Chromium opt-in required')]
PRODUCT_API = '/api/owner-dashboard/product-sales'
FUEL_API = '/api/fleet/driver-report'


def _navigation(page):
    toggle = page.locator('.dispatch-nav-toggle')
    if toggle.is_visible() and toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()
    expect(page.locator('#navigation')).to_be_visible()


def _fixture(role):
    actors = {name: _staff(value) for name, value in (
        ('owner', 'owner'), ('accountant', 'admin'), ('gm', 'admin'),
        ('manager', 'delivery_manager'), ('driver', 'driver'), ('customer', 'customer'))}
    permissions = {
        'accountant': ['accounting_reports', 'accounting_review'],
        'gm': ['accounting_reports', 'vehicle_maintenance_manage'],
        'manager': ['reports_view', 'orders_view', 'shifts_view'],
    }
    with db.tx() as conn:
        for name, grants in permissions.items():
            conn.execute('UPDATE users SET permissions=%s WHERE id=%s',
                         (db.json(grants), actors[name]['id']))
        shift = duty(conn, 'role-browser', brand='kps', branch='kps-branch-1',
                     manager=actors['manager']['id'], driver=actors['driver']['id'])
        sale(conn, 'role-browser', shift, 1650, DAY+'T18:00:00', DAY+'T18:10:00',
             brand='kps', branch='kps-branch-1', driver=actors['driver']['id'],
             manager=actors['manager']['id'], customer=actors['customer']['id'],
             snapshot={'items': [{'product_id': 'kps-05', 'name': 'قهوة اليوم بارد',
                                 'quantity': 3, 'line_total_halalas': 1650, 'unit_price_halalas': 550}]})
        seed(conn, 'role-browser-fuel', 'TEST ROLE FUEL', 12,
             driver=actors['driver']['id'], brand='kps', bids=['kps-branch-1'],
             km=210, liters=21, state='open')
        conn.execute("UPDATE shifts SET vehicle_log=jsonb_set(jsonb_set(vehicle_log,'{refuels,0,amount_halalas}','4200'),'{end}',%s),status='closed' WHERE id='role-browser-fuel'",
                     (db.json({'odometer_km': 1210, 'fuel_percent': 100}),))
    return actors[role], actors['driver']['id']


def _response(path, **filters):
    def matches(response):
        parsed = urlsplit(response.url)
        values = parse_qs(parsed.query)
        return parsed.path == path and all(values.get(key) == [value] for key, value in filters.items())
    return matches


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('role', ['accountant', 'gm', 'manager', 'owner'])
def test_role_report_navigation_filters_and_financial_read_only(live_app, chromium_browser, role, width):
    actor, driver_id = _fixture(role)
    context, page, errors = _context(chromium_browser, live_app)
    mutations = []
    report_failures = []
    try:
        _owner_login(context, live_app, actor)
        page.set_viewport_size({'width': width, 'height': 950})
        page.on('request', lambda request: mutations.append(request.url)
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and
                urlsplit(request.url).path.startswith(('/api/accounting/', '/api/cash-bags', '/api/shifts', '/api/collections', '/api/refunds')) else None)
        page.on('response', lambda response: report_failures.append((urlsplit(response.url).path, response.status))
                if response.status >= 400 and urlsplit(response.url).path in
                (PRODUCT_API, FUEL_API, FUEL_API+'/options', '/api/accounting/manager-cards',
                 '/api/order-financial-cases', '/api/driver-floats') else None)
        page.goto(live_app+'/ops', wait_until='networkidle')
        _navigation(page)
        if role != 'owner':
            expect(page.locator('#navigation [data-page=ownerDashboard]')).to_have_count(0)
            denied = context.request.get(live_app+'/api/owner-dashboard', headers={'X-KPS-Context': 'work'})
            assert denied.status == 403
        page.locator('#navigation [data-page=productSales]').click()
        expect(page.locator('#product-sales')).to_be_visible()
        expect(page.locator('#owner-period')).to_be_enabled()
        expect(page.locator('#owner-results')).to_be_hidden()
        expect(page.locator('#owner-details')).to_be_hidden()
        expect(page.locator('.owner-access-badge')).to_have_text('تقرير للاطلاع')
        page.locator('#owner-period').select_option('custom')
        page.locator('#owner-start').fill(DAY)
        page.locator('#owner-end').fill(DAY)
        with page.expect_response(_response(PRODUCT_API, period='custom', start=DAY, end=DAY)) as product_response:
            page.locator('#owner-custom-dates button[type=submit]').click()
        assert product_response.value.status == 200, product_response.value.text()
        payload = product_response.value.json()
        assert {'rows', 'summary', 'financial_completion_blocked'}.isdisjoint(payload)
        sold = [row for row in payload['product_sales'] if row['quantity']]
        assert len(sold) == 1 and sold[0]['quantity'] == 3 and sold[0]['gross'] == 1650
        with page.expect_response(_response(PRODUCT_API, branch_id='kps-branch-1')) as branch_response:
            page.locator('#owner-branch').select_option('kps-branch-1')
        assert branch_response.value.status == 200
        with page.expect_response(_response(PRODUCT_API, driver_id=driver_id)) as driver_response:
            page.locator('#owner-driver').select_option(driver_id)
        assert driver_response.value.status == 200
        page.locator('#owner-product-search').fill('قهوة اليوم بارد')
        expect(page.locator('#owner-products tbody tr')).to_have_count(1)
        expect(page.locator('#owner-products')).to_contain_text('قهوة اليوم بارد')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        ARTIFACTS.mkdir(exist_ok=True)
        page.screenshot(path=str(ARTIFACTS/f'role-products-{role}-{width}.png'), full_page=True)
        _navigation(page)
        if role in ('owner', 'gm'):
            with page.expect_response(_response(FUEL_API+'/options')) as fuel_options:
                page.locator('#navigation [data-page=driverFuel]').click()
            assert fuel_options.value.status == 200
            form = page.locator('#driver-fuel-filters')
            expect(form).to_be_visible()
            form.locator('[name=start]').fill('2026-10-12')
            form.locator('[name=end]').fill('2026-10-12')
            form.locator('[name=driver_id]').select_option(driver_id)
            with page.expect_response(_response(FUEL_API, start='2026-10-12', end='2026-10-12', driver_id=driver_id)) as fuel_response:
                form.locator('[type=submit]').click()
            assert fuel_response.value.status == 200, fuel_response.value.text()
            fuel = fuel_response.value.json()
            assert fuel['total'] == 1 and fuel['summary']['distance_km'] == 210
            assert fuel['summary']['liters'] == 21 and fuel['summary']['fuel_amount_halalas'] == 4200
            expect(page.locator('[data-fuel-result]')).to_contain_text('TEST ROLE FUEL')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ARTIFACTS/f'role-fuel-{role}-{width}.png'), full_page=True)
        else:
            expect(page.locator('#navigation [data-page=driverFuel]')).to_have_count(0)
            assert context.request.get(live_app+FUEL_API, headers={'X-KPS-Context': 'work'}).status == 403
        assert not mutations, mutations
        assert not report_failures, report_failures
        assert not errors, errors
    finally:
        context.close()

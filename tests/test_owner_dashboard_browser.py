"""Exercise the actual owner dashboard and ops entry point with synthetic IO.

All browser requests are intercepted. No user session, production host, database,
or external service is contacted. Use TEST_CHROMIUM_EXECUTABLE for a local browser.
"""
import copy
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BRANDS = [{'id': 'kps', 'name': 'كبز'}, {'id': 'enbishan', 'name': 'عنبيشن'}]
BRANCHES = [
    {'id': 'qa-kps', 'name': 'فرع كبز', 'branch_no': 2, 'city': 'حفر الباطن', 'brand_id': 'kps', 'brand_name': 'كبز'},
    {'id': 'qa-enbishan', 'name': 'فرع عنبيشن', 'branch_no': 1, 'city': 'رفحاء', 'brand_id': 'enbishan', 'brand_name': 'عنبيشن'},
]
DRIVERS = [
    {'id': 'qa-driver-a', 'name': 'سائق تجريبي أ', 'active': True, 'branch_ids': ['qa-kps'], 'brand_ids': ['kps']},
    {'id': 'qa-driver-b', 'name': 'سائق تجريبي ب', 'active': True, 'branch_ids': ['qa-enbishan'], 'brand_ids': ['enbishan']},
]


def synthetic_rows():
    rows = []
    for i, (brand, branch, driver, status, value) in enumerate((
        ('kps', BRANCHES[0], DRIVERS[0], 'completed', 12500),
        ('enbishan', BRANCHES[1], DRIVERS[1], 'active', 7500),
        ('kps', BRANCHES[0], DRIVERS[0], 'cancelled', 5000),
    )):
        rows.append({
            'id': f'qa-order-{i}', 'reference': f'QA-{i+1:04}', 'order_no': 900+i,
            'brand_id': brand, 'brand_name': branch['brand_name'],
            'branch_id': branch['id'], 'branch_name': branch['name'], 'city': branch['city'],
            'driver_id': driver['id'], 'driver_name': driver['name'], 'status': status,
            'payment_status': 'paid' if status == 'completed' else 'unpaid',
            'operational_date': '2026-10-28', 'created_at': f'2026-10-28T{18+i}:00:00+03:00',
            'value_halalas': value, 'net_collected_halalas': value if status == 'completed' else 0,
            'refunds_halalas': 0, 'record_kind': 'order', 'is_complimentary': False,
        })
    return rows


def summary(rows):
    value = sum(row['value_halalas'] for row in rows if row['status'] == 'completed')
    return {
        'orders': len(rows), **{state: sum(row['status'] == state for row in rows) for state in ('completed', 'active', 'cancelled')},
        'gross_completed_halalas': value, 'net_sales_halalas': value,
        'cash_halalas': value, 'card_halalas': 0, 'collected_halalas': value,
        'refunds_halalas': 0, 'net_collected_halalas': value,
        'complimentary_orders': 0, 'complimentary_value_halalas': 0,
    }


def payload(url):
    query = {key: values[-1] for key, values in parse_qs(urlsplit(url).query).items()}
    brand = query.get('brand_id', 'all')
    branches = [b for b in BRANCHES if brand == 'all' or b['brand_id'] == brand]
    cities = sorted({b['city'] for b in branches})
    if query.get('city'):
        branches = [b for b in branches if b['city'] == query['city']]
    selected_branch_ids = {b['id'] for b in branches}
    drivers = [d for d in DRIVERS if selected_branch_ids.intersection(d['branch_ids'])]
    if query.get('branch_id'):
        drivers = [d for d in drivers if query['branch_id'] in d['branch_ids']]
    rows = synthetic_rows()
    for field in ('brand_id', 'city', 'branch_id', 'driver_id'):
        if query.get(field) and query[field] != 'all':
            rows = [row for row in rows if row[field] == query[field]]
    if query.get('search'):
        rows = [row for row in rows if query['search'].lower() in row['reference'].lower()]
    sort = query.get('sort', 'created_desc')
    rows.sort(key=lambda row: row['value_halalas'] if sort.startswith('value') else row['created_at'], reverse=sort.endswith('desc'))
    total = len(rows)
    offset, limit = int(query.get('offset', 0)), 2
    s = summary(rows)
    return {
        'brand': {'id': brand, 'name': next((b['name'] for b in BRANDS if b['id'] == brand), 'جميع البراندات المخولة')},
        'filters': {'brand_id': brand},
        'period': {'preset': query.get('period', 'today'), 'start': query.get('start', '2026-10-28'), 'end': query.get('end', '2026-10-28'),
                   'timezone': 'Asia/Riyadh', 'basis': 'manager_shift', 'fallback_start': '04:00',
                   'definition': 'الصباحي والمسائي حسب يوم شفت المدير، مع حفظ وقت الحركة الفعلي.'},
        'options': {'brands': BRANDS, 'cities': [{'id': c, 'name': c} for c in cities], 'branches': branches, 'drivers': drivers},
        'summary': s, 'status_breakdown': [{'status': state, 'count': s[state]} for state in ('completed', 'active', 'cancelled')],
        'brand_breakdown': [{'brand_id': b['id'], 'brand_name': b['name'], **summary([r for r in rows if r['brand_id'] == b['id']])}
                            for b in BRANDS if brand == 'all' or b['id'] == brand],
        'trend': [{'day': '2026-10-28', **s}], 'rows': rows[offset:offset+limit],
        'pagination': {'offset': offset, 'limit': limit, 'total': total, 'next_offset': offset+limit if offset+limit < total else None},
        'generated_at': '2026-10-28T23:15:00+03:00', 'financial_completion_blocked': False,
        'scope_note': 'بيانات اختبار اصطناعية فقط.',
    }


def browser_launch(playwright):
    options = {'headless': True, 'args': ['--no-sandbox', '--disable-gpu']}
    if os.getenv('TEST_CHROMIUM_EXECUTABLE'):
        options['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
    return playwright.chromium.launch(**options)


def owner(brand='kps', both=True):
    return {'id': 'synthetic-owner', 'role': 'owner', 'brand_id': brand,
            'display_name': 'مالك تجريبي', 'branch_ids': [b['id'] for b in BRANCHES],
            'effective_permissions': [],
            **({'central_operations': {'brands': BRANDS}} if both else {})}


@pytest.mark.parametrize('width', [390, 1440])
def test_owner_widget_filters_pagination_refresh_faults_and_mobile_layout(width, tmp_path):
    source = (ROOT/'public/assets/owner-dashboard.js').read_text()
    css = (ROOT/'public/assets/owner-dashboard.css').read_text()
    base_css = (ROOT/'public/ops.css').read_text()
    control = {'mode': 'ok', 'requests': [], 'held': []}
    with sync_playwright() as p:
        browser = browser_launch(p)
        context = browser.new_context(viewport={'width': width, 'height': 950})
        page = context.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            url = route.request.url
            path = urlsplit(url).path
            if path == '/owner-qa':
                return route.fulfill(content_type='text/html; charset=utf-8', body='<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><main id="owner-dashboard"></main></html>')
            if path == '/api/owner-dashboard':
                control['requests'].append(url)
                if control['mode'] == 'hold':
                    control['held'].append(route)
                    return
                if control['mode'] == 'error':
                    return route.fulfill(status=503, json={'detail': {'message': 'تعذر تحميل بيانات الاختبار'}})
                if control['mode'] == 'denied':
                    return route.fulfill(status=403, json={'detail': {'message': 'Owner access revoked'}})
                return route.fulfill(json=payload(url))
            return route.abort()

        context.route('**/*', intercept)
        page.goto('https://qa.invalid/owner-qa')
        page.add_style_tag(content=base_css+css)
        page.add_script_tag(content=source)
        page.evaluate('''async user=>{
            window.syntheticUser=user; window.syntheticScope='owner/kps';
            window.openedOrders=[]; window.accessDenied=[];
            window.widget=KPSOwnerDashboard.create({getUser:()=>syntheticUser,getScope:()=>syntheticScope,
                getLang:()=> 'ar',isActive:()=>true,onOrder:(id,brand)=>openedOrders.push({id,brand}),
                onAccessDenied:e=>accessDenied.push(e.status),
                api:async (url,options)=>{const response=await fetch(url,options);const body=await response.json();
                    if(!response.ok){const error=new Error(body.detail.message);error.status=response.status;throw error;}return body;}});
            await widget.mount(document.querySelector('#owner-dashboard'));
        }''', owner())
        expect(page.locator('#owner-brand')).to_have_value('all')
        expect(page.locator('#owner-dashboard')).to_have_attribute('dir', 'rtl')
        count = page.locator('.owner-metric').filter(has=page.get_by_role('heading', name='إجمالي الطلبات', exact=True)).locator('.owner-number')
        expect(count).to_have_text('3')
        assert page.locator('#owner-orders tbody tr').count() == 2
        expect(page.locator('[data-owner-order]').first).to_contain_text('QA-0002')
        expect(page.locator('[data-owner-order]').first).not_to_contain_text('901')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not re.search('[٠-٩]', page.locator('#owner-metrics').inner_text())
        page.screenshot(path=str(tmp_path/f'owner-dashboard-{width}.png'), full_page=True)

        page.locator('[data-owner-action=next]').click()
        expect(page.locator('#owner-pagination')).to_contain_text('3–3')
        expect(count).to_have_text('3')
        assert 'offset=2' in control['requests'][-1]
        page.locator('[data-owner-action=prev]').click()
        expect(page.locator('#owner-pagination')).to_contain_text('1–2')

        for brand, expected in [('kps', '2'), ('enbishan', '1'), ('all', '3')]:
            page.locator('#owner-brand').select_option(brand)
            expect(count).to_have_text(expected)
            assert f'brand_id={brand}' in control['requests'][-1]

        page.locator('#owner-city').select_option('حفر الباطن')
        expect(count).to_have_text('2')
        page.locator('#owner-branch').select_option('qa-kps')
        page.locator('#owner-driver').select_option('qa-driver-a')
        expect(page.locator('#owner-driver')).to_have_value('qa-driver-a')
        page.locator('#owner-city').select_option('رفحاء')
        expect(count).to_have_text('1')
        expect(page.locator('#owner-branch')).to_have_value('')
        expect(page.locator('#owner-driver')).to_have_value('')
        assert 'branch_id=' not in control['requests'][-1] and 'driver_id=' not in control['requests'][-1]

        # Repeated refreshes preserve the form nodes, filters and visible totals.
        page.evaluate("()=>{window.originalFilter=document.querySelector('#owner-city');window.originalMetrics=document.querySelector('#owner-metrics').innerHTML}")
        control['mode'] = 'error'
        page.evaluate('()=>widget.refresh({force:true})')
        expect(page.locator('#owner-notice')).to_contain_text('آخر بيانات ناجحة')
        assert page.evaluate("()=>originalFilter===document.querySelector('#owner-city')")
        assert page.locator('#owner-metrics').inner_html() == page.evaluate('()=>originalMetrics')
        expect(page.locator('#owner-city')).to_have_value('رفحاء')
        control['mode'] = 'ok'
        page.evaluate('()=>widget.refresh({force:true})')
        expect(count).to_have_text('1')

        # A failure for a different cohort must not retain misleading old values.
        control['mode'] = 'error'
        page.locator('#owner-city').select_option('حفر الباطن')
        expect(page.locator('#owner-orders')).to_contain_text('تعذر تحميل البيانات')
        assert page.locator('.owner-metric').count() == 0
        control['mode'] = 'ok'
        page.locator('[data-owner-action=refresh]').first.click()
        expect(count).to_have_text('2')

        # An older request must not overwrite a newer filter selection.
        control['mode'] = 'hold'
        page.evaluate('()=>{window.slowRefresh=widget.refresh({force:true})}')
        page.wait_for_timeout(100)
        assert control['held']
        control['mode'] = 'ok'
        page.locator('#owner-city').select_option('رفحاء')
        expect(count).to_have_text('1')
        for pending in control['held']:
            try:
                pending.fulfill(json=payload(pending.request.url))
            except Exception:
                pass  # The real AbortController may already have cancelled it.
        control['held'].clear()
        page.evaluate('()=>slowRefresh')
        expect(page.locator('#owner-city')).to_have_value('رفحاء')
        expect(count).to_have_text('1')

        page.locator('#owner-period').select_option('custom')
        page.locator('#owner-start').fill('2026-10-27')
        page.locator('#owner-end').fill('2026-10-28')
        requests_before = len(control['requests'])
        page.evaluate('()=>widget.refresh()')
        assert len(control['requests']) == requests_before
        expect(page.locator('#owner-start')).to_have_value('2026-10-27')
        page.get_by_role('button', name='تطبيق الفترة', exact=True).click()
        expect(page.locator('#owner-period-basis')).to_contain_text('2026-10-27')
        assert 'start=2026-10-27' in control['requests'][-1] and 'end=2026-10-28' in control['requests'][-1]
        page.locator('[data-owner-order]').first.click()
        assert page.evaluate('()=>openedOrders') == [{'id': 'qa-order-1', 'brand': 'enbishan'}]
        # A same-filter refresh must not swallow a visible invoice click.
        control['mode'] = 'hold'
        page.evaluate('()=>{window.linkRefresh=widget.refresh({force:true})}')
        page.wait_for_timeout(100)
        assert control['held']
        page.locator('[data-owner-order]').first.click()
        assert page.evaluate('()=>openedOrders') == [{'id': 'qa-order-1', 'brand': 'enbishan'}]*2
        for pending in control['held']:
            pending.fulfill(json=payload(pending.request.url))
        control['held'].clear()
        control['mode'] = 'ok'
        page.evaluate('()=>linkRefresh')


        control['mode'] = 'denied'
        page.evaluate('()=>widget.refresh({force:true})')
        assert page.locator('#owner-dashboard').inner_html() == ''
        assert page.evaluate('()=>accessDenied') == [403]
        before = len(control['requests'])
        page.evaluate("async()=>{syntheticUser.role='driver';await widget.mount(document.querySelector('#owner-dashboard'))}")
        assert len(control['requests']) == before
        assert not errors, errors
        context.close()
        browser.close()


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_actual_ops_lands_owners_on_dashboard_and_rejects_other_roles(brand):
    source = (ROOT/'public/ops.js').read_text()
    source, count = re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
        '  window.qa={state,showApp,canPage,navigation,loadPage,refreshOperationalAlerts};\n})();', source)
    assert count == 1, 'Acceptance hook only replaces automatic startup'
    html = re.sub(r'<script[\s\S]*?</script>|<link[^>]*>', '', (ROOT/'public/ops.html').read_text())
    requests, request_headers = [], []
    with sync_playwright() as p:
        browser = browser_launch(p)
        context = browser.new_context(viewport={'width': 390, 'height': 950})
        page = context.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == '/ops':
                return route.fulfill(content_type='text/html; charset=utf-8', body=html)
            if path == '/api/owner-dashboard':
                requests.append(route.request.url)
                request_headers.append(route.request.headers)
                return route.fulfill(json=payload(route.request.url))
            if path == '/api/operations/alerts':
                return route.fulfill(json={'unassigned': [{'id': 'qa-order-0', 'order_no': 'QA-0001'}]})
            if path == '/api/conversations/alerts':
                return route.fulfill(json={'awaiting_reply': [{'id': 'qa-chat', 'sender_name': 'رسالة تجريبية', 'last_message': 'نص الرسالة'}]})
            if path == '/api/conversations':
                return route.fulfill(json={'conversations': [{'id': 'qa-chat', 'sender_name': 'رسالة تجريبية'}]})
            if path == '/api/conversations/qa-chat/messages':
                return route.fulfill(json={'conversation': {'id': 'qa-chat', 'can_reply': True, 'can_manage': False},
                    'messages': [{'id': 'qa-message', 'text': 'نص الرسالة', 'created_at': '2026-10-31T10:00:00Z'}]})
            if path == '/api/catalog':
                return route.fulfill(json={'brand': {'id': brand, 'name': brand, 'theme': {}}, 'products': []})
            if path == '/api/branches':
                return route.fulfill(json={'branches': BRANCHES})
            if path == '/api/operations/people':
                return route.fulfill(json={'users': []})
            if path == '/api/shifts/current':
                return route.fulfill(json={'shifts': []})
            if path.startswith('/api/'):
                return route.fulfill(json={})
            return route.abort()

        context.route('**/*', intercept)
        page.goto('https://qa.invalid/ops')
        page.add_style_tag(content=(ROOT/'public/ops.css').read_text()+(ROOT/'public/assets/owner-dashboard.css').read_text())
        page.evaluate('''()=>{window.setInterval=()=>0;
            window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset:()=>{}})};
            window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset:()=>{}})};}''')
        page.add_script_tag(content=(ROOT/'public/assets/owner-dashboard.js').read_text())
        page.add_script_tag(content=source)
        page.evaluate('''user=>{qa.state.user=user;qa.state.csrf='synthetic-only';qa.state.page='orders';qa.showApp();}''', owner(brand, both=False))
        expect(page.locator('#owner-filters')).to_be_visible()
        assert page.evaluate('()=>qa.state.page') == 'ownerDashboard'
        expect(page.locator('#navigation [data-page=ownerDashboard]')).to_have_attribute('aria-current', 'page')
        expect(page.locator('#owner-brand')).to_have_value(brand)
        assert requests and f'brand_id={brand}' in requests[0]
        assert 'x-operations-brand' not in request_headers[0], 'A normal brand session must not require the multibrand rollout'
        assert request_headers[0]['x-kps-context'] == 'work'
        notices=page.locator('[data-alert-group=owner-notifications]')
        messages=page.locator('[data-alert-group=messages]')
        expect(notices).to_be_visible()
        assert not notices.evaluate('el=>el.open') and not messages.evaluate('el=>el.open')
        expect(page.get_by_role('button', name='فتح المحادثة والرد')).not_to_be_visible()
        messages.locator('summary').click()
        expect(page.get_by_role('button', name='فتح المحادثة والرد')).to_be_visible()
        # Force rerender with a different latest message; user's open choice survives.
        page.evaluate('''async()=>{document.querySelector('#operational-alerts').dataset.rendered='';await qa.refreshOperationalAlerts()}''')
        assert messages.evaluate('el=>el.open') and not notices.evaluate('el=>el.open')
        messages.locator('summary').click()
        assert not messages.evaluate('el=>el.open')
        notices.locator('summary').click()
        expect(page.get_by_role('button', name='QA-0001', exact=True)).to_be_visible()
        notices.locator('summary').click()
        messages.locator('summary').click()
        page.get_by_role('button', name='فتح المحادثة والرد').click()
        expect(page.locator('#chat-detail')).to_contain_text('نص الرسالة')
        page.get_by_role('button', name='لوحة الملاك', exact=True).click()
        expect(page.locator('#owner-filters')).to_be_visible()

        page.evaluate('''async user=>{qa.state.user=user;await qa.loadPage();}''', owner(brand, both=True))
        expect(page.locator('#owner-brand')).to_have_value('all')
        assert 'brand_id=all' in requests[-1]
        assert request_headers[-1]['x-operations-brand'] == brand, 'Central aggregation must bypass facade fan-out using the authorized home context'
        for role in ['delivery_manager', 'delivery_staff', 'branch_manager', 'driver', 'admin', 'customer']:
            assert page.evaluate("role=>{qa.state.user.role=role;return qa.canPage('ownerDashboard')}", role) is False
        assert not errors, errors
        context.close()
        browser.close()

"""Owner CRM browser acceptance on isolated synthetic PostgreSQL only."""
import os
import pytest
from app import db
from test_browser import live_app, synthetic_mail, chromium_browser, _staff, _owner_login, ARTIFACTS
from tests.test_core import _make_user
from tests.test_customer_crm import seed_order

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance only')]


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_owner_crm_real_browser_counts_preferences_history_and_relogin(live_app, chromium_browser, lang):
    from playwright.sync_api import expect
    owner = _staff()
    people = [_make_user() for _ in range(5)]
    registered, historical = _make_user(), _make_user()
    with db.tx() as c:
        for person in [*people, registered, historical]:
            c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (person['id'],))
        c.execute('UPDATE users SET display_name=%s WHERE id=%s', ('TEST Loyal Customer', people[0]['id']))
        c.execute('UPDATE users SET display_name=%s WHERE id=%s', ('TEST Registration Only', registered['id']))
        c.execute('UPDATE users SET display_name=%s WHERE id=%s', ('TEST Retained Historical', historical['id']))
        for person in people:
            seed_order(c, person, area='TEST District', refund=300, created_at='2026-10-17T00:00:00Z')
        seed_order(c, people[0], area='TEST District', created_at='2026-10-18T00:00:00Z')
        seed_order(c, people[0], area='TEST District', refund=1900)  # fully refunded
        seed_order(c, people[0], amount=90000, test_data=True)
        seed_order(c, people[0], amount=90000, created_at='2026-10-16T00:00:00Z')
        seed_order(c, historical, amount=90000)
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'operational_period_started_at': '2026-10-17T00:00:00Z', 'historical_test_customer_ids': [historical['id']]}),))
        before = c.execute('SELECT id,snapshot,customer,total_halalas FROM orders ORDER BY id').fetchall()
    context = chromium_browser.new_context(locale='ar-SA' if lang == 'ar' else 'en-US', timezone_id='Asia/Riyadh', viewport={'width':1280,'height':900})
    _owner_login(context, live_app, owner)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_app + '/customer-crm')
    expect(page.locator('#customers tbody tr')).to_have_count(6)
    if lang == 'en':
        page.locator('#language').click()
    expect(page.locator('html')).to_have_attribute('lang', lang)
    expect(page.locator('html')).to_have_attribute('dir', 'rtl' if lang == 'ar' else 'ltr')
    cards = page.locator('#totals article strong')
    assert cards.all_text_contents()[:4] == ['6', '5', '6', '1']
    digits = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
    assert '99' in cards.nth(4).inner_text().translate(digits)  # 5 * 16 + 19 SAR net of refunds
    expect(page.locator('#customers')).not_to_contain_text('TEST Retained Historical')
    row = page.locator('#customers tbody tr').filter(has_text='TEST Loyal Customer')
    values = row.locator('td').all_text_contents()
    assert values[6] == '2' and '35' in values[7].translate(digits)
    assert values[4] != values[5]  # first and latest qualifying dates
    registration = page.locator('#customers tbody tr').filter(has_text='TEST Registration Only')
    assert registration.locator('td').nth(6).inner_text() == '0'
    assert registration.locator('td').nth(4).inner_text() == '—'
    expect(page.locator('#areas tbody tr')).to_have_count(1)
    assert page.locator('#areas tbody td').all_text_contents() == ['City', 'TEST District', '5', '6', 'kps-branch-2']
    assert page.locator('#branches tbody td').all_text_contents()[:3] == ['kps-branch-1', '6', '5']
    row.locator('button').click()
    expect(page.locator('#detail')).to_contain_text('kps-branch-2')
    expect(page.locator('#detail')).to_contain_text('kps-branch-1')
    expect(page.locator('#detail tbody tr')).to_have_count(1)
    assert page.locator('#detail tbody td').all_text_contents()[:4] == ['kps-branch-2', 'kps-branch-1', 'delivery', '2']
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'customer-crm-owner-{lang}.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Close the session context, authenticate again, and verify source-backed data persists.
    context.close()
    context = chromium_browser.new_context()
    _owner_login(context, live_app, owner)
    page = context.new_page()
    page.goto(live_app + '/customer-crm')
    expect(page.locator('#customers tbody tr')).to_have_count(6)
    payload = context.request.get(live_app + '/api/crm/overview')
    assert payload.status == 200
    assert payload.json()['totals']['net_spending_halalas'] == 9900
    assert payload.headers['cache-control'] == 'no-store'
    # Server checks the current role on existing sessions and direct API requests.
    with db.tx() as c:
        c.execute("UPDATE users SET role='delivery_manager' WHERE id=%s", (owner['id'],))
    for route in ['/customer-crm', '/api/crm/overview', '/api/crm/customers', '/api/crm/customers/' + people[0]['id']]:
        assert context.request.get(live_app + route).status == 403
    with db.tx() as c:
        after = c.execute('SELECT id,snapshot,customer,total_halalas FROM orders ORDER BY id').fetchall()
        assert before == after
    assert not errors
    context.close()

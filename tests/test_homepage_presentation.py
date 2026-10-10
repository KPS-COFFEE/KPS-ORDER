"""Owner-approved marquee, image-banner rotation, and future verse controls."""
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import admin, db
from test_admin import owner
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail


def test_homepage_presentation_validation_and_safe_defaults():
    default = admin.homepage_presentation_config(brand_id='kps')
    assert default['ticker_enabled'] and default['ticker_lines'] == ['طلبك، على طريقتك']
    assert not default['quote_enabled'] and default['quote_text'] == ''
    assert not admin.homepage_presentation_config(brand_id='another-brand')['ticker_enabled']
    for bad in [
        {'ticker_enabled': 'yes'},
        {'ticker_enabled': True, 'ticker_lines': []},
        {'ticker_seconds': 0},
        {'ticker_seconds': 91},
        {'ticker_lines': ['x'] * 9},
        {'ticker_lines': ['']},
        {'ticker_lines': ['x' * 141]},
        {'quote_enabled': True, 'quote_text': ''},
        {'quote_text': 'x' * 241},
        {'unexpected': True},
    ]:
        with pytest.raises(HTTPException):
            admin.homepage_presentation_config(changes=bad, brand_id='kps')


@pytest.mark.postgres
def test_owner_settings_public_filtered_quote_and_preservation(owner):
    client, mutate = owner
    before = client.get('/api/admin/config').json()
    with db.tx() as conn:
        fingerprint = db.preservation_snapshot(conn)

    public = client.get('/api/banners').json()
    assert public['presentation']['ticker_enabled'] is True
    assert public['presentation']['quote_text'] == ''
    assert public['presentation']['ticker_lines'] == ['طلبك، على طريقتك']

    changed = {
        'ticker_enabled': True,
        'ticker_lines': ['العبارة الأولى', 'العبارة الثانية'],
        'ticker_seconds': 36,
        'quote_enabled': False,
        'quote_text': 'نص أدبي محفوظ ولا يعرض بعد',
    }
    result = mutate('PATCH', '/api/admin/settings', {'homepage_presentation': changed})
    assert result.status_code == 200, result.text
    visible = client.get('/api/banners').json()['presentation']
    assert visible['ticker_lines'] == changed['ticker_lines']
    assert visible['ticker_seconds'] == 36
    assert visible['quote_text'] == ''

    enabled = mutate('PATCH', '/api/admin/settings',
                     {'homepage_presentation': {'quote_enabled': True}})
    assert enabled.status_code == 200, enabled.text
    assert client.get('/api/banners').json()['presentation']['quote_text'] == changed['quote_text']
    assert mutate('PATCH', '/api/admin/settings',
                  {'homepage_presentation': {'ticker_enabled': True, 'ticker_lines': []}}).status_code == 422

    off = mutate('PATCH', '/api/admin/settings',
                 {'homepage_presentation': {'ticker_enabled': False, 'quote_enabled': False}})
    assert off.status_code == 200, off.text
    pub = client.get('/api/banners').json()['presentation']
    assert pub['ticker_lines'] == [] and pub['quote_text'] == ''
    assert pub['ticker_enabled'] is False
    after = client.get('/api/admin/config').json()
    assert before['products'] == after['products']
    assert before['branches'] == after['branches']
    with db.tx() as conn:
        assert db.preservation_snapshot(conn) == fingerprint
    client.post('/api/auth/logout')
    assert mutate('PATCH', '/api/admin/settings',
                  {'homepage_presentation': {'ticker_enabled': True}}).status_code in (401, 403)


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Chromium integration run required.')
def test_customer_banner_no_watermark_and_owner_rotating_presentation(live_app, chromium_browser):
    from playwright.sync_api import expect
    context, page, errors = _context(chromium_browser, live_app)
    owner_headers = _owner_login(context, live_app, _staff('owner'))
    body = {'homepage_presentation': {
        'ticker_enabled': True, 'ticker_lines': ['عبارة تشغيل تجريبية', 'عبارة ثانية'],
        'ticker_seconds': 20, 'quote_enabled': False, 'quote_text': 'بيت شعر غير منشور',
    }}
    response = context.request.patch(live_app + '/api/admin/settings',
                                     headers=owner_headers, data=body)
    assert response.status == 200, response.text()
    for i in range(2):
        created = context.request.post(
            live_app + '/api/admin/banners',
            headers={**owner_headers, 'Idempotency-Key': 'presentation-test-' + uuid4().hex},
            data={'title': f'إعلان تجربة {i+1}', 'body': 'لا عروض تجارية',
                  'sort_order': i, 'active': True, 'branch_ids': []})
        assert created.status == 200, created.text()
    page.goto('/')
    expect(page.locator('#announcement-ticker')).to_be_visible()
    expect(page.locator('.ticker-cycle').first).to_contain_text('عبارة تشغيل تجريبية')
    expect(page.locator('.banner-mark')).to_have_count(0)
    expect(page.locator('.banner-dot')).to_have_count(2)
    expect(page.locator('.banner-dot[aria-current=true]')).to_have_text('١')
    page.locator('#ticker-pause').click()
    assert 'is-paused' in (page.locator('#announcement-track').get_attribute('class') or '')
    page.locator('#banner-controls .banner-dot').last.click()
    expect(page.locator('#banner-content')).to_contain_text('إعلان تجربة 2')
    page.mouse.move(0, 0)
    page.wait_for_timeout(6800)
    expect(page.locator('#banner-content')).to_contain_text('إعلان تجربة 1')
    page.set_viewport_size({'width': 390, 'height': 820})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('#announcement-track').evaluate(
        'el => getComputedStyle(el).animationName') == 'none'
    assert not errors
    context.close()

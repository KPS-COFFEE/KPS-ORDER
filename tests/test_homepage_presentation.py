"""Owner-approved marquee, image-banner rotation, and future verse controls."""
import os
from uuid import uuid4

import pytest
import psycopg
from psycopg import sql
from fastapi import HTTPException

from app import admin, db
from app.exports import build_archive, restore_archive
from test_admin import owner
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail
from tests.sms_policy_helpers import sms_step_up


def _approve_advertising(client, mutate, monkeypatch):
    with db.tx() as conn:
        principal = conn.execute("SELECT * FROM users WHERE id='admin-test-owner'").fetchone()
    sms_step_up(client, principal, monkeypatch)
    current = client.get('/api/advertising/policy').json()['policy']
    approved = mutate('PUT', '/api/advertising/policy', {
        'advertising_enabled': True, 'expected_version': current['version'],
        'reason': 'Explicit owner approval in isolated presentation test',
    })
    assert approved.status_code == 200, approved.text


def test_homepage_presentation_validation_and_safe_defaults():
    default = admin.homepage_presentation_config(brand_id='kps')
    assert not default['ticker_enabled'] and default['ticker_lines'] == ['الأماكن والأوقات تجمّلها القهوة دائمًا', 'Speciality Coffee']
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
def test_owner_settings_public_filtered_quote_and_preservation(owner, monkeypatch):
    client, mutate = owner
    before = client.get('/api/admin/config').json()
    with db.tx() as conn:
        fingerprint = db.preservation_snapshot(conn)

    public = client.get('/api/banners').json()
    assert public['presentation']['ticker_enabled'] is False
    assert public['presentation']['quote_text'] == ''
    assert public['presentation']['ticker_lines'] == []

    changed = {
        'ticker_enabled': True,
        'ticker_lines': ['العبارة الأولى', 'العبارة الثانية'],
        'ticker_seconds': 36,
        'quote_enabled': False,
        'quote_text': 'نص أدبي محفوظ ولا يعرض بعد',
    }
    result = mutate('PATCH', '/api/admin/settings', {'homepage_presentation': changed})
    assert result.status_code == 200, result.text
    assert client.get('/api/banners').json()['presentation']['ticker_lines'] == []
    _approve_advertising(client, mutate, monkeypatch)
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
def test_temporary_owner_pause_preserves_phrases_design_and_later_explicit_activation(owner, monkeypatch):
    client, mutate = owner
    saved = {'ticker_enabled': True, 'ticker_lines': ['Speciality Coffee', 'عبارة محفوظة للمالك'],
             'ticker_seconds': 42, 'quote_enabled': True, 'quote_text': 'نص البنر المحفوظ'}
    settings = {'homepage_presentation': saved, 'theme': {'profile': 'CHARCOAL'}, 'operational_day_start': '05:30'}
    with db.tx() as conn:
        conn.execute('UPDATE app_settings SET settings=%s WHERE brand_id=%s', (db.json(settings), 'kps'))
        conn.execute("INSERT INTO brands(id,name) VALUES('other-pause-test','Other isolated brand')")
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('other-pause-test',%s)", (db.json(settings),))
        fingerprint = db.preservation_snapshot(conn)
        # Simulate an existing installation upgrading from migration 014,
        # within this fixture's disposable local PostgreSQL schema only.
        conn.execute("DELETE FROM schema_migrations WHERE version='015_pause_promotional_strip.sql'")
    db.migrate()
    with db.tx() as conn:
        actual = conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        assert actual == settings | {'homepage_presentation': saved | {'ticker_enabled': False}}
        assert conn.execute("SELECT settings FROM app_settings WHERE brand_id='other-pause-test'").fetchone()['settings'] == settings
        assert db.preservation_snapshot(conn) == fingerprint
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='presentation.owner_ticker_paused'").fetchone()['n'] == 1
    hidden = client.get('/api/banners').json()['presentation']
    assert not hidden['ticker_enabled'] and hidden['ticker_lines'] == []
    assert hidden['quote_text'] == ''
    assert client.get('/api/admin/config').json()['settings']['homepage_presentation']['ticker_lines'] == saved['ticker_lines']
    # The pause audit must also round-trip through an empty-schema restore,
    # without migrations inserting a duplicate audit before archive import.
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, full=True)
    target = 'kps_restore_' + uuid4().hex
    base_url = os.environ['TEST_DATABASE_URL']
    try:
        archive_result = restore_archive(contents, target_url=base_url, target_schema=target)
        assert archive_result['verified'] and archive_result['counts'] == manifest['counts']
        with psycopg.connect(base_url, row_factory=psycopg.rows.dict_row) as conn:
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(target)))
            assert conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'] == actual
            assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='presentation.owner_ticker_paused'").fetchone()['n'] == 1
    finally:
        with psycopg.connect(base_url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
    db.migrate()
    assert not client.get('/api/banners').json()['presentation']['ticker_enabled']
    restored = mutate('PATCH', '/api/admin/settings', {'homepage_presentation': {'ticker_enabled': True}})
    assert restored.status_code == 200, restored.text
    assert client.get('/api/banners').json()['presentation']['ticker_lines'] == []
    _approve_advertising(client, mutate, monkeypatch)
    db.migrate()
    assert client.get('/api/banners').json()['presentation']['ticker_lines'] == saved['ticker_lines']


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
                                     headers={**owner_headers, 'Idempotency-Key': 'presentation-settings-' + uuid4().hex}, data=body)
    assert response.status == 200, response.text()
    # Public presentation still requires a separate, explicit owner decision.
    assert context.request.get(live_app + '/api/banners').json()['presentation']['ticker_lines'] == []
    challenge = context.request.post(live_app + '/api/identity/owner/sms/request', headers=owner_headers)
    assert challenge.ok, challenge.text()
    proof = context.request.post(live_app + '/api/identity/owner/sms/verify', headers=owner_headers,
                                 data={'challenge_id': challenge.json()['challenge_id'], 'code': '0123'})
    assert proof.ok, proof.text()
    current = context.request.get(live_app + '/api/advertising/policy', headers=owner_headers).json()['policy']
    approved = context.request.put(live_app + '/api/advertising/policy',
        headers={**owner_headers, 'Idempotency-Key': 'presentation-approval-' + uuid4().hex},
        data={'advertising_enabled': True, 'expected_version': current['version'],
              'reason': 'Explicit owner approval in isolated browser test'})
    assert approved.ok, approved.text()
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
    # Keyboard focus intentionally pauses an interactive banner; move focus
    # outside it before checking automatic rotation.
    page.locator('#branch-select').focus()
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

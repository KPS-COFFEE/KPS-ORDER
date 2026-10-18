"""Saudi presentation over existing cars, without re-keying PostgreSQL history."""
import os

import pytest

from app import db
from test_browser import (
    ARTIFACTS, _context, _owner_login, _staff,
    chromium_browser,
)
from test_fleet import seed

from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires isolated PostgreSQL/browser acceptance')]


@pytest.mark.parametrize('language,width', [('ar', 390), ('en', 1280)])
def test_existing_private_vehicle_bilingual_edit_preserves_fleet_and_history(live_app, chromium_browser, language, width):
    from playwright.sync_api import expect

    owner, driver = _staff(), _staff('driver')
    original = 'ح م د ١٢٣٤'
    profile = dict(plate=original, description='TEST existing private car', type='TEST Sedan',
                   color='White', model_year=2020, transmission='automatic', baseline_kml=12,
                   odometer_km=1210, owned_by_kps=True)
    with db.tx() as conn:
        conn.execute('UPDATE users SET email=%s,phone=%s,vehicle=%s WHERE id=%s',
                     ('plate-driver@example.invalid', '+966500000123', db.json(profile), driver['id']))
        seed(conn, 'saudi-plate-history', original, 12, driver=driver['id'], brand='kps',
             bids=['kps-branch-1'], profile=profile)
        car = conn.execute('SELECT * FROM fleet_vehicles WHERE brand_id=\'kps\' AND plate=%s', (original,)).fetchone()
        history = conn.execute('SELECT * FROM vehicle_operating_logs WHERE shift_id=\'saudi-plate-history\'').fetchone()
        shift = conn.execute('SELECT vehicle_log FROM shifts WHERE id=\'saudi-plate-history\'').fetchone()
    context, page, errors = _context(chromium_browser, live_app)
    try:
        _owner_login(context, live_app, owner)
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{language}')")
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(live_app + '/ops')
        page.locator('#navigation [data-page=fleet]').click()
        card = page.locator(f'[data-fleet-card="{car["id"]}"]')
        expect(card.locator('[data-saudi-plate]')).to_have_count(1)
        expect(card.locator('.saudi-plate__letters[lang=en]')).to_have_text('DZJ')
        expect(card).to_contain_text('خصوصي')
        page.screenshot(path=str(ARTIFACTS / f'saudi-private-fleet-{language}-{width}.png'), full_page=True)
        card.locator('[data-fleet-vehicle]').click()
        expect(page.locator('[data-fleet-row]')).to_have_count(1)
        expect(page.locator('[data-fleet-row] [data-saudi-plate]')).to_have_count(1)
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        edit = page.locator(f'[data-action=edit-staff][data-id="{driver["id"]}"]')
        edit.locator('xpath=ancestor::details').locator('summary').click()
        edit.click()
        modal = page.locator('#modal')
        expect(modal.locator('[data-saudi-plate-preview] .saudi-plate')).to_be_visible()
        modal.locator('[name=vehicle_plate]').fill('1234 DZJ')
        expect(modal.locator('.saudi-plate__letters[lang=ar]')).to_have_text('دمح')
        modal.locator('[name=reason]').fill('TEST ONLY equivalent English plate presentation')
        expect(modal.locator('[name=step_up_password]')).to_have_count(0)
        modal.locator('button[type=submit]').click()
        complete_owner_sms_dialog(page, owner)
        expect(modal).not_to_be_visible()
        with db.tx() as conn:
            current = conn.execute('SELECT vehicle FROM users WHERE id=%s', (driver['id'],)).fetchone()['vehicle']
            assert current['plate'] == original
            assert current['baseline_kml'] == profile['baseline_kml']
            assert current['model_year'] == profile['model_year']
            assert conn.execute('SELECT count(*) n FROM fleet_vehicles WHERE brand_id=\'kps\'').fetchone()['n'] == 1
            current_car = conn.execute('SELECT * FROM fleet_vehicles WHERE id=%s', (car['id'],)).fetchone()
            assert current_car['plate_key'] == car['plate_key']
            assert conn.execute('SELECT * FROM vehicle_operating_logs WHERE shift_id=\'saudi-plate-history\'').fetchone() == history
            assert conn.execute('SELECT vehicle_log FROM shifts WHERE id=\'saudi-plate-history\'').fetchone() == shift
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
    finally:
        context.close()

"""Real PostgreSQL and mobile/desktop UI journeys in Arabic and English."""
import os
import pytest
from app import db
from test_fleet import seed
from test_browser import _capture,_context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicit isolated browser acceptance.')]

@pytest.mark.parametrize('language',['ar','en'])
def test_temporary_requests_fawzi_and_driver(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    admin=_staff('admin');driver=_staff('driver')
    with db.tx() as c:
        c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['vehicle_maintenance_manage']),admin['id']))
        seed(c,'TEST-check-shift','TEST CHECK CAR',17,driver=driver['id'],brand='kps',bids=['kps-branch-1'],state='open',full=False,liters=0)
        c.execute('UPDATE users SET vehicle=%s WHERE id=%s',(db.json({'plate':'TEST CHECK CAR','type':'TEST car','odometer_km':1000}),driver['id']))
        c.execute("UPDATE shifts SET vehicle_log=jsonb_set(jsonb_set(vehicle_log,'{awaiting_refuel}','true'),'{refuels}','[]') WHERE id='TEST-check-shift'")
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,admin)
    dc,dp,de=_context(chromium_browser,live_app);_owner_login(dc,live_app,driver)
    dp.set_viewport_size({'width':390,'height':844})
    try:
        page.goto(live_app+'/ops');dp.goto(live_app+'/ops')
        if language=='en':
            page.get_by_role('button',name='English',exact=True).click();dp.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-check-open]').click(timeout=15000)
        page.locator('#check-send [name=driver_ids]').check()
        page.locator('#check-send button[type=submit]').click()
        expect(page.locator('#modal-body')).to_contain_text('Waiting for driver' if language=='en' else 'بانتظار السائق')
        expect(dp.locator('[data-check-reply]')).to_be_visible(timeout=20000)
        dp.locator('[data-check-reply] input[name=value]').fill('1250')
        _capture(dp,'temporary_driver_odometer_'+language)
        dp.locator('[data-check-reply] button').click()
        expect(dp.locator('[data-check-reply]')).to_have_count(0)
        page.locator('[data-check-refresh]').click();page.locator('[data-check-review]').click()
        page.locator('#modal-body input[name=value]').fill('1251')
        page.locator('#modal-body input[name=reason]').fill('TEST ONLY correct digit')
        page.locator('#modal-body button[type=submit]').click()
        expect(page.locator('#modal-body')).to_contain_text('1251')
        page.locator('#check-send [name=kind]').select_option('fuel')
        page.locator('#check-send [name=driver_ids]').check();page.locator('#check-send button[type=submit]').click()
        expect(dp.locator('[data-check-reply]')).to_be_visible(timeout=20000)
        dp.locator('[data-check-reply] select').select_option('50')
        _capture(dp,'temporary_driver_fuel_'+language)
        dp.locator('[data-check-reply] button').click();expect(dp.locator('[data-check-reply]')).to_have_count(0)
        page.locator('[data-check-refresh]').click();page.locator('[data-check-review]').click();page.locator('#modal-body button[type=submit]').click()
        expect(page.locator('#check-send')).to_be_visible()
        with db.tx() as c:
            log=c.execute("SELECT vehicle_log FROM shifts WHERE id='TEST-check-shift'").fetchone()['vehicle_log']
            assert not log['awaiting_refuel'] and log['fuel_readiness']['fuel_percent']==50
        page.locator('[data-check-correct]').click();page.locator('#modal-body input[name=value]').fill('1200');page.locator('#modal-body input[name=reason]').fill('TEST ONLY verified current reading')
        page.locator('#modal-body button[type=submit]').click();expect(page.locator('#modal-body')).to_contain_text('1200')
        _capture(page,'temporary_fawzi_approvals_'+language)
        assert dp.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors and not de
    finally:
        context.close();dc.close()

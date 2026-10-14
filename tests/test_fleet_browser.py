"""Real API, PostgreSQL and Chromium; Arabic/English and mobile/desktop."""
import os
import pytest
from app import db
from test_fleet import seed
from test_browser import _capture,_context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail,ARTIFACTS
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicit isolated browser acceptance.')]

def _readable_log(page):
    # An empty tbody used to collapse 23 headings into single-letter columns.
    layout=page.locator('.fleet-log-table').evaluate('''table => ({
        widths: [...table.querySelectorAll('th')].map(th => th.getBoundingClientRect().width),
        wrap: table.parentElement.clientWidth, scroll: table.parentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
        pageWidth: document.documentElement.scrollWidth
    })''')
    assert min(layout['widths']) >= 130
    assert layout['scroll'] > layout['wrap']
    assert layout['pageWidth'] <= layout['viewport'] + 1

@pytest.mark.parametrize('language',['ar','en'])
def test_fawzi_vehicle_history_report_summary_exports_correction(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    admin=_staff('admin');driver=_staff('driver')
    with db.tx() as c:
        c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['vehicle_maintenance_manage']),admin['id']))
        for day in (10,11,12):
            seed(c,'browser-fleet-'+str(day),'TEST FLEET <img>',day,driver=driver['id'],brand='kps',bids=['kps-branch-1'],km=210,liters=30 if day==12 else 21)
        c.execute('UPDATE users SET vehicle=%s WHERE id=%s',(db.json({'plate':'TEST FLEET <img>','type':'TEST Compact','model_year':2020,'color':'White','engine_cc':1193,'transmission':'automatic','baseline_kml':14}),driver['id']))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,admin)
    try:
        page.goto(live_app+'/ops')
        expect(page.locator('[data-page=fleet]')).to_be_visible()
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-page=fleet]').click()
        expect(page.locator('[data-fleet-card]')).to_have_count(1)
        expect(page.locator('[data-fleet-card] img')).to_have_count(0)
        expect(page.locator('[data-fleet-card]')).to_contain_text('TEST Compact')
        expect(page.locator('[data-fleet-card]')).to_contain_text('2020')
        expect(page.locator('[data-fleet-card]')).to_contain_text('White')
        expect(page.locator('[data-fleet-card]')).to_contain_text('Automatic' if language=='en' else 'أوتوماتيك')
        page.locator('[data-fleet-vehicle]').click()
        expect(page.locator('[data-fleet-row]')).to_have_count(3)
        _readable_log(page)
        assert page.locator('[data-fleet-row]').first.get_attribute('data-fleet-row')=='browser-fleet-12'
        _capture(page,'fleet_vehicle_history_'+language)
        page.locator('[data-fleet-report]').click()
        page.locator('#fleet-filters [name=start]').fill('2026-10-01');page.locator('#fleet-filters [name=end]').fill('2026-10-01')
        page.locator('#fleet-filters button[type=submit]').click()
        expect(page.locator('[data-fleet-row]')).to_have_count(0)
        _readable_log(page)
        _capture(page,'fleet_report_empty_'+language)
        page.locator('#fleet-filters [name=start]').fill('2026-10-10');page.locator('#fleet-filters [name=end]').fill('2026-10-12')
        page.locator('#fleet-filters button[type=submit]').click()
        expect(page.locator('[data-fleet-row]')).to_have_count(3)
        page.locator('[data-fleet-metric=red]').click()
        expect(page.locator('[data-fleet-row]')).to_have_count(1)
        expect(page.locator('[data-fleet-row]')).to_have_attribute('data-fleet-row','browser-fleet-12')
        expect(page.locator('[data-fleet-log]')).to_have_count(0) # Report is read-only.
        _capture(page,'fleet_report_red_'+language)
        for fmt in ('xlsx','pdf'):
            with page.expect_download() as pending:page.locator(f'[data-fleet-export={fmt}]').click()
            pending.value.save_as(ARTIFACTS/f'TEST_ONLY_fleet_filtered_{language}.{fmt}')
        page.locator('[data-fleet-home]').click();page.locator('[data-fleet-vehicle]').click();page.locator('[data-fleet-log=browser-fleet-12]').click()
        expect(page.locator('#modal')).to_be_visible()
        page.locator('#modal [name=liters]').fill('21');page.locator('#modal [name=reason]').fill('TEST ONLY corrected fuel receipt')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.locator('[data-fleet-row]').first.locator('.fleet-green')).to_be_visible()
        page.locator('[data-fleet-log=browser-fleet-12]').click()
        expect(page.locator('#modal')).to_contain_text('TEST ONLY corrected fuel receipt')
        _capture(page,'fleet_audit_'+language)
        assert errors==[]
    finally:context.close()

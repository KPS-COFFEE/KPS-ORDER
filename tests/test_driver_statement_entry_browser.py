"""Direct statement entry with real scoped reports and export filters on two viewport sizes."""
import os
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit,parse_qs
import pytest
from app import db
from test_browser import _staff,_context,_owner_login,_capture,ARTIFACTS,live_app,chromium_browser,synthetic_mail
from test_accounting_statements_browser import _documented_snapshot

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


def _navigate(page,key):
    button=page.locator(f'#navigation [data-page={key}]')
    if not button.is_visible():page.locator('.dispatch-nav-toggle').click()
    button.click()


@pytest.mark.parametrize('width,lang',[(390,'ar'),(1440,'en')])
def test_direct_statement_city_multidriver_filters_and_export(live_app,chromium_browser,width,lang):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');driver=_staff('driver');second=_staff('driver')
    day=(datetime.now(ZoneInfo('Asia/Riyadh'))-timedelta(days=1)).date()
    with db.tx() as c:
        c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['accounting_reports']),accountant['id']))
        c.execute("UPDATE users SET display_name=%s WHERE id=%s",('TEST DRIVER ONE',driver['id']))
        c.execute("UPDATE users SET display_name=%s WHERE id=%s",('TEST DRIVER TWO',second['id']))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at) VALUES('entry-manager','manager',%s,'kps','[\"kps-branch-1\"]','closed',%s,now())",(manager['id'],datetime.combine(day,datetime.min.time().replace(hour=16),ZoneInfo('Asia/Riyadh'))))
        for number,person in enumerate((driver,second)):
            shift='entry-driver-'+str(number)
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,manager_shift_id) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]','closed',%s,now(),'entry-manager')",(shift,person['id'],datetime.combine(day,datetime.min.time().replace(hour=16),ZoneInfo('Asia/Riyadh'))))
            c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,'driver','kps','[\"kps-branch-1\"]',%s,%s)",('entry-report-'+str(number),shift,person['id'],db.json(_documented_snapshot(person['id']))))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,accountant)
    try:
        page.set_viewport_size({'width':width,'height':1000});page.goto(live_app+'/ops')
        if lang=='en':page.get_by_role('button',name='English',exact=True).click()
        # Accounting exposes the same direct statement shortcut as the main menu.
        expect(page.locator('[data-ac=driver-statement]')).to_be_visible()
        entry=page.locator('#navigation [data-page=driverStatements]')
        if not entry.is_visible():page.locator('.dispatch-nav-toggle').click()
        page.screenshot(path=str(ARTIFACTS/f'driver-statement-access-{lang}-{width}.png'),full_page=False)
        _navigate(page,'driverStatements')
        expect(page.locator('[name=group]')).to_have_value('driver_statement')
        expect(page.locator('[data-ac=print]')).to_be_visible()
        expect(page.locator('.ac-statement-total')).to_be_visible()
        page.locator('[name=start]').fill(str(day-timedelta(days=1)))
        page.locator('[name=end]').fill(str(day))
        city=page.locator('[name=city] option').nth(1).get_attribute('value')
        with page.expect_response(lambda r:'/api/accounting/reports?' in r.url and 'format=json' in r.url):
            page.locator('[name=city]').select_option(city)
        expect(page.locator('.ac-statement-total')).to_be_visible()
        expect(page.locator('[name=start]')).to_have_value(str(day-timedelta(days=1)))
        page.locator('[name=driver_ids]').select_option([driver['id'],second['id']])
        with page.expect_response(lambda r:'/api/accounting/reports?' in r.url and 'format=json' in r.url):
            page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('[data-statement-driver]')).to_have_count(2)
        expect(page.locator('[data-statement-driver]').first.locator('tbody tr')).to_have_count(2)
        page.evaluate('window.scrollTo(0,0)')
        _capture(page,f'driver-statement-direct-{lang}')
        page.set_viewport_size({'width':width,'height':1000})
        page.evaluate('document.activeElement?.blur();window.scrollTo(0,0)')
        page.screenshot(path=str(ARTIFACTS/f'driver-statement-direct-{lang}-{width}.png'),full_page=False)
        page.locator('.ac-statement-total').scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS/f'driver-statement-totals-{lang}-{width}.png'),full_page=False)
        with page.expect_request(lambda r:'/api/accounting/reports?' in r.url and 'format=xlsx' in r.url) as request:
            with page.expect_download() as download:page.locator('[data-ac=xlsx]').click()
        assert download.value.failure() is None
        query=parse_qs(urlsplit(request.value.url).query)
        assert set(query['driver_ids'][0].split(','))=={driver['id'],second['id']}
        assert query['start']==[str(day-timedelta(days=1))] and query['end']==[str(day)]
        assert query['branch_ids']==['kps-branch-1']
        with page.expect_popup() as preview:
            with page.expect_response(lambda r:'/api/accounting/reports?' in r.url and 'format=pdf' in r.url) as printable:
                page.locator('[data-ac=print]').click()
        print_query=parse_qs(urlsplit(printable.value.url).query)
        for key in ('start','end','branch_ids','driver_ids','variance'):
            assert print_query.get(key)==query.get(key)
        assert printable.value.ok, (printable.value.status,printable.value.headers)
        assert printable.value.headers.get('content-type','').startswith('application/pdf')
        assert context.request.get(printable.value.url).body().startswith(b'%PDF')
        preview.value.close()
        # Switching away and back restores the applied filters without crossing brands/users.
        _navigate(page,'accounting');_navigate(page,'driverStatements')
        expect(page.locator('[name=start]')).to_have_value(str(day-timedelta(days=1)))
        expect(page.locator('[name=city]')).to_have_value(city)
        assert set(page.locator('[name=driver_ids]').evaluate('(el)=>Array.from(el.selectedOptions,x=>x.value)'))=={driver['id'],second['id']}
        with page.expect_response(lambda r:'/api/accounting/reports?' in r.url and 'format=json' in r.url):
            page.locator('[data-ac=all-branches]').click()
        assert set(page.locator('[name=driver_ids]').evaluate('(el)=>Array.from(el.selectedOptions,x=>x.value)'))=={driver['id'],second['id']}
        page.locator('[name=variance]').select_option('shortage_any')
        page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('[data-statement-driver]')).to_have_count(0)
        assert not errors
    finally:context.close()

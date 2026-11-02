"""Real HTTP browser acceptance; no production data, messages, or mocked responses."""
import os
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from test_browser import live_app,chromium_browser,synthetic_mail,_staff,_context,_owner_login,_customer_session
from test_accounting_statements_browser import _documented_snapshot

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


@pytest.mark.parametrize('width',[390,1440])
def test_multi_driver_rows_filters_and_read_only_branch_catalog(live_app,chromium_browser,width):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');drivers=[_staff('driver'),_staff('driver')]
    tz=ZoneInfo('Asia/Riyadh')
    operational_day=(datetime.now(tz)-timedelta(days=1)).date();day=operational_day.isoformat()
    # The report follows generated reporting_operational_date, so anchor every
    # shift timestamp inside the selected day rather than the wall-clock hour.
    closed_at=datetime.combine(operational_day,time(20),tz)
    with db.tx() as c:
        c.execute("UPDATE users SET permissions='[\"accounting_reports\"]' WHERE id=%s",(accountant['id'],))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date) VALUES('clarity-manager','manager',%s,'kps','[\"kps-branch-1\"]','closed',%s,%s,%s)",(manager['id'],closed_at-timedelta(hours=4),closed_at,day))
        for n,driver in enumerate(drivers):
            sid='clarity-driver-'+str(n)
            c.execute("UPDATE users SET display_name=%s WHERE id=%s",('TEST DRIVER '+str(n),driver['id']))
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,manager_shift_id) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]','closed',%s,%s,'clarity-manager')",(sid,driver['id'],closed_at-timedelta(hours=3),closed_at))
            c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,'driver','kps','[\"kps-branch-1\"]',%s,%s)",('clarity-report-'+str(n),sid,driver['id'],db.json(_documented_snapshot(driver['id']))))
        reporting_days=c.execute("SELECT reporting_operational_date FROM shifts WHERE id=ANY(%s)",(['clarity-manager','clarity-driver-0','clarity-driver-1'],)).fetchall()
        assert len(reporting_days)==3
        assert {row['reporting_operational_date'] for row in reporting_days}=={operational_day}
    from app.accounting import build
    from datetime import date
    with db.tx() as c:
        viewer=c.execute('SELECT * FROM users WHERE id=%s',(accountant['id'],)).fetchone()
        snapshot={group:build(c,viewer,day,day,'kps-branch-1',group=group) for group in ('daily','driver_statement')}
        c.execute("INSERT INTO accounting_months(id,brand_id,month,branch_ids,scope_hash,snapshot,closed_by,reason) VALUES('clarity-frozen','kps',%s,'[\"kps-branch-1\"]',%s,%s,%s,'TEST saved closed statement fixture')",(date.fromisoformat(day).replace(day=1),'e'*64,db.json(snapshot),accountant['id']))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,accountant)
    try:
        page.set_viewport_size({'width':width,'height':950});page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=accounting]').click();page.locator('[data-ac=reports]').click()
        expect(page.locator('#ac-description')).not_to_have_text('')
        page.locator('[name=start]').fill(day);page.locator('[name=end]').fill(day)
        page.locator('[name=branch_ids]').select_option('kps-branch-1')
        page.locator('[name=group]').select_option('driver_statement')
        expect(page.locator('#ac-description')).not_to_have_text('')
        expect(page.locator('[name=start]')).to_have_value(day);expect(page.locator('[name=end]')).to_have_value(day)
        assert page.locator('[name=branch_ids]').evaluate('(el)=>Array.from(el.selectedOptions).map(o=>o.value)')==['kps-branch-1']
        page.locator('[name=driver_ids]').select_option([d['id'] for d in drivers])
        page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('[data-statement-driver]')).to_have_count(2)
        expect(page.locator('.ac-driver-statement tbody tr')).to_have_count(2)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=f'test-artifacts/clarity-driver-statement-{width}.png',full_page=True)
        page.locator('[name=driver_ids]').select_option(drivers[0]['id']);page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('[data-statement-driver]')).to_have_count(1)
        page.locator('[name=variance]').select_option('shortage');page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('[data-statement-driver]')).to_have_count(0)
        page.locator('[name=branch_ids]').select_option([])
        page.locator('[name=group]').select_option('daily')
        assert page.locator('[name=branch_ids]').evaluate('(el)=>Array.from(el.selectedOptions).map(o=>o.value)')==[]
        expect(page.locator('[name=start]')).to_have_value(day)
        page.locator('[data-ac=months]').click();page.locator('[data-ac=frozen][data-id=clarity-frozen]').click()
        expect(page.locator('#ac-description')).to_contain_text('كشف شهر مقفل محفوظ')
        page.locator('[name=group]').select_option('driver_statement')
        expect(page.locator('#ac-description')).to_contain_text('كشف شهر مقفل محفوظ')
        page.locator('#navigation [data-page=catalog]').click()
        expect(page.locator('#staff-catalog-items .product-admin-card').first).to_be_visible()
        page.screenshot(path=f'test-artifacts/clarity-branch-catalog-{width}.png',full_page=True)
        assert page.locator('[data-stock]').count()==0
        page.locator('#staff-catalog-filters [name=search]').fill('THIS ITEM DOES NOT EXIST')
        expect(page.locator('#staff-catalog-items .product-admin-card')).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
    finally:context.close()


@pytest.mark.parametrize('language',['ar','en'])
def test_customer_general_complaint_without_selecting_branch(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    customer,token,csrf=_customer_session();context,page,errors=_context(chromium_browser,live_app,token,csrf)
    context.add_init_script(f'localStorage.setItem("kps_customer_lang","{language}")')
    try:
        page.set_viewport_size({'width':390,'height':950});page.goto(live_app+'/')
        expect(page.locator('#branch-select option[value=""]')).to_have_text('Choose a branch' if language=='en' else 'اختر الفرع')
        page.locator('#quick-contact').click()
        expect(page.locator('#contacts-dialog')).to_be_visible()
        expect(page.locator('#contact-branch option[value=""]')).to_have_text('General contact — no branch selected' if language=='en' else 'تواصل عام — بدون تحديد فرع')
        page.locator('#contact-order').select_option('');page.locator('#contact-branch').select_option('')
        page.locator('#contact-category').select_option('complaint')
        page.locator('#contact-details').fill('TEST synthetic complaint without branch or order')
        page.locator('#contact-start').click()
        expect(page.locator('#chat-dialog')).to_be_visible()
        expect(page.locator('#chat-title')).to_contain_text('CS-')
        with db.tx() as c:
            row=c.execute('SELECT * FROM conversations WHERE customer_id=%s ORDER BY created_at DESC LIMIT 1',(customer,)).fetchone()
            assert row['category']=='complaint' and row['branch_id'] is None and row['route']=='review'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
    finally:context.close()

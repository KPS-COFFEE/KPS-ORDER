"""Real owner navigation, audit filtering and idle session revocation."""
import os
import pytest
from app import db
from tests.browser_sms_helpers import sms_browser_app
from test_browser import _context,_owner_login,_staff,chromium_browser
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real browser acceptance')]

@pytest.mark.parametrize('width',[390,1440])
def test_owner_report_filters_recorded_work_and_ends_idle_employee_sessions(sms_browser_app,chromium_browser,width):
    from playwright.sync_api import expect
    base=sms_browser_app;owner=_staff();employee=_staff('driver')
    owner_context,page,errors=_context(chromium_browser,base)
    employee_context,employee_page,employee_errors=_context(chromium_browser,base)
    try:
        _owner_login(owner_context,base,owner);_owner_login(employee_context,base,employee)
        page.set_viewport_size({'width':width,'height':1000})
        employee_page.goto(base+'/ops',wait_until='networkidle')
        expect(employee_page.locator('#app-shell')).to_be_visible()
        with db.tx() as c:
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('TEST-activity-order','kps','kps-branch-1',%s,'delivery','new','pending','cash',1000,0,1000,%s,'{}')",(owner['id'],db.json({'order_reference':'TEST-activity-order'})))
            db.audit(c,employee['id'],'order.created','order','TEST-activity-order',{'password':'must-never-appear'})
        page.goto(base+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=employeeActivity]').click()
        expect(page.locator('#employee-activity')).to_be_visible()
        form=page.locator('#employee-activity-filters')
        form.locator('[name=employee_id]').select_option(employee['id'])
        form.locator('button[type=submit]').click()
        expect(page.locator('#employee-activity-rows')).to_contain_text('دخول مسجل')
        expect(page.locator('#employee-activity-rows article')).to_have_count(1)
        page.locator('[data-activity-kind=work]').click()
        expect(page.locator('#employee-activity-rows')).to_contain_text('TEST-activity-order')
        assert 'must-never-appear' not in page.locator('#employee-activity').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        from pathlib import Path
        output=Path(__file__).resolve().parents[1]/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'owner-employee-work-{width}.png'),full_page=True)
        page.locator('[data-activity-kind=login]').click()
        page.locator('#employee-end-sessions').click()
        page.locator('#modal textarea[name=reason]').fill('Owner ended a synthetic employee session')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        expect(employee_page.locator('#login-shell')).to_be_visible(timeout=10000)
        expect(page.locator('#employee-activity-rows')).to_contain_text('إنهاء جلسات بأمر المالك')
        assert employee_context.request.get(base+'/api/shifts/current',headers={'X-KPS-Context':'work'}).status==401
        with db.tx() as c:
            assert c.execute('SELECT active FROM users WHERE id=%s',(employee['id'],)).fetchone()['active']
        # Ending sessions is not disabling the identity: a genuinely new SMS
        # challenge may establish a fresh session, never reconnect the old one.
        _owner_login(employee_context,base,employee)
        employee_page.reload(wait_until='networkidle')
        expect(employee_page.locator('#app-shell')).to_be_visible()
        assert employee_page.locator('#navigation [data-page=employeeActivity]').count()==0
        assert errors==[] and employee_errors==[]
    finally:
        owner_context.close();employee_context.close()

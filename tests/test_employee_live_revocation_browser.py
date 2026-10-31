"""An idle, already-open work page loses all displayed work after revocation.

Real browser, HTTP authentication, canonical synthetic SMS and isolated database.
No auth, access-status or business API responses are fabricated.
"""
import os
import time

import pytest

from app import db
from tests.browser_sms_helpers import sms_browser_app
from test_browser import _context, _owner_login, _staff, chromium_browser

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires isolated PostgreSQL and Chromium')]


def pause(context, base, headers, employee):
    result = context.request.post(
        base + '/api/identity/staff/' + employee['id'] + '/manage',
        headers=headers, data={'action':'pause','reason':'Owner ended employee access'})
    assert result.ok, result.text()
    assert result.json()['user']['active'] is False


def assert_locked(page):
    from playwright.sync_api import expect
    expect(page.locator('#login-shell')).to_be_visible(timeout=7000)
    expect(page.locator('#app-shell')).not_to_be_visible()
    expect(page.locator('dialog[open]')).to_have_count(0)
    expect(page.locator('#modal-body')).to_be_empty()
    expect(page.locator('#content')).to_be_empty()


@pytest.mark.parametrize('role',['driver','owner'])
def test_owner_pause_locks_idle_page_and_every_open_tab_without_employee_action(
        sms_browser_app, chromium_browser, role):
    from playwright.sync_api import expect
    base=sms_browser_app
    owner, employee, editable = _staff(), _staff(role), _staff('driver')
    admin, admin_page, admin_errors=_context(chromium_browser,base)
    work, page, errors=_context(chromium_browser,base)
    try:
        headers=_owner_login(admin,base,owner)
        employee_headers=_owner_login(work,base,employee)
        page.goto(base+'/ops',wait_until='networkidle')
        expect(page.locator('#app-shell')).to_be_visible()
        second=work.new_page()
        second.goto(base+'/ops',wait_until='networkidle')
        expect(second.locator('#app-shell')).to_be_visible()
        if role=='owner':
            # Actual employee editor contains saved work information, not a
            # synthetic dialog inserted for the test.
            page.locator('#navigation [data-page=admin]').click()
            page.locator('[data-action=admin-tab][data-tab=users]').click()
            card=page.locator(f'.staff-card[data-staff-id="{editable["id"]}"]')
            card.locator('.staff-actions summary').click()
            card.locator('[data-action=edit-staff]').click()
            expect(page.locator('#modal input[name=display_name]')).to_be_visible()
        page_url=page.url
        navigation=[]
        page.on('framenavigated',lambda frame:navigation.append(frame.url) if frame==page.main_frame else None)
        started=time.monotonic()
        pause(admin,base,headers,employee)
        # No click, reload, focus event or navigation is sent to either work tab.
        assert_locked(page)
        assert_locked(second)
        assert time.monotonic()-started < 8
        assert page.url==page_url and not navigation
        denied=work.request.get(base+'/api/shifts/current',headers=employee_headers)
        assert denied.status==401,denied.text()
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND revoked_at IS NULL',
                             (employee['id'],)).fetchone()['n']==0
        assert not errors and not admin_errors
    finally:
        work.close();admin.close()


def test_offline_work_is_hidden_and_revocation_is_checked_before_it_can_reappear(
        sms_browser_app,chromium_browser):
    from playwright.sync_api import expect
    base=sms_browser_app
    owner,employee=_staff(),_staff('driver')
    admin,admin_page,admin_errors=_context(chromium_browser,base)
    work,page,errors=_context(chromium_browser,base)
    try:
        headers=_owner_login(admin,base,owner)
        _owner_login(work,base,employee)
        page.goto(base+'/ops',wait_until='networkidle')
        expect(page.locator('#app-shell')).to_be_visible()
        work.set_offline(True)
        expect(page.locator('html')).to_have_attribute('data-work-access-pending','')
        expect(page.locator('#app-shell')).not_to_be_visible()
        pause(admin,base,headers,employee)
        work.set_offline(False)
        assert_locked(page)
        assert not errors and not admin_errors
    finally:
        work.close();admin.close()


def test_phone_swap_revokes_idle_employee_tabs_and_preserves_employee_history(
        sms_browser_app,chromium_browser):
    from playwright.sync_api import expect
    base=sms_browser_app
    owner,left,right=_staff(),_staff('driver'),_staff('driver')
    admin,admin_page,admin_errors=_context(chromium_browser,base)
    work,page,errors=_context(chromium_browser,base)
    other,other_page,other_errors=_context(chromium_browser,base)
    try:
        headers=_owner_login(admin,base,owner)
        _owner_login(work,base,left)
        _owner_login(other,base,right)
        for p in (page,other_page):
            p.goto(base+'/ops',wait_until='networkidle')
            expect(p.locator('#app-shell')).to_be_visible()
        with db.tx() as c:
            original={r['id']:r for r in c.execute('SELECT id,phone,role,display_name,branch_ids FROM users WHERE id=ANY(%s)',
                                                   ([left['id'],right['id']],)).fetchall()}
        result=admin.request.post(base+'/api/identity/staff/'+left['id']+'/swap-phone',headers=headers,data={
            'other_user_id':right['id'],'expected_phone':original[left['id']]['phone'],
            'expected_other_phone':original[right['id']]['phone'],'reason':'Owner corrected exchanged employee numbers'})
        assert result.ok,result.text()
        assert_locked(page);assert_locked(other_page)
        with db.tx() as c:
            current={r['id']:r for r in c.execute('SELECT id,phone,role,display_name,branch_ids FROM users WHERE id=ANY(%s)',
                                                  ([left['id'],right['id']],)).fetchall()}
            for own,opposite in ((left,right),(right,left)):
                assert current[own['id']]['phone']==original[opposite['id']]['phone']
                assert all(current[own['id']][k]==original[own['id']][k] for k in ('id','role','display_name','branch_ids'))
        assert not errors and not other_errors and not admin_errors
    finally:
        other.close();work.close();admin.close()


@pytest.mark.parametrize('offline',[False,True])
def test_hours_report_closes_after_employee_pause_even_when_report_was_already_open(
        sms_browser_app,chromium_browser,offline):
    from playwright.sync_api import expect
    base=sms_browser_app
    owner,employee=_staff(),_staff('delivery_manager')
    admin,admin_page,admin_errors=_context(chromium_browser,base)
    work,page,errors=_context(chromium_browser,base)
    try:
        headers=_owner_login(admin,base,owner)
        employee_headers=_owner_login(work,base,employee)
        page.goto(base+'/driver-hours',wait_until='networkidle')
        expect(page.locator('#hours-kpis article')).to_have_count(4)
        expect(page.locator('.driver-hours-main')).to_be_visible()
        if offline:
            work.set_offline(True)
            expect(page.locator('html')).to_have_attribute('data-hours-access-pending','')
            expect(page.locator('.driver-hours-main')).not_to_be_visible()
        pause(admin,base,headers,employee)
        if offline:work.set_offline(False)
        expect(page).to_have_url(base+'/ops',timeout=7000)
        expect(page.locator('#login-shell')).to_be_visible()
        expect(page.locator('#hours-kpis')).to_have_count(0)
        denied=work.request.get(base+'/api/driver-work-hours/report',headers=employee_headers)
        assert denied.status==401,denied.text()
        assert not errors and not admin_errors
    finally:
        work.close();admin.close()


def test_ordinary_window_focus_rechecks_live_access_without_blanking_work(
        sms_browser_app,chromium_browser):
    """Observe the real DOM and genuine session endpoint, not a mocked response."""
    from playwright.sync_api import expect
    base=sms_browser_app
    employee=_staff('driver')
    work,page,errors=_context(chromium_browser,base)
    try:
        _owner_login(work,base,employee)
        page.goto(base+'/ops',wait_until='networkidle')
        expect(page.locator('#app-shell')).to_be_visible()
        page.evaluate('''() => {
          window.__unexpectedAccessMasks=[];
          new MutationObserver(records=>{
            for(const record of records)if(record.attributeName==='data-work-access-pending')
              window.__unexpectedAccessMasks.push(document.documentElement.hasAttribute('data-work-access-pending'));
          }).observe(document.documentElement,{attributes:true});
        }''')
        with page.expect_response(lambda r:r.url.endswith('/api/session') and r.status==200):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator('#app-shell')).to_be_visible()
        assert page.evaluate('window.__unexpectedAccessMasks')==[]
        assert not errors
    finally:
        work.close()

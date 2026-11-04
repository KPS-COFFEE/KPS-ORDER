"""Real browser opening after physical receipt while accounting stays pending."""
import os
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_enbishan_dual_brand import dual
from test_central_workdays_browser import central, central_web, staff_page
from test_browser import chromium_browser
from test_aggregate_driver_closure import submit_physical

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]


@pytest.mark.parametrize('language',['ar','en'])
def test_owner_receipt_releases_manager_and_driver_for_fresh_ui_opening(central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    _,_,post,_,_=central
    old_manager,old_driver,driver_path,driver_decl=submit_physical(central)
    post('manager',driver_path+'/physical-receipt',{
        'declaration_id':driver_decl['declaration_id'],'received_halalas':700,
        'confirmed':True,'reference':'Actual driver bag before next physical day'})
    manager_path='/api/central-operations/workdays/'+old_manager['shift']['id']
    declaration=post('manager',manager_path+'/manager-physical-submit',{
        'cash_halalas':700,'recipient_user_id':'dual-owner',
        'reference':'Actual manager handover to owner','confirmed':True})
    post('owner',manager_path+'/manager-physical-receipt',{
        'declaration_id':declaration['declaration_id'],'received_halalas':700,'confirmed':True})
    manager_context,page,errors=staff_page(central,chromium_browser,central_web,'manager',language)
    try:
        page.goto(central_web+'/ops')
        start=page.locator('#content [data-action=open-shift]').first
        expect(start).to_be_visible()
        start.click()
        expect(page.locator('#modal [name=kind]')).to_have_value('manager')
        with page.expect_response(lambda r:r.url.endswith('/api/central-operations/workdays') and r.request.method=='POST') as pending:
            page.locator('#modal-body form button[type=submit]').click()
        response=pending.value
        assert response.status==200,response.text()
        new_manager=response.json()['shift']
        assert new_manager['id']!=old_manager['shift']['id']
        expect(page.locator('#modal')).not_to_be_visible()
        assert not errors,errors
    finally:
        manager_context.close()
    driver_context,page,errors=staff_page(central,chromium_browser,central_web,'driver',language)
    try:
        page.goto(central_web+'/ops')
        start=page.locator('#content .driver-shift-guidance [data-action=open-shift]')
        expect(start).to_be_visible()
        start.click()
        form=page.locator('#modal-body form')
        expect(form.locator('[name=kind]')).to_have_value('driver')
        form.locator('[name=manager_shift_id]').select_option(new_manager['id'])
        form.locator('[name=inspection_odometer]').fill('1010')
        form.locator('[name=inspection_fuel]').fill('100')
        for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        with page.expect_response(lambda r:r.url.endswith('/api/central-operations/workdays') and r.request.method=='POST') as pending:
            form.locator('button[type=submit]').click()
        response=pending.value
        assert response.status==200,response.text()
        new_driver=response.json()['shift']
        assert new_driver['id']!=old_driver['shift']['id']
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.locator('#content .driver-shift-guidance [data-action=open-shift]')).to_have_count(0)
        assert not errors,errors
    finally:
        driver_context.close()
    with db.tx() as c:
        for old in (old_manager,old_driver):
            for leg in old['shifts']:
                saved=c.execute('SELECT status,staffing_ended_at FROM shifts WHERE id=%s',(leg['id'],)).fetchone()
                assert saved['status']=='submitted' and saved['staffing_ended_at'] is not None
        assert not c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone()
        assert not c.execute('SELECT 1 FROM shared_manager_closure_allocations').fetchone()
        for identity in ('dual-driver','dual-manager'):
            assert c.execute("SELECT count(DISTINCT l.workday_id) n FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id WHERE s.user_id=%s AND s.status='open' AND s.staffing_ended_at IS NULL",(identity,)).fetchone()['n']==1

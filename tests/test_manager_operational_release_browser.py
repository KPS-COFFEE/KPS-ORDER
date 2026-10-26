"""An unreceived cash bag cannot strand the manager's next operational duty."""
import os
from uuid import uuid4
import pytest
from app import db
from test_enbishan_dual_brand import dual
from test_central_workdays_browser import central, central_web, staff_page
from test_browser import chromium_browser

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]


@pytest.mark.parametrize('language',['ar','en'])
@pytest.mark.parametrize('legacy',[False,True])
def test_manager_opens_next_shift_in_browser_before_owner_cash_receipt(central,central_web,chromium_browser,language,legacy):
    from playwright.sync_api import expect
    _,_,post,_,_=central
    old=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+old['shift']['id']
    if legacy:
        identifier=uuid4().hex
        with db.tx() as c:
            day=c.execute('SELECT workday_id FROM central_workday_legs WHERE shift_id=%s',(old['shift']['id'],)).fetchone()['workday_id']
            c.execute("INSERT INTO shared_manager_closure_declarations(id,workday_id,revision,cash_halalas,expected_snapshot,recorded_by,recipient_user_id,reference) VALUES(%s,%s,1,700,'[]','dual-manager','dual-owner','Synthetic legacy pending cash')",(identifier,day))
            for leg in old['shifts']:
                c.execute("UPDATE shifts SET status='submitted',submission=%s WHERE id=%s",(db.json({'shared_manager_declaration_id':identifier,'physical_state':'awaiting_owner_receipt','financial_state':'awaiting_allocation'}),leg['id']))
        declared={'declaration_id':identifier}
    else:
        declared=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'reference':'Cash retained until treasury is assigned','confirmed':True})
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'manager',language)
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
        assert response.json()['shift']['id']!=old['shift']['id']
        expect(page.locator('#modal')).not_to_be_visible()
        assert not errors,errors
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM shared_manager_closure_receipts').fetchone()['n']==0
            assert c.execute('SELECT cash_halalas FROM shared_manager_closure_declarations WHERE id=%s',(declared['declaration_id'],)).fetchone()['cash_halalas']==700
    finally:
        ctx.close()

"""Real manager decisions through Chromium and isolated PostgreSQL."""
import os
from uuid import uuid4
import pytest
from app import db
from test_browser import _context, _owner_login, _staff, _customer_session, chromium_browser, live_app, synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('decision',['assign','reject'])
def test_manager_resolves_unassigned_order(live_app,chromium_browser,lang,decision):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver');customer,_,_=_customer_session()
    mid='TEST-manager-'+uuid4().hex;sid='TEST-driver-'+uuid4().hex;oid='TEST-order-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]',%s)",(sid,driver['id'],mid))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','new','pending','cash',1000,0,1000,%s,%s)",(oid,customer,db.json({'delivery_workflow':'manager_dispatch','items':[]}),db.json({'name':'TEST decision customer'})))
    ctx,page,errors=_context(chromium_browser,live_app)
    headers=_owner_login(ctx,live_app,manager)
    ctx.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
    try:
        page.goto(live_app+'/ops')
        page.locator('#operational-alerts [data-action=order-detail][data-id="'+oid+'"]').first.click()
        expect(page.locator('#modal-body [data-action=manager-accept]')).to_have_text('قبول الطلب' if lang=='ar' else 'Accept order')
        expect(page.locator('#modal-body [data-action=assign]')).to_have_count(0)
        expect(page.locator('#modal-body [data-action=reject]')).to_be_visible()
        if decision=='assign':
            page.locator('#modal-body [data-action=manager-accept]').click()
            expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
            with db.tx() as c:
                accepted=c.execute('SELECT preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()
                assert accepted=={'preparation_status':'accepted','delivery_status':'pending'}
                assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==0
            page.locator('#dispatch-orders [data-action=assign][data-id="'+oid+'"]').click()
        else:
            page.locator('#modal-body [data-action=reject]').click()
        if decision=='assign':
            page.locator('#modal-body [name=driver_shift_id]').select_option(sid)
            page.locator('#modal-body button[type=submit]').click()
        else:
            # Controlled delivery rejection records the operational reason in
            # the same order-treatment widget, then the manager executes it.
            # No customer consent/image or higher approver is invented.
            dialog=page.locator('.intervention-dialog')
            expect(dialog).to_be_visible()
            create=dialog.locator('form:has([name=kind])')
            create.locator('[name=kind]').select_option('cancel')
            create.locator('[name=reason_code]').select_option('branch_unavailable')
            create.locator('[name=note]').fill('TEST unable to fulfill')
            create.locator('button').first.click()
            approval=dialog.locator('.intervention-request form:has(button[value=approve])')
            expect(approval.locator('button[value=approve]')).to_be_visible()
            approval.locator('[name=note]').fill('TEST manager confirmed unable to fulfill before acceptance')
            approval.locator('button[value=approve]').click()
            expect(dialog.locator('.intervention-state')).to_have_text('تم الاعتماد')
            dialog.locator('[data-close]').click()
            page.locator('#modal-close').click()
        expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
        with db.tx() as c:
            row=c.execute('SELECT preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()
            assert row==({'preparation_status':'accepted','delivery_status':'assigned'} if decision=='assign' else {'preparation_status':'cancelled','delivery_status':'cancelled'})
            if decision=='reject':
                original=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.requested'",(oid,)).fetchone()['details']
                assert original['reason_code']=='branch_unavailable' and original['note']=='TEST unable to fulfill'
                assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==0
            c.execute("UPDATE users SET permission_denials='[\"delivery_assign\",\"orders_cancel\"]' WHERE id=%s",(manager['id'],))
        # Same authenticated session must immediately lose both server capabilities.
        for path,body in [('assign',{'driver_shift_id':sid,'accept_order':True}),('preparation',{'status':'cancelled','version':2,'reason':'TEST denied'})]:
            response=ctx.request.post(live_app+f'/api/orders/{oid}/{path}',headers={**headers,'Idempotency-Key':uuid4().hex},data=body)
            assert response.status==403,response.text()
            assert response.json()['detail']['code']=='STAFF_PERMISSION_FORBIDDEN'
        assert not errors
    finally:ctx.close()


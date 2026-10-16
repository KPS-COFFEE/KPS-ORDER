"""Cashier presentation using real HTTP and isolated synthetic ledger states.

These fixtures are not evidence of a real provider transaction or a printer.
"""
import os
from uuid import uuid4
import pytest
from playwright.sync_api import expect
from app import db
from test_browser import live_app, chromium_browser, synthetic_mail, _context, _staff, _owner_login, _customer_session

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated browser acceptance')]


@pytest.mark.parametrize('lang,width',[('ar',390),('en',1280)])
@pytest.mark.parametrize('state',['unpaid','unverified_paid','verified'])
def test_cashier_only_offers_preparation_with_durable_payment_proof(live_app,chromium_browser,lang,width,state):
    staff=_staff('branch_manager')
    customer,_,_=_customer_session()
    oid='payment-ui-'+uuid4().hex
    with db.tx() as c:
        snap={'items':[],'branch_name':'Synthetic branch','branch_payment_mode':'test',
              'branch_payment_seller':'fixture-seller','order_reference':'TEST-ONLINE-001'}
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,payment_status,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'pickup','accepted','not_applicable','card',%s,1000,0,1000,%s,'{}')",
                  (oid,customer,'unpaid' if state=='unpaid' else 'paid',db.json(snap)))
        db.event(c,oid,staff['id'],'preparation.accepted',{})
        if state=='verified':
            aid='pay_'+uuid4().hex; pid=str(uuid4())
            c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by,status,verified_at) VALUES(%s,%s,'kps','kps-branch-1','fixture-seller','moyasar',%s,'test',1000,'SAR',0,%s,'verified',now())",(aid,oid,pid,staff['id']))
            c.execute("INSERT INTO branch_online_receipts(id,attempt_id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,disposition) VALUES(%s,%s,%s,'kps','kps-branch-1','fixture-seller','moyasar',%s,'test',1000,'SAR','applied')",('receipt_'+uuid4().hex,aid,oid,pid))
    context,page,errors=_context(chromium_browser,live_app)
    page.set_viewport_size({'width':width,'height':900})
    try:
        _owner_login(context,live_app,staff)
        page.goto('/ops.html')
        if lang=='en':page.locator('#language').click()
        page.locator('[data-page=orders]').click()
        page.locator('[data-action=order-detail][data-id="'+oid+'"]').click()
        card=page.locator('.order-detail-card')
        prepare=card.locator('[data-action=prepare][data-status=preparing]')
        if state=='verified':
            expect(prepare).to_be_enabled()
        else:
            expect(prepare).to_have_count(0)
            expect(card).to_contain_text('Awaiting verified online customer payment' if lang=='en' else 'بانتظار دفع العميل إلكترونيًا')
        expect(card.locator('[data-action=collect]')).to_have_count(0)
        expect(card.locator('[data-action=refund-order]')).to_have_count(0)
        record=context.request.get(live_app+'/api/orders/'+oid)
        assert record.status==200
        assert record.json()['order']['online_payment_verified'] is (state=='verified')
        assert not errors
    finally:
        context.close()

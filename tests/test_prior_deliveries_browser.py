"""Real Arabic/English owner and manager journeys on isolated PostgreSQL."""
import os
from datetime import timezone, timedelta
import pytest
from app import db
from test_enbishan_dual_brand import dual
from test_prior_deliveries import prior_setup
from test_central_workdays_browser import central, central_web, staff_page
from test_browser import chromium_browser

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser run')]


@pytest.mark.parametrize('language',['ar','en'])
def test_owner_authorization_then_manager_completes_existing_order(prior_setup,central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    actor,post,g,meta,pending,actual=prior_setup
    body=pending()
    post('owner','/api/prior-deliveries/grants/'+g['id']+'/revoke',{'reason':'Replace synthetic grant through real UI'},brand='enbishan')
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'owner',language)
    try:
        page.goto(central_web+'/ops')
        page.locator('#navigation [data-page=orders]').click()
        page.locator('[data-action=prior-deliveries]').click()
        page.locator('#prior-authorize').click()
        form=page.locator('#prior-grant-form')
        form.locator('[name=manager_shift_id]').select_option('prior-manager')
        form.locator('[name=driver_shift_ids]').check()
        form.locator('[name=reason]').fill('Synthetic owner approval from browser')
        form.locator('button[type=submit]').click()
        expect(page.locator('[data-prior-entry]')).to_have_count(1)
        assert not errors
    finally:ctx.close()
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'manager',language)
    try:
        page.goto(central_web+'/ops')
        page.locator('#navigation [data-page=orders]').click()
        page.locator('[data-action=prior-deliveries]').click()
        expect(page.locator('#prior-authorize')).to_have_count(0)
        page.locator('[data-prior-entry]').click()
        form=page.locator('#prior-entry-form')
        form.locator('[name=order_id]').select_option(body['order_id'])
        form.locator('[name=actual_delivered_at]').fill(actual.astimezone(timezone(timedelta(hours=3))).strftime('%Y-%m-%dT%H:%M'))
        form.locator('[name=source_reference]').fill('SYNTHETIC-BROWSER-'+language)
        form.locator('[name=cash]').fill('4.00')
        form.locator('[name=card]').fill('6.00')
        form.locator('[name=reason]').fill('Synthetic prior delivery from browser')
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            o=c.execute('SELECT payment_status,delivery_status FROM orders WHERE id=%s',(body['order_id'],)).fetchone()
            assert o=={'payment_status':'paid','delivery_status':'delivered'}
            assert c.execute('SELECT count(*) n FROM prior_delivery_records').fetchone()['n']==1
        assert not errors
    finally:ctx.close()

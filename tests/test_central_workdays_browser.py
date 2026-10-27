"""Real bilingual browser journeys over isolated dual-brand PostgreSQL."""
import os
import socket
import threading
import time
import httpx
import pytest
import uvicorn
from app import auth, db
from app.shift_vehicle import CHECKS
from test_enbishan_dual_brand import dual
from test_central_workdays import open_days, overnight_legacy_shifts
from test_browser import chromium_browser, _context

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]


@pytest.fixture
def central(dual,monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','true')
    return dual


@pytest.fixture
def central_web(central,monkeypatch):
    # No provider credentials or transport substitutes are introduced here.
    # The same genuine sessions and policies used by dual remain in force.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PUBLIC_BASE_URL',base)
    monkeypatch.setenv('APP_ALLOWED_ORIGINS',base)
    from app.main import app
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                if httpx.get(base+'/ready',timeout=1,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:pytest.fail('Isolated central browser server did not become ready')
        yield base
    finally:
        server.should_exit=True;thread.join(timeout=10)
        assert not thread.is_alive()


def staff_page(central,browser,base,name,language):
    _,actor,_,_,_=central
    client=actor(name)
    ctx,page,errors=_context(browser,base,client.cookies.get(auth.COOKIE),client.cookies.get(auth.CSRF_COOKIE))
    ctx.add_init_script('localStorage.setItem("kps_ops_lang",'+repr(language)+')')
    return ctx,page,errors


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_existing_overnight_manager_and_driver_load_and_adopt_without_clock_error(central, central_web, chromium_browser, monkeypatch, language):
    from playwright.sync_api import expect
    manager, driver = overnight_legacy_shifts(central, monkeypatch)
    for name, sid in [('manager', manager), ('driver', driver)]:
        ctx, page, errors = staff_page(central, chromium_browser, central_web, name, language)
        try:
            page.goto(central_web+'/ops')
            expect(page.locator('#navigation')).to_be_visible()
            page.locator('#navigation [data-page=shifts]').click()
            expect(page.locator('[data-action=shift-detail][data-id="'+sid+'"]').first).to_be_visible()
            assert 'MANAGER_SHIFT_HOURS' not in page.locator('body').inner_text()
            assert not errors, errors
            with db.tx() as c:
                assert c.execute('SELECT count(*) n FROM central_workday_legs l JOIN central_workdays w ON w.id=l.workday_id WHERE w.anchor_shift_id=%s', (sid,)).fetchone()['n'] == 2
        finally:
            ctx.close()


@pytest.mark.parametrize('language',['ar','en'])
def test_manager_has_one_workday_both_order_menus_and_unified_conversations(central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    _,_,post,_,order=central
    manager,driver=open_days(central)
    conversations=[]
    for brand in ['kps','enbishan']:
        oid=order(brand)
        item=post('manager','/api/conversations',{'order_id':oid},brand)
        conversations.append(item['conversation']['id'])
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'manager',language)
    try:
        page.goto(central_web+'/ops?operations_brand=enbishan')
        expect(page.locator('#navigation')).to_be_visible()
        expect(page.locator('#operations-brand-switch')).to_have_count(0)
        expect(page.locator('#page-heading [data-action=customer-order][data-brand=kps]')).to_be_visible()
        expect(page.locator('#page-heading [data-action=customer-order][data-brand=enbishan]')).to_be_visible()
        for brand in ['kps','enbishan']:
            page.locator('#page-heading [data-action=customer-order][data-brand='+brand+']').click()
            expect(page.locator('#modal')).to_be_visible()
            branches=page.locator('#modal select[name=branch_id] option').evaluate_all('(options)=>options.map(o=>o.value)')
            assert branches and all(b.startswith('kps-') if brand=='kps' else b.startswith('enbishan-') for b in branches)
            page.locator('#modal-close').click()
        page.locator('#navigation [data-page=shifts]').click()
        expect(page.locator('.shift-card [data-action=shift-detail][data-id="'+manager['shift']['id']+'"]').first).to_be_visible()
        for leg in manager['shifts']:
            if leg['id']!=manager['shift']['id']:
                expect(page.locator('.shift-card [data-action=shift-detail][data-id="'+leg['id']+'"]').first).to_have_count(0)
        page.locator('#navigation [data-page=chat]').click()
        for identifier in conversations:
            expect(page.locator('[data-action=conversation][data-id="'+identifier+'"]').first).to_be_visible()
        assert not errors,errors
    finally:ctx.close()


@pytest.mark.parametrize('language',['ar','en'])
def test_driver_shared_closure_has_one_cash_card_input_and_accounting_allocation(central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    _,_,post,_,_=central
    _,driver=open_days(central);sid=driver['shift']['id'];prefix='/api/central-operations/workdays/'+sid
    pending=post('driver',prefix+'/request',{'reason':'Synthetic browser shift end'})
    post('manager',prefix+'/decision',{'request_id':pending['shift']['closure_authorization']['id'],'decision':'approved','reason':'Synthetic approval'})
    post('driver',prefix+'/closing',{})
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'driver',language)
    try:
        page.goto(central_web+'/ops')
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=shift-detail][data-id="'+sid+'"]').first.click()
        expect(page.locator('#modal input[name=inspection_odometer]')).to_have_count(1)
        expect(page.locator('#modal input[name=cash]')).to_have_count(1)
        expect(page.locator('#modal input[name^="counted:"]')).to_have_count(0)
        expect(page.locator('#modal input[name=card]')).to_have_count(1)
        expect(page.locator('#modal input[name^="terminal:"]')).to_have_count(0)
        assert not errors,errors
        # Submit actual values through the genuine driver form, then receive
        # both canonical ledgers through the manager's single closure dialog.
        form=page.locator('#central-closure-form')
        form.locator('[name=inspection_odometer]').fill('1025')
        form.locator('[name=fuel_full]').select_option('yes')
        for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        form.locator('[name=cash]').fill('0')
        form.locator('[name=card]').fill('0')
        form.locator('[name=handover_branch_id]').select_option('kps-branch-1')
        form.locator('[name=bag_reference]').fill('Synthetic separate driver bag')
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.locator('#modal-close').click()
        expect(form).to_be_visible()
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-driver'").fetchall()}=={'submitted'}
        manager_ctx,manager_page,manager_errors=staff_page(central,chromium_browser,central_web,'manager',language)
        try:
            manager_page.goto(central_web+'/ops')
            manager_page.locator('#navigation [data-page=shifts]').click()
            manager_page.locator('[data-action=shift-detail][data-id="'+sid+'"]').first.click()
            receipt=manager_page.locator('#central-closure-form')
            expect(receipt.locator('[name=inspection_odometer]')).to_have_count(0)
            expect(receipt.locator('[name=received]')).to_have_count(1)
            expect(receipt.locator('[data-action=shared-driver-return]')).to_have_count(1)
            receipt.locator('[name=received]').fill('0')
            receipt.locator('[name=reference]').fill('Physical zero cash receipt')
            receipt.locator('[name=confirmed]').check()
            receipt.locator('button[type=submit]').click()
            expect(manager_page.locator('#modal')).not_to_be_visible(timeout=30000)
            assert not manager_errors,manager_errors
            with db.tx() as c:
                rows=c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall()
                assert {s['status'] for s in rows}=={'submitted'}
                assert all(s['staffing_ended_at'] is not None for s in rows)
                declaration=c.execute('SELECT * FROM shared_driver_closure_declarations').fetchone()
                physical=c.execute('SELECT * FROM shared_driver_closure_receipts').fetchone()
                assert sum('end' in s['vehicle_log'] for s in rows)==1
                assert c.execute("SELECT count(*) n FROM closure_reports WHERE shift_id=ANY(%s)",([s['id'] for s in rows],)).fetchone()['n']==0
            # Evidence-backed accounting allocation creates the brand snapshots;
            # manager receipt itself never fabricates brand actuals.
            accountant_ctx,accountant_page,accountant_errors=staff_page(central,chromium_browser,central_web,'accountant',language)
            try:
                accountant_page.goto(central_web+'/ops')
                accountant_page.locator('#navigation [data-page=accounting]').click()
                accountant_page.locator('[data-action=shared-driver-inbox]').click()
                accountant_page.locator('[data-action=shared-driver-case][data-id="'+sid+'"]').click()
                allocation=accountant_page.locator('#central-closure-form')
                expect(allocation.locator('[name^="cash:"]')).to_have_count(2)
                expect(allocation.locator('[name^="card:"]')).to_have_count(2)
                for shift in rows:
                    for name in ('cash','card','retained'):
                        allocation.locator('[name="'+name+':'+shift['id']+'"]').fill('0')
                    allocation.locator('[name="evidence:'+shift['id']+'"]').fill('No activity verified')
                allocation.locator('[name=reason]').fill('Verified zero-activity brand allocation')
                allocation.locator('[name=confirmed]').check()
                allocation.locator('button[type=submit]').click()
                expect(accountant_page.locator('#modal')).not_to_be_visible(timeout=30000)
                assert not accountant_errors,accountant_errors
            finally:accountant_ctx.close()
            with db.tx() as c:
                assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-driver'").fetchall()}=={'closed'}
                assert c.execute("SELECT count(*) n FROM closure_reports WHERE shift_id=ANY(%s)",([s['id'] for s in rows],)).fetchone()['n']==2
        finally:manager_ctx.close()
        assert not errors,errors
    finally:ctx.close()


@pytest.mark.parametrize('language',['ar','en'])
def test_existing_open_shift_is_adopted_without_repeating_vehicle_receipt(central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    _,_,post,shifts,order=central
    manager_id,driver_id=shifts('kps')
    oid=order('kps')
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver_id})['task']
    with db.tx() as c:
        before=c.execute('SELECT * FROM shifts WHERE id=%s',(driver_id,)).fetchone()
    for name,identifier in [('manager',manager_id),('driver',driver_id)]:
        ctx,page,errors=staff_page(central,chromium_browser,central_web,name,language)
        try:
            page.goto(central_web+'/ops')
            page.locator('#navigation [data-page=shifts]').click()
            expect(page.locator('.shift-card [data-action=shift-detail][data-id="'+identifier+'"]').first).to_be_visible()
            expect(page.locator('#operations-brand-switch')).to_have_count(0)
            assert not errors,errors
            with db.tx() as c:
                assert c.execute('SELECT 1 FROM central_workday_legs WHERE shift_id=%s',(identifier,)).fetchone()
        finally:ctx.close()
    with db.tx() as c:
        after=c.execute('SELECT * FROM shifts WHERE id=%s',(driver_id,)).fetchone()
        assert after['vehicle_log']==before['vehicle_log']
        assert after['opening_halalas']==before['opening_halalas']
        assert after['opened_at']==before['opened_at']
        assert c.execute('SELECT shift_id FROM driver_tasks WHERE id=%s',(task['id'],)).fetchone()['shift_id']==driver_id
        rows=c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall()
        assert len(rows)==2
        assert sum('start' in row['vehicle_log'] for row in rows)==1


@pytest.mark.parametrize('language',['ar','en'])
def test_vault_close_and_actual_bag_count_need_no_owner_confirmation(central,central_web,chromium_browser,language):
    from playwright.sync_api import expect
    from test_aggregate_driver_closure import declare_separate_bag
    _,_,post,_,_=central
    manager,driver,_,declared=declare_separate_bag(central,'النعيرية')
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'manager',language)
    try:
        page.goto(central_web+'/ops')
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=shift-detail][data-id="'+manager['shift']['id']+'"]').first.click()
        expect(page.locator('#modal [name=recipient_user_id]')).to_have_count(0)
        form=page.locator('#modal form')
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        assert not errors,errors
    finally:ctx.close()
    ctx,page,errors=staff_page(central,chromium_browser,central_web,'accountant',language)
    try:
        page.goto(central_web+'/ops')
        page.locator('#navigation [data-page=accounting]').click()
        page.locator('[data-action=cash-bags]').click()
        page.locator('[data-action=cash-bag-count][data-id="'+declared['cash_bag']['id']+'"]').click()
        form=page.locator('#modal form')
        form.locator('[name=cash]').fill('6.50')
        form.locator('[name=card]').fill('9.00')
        form.locator('[name=reference]').fill('Synthetic witnessed accounting count')
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        details=page.locator('[data-action=cash-bag-count][data-id="'+declared['cash_bag']['id']+'"]')
        expect(details).to_be_visible()
        details.click()
        expect(page.locator('#modal-body')).to_contain_text('SYNTHETIC dual-accountant')
        with db.tx() as c:
            assert c.execute('SELECT received_cash_halalas FROM driver_cash_bag_counts WHERE bag_id=%s',(declared['cash_bag']['id'],)).fetchone()['received_cash_halalas']==650
            assert not c.execute('SELECT 1 FROM shared_manager_closure_receipts').fetchone()
        assert not errors,errors
    finally:ctx.close()

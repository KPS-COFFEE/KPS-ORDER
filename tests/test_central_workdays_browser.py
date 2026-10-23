"""Real bilingual browser journeys over isolated dual-brand PostgreSQL."""
import os
import socket
import threading
import time
import httpx
import pytest
import uvicorn
from app import auth, db
from test_enbishan_dual_brand import dual
from test_central_workdays import open_days
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
def test_driver_shared_closure_has_one_inspection_and_both_ledger_inputs(central,central_web,chromium_browser,language):
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
        expect(page.locator('#modal input[name^="counted:"]')).to_have_count(2)
        expect(page.locator('#modal input[name^="terminal:"]')).to_have_count(2)
        assert not errors,errors
        with db.tx() as c:
            assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-driver'").fetchall()}=={'closing'}
    finally:ctx.close()

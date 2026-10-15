"""Opt-in theme, owner-only persistence, exact light rollback and real responsive UI.

All data is synthetic in the existing isolated PostgreSQL test fixtures.
"""
from io import BytesIO
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app import db
from test_admin import owner
from test_v5_browser import v5_live_app
from test_browser import (ARTIFACTS, WIDTHS, _context, _owner_login, _staff,
                          chromium_browser, live_app, synthetic_mail)


def test_appearance_official_domain_cannot_apply_unapproved_preview_or_saved_profile():
    import subprocess
    script = """
    const fs=require('fs'),vm=require('vm');
    for(const host of ['order.kps.sa','kps-order-test.onrender.com','kps-order-emergency-v4.onrender.com']) {
      const root={dataset:{}};
      const window={addEventListener(){}};
      vm.runInNewContext(fs.readFileSync('public/assets/appearance.js','utf8'),{
        document:{documentElement:root,querySelector(){return {setAttribute(){}}}},window,
        location:{hostname:host,search:'?theme=CHARCOAL'},URLSearchParams,URL,
        localStorage:{getItem(){return 'CHARCOAL'},setItem(){}}
      });
      window.KPSAppearance.apply({profile:'CHARCOAL'});
      window.KPSAppearance.preview('CHARCOAL');
      const expected=host==='kps-order-emergency-v4.onrender.com'?'CHARCOAL':'ORIGINAL_Light';
      if(root.dataset.kpsTheme!==expected) throw Error('Unauthorized rollout: '+host);
    }
    """
    subprocess.run(['node','-e',script],check=True)


@pytest.mark.postgres
def test_appearance_owner_merge_validation_scope_and_operational_preservation(owner):
    client, mutate = owner
    before = client.get('/api/admin/config').json()
    with db.tx() as conn:
        existing = conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        fingerprint = db.preservation_snapshot(conn)
    custom = {'accent':'#a94710','font':'Noto Kufi Arabic'}
    assert mutate('PATCH','/api/admin/settings',{'theme':custom}).status_code == 200
    for profile in ['CHARCOAL','ORIGINAL_Light']:
        result = mutate('PATCH','/api/admin/settings',{'theme':{'profile':profile}})
        assert result.status_code == 200, result.text
        assert result.json()['settings']['theme'] == custom | {'profile':profile}
        assert client.get('/api/catalog').json()['brand']['theme']['profile'] == profile
        for key,value in existing.items():
            if key != 'theme': assert result.json()['settings'][key] == value
    for invalid in ['arbitrary',[],{},1,None]:
        assert mutate('PATCH','/api/admin/settings',{'theme':{'profile':invalid}}).status_code == 422
    after = client.get('/api/admin/config').json()
    assert before['products'] == after['products'] and before['branches'] == after['branches']
    with db.tx() as conn:
        assert db.preservation_snapshot(conn) == fingerprint
    client.post('/api/auth/logout')
    assert mutate('PATCH','/api/admin/settings',{'theme':{'profile':'CHARCOAL'}}).status_code in (401,403)


def _capture_theme(page, name):
    for width in WIDTHS:
        page.set_viewport_size({'width':width,'height':900})
        page.wait_for_timeout(80)
        if name=='menu': page.locator('#menu').evaluate("el=>el.scrollIntoView({block:'start'})")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('html').get_attribute('data-kps-theme') == 'CHARCOAL'
        for dialog in page.locator('dialog[open]').all():
            assert dialog.evaluate('(el)=>el.scrollWidth <= el.clientWidth')
        page.screenshot(path=str(ARTIFACTS / f'charcoal-{name}-{width}.png'))


def _settled_store_appearance(page):
    page.evaluate("""async () => {
        await document.fonts.ready;
        await Promise.all(Array.from(document.images).filter(img => {
            const r=img.getBoundingClientRect();
            return img.getAttribute('src') && img.getClientRects().length && r.top < innerHeight && r.bottom > 0;
        }).map(img => img.decode()));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }""")
    return page.locator('body,.site-header,.intro-banner,.service-panel,.search input,.product-card,.basket').evaluate_all("""els => els.map(el => {
        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
        return {x:r.x,y:r.y,width:r.width,height:r.height,color:s.color,background:s.backgroundColor,
            border:s.border,shadow:s.boxShadow,font:s.font,padding:s.padding};
    })""")


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real Chromium test required.')
def test_appearance_real_customer_owner_rollback_and_all_staff_pages(live_app, chromium_browser):
    from playwright.sync_api import expect
    context,page,errors = _context(chromium_browser,live_app)
    page.goto('/')
    expect(page.locator('.product-card')).to_have_count(34)
    original_styles = _settled_store_appearance(page)
    original = page.screenshot(path=str(ARTIFACTS/'original-store-1280.png'),animations='disabled')
    page.set_viewport_size({'width':390,'height':900})
    page.locator('#menu').evaluate("el=>el.scrollIntoView({block:'start'})")
    page.screenshot(path=str(ARTIFACTS/'original-menu-390.png'))
    page.set_viewport_size({'width':1280,'height':900})
    page.goto('/?theme=CHARCOAL')
    expect(page.locator('.product-card')).to_have_count(34)
    page.evaluate('document.fonts.ready')
    assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(38, 38, 38)'
    _capture_theme(page,'store')
    page.locator('#menu').scroll_into_view_if_needed()
    _capture_theme(page,'menu')
    page.locator('[data-product-id="kps-33"] .product-image-button').click()
    expect(page.locator('#product-dialog')).to_be_visible()
    expect(page.locator('#product-title')).to_have_text('الخلية')
    _capture_theme(page,'khaliya')
    page.locator('#product-dialog [data-close]').click()
    page.locator('#account-button').click()
    _capture_theme(page,'account')
    page.locator('#guest-name').fill('ضيف اختبار المظهر فقط')
    page.locator('#guest-phone').fill('0500000044')
    page.locator('#guest-submit').click()
    expect(page.locator('#account-dialog')).not_to_be_visible()
    page.locator('#legal-contact-button').click()
    page.locator('#public-contact-support').click()
    expect(page.locator('#contacts-dialog')).to_be_visible()
    _capture_theme(page,'contacts')
    page.locator('#contacts-dialog [data-close]').click()
    page.emulate_media(media='print')
    assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
    page.emulate_media(media='screen')
    assert not errors
    context.close()

    contexts=[]
    try:
        for role in ['owner','delivery_manager','delivery_staff','branch_manager','driver']:
            user=_staff(role)
            ctx,p,errs=_context(chromium_browser,live_app);contexts.append(ctx)
            headers=_owner_login(ctx,live_app,user)
            p.goto('/ops?theme=CHARCOAL')
            expect(p.locator('#app-shell')).to_be_visible()
            expect(p.locator('#content')).to_have_attribute('aria-busy','false')
            pages=p.locator('#navigation [data-page]').evaluate_all('(els)=>els.map(el=>el.dataset.page)')
            for screen in pages:
                p.locator(f'#navigation [data-page={screen}]').click()
                expect(p.locator('#content')).to_have_attribute('aria-busy','false')
                _capture_theme(p,role+'-'+screen)
            if role=='owner':
                p.locator('[data-action=admin-tab][data-tab=settings]').click()
                expect(p.locator('#appearance-settings')).to_be_visible()
                _capture_theme(p,'owner-appearance')
                p.locator('[data-appearance=CHARCOAL]').click()
                expect(p.locator('#appearance-result')).to_contain_text('تم حفظ المظهر')
                p.reload()
                expect(p.locator('#app-shell')).to_be_visible()
                expect(p.locator('html')).to_have_attribute('data-kps-theme','CHARCOAL')
                p.locator('[data-page=admin]').click()
                p.locator('[data-action=admin-tab][data-tab=settings]').click()
                p.locator('[data-appearance=ORIGINAL_Light]').click()
                expect(p.locator('html')).to_have_attribute('data-kps-theme','ORIGINAL_Light')
                assert 'theme=' not in p.url
                p.reload()
                expect(p.locator('html')).to_have_attribute('data-kps-theme','ORIGINAL_Light')
                # Owner versus anonymous header differs. Compare in a fresh session.
                restored_ctx,restored_page,_=_context(chromium_browser,live_app)
                restored_page.goto('/')
                expect(restored_page.locator('.product-card')).to_have_count(34)
                assert _settled_store_appearance(restored_page) == original_styles
                restored=restored_page.screenshot(path=str(ARTIFACTS/'original-restored-1280.png'),animations='disabled')
                # Decode actual pixels; Chromium can vary rounded-edge antialias
                # by 1/255 in a few pixels. Geometry and CSS above must match exactly.
                difference=ImageChops.difference(Image.open(BytesIO(restored)).convert('RGB'),Image.open(BytesIO(original)).convert('RGB'))
                assert max(high for low,high in difference.getextrema()) <= 1
                assert sum(pixel!=(0,0,0) for pixel in difference.get_flattened_data()) <= 100
                restored_ctx.close()
            else:
                assert p.locator('[data-appearance]').count()==0
                denied=ctx.request.patch(live_app+'/api/admin/settings',headers=headers,data={'theme':{'profile':'CHARCOAL'}})
                assert denied.status in (401,403)
            assert not errs
    finally:
        for ctx in contexts: ctx.close()


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real Chromium test required.')
def test_appearance_charcoal_full_guest_order_driver_collection_and_closures(v5_live_app, chromium_browser, monkeypatch):
    import test_v5_browser as journey
    original_context=journey._context
    def charcoal_context(*args,**kwargs):
        context,page,errors=original_context(*args,**kwargs)
        context.add_init_script("""if(location.hostname==='127.0.0.1') { const u=new URL(location.href);u.searchParams.set('theme','CHARCOAL');history.replaceState(null,'',u); }""")
        return context,page,errors
    monkeypatch.setattr(journey,'_context',charcoal_context)
    monkeypatch.setattr(journey,'_capture',lambda page,name:_capture_theme(page,'journey-'+name))
    journey.test_v5_guest_email_invited_driver_one_collection_final_owner_report(v5_live_app,chromium_browser)

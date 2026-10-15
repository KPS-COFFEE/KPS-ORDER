"""Real HTTP + Chromium, synthetic isolated DB; before assets are exact archived bytes.
Only before-render static assets are intercepted. No API response is intercepted.
"""
import os
import tarfile
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from test_browser import live_app, chromium_browser, synthetic_mail, _context
from app.customer_policy_content import CONTROLLER

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'test-artifacts'
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires Chromium and isolated PostgreSQL')]

@pytest.mark.parametrize('width',[390,1280])
@pytest.mark.parametrize('lang',['ar','en'])
def test_legal_before_after_accessibility_and_contact(live_app,chromium_browser,width,lang):
    from playwright.sync_api import expect
    EVIDENCE.mkdir(exist_ok=True)
    context,page,errors=_context(chromium_browser,live_app)
    page.set_viewport_size({'width':width,'height':900})
    context.add_init_script(f'localStorage.setItem("kps_customer_lang","{lang}")')
    with tarfile.open(ROOT/'docs/legal-presentation/baseline-c37d2d8.tar') as tf:
        baseline={n.removeprefix('public/'):tf.extractfile(n).read() for n in tf.getnames() if n.startswith('public/') and tf.getmember(n).isfile()}
    def previous(route):
        path=urlsplit(route.request.url).path.lstrip('/') or 'index.html'
        if path in baseline:
            mime='text/html' if path.endswith('.html') else 'text/css' if path.endswith('.css') else 'application/javascript'
            route.fulfill(status=200,content_type=mime,body=baseline[path])
        else:route.continue_()
    page.route('**/*',previous)
    page.goto(live_app+'/?brand_id=kps')
    expect(page.locator('#account-button')).to_be_enabled()
    page.locator('.site-footer').scroll_into_view_if_needed()
    page.screenshot(path=str(EVIDENCE/f'legal-before-{lang}-{width}.png'),full_page=True)
    page.unroute('**/*',previous)
    page.reload()
    expect(page.locator('#account-button')).to_be_enabled()
    footer=page.locator('.compact-legal-footer')
    footer.scroll_into_view_if_needed()
    expect(footer).to_be_visible()
    assert footer.bounding_box()['height']<=100
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('html').get_attribute('dir')==('rtl' if lang=='ar' else 'ltr')
    assert CONTROLLER not in page.locator('body').inner_text()
    for selector in ('#menu','#basket'):
        assert CONTROLLER not in page.locator(selector).inner_text()
    assert '@kps.coffe' not in footer.inner_text() and '@Kps.cofee' not in footer.inner_text()
    for a in footer.locator('.social-icons a').all():
        assert a.get_attribute('aria-label') and a.get_attribute('title')
        assert a.get_attribute('target')=='_blank' and a.get_attribute('rel')=='noopener noreferrer'
        assert a.inner_text()==''
        assert 36<=a.bounding_box()['width']<=40 and 36<=a.bounding_box()['height']<=40
        assert a.locator('img').evaluate('e=>e.complete && e.naturalWidth>0')
    for a in footer.locator('.legal-strip>a,.legal-strip>button').all():
        assert 11<=float(a.evaluate('e=>getComputedStyle(e).fontSize').replace('px',''))<=12
        assert a.bounding_box()['height']>=36
        assert 14<=a.locator('svg').bounding_box()['width']<=16
    page.screenshot(path=str(EVIDENCE/f'legal-after-{lang}-{width}.png'),full_page=True)
    footer.screenshot(path=str(EVIDENCE/f'legal-footer-{lang}-{width}.png'))
    page.locator('#legal-contact-button').click()
    panel=page.locator('#public-contact-dialog')
    expect(panel).to_be_visible()
    expect(panel).to_contain_text('KPS COFFEE')
    expect(panel).to_contain_text('0550472662')
    expect(panel).to_contain_text('+966 55 047 2662')
    assert CONTROLLER not in panel.inner_text() and '@Kps.cofee' not in panel.inner_text()
    assert panel.locator('a[href^="mailto:"]').count()==0
    page.screenshot(path=str(EVIDENCE/f'legal-contact-{lang}-{width}.png'))
    page.locator('#public-contact-support').click()
    expect(page.locator('#account-dialog')).to_be_visible()
    assert CONTROLLER not in page.locator('#account-dialog').inner_text()
    page.locator('#account-dialog [data-close]').click()
    for kind in ('privacy','terms','refunds'):
        response=page.goto(live_app+f'/{kind}?lang={lang}')
        assert response.status==200
        assert CONTROLLER not in page.locator('body').inner_text() if kind!='privacy' else page.locator('#data-controller').inner_text().count(CONTROLLER)==1
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(EVIDENCE/f'legal-{kind}-{lang}-{width}.png'),full_page=True)
    assert not errors
    context.close()

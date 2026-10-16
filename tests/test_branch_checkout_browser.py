"""Actual shipped browser script, simulated API/SDK; not provider certification."""
from pathlib import Path
import json
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('state',['verified','review_required','unavailable'])
def test_return_ignores_forged_browser_success(lang,state):
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':390,'height':844})
        calls=[]
        def route(request):
            url=request.request.url
            if '/api/auth/session' in url:
                request.fulfill(json={'user':{'role':'customer'},'csrf_token':'synthetic-csrf'})
            elif '/reconcile' in url:
                calls.append(request.request)
                request.fulfill(status=503 if state=='unavailable' else 200,json={'attempt':{'status':state}})
            else:
                request.fulfill(content_type='text/html',body='<p id="payment-state"></p><div class="mysr-form"></div><button id="verify-payment" hidden></button><a id="back-to-store"></a>')
        page.route('https://fixture.invalid/**',route)
        try:
            page.goto('https://fixture.invalid/payment-return?attempt=pay_fixture&status=paid&lang='+lang)
            page.add_script_tag(path=str(ROOT/'public/assets/branch-payment.js'))
            expected={
                'verified':('تم الدفع بنجاح. تابع تجهيز طلبك من المتجر.','Payment verified. Follow preparation in the store.'),
                'review_required':('الدفع يحتاج مراجعة الفرع. لا تدفع مرة أخرى.','The payment needs branch review. Do not pay again.'),
                'unavailable':('لم يتأكد الدفع بعد. أعد التحقق أو تواصل مع الفرع قبل محاولة دفع أخرى.','Payment is not confirmed. Verify again or contact the branch before another payment.')}
            expect(page.locator('#payment-state')).to_have_text(expected[state][lang=='en'])
            assert len(calls)==1 and calls[0].method=='POST'
            assert calls[0].headers['x-csrf-token']=='synthetic-csrf'
            expect(page.locator('#verify-payment')).to_be_visible()
            assert page.locator('input').count()==0
            assert page.locator('html').get_attribute('dir')==('ltr' if lang=='en' else 'rtl')
        finally:browser.close()

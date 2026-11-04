"""Actual shipped browser script, simulated API/SDK; not provider certification."""
from pathlib import Path
import json
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('matching_key',[True,False])
def test_live_checkout_label_and_cross_mode_key_rejection(lang,matching_key):
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':390,'height':844})
        def route(request):
            url=request.request.url
            if '/api/auth/session' in url:
                request.fulfill(json={'user':{'role':'customer'},'csrf_token':'synthetic-csrf'})
            elif '/checkout' in url:
                request.fulfill(json={'mode':'live','publishable_api_key':('pk_live_' if matching_key else 'pk_test_')+'synthetic_only'})
            else:
                request.fulfill(content_type='text/html',body='<p id="payment-state"></p><div class="mysr-form"></div><button id="verify-payment" hidden></button><a id="back-to-store"></a>')
        page.route('https://fixture.invalid/**',route)
        try:
            page.goto('https://fixture.invalid/branch-payment?attempt=pay_fixture&lang='+lang)
            page.evaluate('window.sdkCalls=[];window.Moyasar={init:(config)=>window.sdkCalls.push(config)}')
            page.add_script_tag(path=str(ROOT/'public/assets/branch-payment.js'))
            if matching_key:
                expect(page.locator('#payment-state')).to_have_text('أكمل الدفع الآمن، ثم انتظر تأكيد العملية.' if lang=='ar' else 'Complete secure payment, then wait for verification.')
                assert page.evaluate('window.sdkCalls.length')==1
                assert page.evaluate('window.sdkCalls[0].methods')==['creditcard']
            else:
                expect(page.locator('#payment-state')).to_have_text('تعذر فتح الدفع. ارجع إلى طلبك وسجّل الدخول أو تواصل مع الفرع.' if lang=='ar' else 'Checkout could not open. Return to your order and sign in or contact the branch.')
                assert page.evaluate('window.sdkCalls.length')==0
        finally:browser.close()

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


@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('allowed',[True,False])
def test_customer_explicit_renewal_uses_server_decision(lang,allowed):
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':390,'height':844})
        calls=[]
        def route(request):
            url=request.request.url
            if '/api/auth/session' in url:
                request.fulfill(json={'user':{'role':'customer'},'csrf_token':'synthetic-csrf'})
            elif '/renew' in url:
                calls.append(request.request)
                request.fulfill(status=200 if allowed else 409,json={'attempt':{'id':'pay_successor'}})
            elif '/checkout' in url:
                request.fulfill(status=409,json={})
            else:
                request.fulfill(content_type='text/html',body='<p id="payment-state"></p><div class="mysr-form"></div><button id="verify-payment" hidden></button><a id="back-to-store"></a>')
        page.route('https://fixture.invalid/**',route)
        try:
            page.goto('https://fixture.invalid/branch-payment?attempt=pay_fixture&lang='+lang)
            page.add_script_tag(path=str(ROOT/'public/assets/branch-payment.js'))
            button=page.locator('#renew-payment')
            expect(button).to_be_visible()
            expect(page.locator('#payment-state')).to_have_text(
                'تعذر فتح الدفع. ارجع إلى طلبك وسجّل الدخول أو تواصل مع الفرع.' if lang=='ar' else
                'Checkout could not open. Return to your order and sign in or contact the branch.')
            button.click()
            if allowed:
                expect(page).to_have_url('https://fixture.invalid/branch-payment?attempt=pay_successor&lang='+lang)
            else:
                expect(page.locator('#payment-state')).to_have_text(
                    'لا يمكن إعادة الدفع قبل حسم الجلسة السابقة. أعد التحقق أو تواصل مع الفرع.' if lang=='ar' else
                    'Payment cannot be retried until the previous session is resolved. Verify again or contact the branch.')
                expect(button).to_be_enabled()
            assert len(calls)==1 and calls[0].method=='POST'
            assert calls[0].headers['x-csrf-token']=='synthetic-csrf'
        finally:browser.close()

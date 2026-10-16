'use strict';
(async () => {
  const query = new URLSearchParams(location.search);
  const lang = query.get('lang') || sessionStorage.getItem('kps-payment-language') || 'ar';
  const en = lang === 'en';
  sessionStorage.setItem('kps-payment-language', en ? 'en' : 'ar');
  document.documentElement.lang = en ? 'en' : 'ar';
  document.documentElement.dir = en ? 'ltr' : 'rtl';
  const text = (ar, english) => en ? english : ar;
  const status = document.getElementById('payment-state');
  const verify = document.getElementById('verify-payment');
  document.getElementById('back-to-store').textContent = text('العودة للمتجر', 'Back to store');
  verify.textContent = text('التحقق من الدفع', 'Verify payment');
  const aid = query.get('attempt') || '';
  let csrf;
  async function post(suffix) {
    const response = await fetch(`/api/branch-payments/attempts/${encodeURIComponent(aid)}/${suffix}`, {
      method: 'POST', credentials: 'same-origin', headers: {'X-CSRF-Token': csrf}, cache: 'no-store'
    });
    if (!response.ok) throw new Error('Payment not confirmed');
    return response.json();
  }
  async function reconcile() {
    verify.disabled = true;
    try {
      const result = await post('reconcile');
      status.textContent = result.attempt.status === 'verified'
        ? text('تم الدفع بنجاح. تابع تجهيز طلبك من المتجر.', 'Payment verified. Follow preparation in the store.')
        : text('الدفع يحتاج مراجعة الفرع. لا تدفع مرة أخرى.', 'The payment needs branch review. Do not pay again.');
    } catch (_) {
      status.textContent = text('لم يتأكد الدفع بعد. أعد التحقق أو تواصل مع الفرع قبل محاولة دفع أخرى.', 'Payment is not confirmed. Verify again or contact the branch before another payment.');
    } finally { verify.disabled = false; }
  }
  try {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(aid)) throw new Error('Invalid attempt');
    const sessionResponse = await fetch('/api/auth/session', {credentials:'same-origin', cache:'no-store'});
    const session = await sessionResponse.json();
    if (!sessionResponse.ok || session.user?.role !== 'customer' || !session.csrf_token) throw new Error('Sign in required');
    csrf = session.csrf_token;
    verify.addEventListener('click', reconcile);
    if (location.pathname === '/payment-return') { verify.hidden = false; await reconcile(); return; }
    status.textContent = text('جارٍ فتح الدفع التجريبي الآمن…', 'Opening secure test checkout…');
    const config = await post('checkout');
    if (config.mode !== 'test' || !config.publishable_api_key.startsWith('pk_test_') || !window.Moyasar) throw new Error('Checkout unavailable');
    window.Moyasar.init({...config, element:'.mysr-form', language:en?'en':'ar', fixed_width:false,
      description:'KPS order payment', use_payment_intent:true, methods:['creditcard'],
      supported_networks:['mada','visa','mastercard'],
      on_failure:() => { status.textContent=text('لم يكتمل الدفع. تحقق من حالته قبل إعادة المحاولة.', 'Payment did not complete. Verify its status before retrying.'); verify.hidden=false; }
    });
    status.textContent = text('دفع تجريبي — لا تستخدم بطاقة حقيقية.', 'Test payment — do not use a real card.');
  } catch (_) {
    status.textContent = text('تعذر فتح الدفع. ارجع إلى طلبك وسجّل الدخول أو تواصل مع الفرع.', 'Checkout could not open. Return to your order and sign in or contact the branch.');
  }
})();

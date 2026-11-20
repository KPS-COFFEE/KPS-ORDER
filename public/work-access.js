(() => {
  'use strict';

  // URL fragments are not sent to the server. Remove the token from browser
  // history before exchanging it; never persist it in browser storage or logs.
  const fragment = new URLSearchParams(location.hash.slice(1));
  let token = fragment.get('token') || '';
  const validFragment = fragment.getAll('token').length === 1 && [...fragment.keys()].length === 1;
  fragment.delete('token');
  history.replaceState(null, '', location.pathname);

  function status(ar, en) {
    document.getElementById('access-status-ar').textContent = ar;
    document.getElementById('access-status-en').textContent = en;
  }

  async function redeem() {
    if (!validFragment || !/^[A-Za-z0-9_-]{43,100}$/.test(token)) {
      token = '';
      status('رابط الإذن غير صالح. اطلب رابطًا جديدًا من المالك.',
        'This authorization link is invalid. Ask the owner for a new link.');
      return;
    }
    try {
      const response = await fetch('/api/staff-geo/redeem', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {'Content-Type': 'application/json', Accept: 'application/json'},
        body: JSON.stringify({token}),
      });
      token = '';
      if (!response.ok) {
        if (response.status === 429) {
          status('محاولات كثيرة. انتظر قليلًا ثم افتح الرابط من جديد.',
            'Too many attempts. Wait briefly, then open the link again.');
        } else {
          status('انتهى الإذن أو استُخدم أو أُلغي. اطلب رابطًا جديدًا من المالك.',
            'This authorization expired, was already used, or was revoked. Ask the owner for a new link.');
        }
        return;
      }
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Invalid response');
      const result = await response.json();
      if (result.accepted !== true || result.redirect !== '/ops') throw new Error('Invalid response');
      status('تم التحقق من الإذن. انتقل الآن إلى تسجيل الدخول بحسابك.',
        'Authorization verified. Opening your account sign-in.');
      // The server cannot turn this entry page into an arbitrary redirect.
      location.replace('/ops');
    } catch {
      status('تعذر تأكيد الإذن الآن. أعد فتح الرابط، أو اطلب رابطًا جديدًا من المالك إذا استُخدم.',
        'Authorization could not be confirmed. Reopen the link, or ask the owner for a new link if it was used.');
    } finally {
      token = '';
    }
  }

  void redeem();
})();

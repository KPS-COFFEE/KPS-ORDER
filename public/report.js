document.addEventListener('click', async (event) => {
  if (event.target.closest('[data-print-report]')) {
    // Printing waits for the actual Arabic font, so the preview and paper match.
    await document.fonts.ready;
    window.print();
  }
});
document.querySelectorAll('#orders tbody tr').forEach((row) => {
  const reference = row.firstElementChild?.textContent;
  const target = row.dataset.drillTarget || (reference && 'order-' + reference);
  const detail = target && document.getElementById(target);
  if (!detail) return;
  const link = document.createElement('a');
  link.href = '#' + target;
  link.textContent = reference;
  link.addEventListener('click', () => { detail.open = true; });
  row.firstElementChild.replaceChildren(link);
});

// A previously opened online report must lose access along with the work session.
// Offline/file document rendering has no authenticated browser session to poll.
if (/^https?:$/.test(location.protocol)) {
  (() => {
    let busy = false, revoked = false, actor = null;
    const style = document.createElement('style');
    style.textContent = 'html[data-report-access-pending] body{visibility:hidden!important;pointer-events:none!important}';
    document.head.append(style);
    const mask = () => document.documentElement.setAttribute('data-report-access-pending', '');
    const clear = () => document.documentElement.removeAttribute('data-report-access-pending');
    function revoke() {
      revoked = true;
      const message = document.createElement('p');
      message.textContent = 'انتهى تصريح الدخول. سجّل الدخول بحساب مخوّل.';
      const link = document.createElement('a');
      link.href = '/ops'; link.textContent = 'الدخول';
      document.body.replaceChildren(message, link);
      clear();
    }
    async function check() {
      if (busy || revoked || document.hidden) return;
      busy = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch('/api/session', {credentials:'same-origin', cache:'no-store',
          headers:{'Accept':'application/json','X-KPS-Context':'work'}, signal:controller.signal});
        if (response.status === 401 || response.status === 403) { revoke(); return; }
        if (!response.ok) throw Error('Access check unavailable');
        const session = await response.json();
        if (!session.user || session.user.role === 'customer' || session.reauthentication_required || (actor && actor !== session.user.id)) revoke();
        else { actor = session.user.id; clear(); }
      } catch { mask(); }
      finally { clearTimeout(timeout); busy = false; }
    }
    const resume = () => { if (!revoked) { mask(); void check(); } };
    document.addEventListener('visibilitychange', resume);
    for (const event of ['pageshow','focus','online','offline']) window.addEventListener(event, resume);
    setInterval(check, 2000);
    resume();
  })();
}

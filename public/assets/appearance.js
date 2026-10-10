'use strict';
// Presentation only. Preview is restricted to the existing test service.
(() => {
  const root = document.documentElement;
  const previewHosts = new Set(['kps-order-emergency-v4.onrender.com', 'localhost', '127.0.0.1', 'testserver']);
  const previewAllowed = previewHosts.has(location.hostname);
  const params = new URLSearchParams(location.search);
  const valid = value => ['CHARCOAL', 'ORIGINAL_Light'].includes(value);
  let brand = params.get('brand_id') || 'kps';
  let preference = 'ORIGINAL_Light';
  let preview = previewAllowed && valid(params.get('theme')) ? params.get('theme') : null;
  const cacheKey = () => `kps-appearance-v1:${brand}`;
  function paint() {
    const profile = previewAllowed ? (preview || preference) : 'ORIGINAL_Light';
    root.dataset.kpsTheme = profile;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', profile === 'CHARCOAL' ? '#262626' : '#fbf9f5');
  }
  try { const cached = localStorage.getItem(cacheKey()); if (valid(cached)) preference = cached; } catch {}
  paint();
  window.KPSAppearance = Object.freeze({
    apply(theme = {}, brandId = brand) {
      if (brandId !== brand) { brand = brandId; preference = 'ORIGINAL_Light'; }
      preference = valid(theme.profile) ? theme.profile : 'ORIGINAL_Light';
      try { localStorage.setItem(cacheKey(), preference); } catch {}
      paint();
    },
    preview(profile) { if (!valid(profile) || !previewAllowed) return; preview = profile; paint(); },
    saved(theme, brandId) { preview = null; this.apply(theme, brandId); },
    get profile() { return root.dataset.kpsTheme; }
  });
  window.addEventListener('storage', event => {
    if (event.key === cacheKey() && valid(event.newValue)) { preference = event.newValue; paint(); }
  });
})();

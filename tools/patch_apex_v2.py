from __future__ import annotations

import re
from pathlib import Path

PATH = Path("index.html")
text = PATH.read_text(encoding="utf-8")

if "KPS_APEX_V2_INTEGRATION" in text:
    print("KPS APEX V2 integration already present")
    raise SystemExit(0)

required = [
    "<script id=\"kpsOrderScript\">",
    "const DELIVERY_WHATSAPP",
    "const addProduct = (article, button) => {",
    "const sendOrder = () => {",
    "$$('.add-to-cart-btn').forEach((button) => {",
    "id=\"kpsCustomerNotes\"",
]
for token in required:
    if token not in text:
        raise SystemExit(f"Required marker not found: {token}")

# Payment remains part of the same checkout; no redesign of the working panel.
payment = '''<div class="kps-field" id="kpsPaymentField">
<label for="kpsPaymentMethod">طريقة الدفع *</label>
<select id="kpsPaymentMethod">
<option value="cash">كاش</option>
<option value="network">شبكة / دفع إلكتروني</option>
</select>
<small class="kps-field-help">يتم تثبيت طريقة الدفع مع الطلب داخل KPS APEX.</small>
</div>
'''
notes_marker = '<div class="kps-field">\n<label for="kpsCustomerNotes">ملاحظات الطلب</label>'
text = text.replace(notes_marker, payment + notes_marker, 1)

# Keep the public endpoint configurable and secret-free. This is a public API URL, not a credential.
const_marker = "  const DELIVERY_WHATSAPP = '966550472662'; // غيّر الرقم هنا عند تخصيص رقم التوصيل.\n"
constants = const_marker + '''  // KPS_APEX_V2_INTEGRATION — public customer-order API only; never place secrets in this public repository.
  const APEX_API_BASE = (document.querySelector('meta[name="kps-apex-api"]')?.content || 'https://kps-apex-os-v507-test.onrender.com').replace(/\\/$/, '');
  const APEX_CATALOG_URL = `${APEX_API_BASE}/api/public/v2/catalog/delivery`;
  const APEX_ORDER_URL = `${APEX_API_BASE}/api/public/v2/orders`;
'''
text = text.replace(const_marker, constants, 1)

vars_marker = "  let activeInvoiceNumber = '';\n  let activeInvoiceGovernorate = '';\n"
vars_new = vars_marker + '''  let activeRequestKey = '';
  let apexCatalogReady = false;
  let apexMenuVersion = 0;
  let apexCatalogController = null;
'''
text = text.replace(vars_marker, vars_new, 1)

# Bind the new payment field inside the existing UI object.
els_marker = "    notes: $('#kpsCustomerNotes'),\n"
text = text.replace(els_marker, els_marker + "    payment: $('#kpsPaymentMethod'),\n", 1)

# Product actions remain the same visually, but the live APEX catalog is authoritative.
add_marker = "  const addProduct = (article, button) => {\n"
text = text.replace(add_marker, add_marker + "    if (!apexCatalogReady) { showToast('جاري تحديث المنيو من KPS APEX…'); return; }\n    activeRequestKey = '';\n", 1)
text = text.replace("  const updateQuantity = (id, delta) => {\n", "  const updateQuantity = (id, delta) => {\n    activeRequestKey = '';\n", 1)
text = text.replace("  const removeItem = (id) => {\n", "  const removeItem = (id) => {\n    activeRequestKey = '';\n", 1)

helper_marker = "  const normalizePhone = (value) => value.replace(/\\D/g, '');\n"
helpers = r'''  const normalizeCatalogText = (value) => String(value || '')
    .replace(/[\u064B-\u065F\u0670]/g, '')
    .replace(/[إأآ]/g, 'ا')
    .replace(/ة/g, 'ه')
    .replace(/ى/g, 'ي')
    .replace(/[^\u0600-\u06FFa-zA-Z0-9]+/g, '')
    .toLowerCase();

  const ensureApexStatus = () => {
    let node = document.getElementById('kpsApexStatus');
    if (node) return node;
    node = document.createElement('div');
    node.id = 'kpsApexStatus';
    node.setAttribute('role', 'status');
    node.style.cssText = 'max-width:760px;margin:12px auto 0;padding:10px 14px;border:1px solid rgba(242,101,50,.35);border-radius:14px;background:#151515;color:#ddd;font-size:13px;font-weight:800;text-align:center';
    const start = document.querySelector('.kps-start');
    if (start) start.appendChild(node);
    else document.body.prepend(node);
    return node;
  };

  const setApexStatus = (kind, message) => {
    const node = ensureApexStatus();
    node.textContent = message;
    node.style.borderColor = kind === 'ok' ? 'rgba(70,200,120,.5)' : kind === 'error' ? 'rgba(255,100,100,.55)' : 'rgba(242,101,50,.42)';
    node.style.color = kind === 'ok' ? '#baf3cf' : kind === 'error' ? '#ffb7b7' : '#ddd';
  };

  const absoluteApexUrl = (path) => {
    if (!path) return '';
    try { return new URL(path, `${APEX_API_BASE}/`).href; }
    catch (_) { return ''; }
  };

  const bindProductButton = (button) => {
    if (!button || button.dataset.apexBound === '1') return;
    button.dataset.apexBound = '1';
    button.addEventListener('click', () => addProduct(button.closest('article.product'), button));
  };

  const sectionForCategory = (category) => {
    const wanted = normalizeCatalogText(category);
    const sections = [...document.querySelectorAll('section')];
    const found = sections.find((section) => {
      const title = section.querySelector('.section-title h2');
      return title && normalizeCatalogText(title.textContent).includes(wanted);
    });
    if (found) return found;
    const section = document.createElement('section');
    const safeId = `apex-category-${wanted || 'menu'}`;
    section.id = safeId;
    section.innerHTML = `<div class="section-title"><div class="line"></div><h2></h2></div><div class="grid"></div>`;
    section.querySelector('h2').textContent = category || 'المنيو';
    const footer = document.querySelector('footer.footer');
    (footer?.parentNode || document.body).insertBefore(section, footer || null);
    const nav = document.querySelector('.top-sticky');
    if (nav) {
      const link = document.createElement('a');
      link.href = `#${safeId}`;
      link.textContent = category || 'المنيو';
      nav.appendChild(link);
    }
    return section;
  };

  const makeProductArticle = (item) => {
    const article = document.createElement('article');
    article.className = 'card product order-product';
    const imageUrl = absoluteApexUrl(item.image_url);
    if (imageUrl) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = item.name || 'KPS COFFEE';
      img.src = imageUrl;
      article.appendChild(img);
    } else {
      const placeholder = document.createElement('div');
      placeholder.textContent = 'KPS COFFEE';
      placeholder.style.cssText = 'min-height:180px;display:grid;place-items:center;background:radial-gradient(circle,#2b130b,#111 68%);color:#f26532;font-weight:900;font-size:24px';
      article.appendChild(placeholder);
    }
    const info = document.createElement('div');
    info.className = 'info';
    const h3 = document.createElement('h3');
    const price = document.createElement('span');
    const actions = document.createElement('div');
    actions.className = 'product-order-actions';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'add-to-cart-btn';
    button.textContent = 'إضافة للسلة';
    actions.appendChild(button);
    info.append(h3, price, actions);
    article.appendChild(info);
    return article;
  };

  const paintCatalogItem = (article, item) => {
    const current = Number(item.price_halalas || 0) / 100;
    const base = Number(item.base_price_halalas ?? item.price_halalas ?? 0) / 100;
    article.hidden = false;
    article.dataset.productId = String(item.id);
    article.dataset.productName = item.name || '';
    article.dataset.productPrice = current.toFixed(2);
    article.dataset.apexProductCode = item.code || '';
    const title = article.querySelector('h3');
    if (title) title.textContent = item.name || '';
    const image = article.querySelector('img');
    const remoteImage = absoluteApexUrl(item.image_url);
    if (image && remoteImage) { image.src = remoteImage; image.alt = item.name || 'KPS COFFEE'; }
    const price = article.querySelector('.info > span') || article.querySelector('.info span');
    if (price) {
      price.textContent = '';
      if (Number(item.discount_halalas || 0) > 0 && base > current) {
        const old = document.createElement('s');
        old.textContent = money(base);
        old.style.cssText = 'color:#888;font-size:.74em;margin-inline-end:8px';
        const live = document.createElement('b');
        live.textContent = money(current);
        const badge = document.createElement('small');
        badge.textContent = item.offer?.label || 'عرض';
        badge.style.cssText = 'display:block;margin-top:5px;color:#fff;background:#f26532;border-radius:999px;padding:3px 8px;width:max-content;font-size:11px';
        price.append(old, live, badge);
      } else {
        price.textContent = money(current);
      }
    }
    bindProductButton(article.querySelector('.add-to-cart-btn'));
  };

  const syncCatalogFromApex = async () => {
    apexCatalogReady = false;
    setApexStatus('loading', 'جاري تحديث المنيو والأسعار والعروض من KPS APEX…');
    $$('.add-to-cart-btn').forEach((button) => { button.disabled = true; });
    if (apexCatalogController) apexCatalogController.abort();
    apexCatalogController = new AbortController();
    const timeout = setTimeout(() => apexCatalogController.abort(), 12000);
    try {
      const response = await fetch(APEX_CATALOG_URL, { headers: { 'Accept': 'application/json' }, cache: 'no-store', signal: apexCatalogController.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload?.ok || !Array.isArray(payload.items)) throw new Error('Invalid catalog payload');
      apexMenuVersion = Number(payload.menu_version || 0);
      const existing = [...document.querySelectorAll('article.order-product')];
      const byName = new Map(existing.map((article) => [normalizeCatalogText(article.dataset.productName || article.querySelector('h3')?.textContent), article]));
      const byCode = new Map(existing.filter((article) => article.dataset.apexProductCode).map((article) => [String(article.dataset.apexProductCode), article]));
      const used = new Set();
      for (const item of payload.items) {
        let article = byCode.get(String(item.code || '')) || byName.get(normalizeCatalogText(item.name));
        if (!article) {
          article = makeProductArticle(item);
          const section = sectionForCategory(item.category || 'المنيو');
          let grid = section.querySelector('.grid');
          if (!grid) { grid = document.createElement('div'); grid.className = 'grid'; section.appendChild(grid); }
          grid.appendChild(article);
        }
        paintCatalogItem(article, item);
        used.add(article);
      }
      existing.forEach((article) => { if (!used.has(article)) article.hidden = true; });
      apexCatalogReady = true;
      $$('.add-to-cart-btn').forEach((button) => { if (!button.closest('article')?.hidden) button.disabled = false; });
      document.querySelectorAll('.kps-price-note').forEach((node) => { node.textContent = `الأسعار والعروض محدثة مباشرة من KPS APEX${apexMenuVersion ? ` — Menu V${apexMenuVersion}` : ''}. يتم تثبيت الطلب في النظام أولًا ثم فتح واتساب.`; });
      setApexStatus('ok', `متصل بـ KPS APEX ✓${apexMenuVersion ? ` · Menu V${apexMenuVersion}` : ''}${payload.delivery_open === false ? ' · التوصيل مغلق حاليًا' : ''}`);
    } catch (error) {
      console.error('KPS APEX catalog sync failed', error);
      apexCatalogReady = false;
      $$('.add-to-cart-btn').forEach((button) => { button.disabled = true; button.title = 'تعذر تحديث المنيو من KPS APEX'; });
      setApexStatus('error', 'تعذر تحديث المنيو من KPS APEX. الطلب متوقف مؤقتًا لحماية الأسعار والطلبات.');
    } finally {
      clearTimeout(timeout);
    }
  };

  const newRequestKey = () => {
    if (window.crypto?.randomUUID) return `KPS-${crypto.randomUUID()}`;
    return `KPS-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  };

'''
text = text.replace(helper_marker, helpers + helper_marker, 1)

# Replace only the old WhatsApp-only finalization. WhatsApp remains, but only after APEX has durably accepted the order.
send_pattern = re.compile(
    r"  const sendOrder = \(\) => \{\n.*?\n  \};\n\n  \$\$\('\.add-to-cart-btn'\)\.forEach\(\(button\) => \{\n    button\.addEventListener\('click', \(\) => addProduct\(button\.closest\('article\.product'\), button\)\);\n  \}\);",
    re.S,
)
send_replacement = r'''  const sendOrder = async () => {
    const error = validateCheckout();
    if (error) { showToast(error); return; }
    if (!apexCatalogReady) { showToast('تعذر تثبيت الطلب: المنيو غير متصل بـ KPS APEX'); return; }
    if (!cart.size) { showToast('السلة فارغة'); return; }
    savePhone();
    if (!activeRequestKey) activeRequestKey = newRequestKey();
    const originalText = els.send.textContent;
    els.send.disabled = true;
    els.send.textContent = 'جاري تثبيت الطلب في KPS APEX…';
    const payload = {
      request_key: activeRequestKey,
      customer_name: els.name.value.trim(),
      mobile: els.phone.value.trim(),
      city: els.governorate.value.trim(),
      district: els.district.value.trim(),
      location_url: els.location.value.trim(),
      payment_method: els.payment?.value || 'cash',
      items: [...cart.values()].map((item) => ({ product_id: Number(item.id), qty: Number(item.qty) }))
    };
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 18000);
      let response;
      try {
        response = await fetch(APEX_ORDER_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
      } finally { clearTimeout(timeout); }
      let result = {};
      try { result = await response.json(); } catch (_) {}
      if (!response.ok || !result?.ok) {
        const detail = result?.detail || result?.message || `HTTP ${response.status}`;
        throw new Error(detail);
      }
      activeInvoiceNumber = result.invoice_no;
      activeInvoiceGovernorate = els.governorate.value.trim();
      apexMenuVersion = Number(result.menu_version || apexMenuVersion || 0);
      const { quantity } = getTotals();
      const serverSubtotal = Number(result.total_halalas || 0) / 100;
      const lines = [...cart.values()].map((item, index) => `${index + 1}) ${item.name} × ${item.qty} = ${money(item.price * item.qty)}`);
      const paymentLabel = els.payment?.value === 'network' ? 'شبكة / دفع إلكتروني' : 'كاش';
      const message = [
        '🟠 *طلب توصيل جديد - KPS COFFEE*',
        `رقم الطلب في KPS APEX: *${result.invoice_no}*`,
        apexMenuVersion ? `نسخة المنيو: Menu V${apexMenuVersion}` : '',
        '',
        `الاسم: ${els.name.value.trim()}`,
        `الجوال: ${els.phone.value.trim()}`,
        `المحافظة: ${els.governorate.value.trim()}`,
        `الحي: ${els.district.value.trim()}`,
        `الموقع: ${els.location.value.trim() || 'لم يُرفق'}`,
        `طريقة الدفع: ${paymentLabel}`,
        '',
        '*تفاصيل الطلب:*',
        ...lines,
        '',
        `عدد القطع: ${quantity}`,
        `*الإجمالي المعتمد من النظام: ${money(serverSubtotal)}*`,
        '',
        `ملاحظات العميل: ${els.notes.value.trim() || 'لا يوجد'}`,
        '',
        '✅ تم تثبيت الطلب في KPS APEX قبل إرسال واتساب'
      ].filter(Boolean).join('\n');
      const url = `https://wa.me/${DELIVERY_WHATSAPP}?text=${encodeURIComponent(message)}`;
      window.open(url, '_blank', 'noopener,noreferrer');
      showToast(`تم تثبيت الطلب ${result.invoice_no} وفتح واتساب`);
    } catch (error) {
      console.error('KPS APEX order creation failed', error);
      const message = error?.name === 'AbortError' ? 'انتهت مهلة الاتصال. اضغط إرسال مرة أخرى؛ لن يتكرر الطلب.' : `تعذر تثبيت الطلب: ${error?.message || 'خطأ اتصال'}`;
      showToast(message);
      setApexStatus('error', `${message} لم يتم فتح واتساب حتى لا يضيع الطلب.`);
    } finally {
      els.send.disabled = false;
      els.send.textContent = originalText;
    }
  };

  $$('.add-to-cart-btn').forEach(bindProductButton);
  syncCatalogFromApex();'''
text, count = send_pattern.subn(send_replacement, text, count=1)
if count != 1:
    raise SystemExit(f"sendOrder patch count={count}, expected 1")

# The server invoice is authoritative; stop generating a customer-only invoice when using APEX.
text = text.replace("    if (!activeInvoiceNumber || activeInvoiceGovernorate !== governorate) {\n      activeInvoiceNumber = buildInvoiceNumber(governorate);\n      activeInvoiceGovernorate = governorate;\n    }\n", "", 1)

# Any meaningful edit after a failed attempt gets a new idempotency key; an unchanged retry keeps its key.
listener_marker = "  els.phone.addEventListener('change', savePhone);\n"
text = text.replace(listener_marker, listener_marker + "  [els.name, els.phone, els.district, els.location, els.notes, els.payment].filter(Boolean).forEach((node) => node.addEventListener('change', () => { activeRequestKey = ''; }));\n", 1)
text = text.replace("  els.governorate.addEventListener('change', () => {\n    activeInvoiceNumber = '';\n", "  els.governorate.addEventListener('change', () => {\n    activeRequestKey = '';\n    activeInvoiceNumber = '';\n", 1)

PATH.write_text(text, encoding="utf-8")
print("KPS ORDER → APEX V2 patch applied")

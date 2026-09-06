from __future__ import annotations

import re
from pathlib import Path

PATH = Path("index.html")
text = PATH.read_text(encoding="utf-8")
MARKER = "KPS_APEX_V2_INTEGRATION"

if MARKER in text:
    print("KPS APEX V2 integration already present")
    raise SystemExit(0)

required = [
    '<script id="kpsOrderScript">',
    'const DELIVERY_WHATSAPP',
    'const addProduct = (article, button) => {',
    "$$('.add-to-cart-btn').forEach((button) => {",
    'els.paymentMethod',
    'openAvailabilityNotice',
]
missing = [x for x in required if x not in text]
if missing:
    raise SystemExit(f"Required marker(s) not found: {missing}")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"Anchor not found: {label}")
    text = text.replace(old, new, 1)


# Public integration endpoints only. No secret/token is stored in this public repository.
delivery = re.search(r"(?m)^(\s*const DELIVERY_WHATSAPP\s*=\s*'[^']+';[^\n]*\n)", text)
if not delivery:
    raise SystemExit("DELIVERY_WHATSAPP constant not found")
integration_constants = delivery.group(1) + r'''  // KPS_APEX_V2_INTEGRATION — APEX is the commercial source of truth.
  const APEX_API_BASE = (document.querySelector('meta[name="kps-apex-api"]')?.content || 'https://kps-apex-os-v507-test.onrender.com').replace(/\/$/, '');
  const APEX_CATALOG_URL = `${APEX_API_BASE}/api/public/v2/catalog/delivery`;
  const APEX_ORDER_URL = `${APEX_API_BASE}/api/public/v2/orders`;
  let apexCatalogReady = false;
  let apexMenuVersion = 0;
  let apexCatalogController = null;
  let activeRequestKey = '';
'''
text = text[:delivery.start()] + integration_constants + text[delivery.end():]

# A cart change means a new logical order attempt. An unchanged retry keeps the same key.
replace_once(
    '  const addProduct = (article, button) => {\n',
    "  const addProduct = (article, button) => {\n    if (!apexCatalogReady) { showToast('جاري تحديث المنيو من KPS APEX…'); return; }\n    activeRequestKey = '';\n",
    'addProduct',
)
replace_once(
    '  const updateQuantity = (id, delta) => {\n',
    "  const updateQuantity = (id, delta) => {\n    activeRequestKey = '';\n",
    'updateQuantity',
)
replace_once(
    '  const removeItem = (id) => {\n',
    "  const removeItem = (id) => {\n    activeRequestKey = '';\n",
    'removeItem',
)

helper_anchor = "  const normalizePhone = (value) => value.replace(/\\D/g, '');\n"
if helper_anchor not in text:
    raise SystemExit('normalizePhone anchor not found')

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
    node.style.cssText = 'max-width:760px;margin:12px auto 0;padding:10px 14px;border:1px solid rgba(242,101,50,.35);border-radius:14px;background:#151515;color:#ddd;font-size:13px;font-weight:800;text-align:center;box-shadow:0 8px 24px rgba(0,0,0,.16)';
    const host = document.querySelector('.kps-start') || document.querySelector('.kps-order-entry') || document.body;
    host.appendChild(node);
    return node;
  };

  const setApexStatus = (kind, message) => {
    const node = ensureApexStatus();
    node.textContent = message;
    node.dataset.kind = kind;
    node.style.borderColor = kind === 'ok' ? 'rgba(70,200,120,.5)' : kind === 'error' ? 'rgba(255,100,100,.55)' : 'rgba(242,101,50,.42)';
    node.style.color = kind === 'ok' ? '#baf3cf' : kind === 'error' ? '#ffb7b7' : '#ddd';
  };

  const absoluteApexUrl = (path) => {
    if (!path) return '';
    try { return new URL(path, `${APEX_API_BASE}/`).href; }
    catch (_) { return ''; }
  };

  const sectionForCategory = (category) => {
    const wanted = normalizeCatalogText(category);
    const sections = [...document.querySelectorAll('section')];
    const existing = sections.find((section) => {
      const title = section.querySelector('.section-title h2');
      return title && normalizeCatalogText(title.textContent).includes(wanted);
    });
    if (existing) return existing;

    const section = document.createElement('section');
    const safeId = `apex-category-${wanted || 'menu'}`;
    section.id = safeId;
    section.innerHTML = '<div class="section-title"><div class="line"></div><h2></h2></div><div class="grid"></div>';
    section.querySelector('h2').textContent = category || 'المنيو';
    const contact = document.getElementById('contact');
    (contact?.parentNode || document.body).insertBefore(section, contact || null);

    const nav = document.querySelector('.top-sticky');
    if (nav && !nav.querySelector(`a[href="#${safeId}"]`)) {
      const link = document.createElement('a');
      link.href = `#${safeId}`;
      link.textContent = category || 'المنيو';
      nav.appendChild(link);
    }
    return section;
  };

  const makeApexProductArticle = (item) => {
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
      placeholder.className = 'kps-apex-image-placeholder';
      placeholder.textContent = 'KPS COFFEE';
      placeholder.style.cssText = 'min-height:180px;display:grid;place-items:center;background:radial-gradient(circle,#2b130b,#111 68%);color:#f26532;font-weight:900;font-size:24px';
      article.appendChild(placeholder);
    }

    const info = document.createElement('div');
    info.className = 'info';
    const title = document.createElement('h3');
    const price = document.createElement('span');
    const actions = document.createElement('div');
    actions.className = 'product-order-actions';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'add-to-cart-btn';
    button.textContent = 'إضافة للسلة';
    button.dataset.apexDynamic = '1';
    button.addEventListener('click', () => addProduct(article, button));
    actions.appendChild(button);
    info.append(title, price, actions);
    article.appendChild(info);
    return article;
  };

  const paintApexCatalogItem = (article, item) => {
    const current = Number(item.price_halalas || 0) / 100;
    const base = Number(item.base_price_halalas ?? item.price_halalas ?? 0) / 100;
    article.hidden = false;
    article.dataset.productId = String(item.id);
    article.dataset.productName = item.name || '';
    article.dataset.productPrice = current.toFixed(2);
    article.dataset.apexProductCode = item.code || '';
    article.dataset.apexManaged = '1';

    const title = article.querySelector('h3');
    if (title) title.textContent = item.name || '';

    const remoteImage = absoluteApexUrl(item.image_url);
    if (remoteImage) {
      let image = article.querySelector('img');
      if (!image) {
        article.querySelector('.kps-apex-image-placeholder')?.remove();
        image = document.createElement('img');
        image.loading = 'lazy';
        article.prepend(image);
      }
      image.src = remoteImage;
      image.alt = item.name || 'KPS COFFEE';
    }

    const priceNode = article.querySelector('.info > span') || article.querySelector('.info span');
    if (priceNode) {
      priceNode.textContent = '';
      if (Number(item.discount_halalas || 0) > 0 && base > current) {
        const oldPrice = document.createElement('s');
        oldPrice.textContent = money(base);
        oldPrice.style.cssText = 'color:#888;font-size:.74em;margin-inline-end:8px';
        const livePrice = document.createElement('b');
        livePrice.textContent = money(current);
        const badge = document.createElement('small');
        badge.textContent = item.offer?.label || 'عرض';
        badge.style.cssText = 'display:block;margin-top:5px;color:#fff;background:#f26532;border-radius:999px;padding:3px 8px;width:max-content;font-size:11px;font-weight:900';
        priceNode.append(oldPrice, livePrice, badge);
      } else {
        priceNode.textContent = money(current);
      }
    }
  };

  const syncCatalogFromApex = async () => {
    apexCatalogReady = false;
    setApexStatus('loading', 'جاري تحديث المنيو والأسعار والعروض من KPS APEX…');
    $$('.add-to-cart-btn').forEach((button) => { button.disabled = true; });

    if (apexCatalogController) apexCatalogController.abort();
    apexCatalogController = new AbortController();
    const timeout = setTimeout(() => apexCatalogController.abort(), 12000);

    try {
      const response = await fetch(APEX_CATALOG_URL, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: apexCatalogController.signal
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload?.ok || !Array.isArray(payload.items)) throw new Error('Invalid catalog payload');

      apexMenuVersion = Number(payload.menu_version || 0);
      const existing = [...document.querySelectorAll('article.product')];
      const byName = new Map(existing.map((article) => [
        normalizeCatalogText(article.dataset.productName || article.querySelector('h3')?.textContent), article
      ]));
      const byCode = new Map(existing.filter((article) => article.dataset.apexProductCode).map((article) => [
        String(article.dataset.apexProductCode), article
      ]));
      const used = new Set();

      for (const item of payload.items) {
        let article = byCode.get(String(item.code || '')) || byName.get(normalizeCatalogText(item.name));
        if (!article) {
          article = makeApexProductArticle(item);
          const section = sectionForCategory(item.category || 'المنيو');
          let grid = section.querySelector('.grid');
          if (!grid) {
            grid = document.createElement('div');
            grid.className = 'grid';
            section.appendChild(grid);
          }
          grid.appendChild(article);
        }
        paintApexCatalogItem(article, item);
        used.add(article);
      }

      existing.forEach((article) => {
        if (!used.has(article) && article.classList.contains('order-product')) article.hidden = true;
      });

      apexCatalogReady = true;
      if (typeof updateProductAvailability === 'function') updateProductAvailability();
      $$('.add-to-cart-btn').forEach((button) => {
        const article = button.closest('article.product');
        if (!article?.hidden && payload.delivery_open !== false) button.disabled = false;
      });

      document.querySelectorAll('.kps-price-note').forEach((node) => {
        node.textContent = `الأسعار والعروض محدثة مباشرة من KPS APEX${apexMenuVersion ? ` — Menu V${apexMenuVersion}` : ''}. يتم تثبيت الطلب في النظام أولًا ثم فتح واتساب.`;
      });
      setApexStatus(
        payload.delivery_open === false ? 'error' : 'ok',
        payload.delivery_open === false
          ? `المنيو محدث من KPS APEX${apexMenuVersion ? ` · Menu V${apexMenuVersion}` : ''} · التوصيل مغلق حاليًا`
          : `متصل بـ KPS APEX ✓${apexMenuVersion ? ` · Menu V${apexMenuVersion}` : ''}`
      );
    } catch (error) {
      console.error('KPS APEX catalog sync failed', error);
      apexCatalogReady = false;
      $$('.add-to-cart-btn').forEach((button) => {
        button.disabled = true;
        button.title = 'تعذر تحديث المنيو من KPS APEX';
      });
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
text = text.replace(helper_anchor, helpers + helper_anchor, 1)

# Replace only the finalization function. Keep the existing availability confirmation and WhatsApp behavior.
send_match = re.search(r"(?m)^\s*const\s+sendOrder\s*=\s*\(\{\s*skipAvailabilityNotice\s*=\s*false\s*\}\s*=\s*\{\}\)\s*=>\s*\{", text)
if not send_match:
    raise SystemExit('current sendOrder signature not found')
listener_anchor = "  $$('.add-to-cart-btn').forEach((button) => {"
listener_pos = text.find(listener_anchor, send_match.start())
if listener_pos < 0:
    raise SystemExit('product listener anchor not found after sendOrder')

send_replacement = r'''  const sendOrder = async ({ skipAvailabilityNotice = false } = {}) => {
    if (!skipAvailabilityNotice && openAvailabilityNotice()) return;

    const error = validateCheckout();
    if (error) { showToast(error); return; }
    if (!apexCatalogReady) {
      showToast('تعذر تثبيت الطلب: المنيو غير متصل بـ KPS APEX');
      return;
    }
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
      payment_method: els.paymentMethod.value.trim() || 'cash',
      items: [...cart.values()].map((item) => ({
        product_id: Number(item.id),
        qty: Number(item.qty)
      }))
    };

    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 18000);
      let response;
      try {
        response = await fetch(APEX_ORDER_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
      } finally {
        clearTimeout(timeout);
      }

      let result = {};
      try { result = await response.json(); } catch (_) {}
      if (!response.ok || !result?.ok) {
        throw new Error(result?.detail || result?.message || `HTTP ${response.status}`);
      }

      activeInvoiceNumber = result.invoice_no;
      activeInvoiceGovernorate = els.governorate.value.trim();
      apexMenuVersion = Number(result.menu_version || apexMenuVersion || 0);

      const { quantity } = getTotals();
      const serverTotal = Number(result.total_halalas || 0) / 100;
      const orderDateInfo = getOrderDateInfo();
      const RTL_ISOLATE = '\u2067';
      const LTR_ISOLATE = '\u2066';
      const DIRECTION_END = '\u2069';
      const ALM = '\u061C';
      const toArabicDigits = (value) => String(value).replace(/\d/g, (digit) => '٠١٢٣٤٥٦٧٨٩'[Number(digit)]);
      const rtlLine = (value) => `${ALM}${value}`;
      const formatMessageMoney = (value) => `${Number(value).toFixed(2)}\u00A0ر.س`;
      const formatProductName = (name) => {
        if (name === 'V60 حار') return 'في 60 حار';
        if (name === 'V60 بارد') return 'في 60 بارد';
        return name;
      };
      const lines = [...cart.values()].flatMap((item, index) => [
        rtlLine(`${index + 1}) ${formatProductName(item.name)} × ${item.qty} = ${formatMessageMoney(item.price * item.qty)}`),
        rtlLine('────────────')
      ]);

      const message = [
        rtlLine('🟠 *طلب توصيل جديد | KPS COFFEE*'),
        rtlLine('━━━━━━━━━━━━━━'),
        rtlLine('📌 *بيانات الطلب*'),
        rtlLine(`• رقم الطلب: *${result.invoice_no}*`),
        apexMenuVersion ? rtlLine(`• نسخة المنيو: Menu V${apexMenuVersion}`) : '',
        rtlLine(`• اليوم: ${orderDateInfo.dayName}`),
        rtlLine(`• التاريخ: ${toArabicDigits(orderDateInfo.gregorianDate)}`),
        '',
        rtlLine('👤 *بيانات العميل*'),
        rtlLine(`• الاسم: ${els.name.value.trim()}`),
        rtlLine(`• الجوال: ${els.phone.value.trim()}`),
        rtlLine(`• المحافظة: ${els.governorate.value.trim()}`),
        rtlLine(`• الحي: ${els.district.value.trim()}`),
        rtlLine(`• طريقة الدفع: *${els.paymentMethod.value.trim()}*`),
        '',
        rtlLine('🛒 *تفاصيل الطلب*'),
        ...lines,
        rtlLine('💰 *ملخص الطلب*'),
        rtlLine(`• عدد القطع: *${quantity}*`),
        rtlLine(`• الإجمالي المعتمد من KPS APEX: *${formatMessageMoney(serverTotal)}*`),
        '',
        rtlLine('📝 *الملاحظات*'),
        rtlLine(`${els.notes.value.trim() || 'لا يوجد'}`),
        '',
        rtlLine('📍 *موقع التوصيل*'),
        `${els.location.value.trim() || 'لم يُرفق'}`,
        '',
        rtlLine('✅ تم تثبيت الطلب في KPS APEX قبل فتح واتساب')
      ].filter(Boolean).join('\n');

      const url = `https://wa.me/${DELIVERY_WHATSAPP}?text=${encodeURIComponent(message)}`;
      const isMobileDevice = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
      if (isMobileDevice) {
        window.location.href = url;
      } else {
        const whatsappWindow = window.open(url, '_blank', 'noopener,noreferrer');
        if (!whatsappWindow) window.location.href = url;
      }
      showToast(`تم تثبيت الطلب ${result.invoice_no} وفتح واتساب`);
      setApexStatus('ok', `تم تثبيت الطلب ${result.invoice_no} في KPS APEX ✓`);
    } catch (error) {
      console.error('KPS APEX order creation failed', error);
      const message = error?.name === 'AbortError'
        ? 'انتهت مهلة الاتصال. اضغط إرسال مرة أخرى؛ لن يتكرر الطلب.'
        : `تعذر تثبيت الطلب: ${error?.message || 'خطأ اتصال'}`;
      showToast(message);
      setApexStatus('error', `${message} لم يتم فتح واتساب حتى لا يضيع الطلب.`);
    } finally {
      els.send.disabled = false;
      els.send.textContent = originalText;
    }
  };

'''
text = text[:send_match.start()] + send_replacement + text[listener_pos:]

# Start authoritative catalog sync only after original static buttons receive their existing listeners.
fab_anchor = "  els.fab.addEventListener('click', () => { showCartStep(); openPanel(); });\n"
replace_once(fab_anchor, "  syncCatalogFromApex();\n\n" + fab_anchor, 'catalog sync start')

# Meaningful edits create a new logical idempotency key. Unchanged retry preserves the prior key.
phone_anchor = "  els.phone.addEventListener('change', savePhone);\n"
replace_once(
    phone_anchor,
    phone_anchor + "  [els.name, els.phone, els.district, els.location, els.notes, els.paymentMethod].filter(Boolean).forEach((node) => node.addEventListener('change', () => { activeRequestKey = ''; }));\n",
    'checkout idempotency listeners',
)
replace_once(
    "  els.governorate.addEventListener('change', () => {\n    activeInvoiceNumber = '';\n",
    "  els.governorate.addEventListener('change', () => {\n    activeRequestKey = '';\n    activeInvoiceNumber = '';\n",
    'governorate idempotency listener',
)

PATH.write_text(text, encoding="utf-8")
print("KPS ORDER → KPS APEX V2 integration patch applied")

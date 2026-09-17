from __future__ import annotations

import re
from pathlib import Path

PATH = Path("index.html")
text = PATH.read_text(encoding="utf-8")
MARKER = "KPS_APEX_PRODUCT_OPTIONS_V1038"

if MARKER in text:
    print("KPS APEX product options V10.38 already present")
    raise SystemExit(0)
if "KPS_APEX_V2_INTEGRATION" not in text:
    raise SystemExit("Base APEX V2 integration is missing")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"Anchor not found: {label}")
    text = text.replace(old, new, 1)


replace_once(
    "  let activeRequestKey = '';\n",
    "  let activeRequestKey = '';\n  const KPS_APEX_PRODUCT_OPTIONS_V1038 = true;\n",
    "version marker",
)

add_start = text.find("  const addProduct = (article, button) => {")
add_end = text.find("\n\n  const normalizeCatalogText", add_start)
if add_start < 0 or add_end < 0:
    raise SystemExit("addProduct block not found")

replacement = r'''  const productOptions = (article) => {
    try { return JSON.parse(article.dataset.optionGroups || '[]'); }
    catch (_) { return []; }
  };

  const configureProduct = (article) => new Promise((resolve) => {
    const groups = productOptions(article);
    if (!groups.length) { resolve([{ options: {}, optionLabel: '', configurationKey: '' }]); return; }

    const overlay = document.createElement('div');
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText = 'position:fixed;z-index:999999;inset:0;background:rgba(0,0,0,.78);display:grid;place-items:center;padding:16px';
    const box = document.createElement('section');
    box.style.cssText = 'width:min(560px,100%);max-height:90vh;overflow:auto;background:#fff8ef;color:#201a17;border:2px solid #f26532;border-radius:22px;padding:20px;box-shadow:0 22px 70px #0008;direction:rtl';
    const title = document.createElement('h2');
    title.textContent = `تخصيص ${article.dataset.productName}`;
    const hint = document.createElement('p');
    hint.textContent = 'اختر العدد ثم حدد النكهة لكل كوب قبل الإضافة.';
    const qtyLabel = document.createElement('label');
    qtyLabel.textContent = 'العدد';
    qtyLabel.style.cssText = 'display:grid;gap:6px;font-weight:900;margin:14px 0';
    const qty = document.createElement('input');
    qty.type = 'number'; qty.min = '1'; qty.max = '20'; qty.value = '1';
    qty.style.cssText = 'padding:12px;border:1px solid #c9b9a7;border-radius:10px;font-size:18px';
    qtyLabel.appendChild(qty);
    const units = document.createElement('div');
    const same = document.createElement('button');
    same.type = 'button'; same.textContent = 'تطبيق نكهة الكوب الأول على الكل';
    same.style.cssText = 'border:1px solid #f26532;background:#fff;color:#b5441d;border-radius:10px;padding:9px 11px;font-weight:900;margin:0 0 12px';
    const renderUnits = () => {
      const count = Math.max(1, Math.min(20, Number(qty.value) || 1));
      units.textContent = '';
      for (let index = 0; index < count; index += 1) {
        const unit = document.createElement('div');
        unit.style.cssText = 'border:1px solid #dbcdbf;border-radius:13px;padding:11px;margin:8px 0;background:#fff';
        const label = document.createElement('strong');
        label.textContent = `الكوب ${index + 1}`;
        unit.appendChild(label);
        groups.forEach((group) => {
          const field = document.createElement('label');
          field.style.cssText = 'display:grid;gap:5px;margin-top:8px';
          const caption = document.createElement('span'); caption.textContent = group.name;
          const select = document.createElement('select');
          select.dataset.groupCode = group.code;
          select.dataset.groupName = group.name;
          select.required = Boolean(group.required);
          select.style.cssText = 'padding:11px;border:1px solid #bfae9d;border-radius:10px;background:#fff;font-size:16px';
          const placeholder = document.createElement('option');
          placeholder.value = ''; placeholder.textContent = `اختر ${group.name}`;
          select.appendChild(placeholder);
          (group.values || []).forEach((value) => {
            const option = document.createElement('option');
            option.value = value.code; option.textContent = value.name;
            select.appendChild(option);
          });
          field.append(caption, select); unit.appendChild(field);
        });
        units.appendChild(unit);
      }
    };
    qty.addEventListener('change', renderUnits);
    same.addEventListener('click', () => {
      const first = units.firstElementChild;
      if (!first) return;
      [...first.querySelectorAll('select')].forEach((source) => {
        [...units.children].slice(1).forEach((unit) => {
          const target = [...unit.querySelectorAll('select')].find((node) => node.dataset.groupCode === source.dataset.groupCode);
          if (target) target.value = source.value;
        });
      });
    });
    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;gap:10px;margin-top:15px';
    const confirm = document.createElement('button');
    confirm.type = 'button'; confirm.textContent = 'تأكيد وإضافة للسلة';
    confirm.style.cssText = 'flex:1;border:0;background:#f26532;color:#fff;border-radius:12px;padding:12px;font-weight:900';
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.textContent = 'إلغاء';
    cancel.style.cssText = 'border:1px solid #777;background:#fff;border-radius:12px;padding:12px;font-weight:900';
    const finish = (value) => { overlay.remove(); resolve(value); };
    cancel.addEventListener('click', () => finish(null));
    overlay.addEventListener('click', (event) => { if (event.target === overlay) finish(null); });
    confirm.addEventListener('click', () => {
      const configured = [];
      for (const unit of units.children) {
        const options = {}; const labels = []; const keys = [];
        for (const select of unit.querySelectorAll('select')) {
          if (!select.value) { select.focus(); showToast(`اختر ${select.dataset.groupName} لكل كوب`); return; }
          const label = select.options[select.selectedIndex]?.textContent || select.value;
          options[select.dataset.groupCode] = select.value;
          labels.push(`${select.dataset.groupName}: ${label}`);
          keys.push(`${select.dataset.groupCode}:${select.value}`);
        }
        configured.push({ options, optionLabel: labels.join(' · '), configurationKey: keys.join('|') });
      }
      finish(configured);
    });
    actions.append(confirm, cancel);
    box.append(title, hint, qtyLabel, same, units, actions);
    overlay.appendChild(box); document.body.appendChild(overlay); renderUnits();
  });

  const addProduct = async (article, button) => {
    if (!apexCatalogReady) { showToast('جاري تحديث المنيو من KPS APEX…'); return; }
    activeRequestKey = '';
    activeInvoiceNumber = '';
    activeInvoiceGovernorate = '';
    const name = article.dataset.productName;
    const price = Number(article.dataset.productPrice);
    const productId = article.dataset.productId || buildCartItemKey(name, price);
    const availableGovernorates = getAvailableGovernorates(article);

    if (!isProductAvailable(article)) {
      showToast(`${name} متوفر في حفرالباطن فقط`);
      return;
    }
    const configurations = await configureProduct(article);
    if (!configurations) return;
    configurations.forEach((configuration) => {
      const id = configuration.configurationKey ? `${productId}::${configuration.configurationKey}` : String(productId);
      const existing = cart.get(id);
      if (existing) existing.qty += 1;
      else cart.set(id, { id, productId, name, price, qty: 1, availableGovernorates, options: configuration.options, optionLabel: configuration.optionLabel });
    });

    removedUnavailableItemsPending = removedUnavailableItemsPending.filter((itemName) => itemName !== name);
    button.classList.add('added');
    button.textContent = 'تمت الإضافة ✓';
    setTimeout(() => { button.classList.remove('added'); button.textContent = 'إضافة للسلة'; }, 900);
    renderCart();
    showToast(`تمت إضافة ${name}`);
  };'''

text = text[:add_start] + replacement + text[add_end:]

replace_once(
    "    article.dataset.apexManaged = '1';\n",
    "    article.dataset.apexManaged = '1';\n    article.dataset.optionGroups = JSON.stringify(item.option_groups || []);\n",
    "catalog option groups",
)

replace_once(
    "          <h3>${escapeHtml(item.name)}</h3>\n          <div class=\"kps-cart-line-price\">",
    "          <h3>${escapeHtml(item.name)}</h3>\n          ${item.optionLabel ? `<div style=\"color:#b84d25;font-weight:900;margin:4px 0\">${escapeHtml(item.optionLabel)}</div>` : ''}\n          <div class=\"kps-cart-line-price\">",
    "cart option label",
)
replace_once(
    "        <span>${escapeHtml(item.name)} × ${item.qty}</span>\n",
    "        <span>${escapeHtml(item.name)}${item.optionLabel ? ` — ${escapeHtml(item.optionLabel)}` : ''} × ${item.qty}</span>\n",
    "review option label",
)
replace_once(
    "        product_id: Number(item.id),\n        qty: Number(item.qty)\n",
    "        product_id: Number(item.productId || item.id),\n        qty: Number(item.qty),\n        options: item.options || {}\n",
    "configured order payload",
)
replace_once(
    "        rtlLine(`${index + 1}) ${formatProductName(item.name)} × ${item.qty} = ${formatMessageMoney(item.price * item.qty)}`),\n",
    "        rtlLine(`${index + 1}) ${formatProductName(item.name)}${item.optionLabel ? ` — ${item.optionLabel}` : ''} × ${item.qty} = ${formatMessageMoney(item.price * item.qty)}`),\n",
    "whatsapp option label",
)

old_refresh = r'''      [...cart.keys()].forEach((id) => {
        if (/^\\d+$/.test(String(id)) && !availableProductIds.has(String(id))) {
          cart.delete(id);
          cartChanged = true;
        }
      });'''
new_refresh = r'''      [...cart.entries()].forEach(([id, cartItem]) => {
        const productId = String(cartItem.productId || id).split('::')[0];
        if (/^\d+$/.test(productId) && !availableProductIds.has(productId)) {
          cart.delete(id);
          cartChanged = true;
        }
      });'''
replace_once(old_refresh, new_refresh, "configured cart availability")

PATH.write_text(text, encoding="utf-8")
print("KPS ORDER product options V10.38 patch applied")

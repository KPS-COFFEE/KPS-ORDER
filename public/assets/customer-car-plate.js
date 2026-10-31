'use strict';
// Flexible by default. A Saudi preview requires the customer's explicit choice;
// no country is inferred from digits or saved into historical order snapshots.
(() => {
  const input = document.getElementById('car-plate');
  if (!input) return;
  const label = input.closest('label');
  const formatLabel = document.createElement('label');
  formatLabel.className = 'field';
  const caption = document.createElement('span');
  const format = document.createElement('select');
  format.id = 'car-plate-format';
  formatLabel.append(caption, format);
  label.before(formatLabel);
  const hint = document.createElement('span');
  hint.id = 'car-plate-hint';
  hint.className = 'small muted';
  input.setAttribute('aria-describedby', hint.id);
  const preview = document.createElement('span');
  preview.id = 'car-plate-preview';
  preview.setAttribute('aria-live', 'polite');
  label.append(hint, preview);
  const normalize = value => value.replace(/[٠-٩۰-۹]/g, digit =>
    String(digit.charCodeAt(0) - (digit >= '۰' ? 0x6f0 : 0x660)));
  function update() {
    const en = document.documentElement.lang === 'en';
    const value = input.value;
    // Client hints mirror the server allowlist; neither format adds requirements.
    input.setCustomValidity(value.length > 30 || /[^A-Za-z0-9\u0621-\u063a\u0641-\u064a٠-٩۰-۹ /-]/.test(value)
      ? (en ? 'Use letters, digits, spaces, hyphens or slashes (up to 30 characters).' : 'استخدم حروفًا وأرقامًا ومسافات وشرطات فقط، بحد أقصى ٣٠ حرفًا.') : '');
    preview.innerHTML = format.value === 'saudi' && window.KPSSaudiPlate?.parse(value)
      ? window.KPSSaudiPlate.render(value, en ? 'en' : 'ar', true) : '';
  }
  function language() {
    const en = document.documentElement.lang === 'en', previous = format.value;
    caption.textContent = en ? 'Plate display' : 'عرض اللوحة';
    format.replaceChildren(new Option(en ? 'Flexible / Gulf and other formats' : 'مرن / خليجي وصيغ أخرى', 'flexible'),
      new Option(en ? 'Saudi plate preview' : 'معاينة لوحة سعودية', 'saudi'));
    format.value = previous || 'flexible';
    hint.textContent = en ? 'Optional. Leave empty or enter digits only, or letters and digits. No country is inferred.'
      : 'اختياري. يمكنك تركه فارغًا أو إدخال أرقام فقط أو حروف وأرقام. لا تُستنتج الدولة من الرقم.';
    update();
  }
  input.addEventListener('input', () => {
    const start = input.selectionStart, end = input.selectionEnd;
    input.value = normalize(input.value);
    input.setSelectionRange(start, end);
    update();
  });
  input.addEventListener('change', update);
  format.addEventListener('change', update);
  new MutationObserver(language).observe(document.documentElement, {attributes:true, attributeFilter:['lang']});
  language();
})();

'use strict';
// Presentation only: never rewrite the stored plate/key or a historical snapshot.
(() => {
  const pairs = Object.freeze({ا:'A',ب:'B',ح:'J',د:'D',ر:'R',س:'S',ص:'X',ط:'T',ع:'E',ق:'G',ك:'K',ل:'L',م:'Z',ن:'N',ه:'H',و:'U',ى:'V'});
  const reverse = Object.fromEntries(Object.entries(pairs).map(([ar,en]) => [en,ar]));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const east = value => value.replace(/[0-9]/g, digit => '٠١٢٣٤٥٦٧٨٩'[Number(digit)]);

  function parse(value) {
    if (typeof value !== 'string' || value.length > 150) return null;
    const normalized = value.normalize('NFKC').toUpperCase()
      .replace(/[أإآٱ]/g,'ا').replace(/[يی]/g,'ى').replace(/ک/g,'ك')
      .replace(/[٠-٩]/g, digit => String(digit.charCodeAt(0)-0x660))
      .replace(/[۰-۹]/g, digit => String(digit.charCodeAt(0)-0x6f0))
      // Common separators and copied direction marks, not arbitrary punctuation.
      .replace(/[\sـ\-/\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'');
    const match = /^(?:([0-9]{1,4})([A-Zا-ى]+)|([A-Zا-ى]+)([0-9]{1,4}))$/.exec(normalized);
    if (!match) return null;
    const digits = match[1] || match[4], letters = Array.from(match[2] || match[3]);
    if (letters.length !== 3) return null;
    let arabic, latin;
    if (letters.every(letter => Object.hasOwn(pairs,letter))) {
      arabic = letters;
      // Arabic input is read RTL; Latin input is read LTR. Physical columns align.
      latin = letters.map(letter => pairs[letter]).reverse();
    } else if (letters.every(letter => Object.hasOwn(reverse,letter))) {
      latin = letters;
      arabic = letters.map(letter => reverse[letter]).reverse();
    } else return null;
    return {digits, arabicDigits:east(digits), arabic, latin, key:digits+':'+latin.join('')};
  }

  function text(value, language='ar') {
    const plate = parse(value);
    if (!plate) return String(value ?? '').trim() || '—';
    return language === 'en' ? plate.digits+' · '+plate.latin.join(' ') : plate.arabic.join(' ')+' · '+plate.arabicDigits;
  }

  function render(value, language='ar', compact=false) {
    const plate = parse(value);
    // Unrecognized legacy values stay visible, escaped, and are never guessed.
    if (!plate) return `<bdi class="saudi-plate-legacy">${esc(String(value ?? '').trim() || '—')}</bdi>`;
    const label = (language === 'en' ? 'Saudi private vehicle plate: ' : 'لوحة سعودية خصوصي: ')+text(value,language);
    const cells = letters => letters.map(letter => `<span>${esc(letter)}</span>`).join('');
    return `<span class="saudi-plate${compact?' saudi-plate--compact':''}" role="img" aria-label="${esc(label)}" dir="ltr" data-saudi-plate>
      <span class="saudi-plate__numbers saudi-plate__arabic" lang="ar" dir="ltr" aria-hidden="true">${plate.arabicDigits}</span>
      <span class="saudi-plate__letters saudi-plate__arabic" lang="ar" dir="ltr" aria-hidden="true">${cells(plate.arabic.slice().reverse())}</span>
      <span class="saudi-plate__country" aria-hidden="true"><span lang="ar">السعودية</span><strong>KSA</strong><span lang="ar">خصوصي</span><span class="saudi-plate__private-dot"></span></span>
      <span class="saudi-plate__numbers" lang="en" dir="ltr" aria-hidden="true">${plate.digits}</span>
      <span class="saudi-plate__letters" lang="en" dir="ltr" aria-hidden="true">${cells(plate.latin)}</span>
    </span>`;
  }

  function field(name, value='', language='ar', label='') {
    const en = language === 'en';
    label ||= en ? 'Vehicle plate — private' : 'لوحة المركبة — خصوصي';
    return `<div class="saudi-plate-field" data-saudi-plate-field><label><span>${esc(label)}</span><input name="${esc(name)}" value="${esc(value)}" data-plate-original="${esc(value)}" maxlength="40" dir="auto" autocomplete="off" spellcheck="false"></label><span class="saudi-plate-field__hint">${esc(en?'Enter 3 letters and 1–4 digits in Arabic or English, in their reading order. The other line updates automatically.':'أدخل ٣ حروف ومن رقم إلى ٤ أرقام بالعربي أو الإنجليزي حسب ترتيب قراءتها؛ يظهر المقابل تلقائيًا.')}</span><span class="saudi-plate-field__preview" data-saudi-plate-preview aria-live="polite" aria-atomic="true"></span></div>`;
  }

  function bindInputs(root, language='ar') {
    for (const box of root.querySelectorAll('[data-saudi-plate-field]')) {
      const input = box.querySelector('input'), preview = box.querySelector('[data-saudi-plate-preview]');
      if (!input || !preview || input.dataset.plateBound) continue;
      input.dataset.plateBound = 'true';
      const update = () => {
        preview.innerHTML = input.value.trim() ? render(input.value,language) : '';
        input.dir = /[A-Za-z]/.test(input.value) ? 'ltr' : 'rtl';
      };
      input.addEventListener('input',update);
      update();
    }
  }

  function preserve(value, original='') {
    const entered = String(value ?? '').trim(), before = parse(original), after = parse(entered);
    // Changing language/spacing alone must not create a second permanent vehicle.
    return before && after && before.key === after.key ? original : entered;
  }

  window.KPSSaudiPlate = Object.freeze({parse,text,render,field,bindInputs,preserve});
})();

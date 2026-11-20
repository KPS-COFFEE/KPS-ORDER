'use strict';
// The existing #car-color remains the canonical checkout value. UI choice IDs
// never enter order snapshots; historical/custom text is preserved verbatim.
(() => {
  const source = document.getElementById('car-color');
  if (!source || document.getElementById('car-color-choice')) return;
  const colors = [
    ['أبيض','White'], ['أسود','Black'], ['فضي','Silver'], ['رمادي','Gray'],
    ['رصاصي','Charcoal Gray'], ['بيج','Beige'], ['ذهبي','Gold'], ['بني','Brown'],
    ['أحمر','Red'], ['عنابي','Burgundy'], ['أزرق','Blue'], ['كحلي','Navy Blue'],
    ['أخضر','Green'], ['زيتي','Olive Green'], ['أصفر','Yellow'], ['برتقالي','Orange']
  ];
  const label = source.closest('label');
  const choice = document.createElement('select');
  choice.id = 'car-color-choice';
  const customLabel = document.createElement('label');
  customLabel.className = 'field';
  customLabel.hidden = true;
  const caption = document.createElement('span');
  const custom = document.createElement('input');
  custom.id = 'car-color-custom';
  custom.maxLength = 40;
  custom.autocomplete = 'off';
  custom.dir = 'auto';
  customLabel.append(caption, custom);
  source.type = 'hidden';
  label.append(choice);
  label.after(customLabel);
  function language() {
    const en = document.documentElement.lang === 'en';
    const selected = choice.value;
    choice.replaceChildren(new Option(en ? 'Choose a color' : 'اختر لون السيارة', ''));
    colors.forEach(([ar,english]) => choice.append(new Option(en ? english : ar, ar)));
    choice.append(new Option(en ? 'Other Color' : 'لون آخر', '__custom__'));
    choice.value = selected;
    caption.textContent = en ? 'Enter car color' : 'اكتب لون السيارة';
    validateCustom();
  }
  function validateCustom() {
    custom.setCustomValidity(choice.value === '__custom__' && !custom.value.trim()
      ? (document.documentElement.lang === 'en' ? 'Enter car color' : 'اكتب لون السيارة') : '');
  }
  function sync(notify = true) {
    const other = choice.value === '__custom__';
    customLabel.hidden = !other;
    custom.required = other;
    custom.disabled = !other;
    if (!other) custom.value = '';
    source.value = other ? custom.value.trim() : choice.value;
    validateCustom();
    if (notify) source.dispatchEvent(new Event('input', {bubbles:true}));
  }
  language();
  function restore() {
    const previous = source.value;
    choice.value = previous ? (colors.some(([ar]) => ar === previous) ? previous : '__custom__') : '';
    if (choice.value === '__custom__') custom.value = previous;
    sync(false);
  }
  restore();
  source.addEventListener('change', restore);
  choice.addEventListener('change', () => sync());
  custom.addEventListener('input', () => sync());
  new MutationObserver(language).observe(document.documentElement,
    {attributes:true, attributeFilter:['lang']});
})();

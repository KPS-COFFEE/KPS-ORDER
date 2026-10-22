/* Approved option 4. Rendering is advertising only, never an eligibility grant. */
(() => {
  'use strict';
  const TITLE = 'البدايات الحلوة… لها هدية من كبز 🎁';
  function render(container, {brandId, banner}) {
    if (brandId !== 'kps' || banner?.title !== TITLE) return false;
    const make = (tag, className, text) => {
      const element = document.createElement(tag);
      element.className = className;
      if (text) element.textContent = text;
      return element;
    };
    const ad = make('section', 'kps-gift-ad');
    ad.dir = 'rtl';
    ad.setAttribute('aria-label', TITLE);
    const scene = make('img', 'kps-gift-scene');
    scene.src = '/assets/kps-gift-scene.webp';
    scene.alt = 'كوب كبز لقهوة اليوم الباردة مع كوكيز بالشوكولاتة';
    scene.width = 1024; scene.height = 1280; scene.decoding = 'async';
    const logo = make('img', 'kps-gift-logo');
    logo.src = '/assets/kps-gift-original.png';
    logo.alt = 'KPS — قهوة مختصة';
    logo.width = 1181; logo.height = 1181; logo.decoding = 'async';
    const copy = make('div', 'kps-gift-copy');
    const opening = make('div', 'kps-gift-opening', 'البدايات الحلوة…');
    const phrase = make('div', 'kps-gift-phrase');
    const appendPhrase = element => element.append('لها ', make('em', '', 'هدية'), ' من كبز 🎁');
    appendPhrase(phrase);
    const glint = make('span', 'kps-gift-glint');
    glint.setAttribute('aria-hidden', 'true');
    appendPhrase(glint); phrase.append(glint);
    const products = make('div', 'kps-gift-products');
    const appendProducts = element => element.append('قهوة اليوم بارد ', make('span', 'kps-gift-plus', '+'), ' كوكيز');
    appendProducts(products);
    const productGlint = make('span', 'kps-gift-glint kps-gift-product-glint');
    productGlint.setAttribute('aria-hidden', 'true');
    appendProducts(productGlint); products.append(productGlint);
    const caption = make('small', 'kps-gift-caption', 'لطلبات التوصيل عبر موقعنا');
    Object.assign(caption.style, {
      position: 'absolute', bottom: '1.5%', left: '5%', right: '5%',
      display: 'block', textAlign: 'right', fontSize: '2.5cqw',
      lineHeight: '1.6', padding: '0.3em 0.6em', borderRadius: '0.5em',
      color: '#fff', background: '#141210cc', textShadow: '0 1px 3px #000'
    });
    copy.append(opening, phrase); ad.append(scene, logo, copy, products, caption);
    container.className = 'kps-gift-host';
    container.replaceChildren(ad);
    return true;
  }
  window.KPSGiftCreative = Object.freeze({render});
})();

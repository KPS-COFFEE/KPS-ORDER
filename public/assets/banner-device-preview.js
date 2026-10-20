/* Static owner preview using real storefront CSS and real device viewports.
 * No order, SMS, upload or advertisement publication request is made here. */
(() => {
  'use strict';
  const sizes={mobile:[390,844],tablet:[768,1024],desktop:[1366,768]};
  const records=new WeakMap();
  function render({root,esc,tr,title,body,label,desktop,mobile}){
    let observers=records.get(root);
    if(!observers){observers=new Map();records.set(root,observers);root.closest('dialog')?.addEventListener('close',()=>{for(const observer of observers.values())observer.disconnect();records.delete(root);},{once:true});}
    for(const [screen,[width,height]] of Object.entries(sizes)){
      const box=root.querySelector('#'+screen+'-preview');if(!box)continue;
      box.classList.add('device-viewport');
      let frame=box.querySelector('iframe');
      if(!frame){frame=document.createElement('iframe');frame.title=tr('معاينة الإعلان — ','Advertisement preview — ')+({mobile:tr('الجوال','Mobile'),tablet:tr('التابلت','Tablet'),desktop:tr('الكمبيوتر','Desktop')}[screen]);frame.width=width;frame.height=height;frame.setAttribute('sandbox','allow-same-origin');box.replaceChildren(frame);}
      const image=desktop||mobile;
      const picture=image?`<picture>${mobile?`<source media="(max-width:550px)" srcset="${esc(mobile)}">`:''}<img src="${esc(image)}" alt="${esc(title)}"></picture>`:'';
      const html=`<!doctype html><html lang="${document.documentElement.lang==='en'?'en':'ar'}" dir="${document.documentElement.dir==='ltr'?'ltr':'rtl'}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/customer.css"><link rel="stylesheet" href="/assets/customer-entry.css"></head><body><header class="site-header"><div class="header-inner"><strong>${esc(tr('معاينة قبل النشر','Preview before publication'))}</strong></div></header><main class="page-shell"><section class="banner-area"><div class="campaign-banner">${picture}<div class="campaign-content"><div><h2>${esc(title)}</h2><p>${esc(body)}</p></div>${label?`<button type="button" class="button secondary" disabled>${esc(label)}</button>`:''}</div></div></section><section class="customer-entry"><h2>${esc(tr('الاسم والجوال أولًا','Your name and mobile first'))}</h2><p class="entry-description">${esc(tr('هنا تظهر بداية رحلة الزبون أسفل الإعلان.','The customer journey starts below the advertisement.'))}</p><div class="entry-form"><label>${esc(tr('الاسم','Name'))}<input disabled></label><label>${esc(tr('رقم الجوال','Mobile'))}<input disabled dir="ltr" placeholder="05XXXXXXXX"></label></div></section></main></body></html>`;
      if(frame.srcdoc!==html)frame.srcdoc=html;
      const resize=()=>{const scale=Math.min(1,box.clientWidth/width);frame.style.transform=`scale(${scale})`;box.style.height=Math.ceil(height*scale)+'px';};
      resize();
      if(!observers.has(box)&&typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(resize);observer.observe(box);observers.set(box,observer);}
    }
  }
  window.KPSBannerDevicePreview={render};
})();

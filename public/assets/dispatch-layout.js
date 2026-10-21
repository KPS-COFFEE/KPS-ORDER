/* Personal presentation preferences only; never changes order or shift state. */
'use strict';
window.KPSDispatchLayout = (() => {
  const selectors = {orders:'.dispatch-orders', map:'.dispatch-map-panel', drivers:'#dispatch-panel-drivers', messages:'#dispatch-panel-messages'};
  const defaults = () => ({summary:true,map:true,custom:false,order:['orders','map','drivers','messages'],widths:{orders:5,map:4,drivers:3,messages:3},heights:{}});
  function read(key) {
    const d=defaults();
    try {
      const s=JSON.parse(localStorage.getItem(key));
      if(!s || s.version!==1)return d;
      for(const k of ['summary','map','custom'])if(typeof s[k]==='boolean')d[k]=s[k];
      if(Array.isArray(s.order)&&s.order.length===4&&new Set(s.order).size===4&&s.order.every(k=>k in selectors))d.order=s.order;
      for(const k of Object.keys(selectors)) {
        if(Number.isInteger(s.widths?.[k])&&s.widths[k]>=3&&s.widths[k]<=12)d.widths[k]=s.widths[k];
        if(Number.isInteger(s.heights?.[k])&&s.heights[k]>=120&&s.heights[k]<=1600)d.heights[k]=s.heights[k];
      }
    } catch {/* Storage can be unavailable in private browsers. */}
    return d;
  }
  function mount(root,user,lang,onResize) {
    if(!root || !user?.id || !['delivery_manager','delivery_staff','owner','admin'].includes(user.role))return;
    const workspace=root.querySelector('.dispatch-workspace'),grid=root.querySelector('.dispatch-layout');
    if(!workspace||!grid||workspace.querySelector('.dispatch-personal-controls'))return;
    const en=lang==='en',t=(ar,english)=>en?english:ar;
    // No credentials, customer records or operational data are stored.
    const key='kps_dispatch_layout_v1:'+JSON.stringify([location.host,user.brand_id,user.id]);
    let prefs=read(key);
    const names={orders:t('الطلبات','Orders'),map:t('الخريطة','Map'),drivers:t('السواقون','Drivers'),messages:t('الرسائل','Messages')};
    const controls=document.createElement('div');controls.className='dispatch-personal-controls';
    const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.className='quiet dispatch-layout-button';b.textContent=label;b.addEventListener('click',fn);return b;};
    const save=()=>{try{localStorage.setItem(key,JSON.stringify({...prefs,version:1}));}catch{}};
    const summary=button('',()=>{prefs.summary=!prefs.summary;apply();save();});summary.setAttribute('aria-controls','dispatch-summary-all');
    const map=button('',()=>{prefs.map=!prefs.map;apply();save();});map.setAttribute('aria-controls','dispatch-personal-map');
    const settings=document.createElement('details');settings.className='dispatch-layout-settings';
    const trigger=document.createElement('summary');trigger.textContent=t('تخصيص الشاشة','Customize screen');settings.append(trigger);
    const editor=document.createElement('div');editor.className='dispatch-layout-editor';settings.append(editor);
    controls.append(summary,map,settings);workspace.querySelector('#dispatch-counts').before(controls);
    const panels=Object.fromEntries(Object.entries(selectors).map(([k,s])=>[k,root.querySelector(s)]));
    panels.map.id='dispatch-personal-map';
    const contents={orders:root.querySelector('#dispatch-orders'),map:root.querySelector('#dispatch-map'),drivers:root.querySelector('#dispatch-drivers'),messages:root.querySelector('#dispatch-panel-messages .conversation-list')};
    const observer=new MutationObserver(()=>applySummary());observer.observe(root.querySelector('#dispatch-counts'),{childList:true});
    // Disconnect automatically when the workspace is replaced by navigation/refresh.
    const removal=new MutationObserver(()=>{if(!workspace.isConnected){observer.disconnect();removal.disconnect();}});removal.observe(root,{childList:true});
    function applySummary(){const row=root.querySelector('[data-count-scope="all"]');if(row){row.id='dispatch-summary-all';row.hidden=!prefs.summary;}}
    function apply(){
      applySummary();
      summary.textContent=t(prefs.summary?'إخفاء الملخص':'إظهار الملخص',prefs.summary?'Hide summary':'Show summary');summary.setAttribute('aria-expanded',String(prefs.summary));
      map.textContent=t(prefs.map?'إخفاء الخريطة':'إظهار الخريطة',prefs.map?'Hide map':'Show map');map.setAttribute('aria-expanded',String(prefs.map));panels.map.hidden=!prefs.map;
      grid.classList.toggle('dispatch-personal-grid',prefs.custom);grid.classList.toggle('dispatch-without-map',!prefs.map);
      for(const k of Object.keys(selectors)){
        panels[k].style.setProperty('--personal-order',prefs.order.indexOf(k));panels[k].style.setProperty('--personal-span',prefs.widths[k]);
        const el=contents[k];el.style.removeProperty('height');el.style.removeProperty('max-height');
        if(prefs.heights[k]){el.style.height=prefs.heights[k]+'px';el.style.maxHeight=prefs.heights[k]+'px';}
      }
      // Mapbox observes its host size; also notify providers using window resize.
      window.dispatchEvent(new Event('resize'));
      requestAnimationFrame(()=>onResize?.());
    }
    function renderEditor(){
      editor.replaceChildren();
      for(const k of prefs.order){
        const row=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=names[k];row.append(legend);
        for(const [delta,label] of [[-1,t('تقديم','Move earlier')],[1,t('تأخير','Move later')]]){
          const b=button(label,()=>{const i=prefs.order.indexOf(k),j=i+delta;[prefs.order[i],prefs.order[j]]=[prefs.order[j],prefs.order[i]];prefs.custom=true;apply();save();renderEditor();editor.querySelector(`[data-move="${k}:${delta}"]`)?.focus();});
          b.dataset.move=k+':'+delta;b.disabled=delta<0?prefs.order.indexOf(k)===0:prefs.order.indexOf(k)===3;row.append(b);
        }
        for(const [dimension,label,min,max,step] of [['width',t('العرض','Width'),3,12,1],['height',t('الارتفاع','Height'),120,1600,20]]){
          const wrap=document.createElement('label'),input=document.createElement('input');wrap.textContent=label;input.type='range';input.min=min;input.max=max;input.step=step;input.dataset.panel=k;input.dataset.dimension=dimension;
          input.value=dimension==='width'?prefs.widths[k]:(prefs.heights[k]||Math.round(contents[k].getBoundingClientRect().height)||300);input.setAttribute('aria-label',names[k]+' — '+label);
          input.addEventListener('input',()=>{if(dimension==='width'){prefs.widths[k]=Number(input.value);prefs.custom=true;}else prefs.heights[k]=Number(input.value);apply();save();});wrap.append(input);row.append(wrap);
        }
        editor.append(row);
      }
      editor.append(button(t('استعادة الافتراضي','Restore defaults'),()=>{prefs=defaults();apply();save();renderEditor();}));
    }
    // Drag only dedicated heading grips, never order buttons, text or map gestures.
    let dragged=null;
    settings.addEventListener('toggle',()=>workspace.classList.toggle('dispatch-layout-editing',settings.open));
    for(const k of Object.keys(selectors)){
      const handle=button('⠿',()=>{});handle.classList.add('dispatch-move-grip');handle.draggable=true;
      handle.title=t('اسحب لتغيير المكان أو استخدم التقديم والتأخير في التخصيص','Drag to move, or use Move earlier/later in customization');
      handle.setAttribute('aria-label',t('نقل ','Move ')+names[k]);
      handle.addEventListener('dragstart',e=>{dragged=k;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',k);});
      handle.addEventListener('dragend',()=>{dragged=null;});
      panels[k].querySelector('.dispatch-pane-heading')?.append(handle);
      panels[k].addEventListener('dragover',e=>{if(dragged&&dragged!==k){e.preventDefault();e.dataTransfer.dropEffect='move';}});
      panels[k].addEventListener('drop',e=>{if(!dragged||dragged===k)return;e.preventDefault();const next=prefs.order.filter(id=>id!==dragged);next.splice(next.indexOf(k),0,dragged);prefs.order=next;prefs.custom=true;dragged=null;apply();save();renderEditor();});
    }
    // A discreet grip expands list height without touching cards or their actions.
    for(const k of Object.keys(selectors)){
      const grip=button('↕',()=>{});grip.classList.add('dispatch-height-grip');grip.setAttribute('aria-label',t('تغيير ارتفاع ','Resize height: ')+names[k]);grip.title=grip.getAttribute('aria-label');
      grip.addEventListener('keydown',e=>{if(!['ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();prefs.heights[k]=Math.max(120,Math.min(1600,(prefs.heights[k]||contents[k].getBoundingClientRect().height)+(e.key==='ArrowDown'?40:-40)));apply();save();});
      grip.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const start=e.clientY,height=contents[k].getBoundingClientRect().height;grip.setPointerCapture(e.pointerId);const move=event=>{prefs.heights[k]=Math.round(Math.max(120,Math.min(1600,height+event.clientY-start)));apply();};const end=()=>{grip.removeEventListener('pointermove',move);grip.removeEventListener('pointerup',end);grip.removeEventListener('pointercancel',end);save();renderEditor();};grip.addEventListener('pointermove',move);grip.addEventListener('pointerup',end);grip.addEventListener('pointercancel',end);});
      panels[k].append(grip);
    }
    apply();renderEditor();
  }
  return {mount};
})();

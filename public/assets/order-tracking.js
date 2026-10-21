/* Read-only wall display. Scoped APIs remain the authority; refresh never asks for routes. */
(() => {
  'use strict';
  async function mount(host,{api,branches=[],tr,lang='ar',isActive=()=>true}){
    let destroyed=false,map=null,busy=false,generation=0,city='',completed=false,nextOffset=null,orders=[],lastCity='',rotateAt=Date.now();
    const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
    const root=el('section',null,'tracking-screen'),bar=el('div',null,'tracking-toolbar'),title=el('h1',tr('تتبع الطلبات','Order tracking'));
    const cityLabel=el('label',tr('المحافظة','City')),citySelect=el('select');cityLabel.append(citySelect);
    const cities=[...new Set(branches.map(b=>b.city).filter(Boolean))].sort();
    for(const value of ['',...cities]){const o=el('option',value||tr('جميع المحافظات','All cities'));o.value=value;citySelect.append(o);}
    const rotateLabel=el('label',null,'tracking-rotate'),rotate=el('input');rotate.type='checkbox';rotateLabel.append(rotate,document.createTextNode(tr('تبديل المحافظة كل 30 ثانية','Rotate city every 30 seconds')));
    const fullscreen=el('button',tr('ملء الشاشة','Full screen')),exit=el('button',tr('العودة','Back'));fullscreen.type=exit.type='button';exit.dataset.page='dashboard';
    bar.append(title,cityLabel,rotateLabel,fullscreen,exit);
    const status=el('p','', 'tracking-status'),metrics=el('div',null,'tracking-metrics'),activeButton=el('button'),doneButton=el('button');activeButton.type=doneButton.type='button';metrics.append(activeButton,doneButton);
    const body=el('div',null,'tracking-body'),mapHost=el('div',null,'tracking-map'),list=el('section',null,'tracking-orders'),rows=el('div'),more=el('button',tr('تحميل المزيد','Load more'));more.type='button';more.hidden=true;const driverRows=el('section',null,'tracking-drivers');list.append(driverRows,rows,more);body.append(mapHost,list);root.append(bar,metrics,status,body);host.replaceChildren(root);
    const alive=()=>!destroyed&&root.isConnected&&isActive();
    const stage=o=>o.delivery_status==='delivered'||o.completed_at?'delivered':['cancelled','rejected'].includes(o.preparation_status)||o.delivery_status==='cancelled'?'cancelled':o.delivery_status==='picked_up'?'road':o.preparation_status==='new'?'new':'preparing';
    const stageLabel={delivered:tr('تم التسليم','Delivered'),cancelled:tr('ملغي','Cancelled'),road:tr('في الطريق','On the way'),new:tr('جديد','New'),preparing:tr('جارٍ التنفيذ','In progress')};
    function renderRows(){
      rows.replaceChildren(el('h2',completed?tr('المكتملة — الأحدث أولًا','Completed — newest first'):tr('الطلبات الجارية','Active orders')));
      const sorted=[...orders].sort((a,b)=>(Date.parse(completed?(b.completed_at||b.created_at):b.created_at)||0)-(Date.parse(completed?(a.completed_at||a.created_at):a.created_at)||0));
      for(const o of sorted){const card=el('article',null,'tracking-order');card.append(el('strong',String(o.order_reference||o.snapshot?.order_reference||o.order_no||o.id)),el('span',stageLabel[stage(o)]),el('p',String(o.branch_name||o.snapshot?.branch_name||'')));if(o.driver_name||o.driver?.display_name)card.append(el('p',tr('السائق: ','Driver: ')+(o.driver_name||o.driver.display_name)));const time=el('time',new Date(completed?(o.completed_at||o.created_at):o.created_at).toLocaleString(lang==='en'?'en-GB':'ar-SA'));card.append(time);rows.append(card);}
      if(!sorted.length)rows.append(el('p',tr('لا توجد طلبات ضمن النطاق المحدد.','No orders in the selected scope.')));
      more.hidden=nextOffset==null;
    }
    async function refresh(append=false){
      if(!alive()||document.hidden||busy)return;busy=true;const version=generation,requestedCity=city;
      const query=new URLSearchParams({service:'delivery',limit:'100',sort:'newest',dispatch_workspace:'true',dispatch_scope:'current',dispatch_stage:completed?'delivered':'active'});
      if(city)query.set('dispatch_city',city);if(append&&nextOffset!=null)query.set('offset',String(nextOffset));
      try{
        const [result,gps]=await Promise.all([api('/api/orders?'+query),api('/api/operations/driver-locations')]);
        if(!alive()||version!==generation)return;
        orders=append?[...orders,...result.orders||[]]:result.orders||[];nextOffset=result.next_offset;
        const counts=result.workspace_counts?.current||{},active=Object.entries(counts).filter(([k])=>!['total','delivered','cancelled'].includes(k)).reduce((n,[,v])=>n+Number(v||0),0);
        activeButton.textContent=tr('الجارية: ','Active: ')+active;doneButton.textContent=tr('المكتملة: ','Completed: ')+Number(counts.delivered||0);activeButton.setAttribute('aria-pressed',String(!completed));doneButton.setAttribute('aria-pressed',String(completed));
        renderRows();
        const visibleBranches=branches.filter(b=>!city||b.city===city),ids=new Set(visibleBranches.map(b=>b.id));
        const drivers=(gps.drivers||[]).filter(d=>!city||(d.branch_ids||[]).some(id=>ids.has(id))).map(d=>({...d,gps:d}));
        driverRows.replaceChildren(el('h2',tr('السائقون والوجهة','Drivers and destination')));
        for(const driver of drivers){
          const card=el('article',null,'tracking-driver'),brand=driver.active_order_brand,next=driver.next_destination;
          const brandId=['kps','enbishan','mixed'].includes(brand?.id)?brand.id:'neutral',swatch=el('i',null,'dispatch-driver-swatch brand-'+brandId);swatch.setAttribute('aria-hidden','true');
          const heading=el('strong',driver.display_name||driver.name||driver.id);card.append(swatch,heading,el('span',brandId==='kps'?'KPS':brandId==='enbishan'?tr('عنبيشان','Enbishan'):brandId==='mixed'?tr('براندات متعددة','Mixed brands'):tr('بدون طلب نشط','No active order')));
          if(brand?.source==='driver_declared')card.append(el('p',tr('البراند بإفادة السائق','Brand declared by driver')));
          card.append(el('p',next?tr('الوجهة المخططة: ','Planned destination: ')+(next.kind==='external_enbishan'?tr('عنبيشان — إفادة السائق','Enbishan — driver declaration'):(next.name||tr('فرع','Branch'))):tr('الوجهة التالية غير محددة','Next destination not specified')));
          const age=Date.now()-Date.parse(driver.updated_at),stale=driver.stale||!Number.isFinite(age)||age>120000;
          card.append(el('p',stale?tr('موقع قديم — ليس مباشرًا','Stale location — not live'):tr('موقع حديث','Recent location')));
          card.append(el('p',tr('وقت الوصول: احسب المسار يدويًا أسفل الخريطة','ETA: calculate a route manually below the map'),'muted small-text'));driverRows.append(card);
        }
        if(!drivers.length)driverRows.append(el('p',tr('لا توجد مواقع سائقين ضمن النطاق','No driver locations in this scope')));
        const mapData={branches:visibleBranches,drivers,orders:completed?[]:orders,lang};
        if(!map){const mounted=await window.KPSDispatchMap.mount(mapHost,{api,...mapData});if(!alive()){mounted?.destroy();return;}map=mounted;}else map.update(mapData);
        if(lastCity!==requestedCity){map?.fit?.();lastCity=requestedCity;}
        status.textContent=tr('الشفت الحالي · آخر تحديث ','Current shift · Updated ')+new Date().toLocaleTimeString(lang==='en'?'en-GB':'ar-SA')+(nextOffset!=null?tr(' · توجد نتائج إضافية',' · More results available'):'');
      }catch{if(alive()&&version===generation)status.textContent=tr('تعذر التحديث؛ آخر بيانات معروضة قديمة.','Refresh failed; displayed data may be stale.');}
      finally{busy=false;if(alive()&&version!==generation)void refresh();}
    }
    function change(){generation++;orders=[];nextOffset=null;rows.replaceChildren();map?.update({orders:[],drivers:[]});void refresh();}
    citySelect.addEventListener('change',()=>{city=citySelect.value;rotateAt=Date.now();change();});
    activeButton.addEventListener('click',()=>{completed=false;change();});doneButton.addEventListener('click',()=>{completed=true;rotate.checked=false;change();});more.addEventListener('click',()=>void refresh(true));
    fullscreen.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen();map?.resize();}catch{status.textContent=tr('ملء الشاشة غير متاح في هذا المتصفح.','Fullscreen is unavailable in this browser.');}});
    const resize=()=>map?.resize();document.addEventListener('fullscreenchange',resize);
    const timer=setInterval(()=>{if(!alive()||document.hidden)return;if(rotate.checked&&cities.length&&Date.now()-rotateAt>=30000){city=cities[(cities.indexOf(city)+1)%cities.length];citySelect.value=city;rotateAt=Date.now();change();}else void refresh();},15000);
    await refresh();return {destroy(){destroyed=true;generation++;clearInterval(timer);document.removeEventListener('fullscreenchange',resize);map?.destroy();}};
  }
  window.KPSOrderTracking={mount};
})();

/* Scoped wall display. Automatic previews never publish or change a driver's route. */
(() => {
  'use strict';
  async function mount(host,{api,branches=[],tr,lang='ar',isActive=()=>true}){
    let destroyed=false,map=null,busy=false,generation=0,city='',completed=false,nextOffset=null,orders=[],lastCity='',rotateAt=Date.now();
    const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
    const root=el('section',null,'tracking-screen'),bar=el('div',null,'tracking-toolbar'),title=el('h1',tr('تتبع الطلبات','Order tracking'));
    const cityLabel=el('label',tr('المحافظة','City')),citySelect=el('select');citySelect.setAttribute('aria-label',tr('المحافظة','City'));cityLabel.append(citySelect);
    const cities=[...new Set(branches.map(b=>b.city).filter(Boolean))].sort();
    for(const value of ['',...cities]){const o=el('option',value||tr('جميع المحافظات','All cities'));o.value=value;citySelect.append(o);}
    const rotateLabel=el('label',null,'tracking-rotate'),rotate=el('input');rotate.type='checkbox';rotateLabel.append(rotate,document.createTextNode(tr('تبديل المحافظة كل 30 ثانية','Rotate city every 30 seconds')));
    const fullscreen=el('button',tr('ملء الشاشة','Full screen')),exit=el('button',tr('العودة','Back'));fullscreen.type=exit.type='button';exit.dataset.page='dashboard';
    bar.append(title,cityLabel,rotateLabel,fullscreen,exit);
    const status=el('p','', 'tracking-status'),metrics=el('div',null,'tracking-metrics'),activeButton=el('button'),doneButton=el('button');activeButton.type=doneButton.type='button';metrics.append(activeButton,doneButton);
    const body=el('div',null,'tracking-body'),mapHost=el('div',null,'tracking-map'),list=el('section',null,'tracking-orders'),rows=el('div'),more=el('button',tr('تحميل المزيد','Load more'));more.type='button';more.hidden=true;const driverRows=el('section',null,'tracking-drivers');list.append(driverRows,rows,more);body.append(mapHost,list);root.append(bar,metrics,status,body);host.replaceChildren(root);
    const alive=()=>!destroyed&&root.isConnected&&isActive();
    const etaCache=new Map(),etaHosts=new Map();let visibleDrivers=[],etaBusy=false,etaRevision=0;
    const stamp=value=>typeof value==='string'?Date.parse(value):NaN;
    const fresh=value=>Number.isFinite(stamp(value))&&Date.now()-stamp(value)>=0&&Date.now()-stamp(value)<=120000;
    const locationFresh=driver=>!driver.stale&&fresh(driver.captured_at||driver.updated_at);
    const eligible=driver=>locationFresh(driver)&&(Number(driver.visible_active_task_count)>0||['delivering','awaiting_pickup','returning'].includes(driver.movement_state));
    function remaining(value){const seconds=(stamp(value)-Date.now())/1000;return !Number.isFinite(seconds)?tr('غير متاح','Unavailable'):seconds<=0?tr('حل الموعد المتوقع — بانتظار تحديث','Expected time reached — awaiting update'):tr('متبقي نحو ','About ')+Math.ceil(seconds/60)+tr(' دقيقة',' min remaining');}
    function stopLabel(stop){
      const order=orders.find(o=>o.id===stop.order_id&&(!stop.brand_id||o.brand_id===stop.brand_id));
      return ['pickup','return'].includes(stop.kind)?String(stop.name||tr('الفرع','Branch')):[stop.customer_name||order?.customer_name||order?.customer?.name,stop.order_no||order?.order_reference||order?.order_no||stop.order_id].filter(Boolean).join(' · ');
    }
    function reasonLabel(reason){
      const labels={route_budget_or_cooldown:['التقدير مؤجل حسب حد استخدام الخرائط؛ يعاد تلقائيًا','Estimate deferred by map usage limit; retries automatically'],routing_token_unconfigured:['خدمة تقدير الطرق غير مهيأة','Road estimates are not configured'],itinerary_scope_incomplete:['لا يمكن تقدير رحلة كاملة ضمن صلاحيات الحساب','Full itinerary unavailable within account permissions'],no_stops:['لا توجد محطات لتقدير وقتها','No stops to estimate'],location_stale:['بانتظار موقع حديث','Awaiting a fresh location'],itinerary_changed:['تغيرت الرحلة — بانتظار إعادة الحساب','Itinerary changed — awaiting recalculation'],route_access_unavailable:['التقدير غير متاح ضمن الصلاحيات الحالية','Estimate unavailable within current permissions'],route_session_expired:['انتهت الجلسة — سجل الدخول مجددًا','Session expired — sign in again']};
      return labels[reason]?tr(...labels[reason]):tr('تعذر تقدير الطريق — ستتم إعادة المحاولة تلقائيًا','Road estimate unavailable — retries automatically');
    }
    function renderEta(driver){
      const host=etaHosts.get(driver.id);if(!host)return;host.replaceChildren();
      const record=etaCache.get(driver.id),plan=record?.plan;
      if(!locationFresh(driver)){host.append(el('p',tr('التقدير متوقف حتى يصل موقع حديث','Estimates paused until a fresh location arrives')));return;}
      if(!navigator.onLine){host.append(el('p',tr('انقطع الاتصال — الأوقات غير مباشرة','Offline — estimates are not live')));return;}
      if(!eligible(driver)){host.append(el('p',tr('لا توجد رحلة نشطة لحساب وقتها','No active itinerary to estimate')));return;}
      if(!plan||plan.status!=='ready'||!fresh(plan.calculated_at)){
        host.append(el('p',record?.pending?tr('جارٍ حساب وقت الطريق…','Calculating road time…'):plan?.status==='ready'?tr('تقدير الطريق قديم — بانتظار إعادة الحساب','Road estimate expired — awaiting recalculation'):reasonLabel(record?.reason||plan?.reason)));return;
      }
      if(record.reason)host.append(el('p',tr('آخر تقدير محفوظ · ','Last saved estimate · ')+reasonLabel(record.reason),'muted small-text'));
      const next=plan.next_stop;
      if(next)host.append(el('p',tr('الوجهة القادمة: ','Next destination: ')+stopLabel(next),'tracking-next-stop'));
      const list=el('ol',null,'tracking-eta-stops');
      for(const stop of plan.stops||[]){const kind=stop.kind==='pickup'?tr('استلام','Pickup'):stop.kind==='return'?tr('عودة','Return'):tr('تسليم','Delivery');list.append(el('li',kind+' · '+stopLabel(stop)+' — '+remaining(stop.arrival_at)));}
      host.append(list);
      host.append(el('p',tr('إنهاء التوصيلات: ','Deliveries complete: ')+remaining(plan.delivery_complete_at),'tracking-eta-summary'));
      host.append(el('p',tr('العودة للفرع: ','Branch return: ')+(plan.return_at?remaining(plan.return_at):tr('حدد فرع العودة من بطاقة السائق لاحتساب وقته','Select a return branch in the driver card to estimate it')),'tracking-eta-summary'));
      host.append(el('p',tr('تقدير طرق وتوقفات · يتجدد كل دقيقة · ','Road and stop estimate · refreshes every minute · ')+new Date(plan.calculated_at).toLocaleTimeString(lang==='en'?'en-GB':'ar-SA'),'muted small-text'));
      if(plan.intent_basis==='road_suggestion')host.append(el('p',tr('ترتيب مقترح — لم يُعتمد للسائق','Suggested order — not applied to the driver'),'muted small-text'));
    }
    async function refreshEtas(){
      if(etaBusy||!alive()||document.hidden||!navigator.onLine)return;etaBusy=true;const version=generation;
      try{
        // Serial requests keep fleet refreshes from bursting the road service.
        for(const queuedDriver of visibleDrivers){
          if(!alive()||version!==generation||document.hidden||!navigator.onLine)break;
          const driver=visibleDrivers.find(d=>d.id===queuedDriver.id);if(!driver)continue;
          if(!eligible(driver))continue;
          const record=etaCache.get(driver.id)||{};
          if(record.attempt!=null&&Date.now()-record.attempt<60000)continue;
          const revision=etaRevision;record.attempt=Date.now();record.pending=true;etaCache.set(driver.id,record);renderEta(driver);
          try{
            const plan=await api('/api/operations/route-eta',{method:'POST',body:{driver_id:driver.id,automatic_preview:true}});
            if(!alive()||version!==generation)break;
            if(revision!==etaRevision)continue;
            const transient=['route_budget_or_cooldown','routing_provider_unavailable'].includes(plan?.reason);
            if(plan?.status==='ready'||!transient||!fresh(record.plan?.calculated_at))record.plan=plan;
            record.reason=plan?.status==='ready'?null:plan?.reason;
          }catch(error){
            if(!alive()||version!==generation)break;
            if(revision!==etaRevision)continue;
            const status=Number(error?.status);record.reason=status===401?'route_session_expired':status===403||status===404?'route_access_unavailable':status===409?'itinerary_changed':'route_network_unavailable';
            if(status>=400&&status<500||!fresh(record.plan?.calculated_at))record.plan=null;
          }finally{record.pending=false;}
          const latest=visibleDrivers.find(d=>d.id===driver.id);if(latest&&alive()&&version===generation)renderEta(latest);
        }
      }finally{etaBusy=false;}
    }
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
      if(!alive()||document.hidden||!navigator.onLine||busy)return;busy=true;const version=generation,requestedCity=city;
      const query=new URLSearchParams({service:'delivery',limit:'100',sort:'newest',dispatch_workspace:'true',dispatch_scope:'current',dispatch_stage:completed?'delivered':'active'});
      if(city)query.set('dispatch_city',city);if(append&&nextOffset!=null)query.set('offset',String(nextOffset));
      try{
        const [result,gps]=await Promise.all([api('/api/orders?'+query),api('/api/operations/driver-locations')]);
        if(!alive()||version!==generation)return;
        const itineraryKey=value=>JSON.stringify(value.map(o=>[o.brand_id,o.id,o.driver_id,o.driver?.id,o.delivery_status,o.preparation_status,o.branch_id,o.customer?.location||o.customer?.lat,o.customer?.lng]).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))));
        const previousItinerary=itineraryKey(orders);
        orders=append?[...orders,...result.orders||[]]:result.orders||[];nextOffset=result.next_offset;
        if(!append&&previousItinerary!==itineraryKey(orders)){etaRevision++;for(const record of etaCache.values()){record.plan=null;record.reason='itinerary_changed';}}
        const counts=result.workspace_counts?.current||{},active=Object.entries(counts).filter(([k])=>!['total','delivered','cancelled'].includes(k)).reduce((n,[,v])=>n+Number(v||0),0);
        activeButton.textContent=tr('الجارية: ','Active: ')+active;doneButton.textContent=tr('المكتملة: ','Completed: ')+Number(counts.delivered||0);activeButton.setAttribute('aria-pressed',String(!completed));doneButton.setAttribute('aria-pressed',String(completed));
        renderRows();
        const visibleBranches=branches.filter(b=>!city||b.city===city),ids=new Set(visibleBranches.map(b=>b.id));
        const drivers=(gps.drivers||[]).filter(d=>!city||(d.branch_ids||[]).some(id=>ids.has(id))).map(d=>({...d,gps:d}));
        const driverIntentKey=values=>JSON.stringify(values.map(d=>[d.id,d.movement_state,d.visible_active_task_count,d.next_destination]).sort((a,b)=>String(a[0]).localeCompare(String(b[0]))));
        if(driverIntentKey(visibleDrivers)!==driverIntentKey(drivers)){etaRevision++;for(const record of etaCache.values()){record.plan=null;record.reason='itinerary_changed';}}
        visibleDrivers=drivers;etaHosts.clear();for(const id of etaCache.keys())if(!drivers.some(d=>d.id===id))etaCache.delete(id);
        driverRows.replaceChildren(el('h2',tr('السائقون والوجهة','Drivers and destination')));
        for(const driver of drivers){
          const card=el('article',null,'tracking-driver'),brand=driver.active_order_brand,next=driver.next_destination;
          const brandId=['kps','enbishan','mixed'].includes(brand?.id)?brand.id:'neutral',swatch=el('i',null,'dispatch-driver-swatch brand-'+brandId);swatch.setAttribute('aria-hidden','true');
          const heading=el('strong',driver.display_name||driver.name||driver.id);card.append(swatch,heading,el('span',brandId==='kps'?'KPS':brandId==='enbishan'?tr('عنبيشان','Enbishan'):brandId==='mixed'?tr('براندات متعددة','Mixed brands'):tr('بدون طلب نشط','No active order')));
          const phaseLabels={delivering:tr('يوصل طلبًا','Delivering an order'),awaiting_pickup:tr('بانتظار استلام الطلب','Awaiting order pickup'),returning:tr('متجه للفرع — وجهة مخططة وليست إثبات وصول','Heading to branch — planned destination, not confirmed arrival'),external_enbishan:tr('متجه لعنبيشان — بإفادة السائق','Heading to Enbishan — driver-declared'),awaiting_destination:tr('بانتظار تحديد الوجهة التالية','Awaiting next destination')};
          card.append(el('p',phaseLabels[driver.movement_state]||tr('حالة الحركة غير متاحة','Movement state unavailable'),'tracking-driver-phase'));
          if(brand?.source==='driver_declared')card.append(el('p',tr('البراند بإفادة السائق','Brand declared by driver')));
          card.append(el('p',next?tr('الوجهة المخططة: ','Planned destination: ')+(next.kind==='external_enbishan'?tr('عنبيشان — إفادة السائق','Enbishan — driver declaration'):(next.name||tr('فرع','Branch'))):['delivering','awaiting_pickup'].includes(driver.movement_state)?tr('الوجهة التالية تُحدد بعد إتمام الطلبات','Next destination is set after completing orders'):tr('الوجهة التالية غير محددة','Next destination not specified')));
          const stale=!locationFresh(driver);
          card.append(el('p',(stale?tr('موقع قديم — ليس مباشرًا','Stale location — not live'):tr('موقع حديث','Recent location'))+' · '+(Number.isFinite(stamp(driver.captured_at||driver.updated_at))?new Date(driver.captured_at||driver.updated_at).toLocaleTimeString(lang==='en'?'en-GB':'ar-SA'):tr('وقت الموقع غير متاح','Location time unavailable'))));
          const eta=el('div',null,'tracking-driver-eta');etaHosts.set(driver.id,eta);card.append(eta);driverRows.append(card);renderEta(driver);
        }
        if(!drivers.length)driverRows.append(el('p',tr('لا توجد مواقع سائقين ضمن النطاق','No driver locations in this scope')));
        const mapData={branches:visibleBranches,drivers,orders:completed?[]:orders,lang};
        if(!map){const mounted=await window.KPSDispatchMap.mount(mapHost,{api,...mapData});if(!alive()){mounted?.destroy();return;}map=mounted;}else map.update(mapData);
        if(lastCity!==requestedCity){map?.fit?.();lastCity=requestedCity;}
        status.textContent=tr('الشفت الحالي · آخر تحديث ','Current shift · Updated ')+new Date().toLocaleTimeString(lang==='en'?'en-GB':'ar-SA')+(nextOffset!=null?tr(' · توجد نتائج إضافية',' · More results available'):'');
        void refreshEtas();
      }catch(error){if(alive()&&version===generation){
        etaRevision++;
        for(const record of etaCache.values()){record.plan=null;record.reason='route_network_unavailable';}
        for(const driver of visibleDrivers)renderEta(driver);
        if([401,403,404].includes(Number(error?.status))){etaCache.clear();visibleDrivers=[];etaHosts.clear();orders=[];driverRows.replaceChildren();rows.replaceChildren();map?.update({orders:[],drivers:[]});}
        status.textContent=tr('تعذر التحديث؛ آخر بيانات معروضة قديمة.','Refresh failed; displayed data may be stale.');
      }}
      finally{busy=false;if(alive()&&version!==generation)void refresh();}
    }
    function change(){generation++;etaRevision++;orders=[];nextOffset=null;visibleDrivers=[];etaHosts.clear();for(const record of etaCache.values()){record.plan=null;record.reason='itinerary_changed';}driverRows.replaceChildren();rows.replaceChildren();map?.update({orders:[],drivers:[]});void refresh();}
    citySelect.addEventListener('change',()=>{city=citySelect.value;rotateAt=Date.now();change();});
    activeButton.addEventListener('click',()=>{completed=false;change();});doneButton.addEventListener('click',()=>{completed=true;rotate.checked=false;change();});more.addEventListener('click',()=>void refresh(true));
    fullscreen.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen();map?.resize();}catch{status.textContent=tr('ملء الشاشة غير متاح في هذا المتصفح.','Fullscreen is unavailable in this browser.');}});
    const resize=()=>map?.resize();document.addEventListener('fullscreenchange',resize);
    const timer=setInterval(()=>{if(!alive()||document.hidden)return;for(const driver of visibleDrivers)renderEta(driver);if(rotate.checked&&cities.length&&Date.now()-rotateAt>=30000){city=cities[(cities.indexOf(city)+1)%cities.length];citySelect.value=city;rotateAt=Date.now();change();}else void refresh();},15000);
    const resume=()=>{if(alive()&&!document.hidden){for(const driver of visibleDrivers)renderEta(driver);void refresh();}};document.addEventListener('visibilitychange',resume);window.addEventListener('online',resume);window.addEventListener('offline',resume);
    await refresh();return {destroy(){destroyed=true;generation++;etaCache.clear();etaHosts.clear();visibleDrivers=[];clearInterval(timer);document.removeEventListener('fullscreenchange',resize);document.removeEventListener('visibilitychange',resume);window.removeEventListener('online',resume);window.removeEventListener('offline',resume);map?.destroy();}};
  }
  window.KPSOrderTracking={mount};
})();

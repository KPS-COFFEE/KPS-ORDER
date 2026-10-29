/* Operational pins use only authorized API records; GPS freshness is explicit. */
(() => {
  'use strict';
  const SDK='https://api.mapbox.com/mapbox-gl-js/v3.33.0/';
  let loading;
  function sdk(){
    if(window.mapboxgl)return Promise.resolve(window.mapboxgl);
    if(loading)return loading;
    loading=new Promise((resolve,reject)=>{
      const css=document.createElement('link');css.rel='stylesheet';css.href=SDK+'mapbox-gl.css';document.head.append(css);
      const script=document.createElement('script');script.src=SDK+'mapbox-gl.js';script.async=true;
      script.onload=()=>window.mapboxgl?resolve(window.mapboxgl):reject(new Error('sdk'));
      script.onerror=()=>{script.remove();css.remove();loading=null;reject(new Error('sdk'));};document.head.append(script);
    });return loading;
  }
  function point(value){return value&&typeof value.lat==='number'&&typeof value.lng==='number'&&Number.isFinite(value.lat)&&Number.isFinite(value.lng)&&Math.abs(value.lat)<=90&&Math.abs(value.lng)<=180?{lat:value.lat,lng:value.lng}:null;}
  function pins(data){
    const result=[];
    for(const branch of data.branches||[]){const location=point(branch.location);if(location)result.push({key:'branch:'+branch.id,id:branch.id,kind:'branch',location,label:String(branch.name||branch.id),reference:String(branch.branch_no||'')});}
    for(const order of data.orders||[]){const location=point(order.customer_location||order.customer?.location);if(location)result.push({key:'order:'+order.id,id:order.id,kind:'order',location,label:String(order.order_reference||order.snapshot?.order_reference||order.order_no||order.id),reference:String(order.order_reference||order.order_no||order.id),status:order.delivery_status,late:order.stage_timing?.color==='red'});}
    for(const driver of data.drivers||[]){
      const gps=driver.gps,location=point(gps?.location),updated=Date.parse(gps?.captured_at||gps?.updated_at);
      if(!location||!Number.isFinite(updated)||Date.now()-updated>900000)continue;
      result.push({key:'driver:'+driver.id,id:driver.id,kind:'driver',location,label:String(driver.display_name||driver.name||driver.id),updated_at:gps.captured_at||gps.updated_at,accuracy_m:gps.accuracy_m,movement_state:gps.movement_state,next_destination:gps.next_destination,brand_source:gps.active_order_brand?.source,brand:['kps','enbishan','mixed'].includes(gps.active_order_brand?.id)?gps.active_order_brand.id:'neutral',stale:gps.stale||Date.now()-updated>120000});
    }
    return result;
  }
  async function mount(host,options){
    if(!host)return null;
    let data={orders:options.orders||[],drivers:options.drivers||[],branches:options.branches||[],lang:options.lang||'ar'};
    let customerTrackingEnabled=false;
    let map=null,library=null,destroyed=false,ready=false,fitted=false;const markers=new Map(),trackingLinks=new Map();
    const tr=(ar,en)=>data.lang==='en'?en:ar;
    const state=document.createElement('div');state.className='dispatch-map-state';
    const canvas=document.createElement('div');canvas.className='dispatch-map-canvas';canvas.hidden=true;
    const legend=document.createElement('div');legend.className='dispatch-map-legend';
    const count=document.createElement('span'),fit=document.createElement('button');fit.type='button';fit.className='quiet small';fit.hidden=true;legend.append(count,fit);
    const note=document.createElement('p');note.className='dispatch-map-note';host.classList.add('dispatch-map-host');host.replaceChildren(state,canvas,legend,note);
    const routeBox=document.createElement('div');routeBox.className='dispatch-map-route';routeBox.hidden=true;
    const driverSelect=document.createElement('select'),destinationSelect=document.createElement('select'),routeButton=document.createElement('button'),routeStatus=document.createElement('p');
    driverSelect.setAttribute('aria-label',tr('اختر السائق للمسار','Choose driver for route'));
    destinationSelect.setAttribute('aria-label',tr('اختر وجهة المسار','Choose route destination'));
    routeButton.type='button';routeButton.textContent=tr('حساب المسار والوقت','Calculate route and ETA');routeButton.disabled=true;
    routeStatus.setAttribute('role','status');routeBox.append(driverSelect,destinationSelect,routeButton,routeStatus);host.append(routeBox);
    let publicToken='',routeGeneration=0,routeBusy=false,routeAbort=null,routeChoices=[];
    function clearRoute(){
      routeGeneration++;routeAbort?.abort();routeAbort=null;
      if(map?.getLayer?.('dispatch-route'))map.removeLayer('dispatch-route');
      if(map?.getSource?.('dispatch-route'))map.removeSource('dispatch-route');
      routeStatus.textContent='';
    }
    function selectPlaceholder(select,label){const option=document.createElement('option');option.value='';option.textContent=label;select.replaceChildren(option);}
    function routeDrivers(){
      const selected=driverSelect.value;
      selectPlaceholder(driverSelect,tr('اختر السائق','Choose driver'));
      for(const pin of pins(data).filter(p=>p.kind==='driver')){const option=document.createElement('option');option.value=pin.id;option.textContent=pin.label+(pin.stale?tr(' — موقع قديم',' — stale location'):'');option.disabled=!!pin.stale;driverSelect.append(option);}
      const available=[...driverSelect.options].some(o=>o.value===selected&&!o.disabled);
      driverSelect.value=available?selected:'';
      if(selected&&!available){clearRoute();routeChoices=[];selectPlaceholder(destinationSelect,tr('يلزم موقع حديث للسائق','Fresh driver location required'));routeStatus.textContent=tr('توقف عرض المسار لأن موقع السائق قديم أو غير متاح.','Route cleared because the driver location is stale or unavailable.');}
      routeButton.disabled=routeBusy||!driverSelect.value||!destinationSelect.value;
    }
    selectPlaceholder(destinationSelect,tr('اختر السائق أولًا','Choose driver first'));
    driverSelect.addEventListener('change',async()=>{
      clearRoute();routeChoices=[];selectPlaceholder(destinationSelect,tr('اختر الوجهة','Choose destination'));routeButton.disabled=true;
      const driverId=driverSelect.value,generation=routeGeneration;if(!driverId)return;
      routeStatus.textContent=tr('جاري تحميل الوجهات…','Loading destinations…');
      try{
        const context=await options.api('/api/operations/driver-route-context?driver_id='+encodeURIComponent(driverId));
        if(destroyed||generation!==routeGeneration)return;
        routeChoices=[...((context.tasks||[]).length>=2&&(context.tasks||[]).length<=8?[{value:'plan',plan:true,label:tr('ترتيب التوصيلات المستلمة الظاهرة','Plan visible picked-up deliveries')}]:[]),...(context.tasks||[]).map(t=>({value:'task:'+t.id,task_id:t.id,label:tr('توصيل الطلب ','Deliver order ')+(t.order_no||t.order_id)})),...(context.return_branches||[]).map(b=>({value:'branch:'+b.id,return_branch_id:b.id,label:tr('رجوع مباشر إلى ','Direct return to ')+b.name}))];
        for(const choice of routeChoices){const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;destinationSelect.append(option);}
        routeStatus.textContent=routeChoices.length?tr('اختر طلبًا مستلمًا أو فرع رجوع. وقت الرجوع المباشر لا يشمل تسليم الطلبات المعلقة.','Choose a picked-up order or return branch. Direct return ETA excludes pending deliveries.'):tr('لا توجد وجهة متاحة لهذا السائق.','No available destination for this driver.');
      }catch{if(!destroyed&&generation===routeGeneration)routeStatus.textContent=tr('تعذر تحميل الوجهات. تحقق من حداثة موقع السائق وصلاحياتك.','Could not load destinations. Check driver location freshness and permissions.');}
    });
    destinationSelect.addEventListener('change',()=>{clearRoute();routeButton.disabled=routeBusy||!driverSelect.value||!destinationSelect.value;});
    routeButton.addEventListener('click',async()=>{
      if(routeBusy||!ready)return;
      const choice=routeChoices.find(c=>c.value===destinationSelect.value),driverId=driverSelect.value;
      if(!choice||!pins(data).some(p=>p.kind==='driver'&&p.id===driverId&&!p.stale))return;
      clearRoute();const generation=routeGeneration;routeBusy=true;routeButton.disabled=true;
      routeStatus.textContent=tr('جاري حساب المسار…','Calculating route…');
      try{
        const permit=await options.api(choice.plan?'/api/operations/map-services/plan':'/api/operations/driver-route-context',{method:'POST',key:'route:'+crypto.randomUUID(),body:{driver_id:driverId,...(choice.plan?{}:choice.task_id?{task_id:choice.task_id}:{return_branch_id:choice.return_branch_id})}});
        if(destroyed||generation!==routeGeneration)return;
        const origin=point(permit.origin),stops=choice.plan?permit.stops:[{location:permit.destination}];
        if(!origin||!Array.isArray(stops)||!stops.length||stops.some(t=>!point(t.location)))throw new Error('invalid route');
        routeAbort=new AbortController();
        const coordinates=[origin,...stops.map(t=>t.location)].map(p=>`${p.lng},${p.lat}`).join(';');
        const response=await fetch('https://api.mapbox.com/directions/v5/mapbox/driving/'+coordinates+'?geometries=geojson&overview=full&steps=false&alternatives=false&access_token='+encodeURIComponent(publicToken),{signal:routeAbort.signal,credentials:'omit'});
        if(!response.ok)throw new Error('directions');
        const result=await response.json(),route=result.routes?.[0];
        if(destroyed||generation!==routeGeneration)return;
        if(!pins(data).some(p=>p.kind==='driver'&&p.id===driverId&&!p.stale))throw new Error('stale GPS');
        if(result.code!=='Ok'||!route||!Number.isFinite(route.duration)||route.duration<0||route.geometry?.type!=='LineString'||!Array.isArray(route.geometry.coordinates)||route.geometry.coordinates.length<2)throw new Error('route unavailable');
        map.addSource('dispatch-route',{type:'geojson',data:{type:'Feature',properties:{},geometry:route.geometry}});
        map.addLayer({id:'dispatch-route',type:'line',source:'dispatch-route',layout:{'line-join':'round','line-cap':'round'},paint:{'line-color':'#176b9b','line-width':5,'line-opacity':0.85}});
        const bounds=new library.LngLatBounds();route.geometry.coordinates.forEach(p=>bounds.extend(p));map.fitBounds(bounds,{padding:48,maxZoom:15,duration:0});
        const minutes=Math.max(1,Math.ceil(route.duration/60));
        routeStatus.textContent=(choice.plan?tr('ترتيب مقترح حسب المسافة المباشرة: ','Suggested order by straight-line distance: ')+stops.map(t=>t.order_no||t.id).join(' ← ')+tr(' · إجمالي قيادة تقديري: ',' · Estimated driving total: '):permit.kind==='customer'?tr('الوصول التقريبي للعميل: ','Estimated customer arrival: '):tr('الرجوع المباشر التقريبي للفرع: ','Estimated direct return to branch: '))+minutes+tr(' دقيقة. ',' minutes. ')+tr('حُسب الآن؛ لا يتحدث تلقائيًا. ','Calculated now; does not update automatically. ')+(permit.kind==='return_branch'?tr('لا يشمل تسليم الطلبات المعلقة.','Excludes pending deliveries.'):tr('لا يشمل وقت الاستلام أو التسليم والتوقفات.','Excludes pickup, handoff and stops.'));
      }catch(error){if(!destroyed&&generation===routeGeneration)routeStatus.textContent=error.status===429?tr('انتظر دقيقة أو تحقق من حد المسارات الشهري.','Wait one minute or check the monthly route limit.'):tr('تعذر حساب المسار. يلزم موقع حديث ووجهة متاحة؛ لم تتم إعادة المحاولة تلقائيًا.','Could not calculate route. Fresh location and an available destination are required; no automatic retry was made.');}
      finally{routeBusy=false;if(!destroyed)routeDrivers();}
    });
    // Optional service panel remains usable when base-map tiles fail.
    const services=document.createElement('details'),servicesTitle=document.createElement('summary'),serviceRows=document.createElement('div');
    servicesTitle.textContent=tr('متابعة الموقع والرحلات ونطاقات التوصيل','Location health, trips and delivery zones');services.append(servicesTitle,serviceRows);host.append(services);
    let serviceBusy=false,lastServices=0;
    const serviceText=text=>{const p=document.createElement('p');p.textContent=text;return p;};
    async function refreshServices(){
      if(destroyed||serviceBusy||!services.open||document.hidden||Date.now()-lastServices<30000)return;serviceBusy=true;lastServices=Date.now();
      try{
        const result=await options.api('/api/operations/map-services/alerts');if(destroyed)return;servicesTitle.textContent=tr('متابعة الموقع والرحلات والنطاقات','Location, trips and zones')+' · '+(result.alerts||[]).length+tr(' تنبيه',' alerts');if(!services.open)return;serviceRows.replaceChildren(serviceText(tr('رصد الانحراف يحتاج مسارًا معتمدًا ومرجع طريق متاحًا وموقعًا حديثًا؛ غياب التنبيه لا يثبت الالتزام بالطريق.','Deviation monitoring needs a published plan, an available road reference and fresh GPS; no alert is not proof of adherence.')));
        const names={route_corridor_deviation:tr('انحراف مستمر عن الطريق المخطط — تواصل مع السائق','Sustained departure from planned road — contact driver'),long_stop:tr('توقف طويل — تواصل مع السائق','Long stop — contact driver'),route_progress_deviation:tr('ابتعاد عن الوجهة — راجع المسار','Moving away from destination — review route'),arrival_delayed:tr('تأخر عن الوصول المسجل','Recorded arrival delayed'),near_customer:tr('قريب من العميل — ليس إثبات تسليم','Near customer — not delivery confirmation'),location_missing:tr('لم يصل موقع أثناء التوصيل','No location received during delivery'),location_stale:tr('تحديث الموقع متأخر','Location update delayed'),location_approximate:tr('الموقع تقريبي','Approximate location')};
        for(const item of result.alerts||[]){if(!(data.drivers||[]).some(d=>d.id===item.driver_id))continue;serviceRows.append(serviceText(item.display_name+' · '+(names[item.kind]||item.kind)+tr(' · السبب غير مؤكد',' · Cause unconfirmed')));if(item.shift_id){const handle=document.createElement('button');handle.type='button';handle.textContent=tr('تسجيل السبب والمعالجة','Record reason and action');handle.addEventListener('click',async()=>{const reason=window.prompt(tr('سبب الحالة بعد التواصل مع السائق','Reason after contacting the driver'));if(!reason||reason.trim().length<3)return;const action=window.prompt(tr('الإجراء المتخذ','Action taken'));if(!action||action.trim().length<3)return;handle.disabled=true;try{await options.api('/api/operations/map-services/alerts/resolve',{method:'POST',key:'alert:'+crypto.randomUUID(),brand:item.brand_id,body:{shift_id:item.shift_id,kind:item.kind,reason:reason.trim(),action:action.trim()}});handle.textContent=tr('سُجل الإجراء — التنبيه يبقى حتى زوال سببه','Action recorded — alert remains until condition clears');}catch{handle.disabled=false;handle.textContent=tr('تعذر التسجيل؛ أعد المحاولة','Could not record; retry');}});serviceRows.append(handle);}}
        if(!(result.alerts||[]).length)serviceRows.append(serviceText(tr('لا توجد تنبيهات موقع حاليًا للطلبات المستلمة.','No current location alerts for picked-up orders.')));
        for(const driver of data.drivers||[]){const shiftId=driver.gps?.shift_id||driver.shift_id;if(!shiftId)continue;const button=document.createElement('button');button.type='button';button.textContent=tr('سجل الموقع والرحلات: ','Location and trips: ')+(driver.display_name||driver.name||driver.id);button.addEventListener('click',async()=>{button.disabled=true;try{const [health,trips]=await Promise.all([options.api('/api/operations/map-services/health?shift_id='+encodeURIComponent(shiftId)),options.api('/api/operations/map-services/trips?shift_id='+encodeURIComponent(shiftId))]);if(destroyed)return;const report=document.createElement('div');report.append(serviceText(tr('تحديثات مستلمة: ','Received updates: ')+health.received_count+tr(' · أكبر فجوة بين تحديثين: ',' · Largest gap between updates: ')+(health.largest_received_gap_seconds??'—')+tr(' ثانية. سبب الانقطاع غير معلوم؛ السجل يبدأ من تفعيل الميزة.',' seconds. Cause unknown; evidence starts when this feature is activated.')));for(const trip of trips.trips||[])report.append(serviceText(String(trip.order_no||trip.task_id)+' · '+(trip.delivery_seconds==null?tr('لم يكتمل توقيت الرحلة','Trip duration incomplete'):Math.round(trip.delivery_seconds/60)+tr(' دقيقة',' min'))+' · '+(trip.direct_distance_m==null?'—':trip.direct_distance_m+tr(' متر مسافة مباشرة وليست مسافة القيادة',' m straight-line, not driven distance'))));button.after(report);}catch{button.after(serviceText(tr('تعذر تحميل التقرير أو لا تملك صلاحيته.','Report unavailable or outside your permission.')));}finally{button.disabled=false;}});serviceRows.append(button);}
        const zonesButton=document.createElement('button');zonesButton.type='button';zonesButton.textContent=tr('نطاقات التوصيل والرسوم المعتمدة','Approved delivery zones and fees');zonesButton.addEventListener('click',async()=>{zonesButton.disabled=true;try{const result=await options.api('/api/operations/map-services/zones');if(destroyed)return;const allowed=new Set((data.branches||[]).map(b=>b.id)),zones=(result.zones||[]).filter(z=>allowed.has(z.branch_id));for(const zone of zones){const coverage=zone.coverage,fee=zone.delivery_fee_halalas;serviceRows.append(serviceText(zone.name+' · '+(coverage.type==='radius'?tr('نطاق دائري: ','Radius: ')+coverage.radius_meters+tr(' متر',' m'):tr('نطاق مرسوم معتمد','Approved polygon'))+' · '+(Number.isInteger(fee)?(fee/100).toFixed(2)+tr(' ر.س',' SAR'):tr('رسوم غير مهيأة','Fee not configured'))));}drawZones(zones);}catch{serviceRows.append(serviceText(tr('تعذر تحميل النطاقات.','Zones unavailable.')));}finally{zonesButton.disabled=false;}});serviceRows.append(zonesButton);
      }catch{if(!destroyed){servicesTitle.textContent=tr('متابعة الموقع — تعذر تحديث التنبيهات','Location — alerts could not refresh');serviceRows.replaceChildren(serviceText(tr('تعذر تحديث تنبيهات الموقع؛ لا تعتبر الحالة سليمة.','Location alerts could not refresh; status is unknown.')));}}finally{serviceBusy=false;}
    }
    function drawZones(zones){if(!ready||!map)return;const features=[];for(const zone of zones){const c=zone.coverage;let ring;if(c.type==='polygon'){ring=c.coordinates.map(p=>[p[0],p[1]]);}else{const center=point(c.center);if(!center)continue;const lat=center.lat*Math.PI/180,lon=center.lng*Math.PI/180,d=c.radius_meters/6371000;ring=Array.from({length:65},(_,i)=>{const angle=2*Math.PI*i/64,a=Math.asin(Math.sin(lat)*Math.cos(d)+Math.cos(lat)*Math.sin(d)*Math.cos(angle)),b=lon+Math.atan2(Math.sin(angle)*Math.sin(d)*Math.cos(lat),Math.cos(d)-Math.sin(lat)*Math.sin(a));return [b*180/Math.PI,a*180/Math.PI];});}if(ring.length<3)continue;if(ring[0][0]!==ring.at(-1)[0]||ring[0][1]!==ring.at(-1)[1])ring.push(ring[0]);features.push({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[ring]}});}const geo={type:'FeatureCollection',features};if(map.getSource('dispatch-zones'))map.getSource('dispatch-zones').setData(geo);else{map.addSource('dispatch-zones',{type:'geojson',data:geo});map.addLayer({id:'dispatch-zones',type:'fill',source:'dispatch-zones',paint:{'fill-color':'#d9792b','fill-opacity':0.12}});}}
    services.addEventListener('toggle',()=>{if(services.open){lastServices=0;void refreshServices();}});
    function trackingControls(pin,item){
      const cached=trackingLinks.get(pin.id);if(!cached||cached.expires<Date.now())return;
      const link=document.createElement('a');link.href=cached.path;link.target='_blank';link.rel='noopener noreferrer';link.textContent=tr('فتح رابط تتبع العميل','Open customer tracking link');item.popupRoot.append(link);
      const copy=document.createElement('button');copy.type='button';copy.textContent=tr('نسخ رابط التتبع','Copy tracking link');copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(new URL(cached.path,location.origin).href);copy.textContent=tr('تم النسخ','Copied');}catch{copy.textContent=tr('افتح الرابط وانسخه من المتصفح','Open the link and copy it from the browser');}});item.popupRoot.append(copy);
      const revoke=document.createElement('button');revoke.type='button';revoke.textContent=tr('إلغاء رابط التتبع','Revoke tracking link');revoke.addEventListener('click',async()=>{revoke.disabled=true;try{await options.api('/api/operations/orders/'+encodeURIComponent(pin.id)+'/tracking-link',{method:'DELETE'});trackingLinks.delete(pin.id);refresh();}catch{revoke.disabled=false;}});item.popupRoot.append(revoke);
    }
    const brandLegend=document.createElement('div');brandLegend.className='dispatch-map-brand-legend';host.append(brandLegend);
    let stateKind='loading';
    function labels(){
      const values=pins(data),orders=values.filter(p=>p.kind==='order').length,branches=values.filter(p=>p.kind==='branch').length,drivers=values.filter(p=>p.kind==='driver').length;
      count.textContent=tr(`${drivers} سائقين · ${orders} مواقع طلبات · ${branches} فروع`,`${drivers} drivers · ${orders} order locations · ${branches} branches`);
      note.textContent=tr('مواقع السائقين تتحدث أثناء الشفت عند وصول تحديث من التطبيق. العلامة الرمادية آخر موقع قديم وليست موقعًا مباشرًا.','Driver locations update during a shift when the app sends a fix. Gray markers are stale last-known positions, not live locations.');
      fit.textContent=tr('عرض جميع المواقع','Show all locations');
      brandLegend.replaceChildren();
      for(const [brand,label] of [['kps','KPS'],['enbishan',tr('عنبيشان','Enbishan')],['mixed',tr('طلبات متعددة البراندات','Mixed brands')],['neutral',tr('بدون طلب نشط','No active order')],['stale',tr('موقع قديم','Stale location')]]){const entry=document.createElement('span'),swatch=document.createElement('i');swatch.className='dispatch-driver-swatch brand-'+brand;swatch.setAttribute('aria-hidden','true');entry.append(swatch,document.createTextNode(label));brandLegend.append(entry);}

      const messages={loading:[tr('جاري تجهيز الخريطة','Preparing map'),tr('تحميل إعدادات الخريطة…','Loading map settings…')],missing:[tr('الخريطة تحتاج تفعيل Mapbox','Mapbox activation required'),tr('لم يُضف مفتاح الخرائط لهذا البراند بعد. ستظهر هنا مواقع الطلبات والفروع بعد الربط.','No map key is configured for this brand. Order and branch locations will appear here once connected.')],failed:[tr('تعذر تحميل الخريطة','Map could not load'),tr('راجع اتصال الشبكة وإعدادات مفتاح الخرائط. يمكنك متابعة الطلبات من القائمة.','Check the connection and map key configuration. Orders remain available in the list.')]};
      if(messages[stateKind]){const title=document.createElement('strong'),body=document.createElement('span');title.textContent=messages[stateKind][0];body.textContent=messages[stateKind][1];state.replaceChildren(title,body);}
    }
    function fitAll(){if(!map||!library||!ready)return;const values=pins(data);if(!values.length)return;const bounds=new library.LngLatBounds();values.forEach(pin=>bounds.extend([pin.location.lng,pin.location.lat]));map.fitBounds(bounds,{padding:48,maxZoom:14,duration:0});fitted=true;}
    function populatePopup(pin,item){
        const title=document.createElement('strong');title.textContent=pin.label;item.popupRoot.replaceChildren(title);
        if(pin.kind==='driver'){
          const movementLabels={delivering:tr('يوصل طلبًا','Delivering an order'),awaiting_pickup:tr('بانتظار استلام الطلب','Awaiting order pickup'),returning:tr('متجه للفرع — وجهة مخططة وليست إثبات وصول','Heading to branch — planned destination, not confirmed arrival'),external_enbishan:tr('متجه لعنبيشان — بإفادة السائق','Heading to Enbishan — driver-declared'),awaiting_destination:tr('بانتظار تحديد الوجهة التالية','Awaiting next destination')};
          const movement=document.createElement('p');movement.className='dispatch-driver-movement';movement.textContent=movementLabels[pin.movement_state]||tr('حالة الحركة غير متاحة','Movement state unavailable');item.popupRoot.append(movement);
          const brandInfo=document.createElement('p');brandInfo.textContent=(pin.brand==='kps'?'KPS':pin.brand==='enbishan'?tr('عنبيشان','Enbishan'):pin.brand==='mixed'?tr('طلبات متعددة البراندات','Mixed brands'):tr('بدون طلب نشط','No active order'))+(pin.brand_source==='driver_declared'?tr(' — بإفادة السائق',' — driver-declared'):'');item.popupRoot.append(brandInfo);
          if(pin.next_destination){const next=document.createElement('p');next.textContent=tr('الوجهة المخططة: ','Planned destination: ')+(pin.next_destination.kind==='external_enbishan'?tr('عنبيشان — إفادة السائق','Enbishan — driver declaration'):(pin.next_destination.name||tr('الفرع','Branch')));item.popupRoot.append(next);}
          const detail=document.createElement('p');
          detail.textContent=tr(pin.stale?'آخر موقع قديم · ':'آخر تحديث · ',pin.stale?'Stale last location · ':'Last update · ')+new Date(pin.updated_at).toLocaleTimeString(data.lang==='en'?'en-GB':'ar-SA')+tr(' · الدقة ',' · Accuracy ')+Math.round(pin.accuracy_m||0)+tr(' متر',' m');item.popupRoot.append(detail);
        }
        if(pin.kind==='order'&&options.onSelectOrder){const button=document.createElement('button');button.type='button';button.textContent=tr('فتح الطلب','Open order');button.addEventListener('click',()=>options.onSelectOrder(pin.id));item.popupRoot.append(button);}
        if(customerTrackingEnabled&&pin.kind==='order'&&!['delivered','cancelled'].includes(pin.status)){
          const share=document.createElement('button');share.type='button';share.textContent=tr('إنشاء رابط تتبع للعميل','Create customer tracking link');
          share.addEventListener('click',async()=>{share.disabled=true;try{const result=await options.api('/api/operations/orders/'+encodeURIComponent(pin.id)+'/tracking-link',{method:'POST',body:{}});if(destroyed)return;if(!/^\/delivery-track#[A-Za-z0-9_-]{43}$/.test(result.path))throw new Error('invalid link');trackingLinks.set(pin.id,{path:result.path,expires:Date.now()+4*3600000});refresh();}catch{item.popupRoot.append(serviceText(tr('تعذر إنشاء رابط؛ تحقق من حالة الطلب وصلاحياتك.','Link unavailable; check order status and permission.')));}finally{share.disabled=false;}});item.popupRoot.append(share);trackingControls(pin,item);
        }
    }
    let orderLayerReady=false,orderLayerSignature='',orderLayerPins=new Map(),orderPopup=null,orderPopupId=null,orderPopupItem=null,orderPopupSignature='';
    function syncOrderLayer(current){
      const orders=current.filter(pin=>pin.kind==='order');
      if(!orderLayerReady&&orders.length<80)return current;
      orderLayerPins=new Map(orders.map(pin=>[pin.id,pin]));
      const geo={type:'FeatureCollection',features:orders.map(pin=>({type:'Feature',geometry:{type:'Point',coordinates:[pin.location.lng,pin.location.lat]},properties:{id:pin.id,color:pin.late?'#c43c31':pin.status==='delivered'?'#287b48':['picked_up','on_the_way'].includes(pin.status)?'#2865a7':'#e95b19'}}))};
      const signature=JSON.stringify(geo);
      if(!orderLayerReady){
        map.addSource('dispatch-orders',{type:'geojson',data:geo,cluster:true,clusterMaxZoom:13,clusterRadius:45});
        map.addLayer({id:'dispatch-order-clusters',type:'circle',source:'dispatch-orders',filter:['has','point_count'],paint:{'circle-color':'#e95b19','circle-radius':19,'circle-stroke-width':2,'circle-stroke-color':'#fff'}});
        map.addLayer({id:'dispatch-order-counts',type:'symbol',source:'dispatch-orders',filter:['has','point_count'],layout:{'text-field':['get','point_count_abbreviated'],'text-size':12},paint:{'text-color':'#fff'}});
        map.addLayer({id:'dispatch-order-points',type:'circle',source:'dispatch-orders',filter:['!',['has','point_count']],paint:{'circle-color':['get','color'],'circle-radius':9,'circle-stroke-width':2,'circle-stroke-color':'#fff'}});
        map.on('click','dispatch-order-clusters',event=>{if(destroyed)return;const feature=event.features?.[0];if(feature)map.easeTo({center:feature.geometry.coordinates,zoom:Math.min(map.getZoom()+2,16),duration:200});});
        map.on('click','dispatch-order-points',event=>{
          if(destroyed)return;const pin=orderLayerPins.get(event.features?.[0]?.properties?.id);if(!pin)return;
          orderPopup?.remove();const popupRoot=document.createElement('div');popupRoot.className='dispatch-map-popup';populatePopup(pin,{popupRoot});
          orderPopup=new library.Popup({offset:12,focusAfterOpen:false}).setLngLat([pin.location.lng,pin.location.lat]).setDOMContent(popupRoot).addTo(map);orderPopupId=pin.id;orderPopupItem={popupRoot};orderPopupSignature='';
        });
        orderLayerReady=true;
      }else if(signature!==orderLayerSignature)map.getSource('dispatch-orders').setData(geo);
      orderLayerSignature=signature;
      if(orderPopupId&&!orderLayerPins.has(orderPopupId)){orderPopup?.remove();orderPopup=null;orderPopupId=null;orderPopupItem=null;}
      const selected=orderLayerPins.get(orderPopupId);
      if(selected&&orderPopupItem){
        const cached=trackingLinks.get(selected.id),next=JSON.stringify([data.lang,selected,cached&&cached.expires>=Date.now()?cached:null]);
        if(next!==orderPopupSignature){populatePopup(selected,orderPopupItem);orderPopup.setLngLat([selected.location.lng,selected.location.lat]);orderPopupSignature=next;}
      }
      return current.filter(pin=>pin.kind!=='order');
    }
    function refresh(){
      // Hidden panels and detached screens must not keep rebuilding markers.
      if(destroyed||!host.isConnected||!host.getClientRects().length)return;
      labels();routeDrivers();if(!map||!library||!ready||destroyed)return;
      const current=syncOrderLayer(pins(data)),keys=new Set(current.map(p=>p.key));
      for(const [key,item] of markers)if(!keys.has(key)){item.marker.remove();markers.delete(key);}
      for(const pin of current){
        let item=markers.get(pin.key);
        const cached=trackingLinks.get(pin.id);
        const signature=JSON.stringify([data.lang,pin,cached&&cached.expires>=Date.now()?cached:null]);
        if(item?.signature===signature)continue;
        if(!item){const element=document.createElement('button');element.type='button';
          const popupRoot=document.createElement('div');popupRoot.className='dispatch-map-popup';
          const popup=new library.Popup({offset:20,focusAfterOpen:false}).setDOMContent(popupRoot);
          const marker=new library.Marker({element}).setLngLat([pin.location.lng,pin.location.lat]).setPopup(popup).addTo(map);
          item={element,popupRoot,marker};markers.set(pin.key,item);
        }
        if(!item.position||item.position.lat!==pin.location.lat||item.position.lng!==pin.location.lng){
          item.marker.setLngLat([pin.location.lng,pin.location.lat]);
          item.position=pin.location;
        }
        item.signature=signature;
        // Keep Mapbox's marker/anchor classes: they supply absolute positioning.
        item.element.classList.remove('driver','stale','branch','late','delivered','road','brand-kps','brand-enbishan','brand-mixed','brand-neutral');
        item.element.classList.add('dispatch-map-pin');
        const appearance=pin.kind==='driver'?(pin.stale?['driver','stale']:['driver']):pin.kind==='branch'?['branch']:pin.late?['late']:pin.status==='delivered'?['delivered']:pin.status==='picked_up'||pin.status==='on_the_way'?['road']:[];
        item.element.classList.add(...appearance);
        if(pin.kind==='driver'){
          item.element.classList.add('brand-'+pin.brand);
          const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.classList.add('dispatch-driver-car');
          const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M5 4h14l2 7v9h-3v-3H6v3H3v-9l2-7zm2 2-1.4 5h12.8L17 6H7zm-.5 7a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm11 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z');icon.append(path);
          const name=document.createElement('span');name.className='dispatch-driver-name';name.textContent=pin.label;name.dir='auto';item.element.replaceChildren(icon,name);item.element.title=pin.label;
        }else item.element.textContent=pin.kind==='branch'?pin.reference:'●';
        item.element.setAttribute('aria-label',tr(pin.kind==='driver'?'سائق ':pin.kind==='branch'?'فرع ':'طلب ',pin.kind==='driver'?'Driver ':pin.kind==='branch'?'Branch ':'Order ')+pin.label);
        populatePopup(pin,item);

      }
      if(!fitted)fitAll();
    }
    fit.addEventListener('click',fitAll);labels();void refreshServices();
    const freshnessTimer=setInterval(()=>{if(!destroyed&&!document.hidden){refresh();void refreshServices();}},15000);
    const controller={update(next){if(destroyed)return;data={...data,...next};refresh();},resize(){if(!destroyed){map?.resize();refresh();}},fit:fitAll,destroy(){if(destroyed)return;destroyed=true;removalObserver.disconnect();clearRoute();clearInterval(freshnessTimer);for(const item of markers.values())item.marker.remove();markers.clear();trackingLinks.clear();orderPopup?.remove();orderLayerPins.clear();map?.remove();map=null;}};
    // Navigation replaces #content. Dispose WebGL resources and the polling
    // timer immediately instead of retaining an invisible map until next visit.
    const removalObserver=new MutationObserver(()=>{if(!host.isConnected)controller.destroy();});
    const removalRoot=host.closest('#content')||host.parentElement;
    if(removalRoot)removalObserver.observe(removalRoot,{childList:true});
    try{
      const config=await options.api('/api/operations/map-config');
      if(destroyed||!host.isConnected)return controller;
      if(!config.enabled||config.provider!=='mapbox'||!/^pk\./.test(config.public_token)){stateKind='missing';labels();return controller;}
      customerTrackingEnabled=config.customer_tracking_enabled===true;
      publicToken=config.public_token;
      library=await sdk();if(destroyed||!host.isConnected)return controller;
      if(library.supported&&!library.supported())throw new Error('webgl');
      const first=pins(data)[0];canvas.hidden=false;
      map=new library.Map({container:canvas,accessToken:config.public_token,style:config.style,center:first?[first.location.lng,first.location.lat]:[45.96,28.43],zoom:10,attributionControl:true});
      map.addControl(new library.NavigationControl({showCompass:false}));
      map.on('load',()=>{if(destroyed)return;ready=true;state.hidden=true;fit.hidden=false;routeBox.hidden=false;refresh();map.resize();});
      map.on('error',()=>{if(destroyed)return;stateKind='failed';state.hidden=false;labels();});
    }catch{stateKind='failed';canvas.hidden=true;labels();}
    return controller;
  }
  function replay(host,points,options={}){
    const tr=options.tr||((ar,en)=>ar),valid=points.filter(p=>point(p)&&Number.isFinite(Date.parse(p.captured_at)));
    host.replaceChildren();if(!valid.length){host.textContent=tr('لا توجد نقاط محفوظة.','No recorded points.');return;}
    const title=document.createElement('p');title.textContent=tr('إعادة عرض نقاط GPS المسجلة — الفجوات لا تُوصل كمسار مؤكد','Recorded GPS playback — gaps are not confirmed roads');host.append(title);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 600 300');svg.setAttribute('role','img');svg.setAttribute('aria-label',tr('نقاط مسار GPS','GPS trajectory'));svg.style.width='100%';svg.style.maxHeight='300px';
    const longitudes=valid.map(p=>p.lng),latitudes=valid.map(p=>p.lat),minX=Math.min(...longitudes),minY=Math.min(...latitudes),spanX=Math.max(...longitudes)-minX||.001,spanY=Math.max(...latitudes)-minY||.001;
    const dots=valid.map(p=>{const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('cx',String(20+(p.lng-minX)/spanX*560));dot.setAttribute('cy',String(280-(p.lat-minY)/spanY*260));dot.setAttribute('r','2');dot.setAttribute('fill','#176b9b');svg.append(dot);return dot;});
    const slider=document.createElement('input'),caption=document.createElement('p');slider.type='range';slider.min='0';slider.max=String(valid.length-1);slider.value=slider.max;slider.setAttribute('aria-label',tr('وقت التقاط النقطة','Point capture time'));
    const render=()=>{const index=Number(slider.value);dots.forEach((dot,i)=>{dot.style.opacity=i<=index?'1':'.12';dot.setAttribute('r',i===index?'5':'2');});caption.textContent=valid[index].captured_at+' · '+(index+1)+' / '+valid.length;};slider.addEventListener('input',render);host.append(svg,slider,caption);render();
  }
  window.KPSDispatchMap={mount,pins,replay};
})();

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
      const gps=driver.gps,location=point(gps?.location),updated=Date.parse(gps?.updated_at);
      if(!location||!Number.isFinite(updated)||Date.now()-updated>900000)continue;
      result.push({key:'driver:'+driver.id,id:driver.id,kind:'driver',location,label:String(driver.display_name||driver.name||driver.id),updated_at:gps.updated_at,accuracy_m:gps.accuracy_m,next_destination:gps.next_destination,brand_source:gps.active_order_brand?.source,brand:['kps','enbishan','mixed'].includes(gps.active_order_brand?.id)?gps.active_order_brand.id:'neutral',stale:gps.stale||Date.now()-updated>120000});
    }
    return result;
  }
  async function mount(host,options){
    if(!host)return null;
    let data={orders:options.orders||[],drivers:options.drivers||[],branches:options.branches||[],lang:options.lang||'ar'};
    let map=null,library=null,destroyed=false,ready=false,fitted=false;const markers=new Map();
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
        routeChoices=[...(context.tasks||[]).map(t=>({value:'task:'+t.id,task_id:t.id,label:tr('توصيل الطلب ','Deliver order ')+(t.order_no||t.order_id)})),...(context.return_branches||[]).map(b=>({value:'branch:'+b.id,return_branch_id:b.id,label:tr('رجوع مباشر إلى ','Direct return to ')+b.name}))];
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
        const permit=await options.api('/api/operations/driver-route-context',{method:'POST',key:'route:'+crypto.randomUUID(),body:{driver_id:driverId,...(choice.task_id?{task_id:choice.task_id}:{return_branch_id:choice.return_branch_id})}});
        if(destroyed||generation!==routeGeneration)return;
        const origin=point(permit.origin),destination=point(permit.destination);
        if(!origin||!destination)throw new Error('invalid route');
        routeAbort=new AbortController();
        const coordinates=`${origin.lng},${origin.lat};${destination.lng},${destination.lat}`;
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
        routeStatus.textContent=(permit.kind==='customer'?tr('الوصول التقريبي للعميل: ','Estimated customer arrival: '):tr('الرجوع المباشر التقريبي للفرع: ','Estimated direct return to branch: '))+minutes+tr(' دقيقة. ',' minutes. ')+tr('حُسب الآن؛ لا يتحدث تلقائيًا. ','Calculated now; does not update automatically. ')+(permit.kind==='return_branch'?tr('لا يشمل تسليم الطلبات المعلقة.','Excludes pending deliveries.'):tr('لا يشمل التوقفات الأخرى.','Excludes other stops.'));
      }catch(error){if(!destroyed&&generation===routeGeneration)routeStatus.textContent=error.status===429?tr('انتظر دقيقة أو تحقق من حد المسارات الشهري.','Wait one minute or check the monthly route limit.'):tr('تعذر حساب المسار. يلزم موقع حديث ووجهة متاحة؛ لم تتم إعادة المحاولة تلقائيًا.','Could not calculate route. Fresh location and an available destination are required; no automatic retry was made.');}
      finally{routeBusy=false;if(!destroyed)routeDrivers();}
    });
    const brandLegend=document.createElement('div');brandLegend.className='dispatch-map-brand-legend';host.append(brandLegend);
    let stateKind='loading';
    function labels(){
      const values=pins(data),orders=values.filter(p=>p.kind==='order').length,branches=values.filter(p=>p.kind==='branch').length,drivers=values.filter(p=>p.kind==='driver').length;
      count.textContent=tr(`${drivers} سائقين · ${orders} مواقع طلبات · ${branches} فروع`,`${drivers} drivers · ${orders} order locations · ${branches} branches`);
      note.textContent=tr('مواقع السائقين تتحدث أثناء مشاركة الموقع والصفحة مفتوحة. العلامة الرمادية آخر موقع قديم وليست موقعًا مباشرًا.','Driver locations update while sharing with the page visible. Gray markers are stale last-known positions, not live locations.');
      fit.textContent=tr('عرض جميع المواقع','Show all locations');
      brandLegend.replaceChildren();
      for(const [brand,label] of [['kps','KPS'],['enbishan',tr('عنبيشان','Enbishan')],['mixed',tr('طلبات متعددة البراندات','Mixed brands')],['neutral',tr('بدون طلب نشط','No active order')],['stale',tr('موقع قديم','Stale location')]]){const entry=document.createElement('span'),swatch=document.createElement('i');swatch.className='dispatch-driver-swatch brand-'+brand;swatch.setAttribute('aria-hidden','true');entry.append(swatch,document.createTextNode(label));brandLegend.append(entry);}

      const messages={loading:[tr('جاري تجهيز الخريطة','Preparing map'),tr('تحميل إعدادات الخريطة…','Loading map settings…')],missing:[tr('الخريطة تحتاج تفعيل Mapbox','Mapbox activation required'),tr('لم يُضف مفتاح الخرائط لهذا البراند بعد. ستظهر هنا مواقع الطلبات والفروع بعد الربط.','No map key is configured for this brand. Order and branch locations will appear here once connected.')],failed:[tr('تعذر تحميل الخريطة','Map could not load'),tr('راجع اتصال الشبكة وإعدادات مفتاح الخرائط. يمكنك متابعة الطلبات من القائمة.','Check the connection and map key configuration. Orders remain available in the list.')]};
      if(messages[stateKind]){const title=document.createElement('strong'),body=document.createElement('span');title.textContent=messages[stateKind][0];body.textContent=messages[stateKind][1];state.replaceChildren(title,body);}
    }
    function fitAll(){if(!map||!library||!ready)return;const values=pins(data);if(!values.length)return;const bounds=new library.LngLatBounds();values.forEach(pin=>bounds.extend([pin.location.lng,pin.location.lat]));map.fitBounds(bounds,{padding:48,maxZoom:14,duration:0});fitted=true;}
    function refresh(){
      labels();routeDrivers();if(!map||!library||!ready||destroyed)return;
      const current=pins(data),keys=new Set(current.map(p=>p.key));
      for(const [key,item] of markers)if(!keys.has(key)){item.marker.remove();markers.delete(key);}
      for(const pin of current){
        let item=markers.get(pin.key);
        if(!item){const element=document.createElement('button');element.type='button';
          const popupRoot=document.createElement('div');popupRoot.className='dispatch-map-popup';
          const popup=new library.Popup({offset:20,focusAfterOpen:false}).setDOMContent(popupRoot);
          const marker=new library.Marker({element}).setLngLat([pin.location.lng,pin.location.lat]).setPopup(popup).addTo(map);
          item={element,popupRoot,marker};markers.set(pin.key,item);
        }
        item.marker.setLngLat([pin.location.lng,pin.location.lat]);
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
        const title=document.createElement('strong');title.textContent=pin.label;item.popupRoot.replaceChildren(title);
        if(pin.kind==='driver'){
          const brandInfo=document.createElement('p');brandInfo.textContent=(pin.brand==='kps'?'KPS':pin.brand==='enbishan'?tr('عنبيشان','Enbishan'):pin.brand==='mixed'?tr('طلبات متعددة البراندات','Mixed brands'):tr('بدون طلب نشط','No active order'))+(pin.brand_source==='driver_declared'?tr(' — بإفادة السائق',' — driver-declared'):'');item.popupRoot.append(brandInfo);
          if(pin.next_destination){const next=document.createElement('p');next.textContent=tr('الوجهة المخططة: ','Planned destination: ')+(pin.next_destination.kind==='external_enbishan'?tr('عنبيشان — إفادة السائق','Enbishan — driver declaration'):(pin.next_destination.name||tr('الفرع','Branch')));item.popupRoot.append(next);}
          const detail=document.createElement('p');
          detail.textContent=tr(pin.stale?'آخر موقع قديم · ':'آخر تحديث · ',pin.stale?'Stale last location · ':'Last update · ')+new Date(pin.updated_at).toLocaleTimeString(data.lang==='en'?'en-GB':'ar-SA')+tr(' · الدقة ',' · Accuracy ')+Math.round(pin.accuracy_m||0)+tr(' متر',' m');item.popupRoot.append(detail);
        }
        if(pin.kind==='order'&&options.onSelectOrder){const button=document.createElement('button');button.type='button';button.textContent=tr('فتح الطلب','Open order');button.addEventListener('click',()=>options.onSelectOrder(pin.id));item.popupRoot.append(button);}
      }
      if(!fitted)fitAll();
    }
    fit.addEventListener('click',fitAll);labels();
    const freshnessTimer=setInterval(()=>{if(!destroyed&&!document.hidden)refresh();},15000);
    const controller={update(next){data={...data,...next};refresh();},resize(){map?.resize();},fit:fitAll,destroy(){destroyed=true;clearRoute();clearInterval(freshnessTimer);for(const item of markers.values())item.marker.remove();markers.clear();map?.remove();map=null;}};
    try{
      const config=await options.api('/api/operations/map-config');
      if(destroyed||!host.isConnected)return controller;
      if(!config.enabled||config.provider!=='mapbox'||!/^pk\./.test(config.public_token)){stateKind='missing';labels();return controller;}
      publicToken=config.public_token;
      library=await sdk();if(destroyed||!host.isConnected)return controller;
      if(library.supported&&!library.supported())throw new Error('webgl');
      const first=pins(data)[0];canvas.hidden=false;
      map=new library.Map({container:canvas,accessToken:config.public_token,style:config.style,center:first?[first.location.lng,first.location.lat]:[45.96,28.43],zoom:10,attributionControl:true});
      map.addControl(new library.NavigationControl({showCompass:false}));
      map.on('load',()=>{ready=true;state.hidden=true;fit.hidden=false;routeBox.hidden=false;refresh();map.resize();});
      map.on('error',()=>{stateKind='failed';state.hidden=false;labels();});
    }catch{stateKind='failed';canvas.hidden=true;labels();}
    return controller;
  }
  window.KPSDispatchMap={mount,pins};
})();

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
      result.push({key:'driver:'+driver.id,id:driver.id,kind:'driver',location,label:String(driver.display_name||driver.name||driver.id),updated_at:gps.updated_at,accuracy_m:gps.accuracy_m,stale:gps.stale||Date.now()-updated>120000});
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
    let stateKind='loading';
    function labels(){
      const values=pins(data),orders=values.filter(p=>p.kind==='order').length,branches=values.filter(p=>p.kind==='branch').length,drivers=values.filter(p=>p.kind==='driver').length;
      count.textContent=tr(`${drivers} سائقين · ${orders} مواقع طلبات · ${branches} فروع`,`${drivers} drivers · ${orders} order locations · ${branches} branches`);
      note.textContent=tr('مواقع السائقين تتحدث أثناء مشاركة الموقع والصفحة مفتوحة. العلامة الرمادية آخر موقع قديم وليست موقعًا مباشرًا.','Driver locations update while sharing with the page visible. Gray markers are stale last-known positions, not live locations.');
      fit.textContent=tr('عرض جميع المواقع','Show all locations');
      const messages={loading:[tr('جاري تجهيز الخريطة','Preparing map'),tr('تحميل إعدادات الخريطة…','Loading map settings…')],missing:[tr('الخريطة تحتاج تفعيل Mapbox','Mapbox activation required'),tr('لم يُضف مفتاح الخرائط لهذا البراند بعد. ستظهر هنا مواقع الطلبات والفروع بعد الربط.','No map key is configured for this brand. Order and branch locations will appear here once connected.')],failed:[tr('تعذر تحميل الخريطة','Map could not load'),tr('راجع اتصال الشبكة وإعدادات مفتاح الخرائط. يمكنك متابعة الطلبات من القائمة.','Check the connection and map key configuration. Orders remain available in the list.')]};
      if(messages[stateKind]){const title=document.createElement('strong'),body=document.createElement('span');title.textContent=messages[stateKind][0];body.textContent=messages[stateKind][1];state.replaceChildren(title,body);}
    }
    function fitAll(){if(!map||!library||!ready)return;const values=pins(data);if(!values.length)return;const bounds=new library.LngLatBounds();values.forEach(pin=>bounds.extend([pin.location.lng,pin.location.lat]));map.fitBounds(bounds,{padding:48,maxZoom:14,duration:0});fitted=true;}
    function refresh(){
      labels();if(!map||!library||!ready||destroyed)return;
      const current=pins(data),keys=new Set(current.map(p=>p.key));
      for(const [key,item] of markers)if(!keys.has(key)){item.marker.remove();markers.delete(key);}
      for(const pin of current){
        let item=markers.get(pin.key);
        if(!item){const element=document.createElement('button');element.type='button';
          const popupRoot=document.createElement('div');popupRoot.className='dispatch-map-popup';
          const popup=new library.Popup({offset:20}).setDOMContent(popupRoot);
          const marker=new library.Marker({element}).setLngLat([pin.location.lng,pin.location.lat]).setPopup(popup).addTo(map);
          item={element,popupRoot,marker};markers.set(pin.key,item);
        }
        item.marker.setLngLat([pin.location.lng,pin.location.lat]);
        item.element.className='dispatch-map-pin '+(pin.kind==='driver'?(pin.stale?'driver stale':'driver'):pin.kind==='branch'?'branch':pin.late?'late':pin.status==='delivered'?'delivered':pin.status==='picked_up'||pin.status==='on_the_way'?'road':'');
        item.element.textContent=pin.kind==='branch'?pin.reference:pin.kind==='driver'?'🚗':'●';
        item.element.setAttribute('aria-label',tr(pin.kind==='driver'?'سائق ':pin.kind==='branch'?'فرع ':'طلب ',pin.kind==='driver'?'Driver ':pin.kind==='branch'?'Branch ':'Order ')+pin.label);
        const title=document.createElement('strong');title.textContent=pin.label;item.popupRoot.replaceChildren(title);
        if(pin.kind==='driver'){
          const detail=document.createElement('p');
          detail.textContent=tr(pin.stale?'آخر موقع قديم · ':'آخر تحديث · ',pin.stale?'Stale last location · ':'Last update · ')+new Date(pin.updated_at).toLocaleTimeString(data.lang==='en'?'en-GB':'ar-SA')+tr(' · الدقة ',' · Accuracy ')+Math.round(pin.accuracy_m||0)+tr(' متر',' m');item.popupRoot.append(detail);
        }
        if(pin.kind==='order'&&options.onSelectOrder){const button=document.createElement('button');button.type='button';button.textContent=tr('فتح الطلب','Open order');button.addEventListener('click',()=>options.onSelectOrder(pin.id));item.popupRoot.append(button);}
      }
      if(!fitted)fitAll();
    }
    fit.addEventListener('click',fitAll);labels();
    const freshnessTimer=setInterval(()=>{if(!destroyed&&!document.hidden)refresh();},15000);
    const controller={update(next){data={...data,...next};refresh();},resize(){map?.resize();},destroy(){destroyed=true;clearInterval(freshnessTimer);for(const item of markers.values())item.marker.remove();markers.clear();map?.remove();map=null;}};
    try{
      const config=await options.api('/api/operations/map-config');
      if(destroyed||!host.isConnected)return controller;
      if(!config.enabled||config.provider!=='mapbox'||!/^pk\./.test(config.public_token)){stateKind='missing';labels();return controller;}
      library=await sdk();if(destroyed||!host.isConnected)return controller;
      if(library.supported&&!library.supported())throw new Error('webgl');
      const first=pins(data)[0];canvas.hidden=false;
      map=new library.Map({container:canvas,accessToken:config.public_token,style:config.style,center:first?[first.location.lng,first.location.lat]:[45.96,28.43],zoom:10,attributionControl:true});
      map.addControl(new library.NavigationControl({showCompass:false}));
      map.on('load',()=>{ready=true;state.hidden=true;fit.hidden=false;refresh();map.resize();});
      map.on('error',()=>{stateKind='failed';state.hidden=false;labels();});
    }catch{stateKind='failed';canvas.hidden=true;labels();}
    return controller;
  }
  window.KPSDispatchMap={mount,pins};
})();

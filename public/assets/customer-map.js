/* One lazily created map per page. Moving the pin never commits a location. */
(() => {
  'use strict';
  const SDK='https://api.mapbox.com/mapbox-gl-js/v3.33.0/';
  let sdkPromise;
  function loadSDK(){
    if(window.mapboxgl)return Promise.resolve(window.mapboxgl);
    if(sdkPromise)return sdkPromise;
    sdkPromise=new Promise((resolve,reject)=>{
      const css=document.createElement('link');css.rel='stylesheet';css.href=SDK+'mapbox-gl.css';document.head.append(css);
      const script=document.createElement('script');script.src=SDK+'mapbox-gl.js';script.async=true;
      script.onload=()=>window.mapboxgl?resolve(window.mapboxgl):reject(new Error('sdk'));
      script.onerror=()=>{script.remove();css.remove();sdkPromise=null;reject(new Error('sdk'));};document.head.append(script);
    });return sdkPromise;
  }
  const valid=p=>p&&Number.isFinite(p.lat)&&Number.isFinite(p.lng)&&Math.abs(p.lat)<=90&&Math.abs(p.lng)<=180;
  async function mount({api,brandId,host,getLocation,onConfirm,tr,canOpen}){
    if(!host||!brandId)return;
    let config;try{config=await api('/api/customer-map/config?brand_id='+encodeURIComponent(brandId));}catch{return;}
    if(config.enabled&&config.provider==='google'){return window.KPSCustomerGoogleMap?.mount({config,brandId,host,getLocation,onConfirm,tr,canOpen});}
    if(!config.enabled||config.provider!=='mapbox'||!/^pk\./.test(config.public_token))return;
    const button=document.createElement('button');button.type='button';button.className='button secondary';
    const dialog=document.createElement('dialog');dialog.className='customer-map-dialog';dialog.setAttribute('aria-labelledby','customer-map-title');
    const toolbar=document.createElement('div');toolbar.className='map-toolbar';
    const title=document.createElement('h2');title.id='customer-map-title';
    const close=document.createElement('button');close.type='button';close.className='button quiet';
    const canvas=document.createElement('div');canvas.className='customer-map-canvas';
    const status=document.createElement('p');status.className='map-status';status.setAttribute('role','status');
    const actions=document.createElement('div');actions.className='map-actions';
    const locate=document.createElement('button');locate.type='button';locate.className='button secondary';
    const confirm=document.createElement('button');confirm.type='button';confirm.className='button';confirm.disabled=true;
    toolbar.append(title,close);actions.append(locate,confirm);dialog.append(toolbar,canvas,status,actions);document.body.append(dialog);host.prepend(button);
    let map,marker,draft=null,ready=false,opening=false,generation=0;
    const say=(ar,en)=>status.textContent=tr(ar,en);
    function labels(){button.textContent=tr('حدد موقعك على الخريطة','Choose location on map');title.textContent=tr('حدد مكان التوصيل','Choose delivery location');close.textContent=tr('إغلاق','Close');locate.textContent=tr('استخدام موقعي الحالي','Use my current location');confirm.textContent=tr('تأكيد الموقع وعرض الفروع','Confirm location and show branches');}
    function choose(p){if(!valid(p)||!marker)return;draft={lat:p.lat,lng:p.lng};marker.setLngLat([draft.lng,draft.lat]);confirm.disabled=!ready;say('حرّك الدبوس أو اضغط على مكان آخر، ثم أكد الموقع.','Drag the pin or tap another place, then confirm.');}
    function failure(reason){generation++;ready=false;confirm.disabled=true;locate.disabled=true;if(map){map.remove();map=null;marker=null;}if(reason==='webgl')say('هذا المتصفح لا يدعم عرض الخريطة. استخدم متصفحًا حديثًا أو حدد موقعك من خارج الخريطة.','This browser cannot display the map. Use a current browser or select your location outside the map.');else say('تعذر تحميل الخريطة. أغلقها واستخدم موقعك الحالي أو أدخل الموقع يدويًا.','The map could not load. Close it and use your current location or enter the location manually.');}
    async function open(){
      if(opening||canOpen&&!canOpen())return;opening=true;labels();dialog.showModal();generation++;draft=null;confirm.disabled=true;locate.disabled=!ready;say('جاري تحميل الخريطة…','Loading map…');
      const current=getLocation();
      try{
        const sdk=await loadSDK();if(!dialog.open)return;
        if(!map){
          if(sdk.supported&&!sdk.supported())throw new Error('webgl');
          map=new sdk.Map({container:canvas,accessToken:config.public_token,style:config.style,center:valid(current)?[current.lng,current.lat]:[45.96,28.43],zoom:valid(current)?16:11,attributionControl:true});
          marker=new sdk.Marker({draggable:true,color:'#ee5a16'}).setLngLat(valid(current)?[current.lng,current.lat]:[45.96,28.43]).addTo(map);
          map.addControl(new sdk.NavigationControl({showCompass:false}));
          map.on('load',()=>{ready=true;locate.disabled=false;confirm.disabled=!draft;say('اضغط على مكان التوصيل أو استخدم موقعك الحالي، ثم أكد.','Tap your delivery location or use your current location, then confirm.');});
          map.on('error',failure);
          map.on('click',event=>{generation++;locate.disabled=false;choose(event.lngLat);});
          marker.on('dragend',()=>{generation++;locate.disabled=false;choose(marker.getLngLat());});
        }
        map.resize();if(valid(current)){choose(current);map.jumpTo({center:[current.lng,current.lat],zoom:16});}
        else if(ready)say('اضغط على مكان التوصيل أو استخدم موقعك الحالي، ثم أكد.','Tap your delivery location or use your current location, then confirm.');
      }catch(error){failure(error.message==='webgl'?'webgl':undefined);}finally{opening=false;}
    }
    button.addEventListener('click',open);close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{generation++;draft=null;confirm.disabled=true;locate.disabled=false;button.focus();});
    confirm.addEventListener('click',()=>{if(!ready||!valid(draft)||canOpen&&!canOpen())return;const point={...draft};dialog.close();onConfirm(point.lat,point.lng);});
    locate.addEventListener('click',()=>{if(!navigator.geolocation){say('الموقع غير متاح؛ اختر المكان بالضغط على الخريطة.','Location is unavailable; tap your location on the map.');return;}const request=++generation;locate.disabled=true;say('جاري تحديد موقعك…','Finding your location…');navigator.geolocation.getCurrentPosition(position=>{if(request!==generation||!dialog.open)return;locate.disabled=false;choose({lat:position.coords.latitude,lng:position.coords.longitude});const accuracy=Number(position.coords.accuracy);if(Number.isFinite(accuracy)&&accuracy>0){const meters=Math.ceil(accuracy);say(`دقة موقع الجهاز تقريبًا ${meters} متر. ${accuracy>50?'الدقة منخفضة؛ حرّك الدبوس إلى مدخل المبنى قبل التأكيد.':'تأكد أن الدبوس عند مدخل المبنى، ثم أكد الموقع.'}`,`Device location accuracy is approximately ${meters} metres. ${accuracy>50?'Low accuracy; move the pin to the building entrance before confirming.':'Check the pin is at the building entrance, then confirm.'}`);}map?.jumpTo({center:[position.coords.longitude,position.coords.latitude],zoom:16});},()=>{if(request!==generation||!dialog.open)return;locate.disabled=false;say('تعذر تحديد موقعك؛ اضغط على مكان التوصيل على الخريطة.','Could not locate you; tap the delivery location on the map.');},{enableHighAccuracy:true,timeout:15000,maximumAge:0});});
    new MutationObserver(labels).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});labels();
  }
  window.KPSCustomerMap={mount};
})();

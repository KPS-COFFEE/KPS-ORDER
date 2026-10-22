/* Optional Google raster picker. No SDK request until the visitor opens it. */
(() => {
  'use strict';
  let sdkPromise,authFailed=false,authHookInstalled=false;
  const authListeners=new Set();
  function installAuthHook(){
    if(authHookInstalled)return;authHookInstalled=true;
    const previous=window.gm_authFailure;
    window.gm_authFailure=()=>{authFailed=true;for(const listener of authListeners)listener();if(typeof previous==='function'){try{previous();}catch{}}};
  }
  function loadSDK(browserKey,language){
    installAuthHook();
    if(authFailed)return Promise.reject(new Error('auth'));
    if(window.google?.maps?.Map)return Promise.resolve(window.google.maps);
    if(sdkPromise)return sdkPromise;
    sdkPromise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');script.async=true;
      script.referrerPolicy='strict-origin-when-cross-origin';
      let settled=false;
      const cleanup=()=>{clearTimeout(timeout);authListeners.delete(fail);delete window.__kpsCustomerGoogleReady;};
      const fail=()=>{if(settled)return;settled=true;cleanup();script.remove();sdkPromise=null;reject(new Error('sdk'));};
      const timeout=setTimeout(fail,20000);
      authListeners.add(fail);
      window.__kpsCustomerGoogleReady=()=>{if(settled)return;if(!window.google?.maps?.Map){fail();return;}settled=true;cleanup();resolve(window.google.maps);};
      script.onerror=fail;
      const params=new URLSearchParams({key:browserKey,v:'quarterly',loading:'async',callback:'__kpsCustomerGoogleReady',language:language==='en'?'en':'ar',region:'SA'});
      script.src='https://maps.googleapis.com/maps/api/js?'+params;document.head.append(script);
    });return sdkPromise;
  }
  const valid=p=>p&&Number.isFinite(p.lat)&&Number.isFinite(p.lng)&&Math.abs(p.lat)<=90&&Math.abs(p.lng)<=180;
  const point=value=>typeof value?.toJSON==='function'?value.toJSON():value;
  const same=(a,b)=>valid(a)&&valid(b)&&Math.abs(a.lat-b.lat)<1e-9&&Math.abs(a.lng-b.lng)<1e-9;
  async function mount({config,brandId,host,getLocation,onConfirm,tr,canOpen}){
    if(!host||!brandId||!config?.enabled||config.provider!=='google'||!/^AIza[A-Za-z0-9_-]{35}$/.test(config.browser_key))return;
    const button=document.createElement('button');button.type='button';button.className='button secondary';
    const dialog=document.createElement('dialog');dialog.className='customer-map-dialog customer-google-map-dialog';dialog.setAttribute('aria-labelledby','customer-google-map-title');
    const toolbar=document.createElement('div');toolbar.className='map-toolbar';
    const title=document.createElement('h2');title.id='customer-google-map-title';
    const close=document.createElement('button');close.type='button';close.className='button quiet';
    const canvas=document.createElement('div');canvas.className='customer-map-canvas customer-google-map-frame';
    const surface=document.createElement('div');surface.className='customer-google-map-surface';
    const pin=document.createElement('span');pin.className='customer-google-map-pin';pin.setAttribute('aria-hidden','true');
    canvas.append(surface,pin);
    const status=document.createElement('p');status.className='map-status';status.setAttribute('role','status');
    const actions=document.createElement('div');actions.className='map-actions';
    const locate=document.createElement('button');locate.type='button';locate.className='button secondary';
    const confirm=document.createElement('button');confirm.type='button';confirm.className='button';confirm.disabled=true;
    toolbar.append(title,close);actions.append(locate,confirm);dialog.append(toolbar,canvas,status,actions);document.body.append(dialog);host.prepend(button);
    let map,sdk,draft=null,ready=false,opening=false,generation=0,loadTimer,programmaticTarget=null,moving=false,statusMessage=['',''];
    const say=(ar,en)=>{statusMessage=[ar,en];status.textContent=tr(ar,en);};
    const hint=()=>say('حرّك الخريطة حتى يكون الدبوس عند مدخل المبنى، أو اضغط على المكان، ثم أكد.','Move the map to put the pin at the building entrance, or tap the place, then confirm.');
    function labels(){status.textContent=tr(...statusMessage);button.textContent=tr('حدد موقعك على خرائط Google','Choose location on Google Maps');title.textContent=tr('حدد مكان التوصيل','Choose delivery location');close.textContent=tr('إغلاق','Close');locate.textContent=tr('استخدام موقعي الحالي','Use my current location');confirm.textContent=tr('تأكيد الموقع وعرض الفروع','Confirm location and show branches');surface.setAttribute('aria-label',tr('خريطة مكان التوصيل؛ حرّكها لوضع الدبوس عند مدخل المبنى','Delivery map; move it to put the pin at the building entrance'));}
    function choose(p){if(!valid(p))return;draft={lat:p.lat,lng:p.lng};confirm.disabled=!ready||moving;}
    function moveTo(p,zoom,select=true){if(!map||!valid(p))return;programmaticTarget={...p};map.setCenter(p);map.setZoom(zoom);draft=null;if(select)choose(p);else confirm.disabled=true;}
    function failure(){
      generation++;ready=false;moving=false;draft=null;confirm.disabled=true;locate.disabled=true;clearTimeout(loadTimer);
      if(map){sdk?.event?.clearInstanceListeners(map);map=null;surface.replaceChildren();}
      pin.hidden=true;
      say('تعذر تحميل خرائط Google. أغلقها واستخدم موقعك الحالي أو الصق رابط خرائط جوجل.','Google Maps could not load. Close it and use your current location or paste a Google Maps link.');
    }
    installAuthHook();authListeners.add(failure);
    async function open(){
      if(opening||canOpen&&!canOpen())return;opening=true;labels();dialog.showModal();generation++;draft=null;confirm.disabled=true;locate.disabled=!ready;pin.hidden=true;
      say('جارٍ تحميل الخريطة…','Loading map…');const current=getLocation();
      try{
        sdk=await loadSDK(config.browser_key,document.documentElement.lang);if(!dialog.open)return;if(canOpen&&!canOpen()){dialog.close();return;}if(authFailed)throw new Error('auth');
        if(!map){
          const instance=new sdk.Map(surface,{center:valid(current)?current:{lat:28.43,lng:45.96},zoom:valid(current)?16:11,
            renderingType:sdk.RenderingType?.RASTER||'RASTER',mapTypeId:'roadmap',mapTypeControl:false,streetViewControl:false,
            fullscreenControl:false,clickableIcons:false,gestureHandling:'greedy',keyboardShortcuts:true,zoomControl:true});map=instance;
          loadTimer=setTimeout(()=>{if(map===instance&&!ready)failure();},20000);
          map.addListener('tilesloaded',()=>{if(map!==instance||authFailed)return;clearTimeout(loadTimer);ready=true;pin.hidden=false;locate.disabled=false;confirm.disabled=!valid(draft)||moving;if(!draft)hint();});
          map.addListener('click',event=>{if(!ready||map!==instance)return;const p=point(event.latLng);if(!valid(p))return;generation++;moving=false;locate.disabled=false;moveTo(p,map.getZoom());hint();});
          map.addListener('dragstart',()=>{if(map!==instance)return;generation++;programmaticTarget=null;moving=true;locate.disabled=false;confirm.disabled=true;});
          map.addListener('dragend',()=>{if(map!==instance||!ready)return;moving=false;choose(point(map.getCenter()));hint();});
          map.addListener('center_changed',()=>{if(map!==instance||!ready)return;const p=point(map.getCenter());if(same(p,programmaticTarget))return;programmaticTarget=null;generation++;locate.disabled=false;choose(p);hint();});
          map.addListener('idle',()=>{if(map!==instance)return;programmaticTarget=null;if(moving&&ready){moving=false;choose(point(map.getCenter()));hint();}});
        }
        sdk.event?.trigger(map,'resize');moveTo(valid(current)?current:{lat:28.43,lng:45.96},valid(current)?16:11,!!valid(current));
        if(ready){pin.hidden=false;locate.disabled=false;hint();}
      }catch{failure();}finally{opening=false;}
    }
    button.addEventListener('click',open);close.addEventListener('click',()=>dialog.close());
    // Deliberate touches/keyboard navigation invalidate a still-pending GPS callback.
    surface.addEventListener('pointerdown',()=>{generation++;locate.disabled=!ready;});
    surface.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','-'].includes(event.key))return;generation++;programmaticTarget=null;moving=true;confirm.disabled=true;locate.disabled=!ready;});
    dialog.addEventListener('close',()=>{generation++;draft=null;moving=false;confirm.disabled=true;locate.disabled=!ready;button.focus();});
    confirm.addEventListener('click',()=>{if(!ready||authFailed||!map||!valid(draft)||moving||canOpen&&!canOpen())return;const p={...draft};dialog.close();onConfirm(p.lat,p.lng);});
    locate.addEventListener('click',()=>{
      if(!ready)return;if(!navigator.geolocation){say('الموقع غير متاح؛ حرّك الخريطة لتحديد مكان التوصيل.','Location is unavailable; move the map to choose your delivery location.');return;}
      const request=++generation;locate.disabled=true;say('جارٍ تحديد موقعك…','Finding your location…');
      navigator.geolocation.getCurrentPosition(position=>{
        if(request!==generation||!dialog.open||!ready||authFailed)return;locate.disabled=false;
        const p={lat:position.coords.latitude,lng:position.coords.longitude};if(!valid(p)){hint();return;}
        moving=false;moveTo(p,16);const accuracy=Number(position.coords.accuracy);
        if(Number.isFinite(accuracy)&&accuracy>0){const meters=Math.ceil(accuracy);say(`دقة موقع الجهاز تقريبًا ${meters} متر. ${accuracy>50?'الدقة منخفضة؛ حرّك الخريطة حتى يكون الدبوس عند مدخل المبنى قبل التأكيد.':'تأكد أن الدبوس عند مدخل المبنى، ثم أكد الموقع.'}`,`Device location accuracy is approximately ${meters} metres. ${accuracy>50?'Low accuracy; move the map to put the pin at the building entrance before confirming.':'Check the pin is at the building entrance, then confirm.'}`);}else hint();
      },()=>{if(request!==generation||!dialog.open||!ready)return;locate.disabled=false;say('تعذر تحديد موقعك؛ حرّك الخريطة لتحديد مكان التوصيل.','Could not locate you; move the map to choose your delivery location.');},{enableHighAccuracy:true,timeout:15000,maximumAge:0});
    });
    new MutationObserver(labels).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});labels();
  }
  window.KPSCustomerGoogleMap={mount};
})();

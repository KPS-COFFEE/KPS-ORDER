/* Foreground-only, opt-in location sharing. No coordinates are stored in the browser. */
(() => {
  'use strict';
  function init({api,getUser,tr,host}) {
    let owner=null,shift=null,consent=false,watch=null,sampleTimer=null,pendingWrite=null,generation=0,busy=false,checking=false,lastAttempt=0,lastUpdate=null,message='';
    const button=document.createElement('button'),status=document.createElement('span'),note=document.createElement('small');
    button.type='button';button.className='quiet small';host.className='panel';host.hidden=true;status.setAttribute('role','status');
    host.append(button,status,note);
    function render(){
      host.hidden=getUser()?.role!=='driver';
      button.textContent=consent?tr('إيقاف مشاركة موقعي','Stop sharing my location'):tr('مشاركة موقعي مع مدير التوصيل','Share my location with delivery manager');
      button.disabled=!shift||checking;
      status.textContent=' '+(message||(consent?tr('جارٍ تحديد الموقع…','Finding location…'):shift?tr('مشاركة الموقع متوقفة','Location sharing is off'):tr('مشاركة الموقع متاحة أثناء الشفت المفتوح','Location sharing is available during an open shift')));
      note.textContent=' '+tr('أبقِ الصفحة ظاهرة والشاشة مفتوحة لتحديث موقعك.','Keep this page visible and the screen on to update your location.');
    }
    function halt(){generation++;if(sampleTimer!==null){clearInterval(sampleTimer);sampleTimer=null;}if(watch!==null){navigator.geolocation?.clearWatch(watch);watch=null;}}
    function reset(){halt();owner=null;shift=null;consent=false;lastUpdate=null;message='';render();}
    async function send(position,token){
      if(token!==generation||!consent||!shift||document.hidden||getUser()?.id!==owner||busy||Date.now()-lastAttempt<20000)return;
      const c=position.coords;
      if(!Number.isFinite(c.latitude)||!Number.isFinite(c.longitude)||!Number.isFinite(c.accuracy)||c.accuracy>1000||!Number.isFinite(position.timestamp)||Date.now()-position.timestamp>30000){message=tr('الموقع غير دقيق أو قديم؛ جارٍ المحاولة مجددًا. تأكد من تشغيل الموقع الدقيق.','Location is inaccurate or old; retrying. Enable precise location.');render();return;}
      busy=true;lastAttempt=Date.now();
      try{
        pendingWrite=api('/api/operations/driver-location',{method:'POST',body:{lat:c.latitude,lng:c.longitude,accuracy_m:c.accuracy},key:'driver-location:'+crypto.randomUUID()});
        const result=await pendingWrite;
        if(token===generation&&getUser()?.id===owner){lastUpdate=result.updated_at;message=tr('موقعك يُشارك — آخر تحديث: ','Location shared — last update: ')+new Date(lastUpdate).toLocaleTimeString();}
      }catch(error){if(token===generation){message=tr('تعذر تحديث الموقع؛ تحقق من الاتصال.','Location update failed; check your connection.');if([401,403,409].includes(error.status)){consent=false;shift=null;halt();}}}
      finally{busy=false;pendingWrite=null;render();}
    }
    function start(){
      if(!consent||!shift||watch!==null||document.hidden||getUser()?.id!==owner||!navigator.onLine)return;
      if(!navigator.geolocation){consent=false;message=tr('المتصفح لا يدعم مشاركة الموقع.','This browser does not support location sharing.');render();return;}
      const token=++generation;message='';
      watch=navigator.geolocation.watchPosition(position=>void send(position,token),error=>{
        if(token!==generation)return;
        message=error.code===1?tr('اسمح بالموقع من إعدادات المتصفح ثم اضغط مشاركة موقعي.','Allow location in browser settings, then press Share my location.'):tr('تعذر تحديد الموقع؛ تأكد من تشغيل خدمة الموقع.','Location unavailable; check device location services.');
        if(error.code===1){consent=false;halt();}render();
      },{enableHighAccuracy:true,timeout:15000,maximumAge:0});
      sampleTimer=setInterval(()=>{if(token===generation&&consent&&shift&&!document.hidden&&navigator.onLine&&!busy)navigator.geolocation.getCurrentPosition(position=>void send(position,token),()=>{}, {enableHighAccuracy:true,timeout:15000,maximumAge:0});},25000);render();
    }
    async function refresh(){
      const user=getUser();
      if(user?.role!=='driver'){reset();return;}
      if(owner!==user.id){reset();owner=user.id;}
      if(checking||document.hidden)return;
      checking=true;render();const who=owner;
      try{
        const result=await api('/api/shifts/current');
        if(getUser()?.id!==who||owner!==who)return;
        const next=(result.shifts||[]).find(s=>s.user_id===who&&s.kind==='driver'&&s.status==='open');
        if(shift&&shift!==next?.id){consent=false;halt();message='';}
        shift=next?.id||null;if(!shift)halt();else start();
      }catch{halt();message=tr('تعذر التحقق من الشفت؛ مشاركة الموقع متوقفة مؤقتًا.','Could not verify the shift; location sharing is paused.');}
      finally{checking=false;render();}
    }
    button.addEventListener('click',async()=>{
      if(consent){consent=false;halt();message='';render();try{if(pendingWrite)await pendingWrite.catch(()=>{});await api('/api/operations/driver-location',{method:'DELETE',body:{},key:'driver-location-stop:'+crypto.randomUUID()});}catch{message=tr('توقف الإرسال؛ آخر موقع سيظهر كغير محدّث.','Sending stopped; the last location will become stale.');render();}return;}
      if(!shift)return;consent=true;lastAttempt=0;start();
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden){halt();message=tr('مشاركة الموقع متوقفة مؤقتًا والصفحة بالخلفية.','Location sharing is paused while the page is in the background.');render();}else void refresh();});
    window.addEventListener('pagehide',halt);
    window.addEventListener('pageshow',()=>void refresh());
    window.addEventListener('offline',()=>{halt();message=tr('انقطع الاتصال؛ مشاركة الموقع متوقفة مؤقتًا.','Offline; location sharing is paused.');render();});
    window.addEventListener('online',()=>void refresh());
    setInterval(()=>void refresh(),30000);
    return {refresh,reset};
  }
  window.KPSDriverLocation={init};
})();

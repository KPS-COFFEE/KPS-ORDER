'use strict';
window.KPSDriverChecks={init(ctx){
 const {api,mutation,tr,esc,modal,closeModal}=ctx;
 const host=document.querySelector('#driver-checks');let busy=false,identity='',offset=0;
 const title=k=>k==='fuel'?tr('تحديث البنزين','Fuel update'):tr('قراءة العداد','Odometer reading');
 const levels=()=>[[100,tr('فل','Full')],[75,tr('ثلاثة أرباع','Three quarters')],[50,tr('نصف','Half')],[25,tr('ربع','Quarter')],[0,tr('أقل من ربع','Less than a quarter')]];
 const fuel=v=>levels().find(x=>x[0]===v)?.[1]||'—';
 const when=v=>v?new Intl.DateTimeFormat(ctx.getLang()==='en'?'en-GB':'ar-SA-u-ca-gregory',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—';
 const canManage=()=>['owner','admin'].includes(ctx.getUser()?.role)&&ctx.can('vehicle_maintenance_manage');
 const status=s=>({pending:tr('بانتظار السائق','Waiting for driver'),submitted:tr('بانتظار اعتمادك','Awaiting your approval'),approved:tr('معتمد','Approved'),rejected:tr('مرفوض','Rejected'),cancelled:tr('ملغى','Cancelled'),expired:tr('انتهت المهلة','Expired'),stale:tr('تغير الشفت أو السيارة','Shift or vehicle changed')})[s]||s;
 function tick(){
  host.querySelectorAll('[data-check-expiry]').forEach(el=>{
   const left=Math.ceil((Number(el.dataset.checkExpiry)-Date.now()-offset)/1000);
   if(left<=0){el.remove();return;}
   const time=el.querySelector('[data-check-time]');if(time)time.textContent=Math.floor(left/60)+':'+String(left%60).padStart(2,'0');
  });
 }
 async function refresh(){
  const user=ctx.getUser(),key=(user?.id||'')+':'+ctx.getLang();
  if(identity!==key){host.replaceChildren();identity=key;}
  if(!user){host.replaceChildren();return;}
  if(canManage()){
   if(!host.querySelector('[data-check-open]'))host.innerHTML=`<section class="panel"><button type="button" data-check-open>${esc(tr('طلبات العداد والبنزين — إجراء مؤقت','Odometer & fuel requests — temporary'))}</button><p class="muted">${esc(tr('لسواقين تحددهم، لمدة 20 دقيقة. اعتماد البنزين لهذا الشفت والسيارة فقط.','Selected drivers, for 20 minutes. Fuel approval applies only to this shift and vehicle.'))}</p></section>`;
   return;
  }
  if(user.role!=='driver'){host.replaceChildren();return;}
  if(busy||document.hidden||!navigator.onLine)return;busy=true;
  try{
   const data=await api('/api/driver-checks');if(identity!==key||ctx.getUser()?.id!==user.id)return;
   offset=Date.parse(data.server_time)-Date.now();const ids=new Set(data.requests.map(r=>r.id));
   host.querySelectorAll('[data-check-request]').forEach(el=>{if(!ids.has(el.dataset.checkRequest))el.remove();});
   for(const r of data.requests){
    if([...host.querySelectorAll('[data-check-request]')].some(el=>el.dataset.checkRequest===r.id))continue;
    const input=r.kind==='odometer'?`<input name="value" type="number" min="0" max="9999999" step="1" inputmode="numeric" required>`:`<select name="value" required><option value="">${esc(tr('اختر المستوى الحقيقي','Choose the actual level'))}</option>${levels().map(([v,t])=>`<option value="${v}">${esc(t)}</option>`).join('')}</select>`;
    host.insertAdjacentHTML('beforeend',`<section class="panel" data-check-request="${esc(r.id)}" data-check-expiry="${Date.parse(r.expires_at)}"><p role="alert"><strong>${esc(tr('طلب جديد من الإدارة','New request from management'))}</strong> · ${esc(r.plate)}</p><form data-check-reply="${esc(r.id)}"><label>${esc(r.kind==='fuel'?tr('كم مستوى البنزين الآن؟','What is the fuel level now?'):tr('اكتب عداد الكيلو الحالي','Enter the current odometer reading'))}${input}</label><p>${esc(tr('الوقت المتبقي','Time remaining'))}: <bdi data-check-time></bdi></p><p class="muted">${esc(tr('أرسل القراءة فقط؛ لا تقفل الشفت ولا تسجل تعبئة مرة ثانية.','Send the reading only; do not close the shift or record fuel again.'))}</p><p class="form-error" role="alert"></p><button type="submit">${esc(tr('إرسال لفوزي للاعتماد','Send for approval'))}</button></form></section>`);
   }
   tick();
  }catch(e){ctx.error(e.message);}finally{busy=false;}
 }
 host.addEventListener('submit',async e=>{
  const f=e.target.closest('[data-check-reply]');if(!f)return;e.preventDefault();const b=f.querySelector('button'),error=f.querySelector('.form-error');error.textContent='';
  try{await mutation('/api/driver-checks/'+encodeURIComponent(f.dataset.checkReply)+'/reply',{value:Number(new FormData(f).get('value'))},b);f.closest('[data-check-request]').remove();await refresh();}catch(x){error.textContent=x.message;}
 });
 async function review(r){
  const odo=r.kind==='odometer';
  modal(tr('مراجعة رد السائق','Review driver response'),`<form><p><strong>${esc(r.display_name)}</strong> · ${esc(r.plate)}</p><p>${esc(title(r.kind))}: <strong>${esc(odo?r.response.value+' '+tr('كم','km'):fuel(r.response.value))}</strong></p>${odo?`<label>${esc(tr('القراءة المعتمدة','Approved reading'))}<input name="value" type="number" min="0" max="9999999" step="1" value="${esc(r.response.value)}" required></label>`:`<p>${esc(tr('اعتماد هذه الحالة يرفع تعليق البنزين فقط للشفت والسيارة الحالية. الاستراحة والإيقاف اليدوي لا يتغيران.','This approval clears only the fuel hold for the current shift and vehicle. Breaks and manual pauses remain unchanged.'))}</p>`}<label>${esc(tr('سبب التعديل أو ملاحظة الاعتماد','Correction reason or approval note'))}<input name="reason" maxlength="500"></label><p class="form-error" role="alert"></p><button type="submit">${esc(odo?tr('اعتماد القراءة','Approve reading'):tr('اعتماد والسماح باستقبال الطلبات','Approve fuel readiness'))}</button></form>`,async fd=>{
   await mutation('/api/driver-checks/'+encodeURIComponent(r.id)+'/decision',{decision:'approved',...(odo?{value:Number(fd.get('value'))}:{}),reason:String(fd.get('reason')||'')});await manage();
  });
 }
 async function correct(d){
  modal(tr('تعديل قراءة العداد','Correct odometer reading'),`<form><p>${esc(d.name)} · ${esc(d.plate)}</p><p>${esc(tr('القراءة السابقة','Previous reading'))}: ${esc(d.odometer_km??'—')}</p><label>${esc(tr('القراءة الصحيحة الآن','Correct current reading'))}<input name="value" type="number" min="0" max="9999999" step="1" required></label><label>${esc(tr('سبب التعديل — يظهر في السجل','Reason — retained in history'))}<input name="reason" minlength="3" maxlength="500" required></label><p class="muted">${esc(tr('تُحفظ القراءات الأصلية. تصحيح قراءة أقل يستبعد مسافة هذه الفترة من الحساب حتى مراجعتها.','Original readings are retained. A downward correction excludes this interval from mileage calculations pending review.'))}</p><p class="form-error" role="alert"></p><button type="submit">${esc(tr('حفظ التصحيح','Save correction'))}</button></form>`,async fd=>{
   await mutation('/api/driver-checks/odometer/correct',{driver_id:d.id,value:Number(fd.get('value')),reason:String(fd.get('reason')),expected_revision:d.revision});await manage();
  });
 }
 async function manage(){
  const data=await api('/api/driver-checks');
  modal(tr('طلبات العداد والبنزين — إجراء مؤقت','Odometer & fuel requests — temporary'),`<form id="check-send"><p>${esc(tr('اختر السواقين ثم نوع الطلب. يختفي الطلب بعد الرد أو بعد 20 دقيقة. الرد وحده لا يغيّر الجاهزية حتى تعتمد أنت.','Select drivers and request type. The request disappears after replying or after 20 minutes. A reply does not change readiness until you approve it.'))}</p><label>${esc(tr('نوع الطلب','Request type'))}<select name="kind"><option value="odometer">${esc(tr('طلب قراءة العداد','Request odometer reading'))}</option><option value="fuel">${esc(tr('طلب تحديث البنزين','Request fuel update'))}</option></select></label><fieldset><legend>${esc(tr('السائقون ذوو الشفت المفتوح','Drivers with an open shift'))}</legend>${data.drivers.map(d=>`<label><input type="checkbox" name="driver_ids" value="${esc(d.id)}"> ${esc(d.name)} · ${esc(d.plate)} ${d.awaiting_refuel?'· '+esc(tr('بانتظار البنزين','Awaiting fuel')):''}</label>`).join('')||esc(tr('لا يوجد سائق بشفت مفتوح ضمن نطاقك.','No drivers with an open shift in your scope.'))}</fieldset><p class="form-error" role="alert"></p><button type="submit">${esc(tr('إرسال الطلب للمحددِين','Send to selected drivers'))}</button></form><hr><button type="button" data-check-refresh>${esc(tr('تحديث الردود','Refresh responses'))}</button><h3>${esc(tr('الردود وحالة الطلبات','Responses and request status'))}</h3><div class="stack">${data.requests.map(r=>`<section class="panel"><strong>${esc(r.display_name)}</strong> · ${esc(r.plate)}<p>${esc(title(r.kind))} · ${esc(status(r.ui_status))} · ${esc(when(r.created_at))}</p>${r.response?`<p>${esc(tr('رد السائق','Driver response'))}: ${esc(r.kind==='fuel'?fuel(r.response.value):r.response.value)}</p>`:''}${r.status==='approved'&&r.kind==='odometer'?`<p>${esc(tr('القراءة المعتمدة','Approved reading'))}: ${esc(r.decision?.value??r.response?.value)}</p>`:''}${r.decision?.reason?`<p>${esc(r.decision.reason)}</p>`:''}${r.ui_status==='submitted'?`<button type="button" data-check-review="${esc(r.id)}">${esc(tr('مراجعة واعتماد','Review and approve'))}</button> <button type="button" class="quiet" data-check-reject="${esc(r.id)}">${esc(tr('رفض','Reject'))}</button>`:r.ui_status==='pending'?`<button type="button" class="quiet" data-check-cancel="${esc(r.id)}">${esc(tr('إلغاء الطلب','Cancel request'))}</button>`:''}</section>`).join('')||esc(tr('لا توجد طلبات بعد.','No requests yet.'))}</div><h3>${esc(tr('تصحيح العداد','Odometer corrections'))}</h3>${data.drivers.map(d=>`<section class="panel"><p>${esc(d.name)} · ${esc(d.plate)} · ${esc(d.odometer_km??'—')} ${esc(tr('كم','km'))}</p>${d.checkpoint?`<p>${esc(tr('تم تحديث القراءة','Reading updated'))}: ${esc(d.checkpoint.previous_odometer_km??'—')} ← ${esc(d.odometer_km)} · ${esc(when(d.checkpoint.recorded_at))} · ${esc(d.checkpoint.reason||'')}</p>`:''}<button type="button" class="quiet" data-check-correct="${esc(d.id)}">${esc(tr('تعديل قراءة العداد','Correct odometer reading'))}</button></section>`).join('')}`,async fd=>{
   const ids=fd.getAll('driver_ids');if(!ids.length)throw new Error(tr('حدد سائقًا واحدًا على الأقل.','Select at least one driver.'));
   await mutation('/api/driver-checks',{driver_ids:ids,kind:fd.get('kind')});await manage();
  });
  const body=document.querySelector('#modal-body');body.onclick=async e=>{
   const b=e.target.closest('[data-check-review],[data-check-reject],[data-check-cancel],[data-check-refresh],[data-check-correct]');if(!b)return;b.disabled=true;
   try{
    if(b.hasAttribute('data-check-refresh'))await manage();
    else if(b.dataset.checkReview)await review(data.requests.find(r=>r.id===b.dataset.checkReview));
    else if(b.dataset.checkCorrect)await correct(data.drivers.find(d=>d.id===b.dataset.checkCorrect));
    else {const id=b.dataset.checkReject||b.dataset.checkCancel;await mutation('/api/driver-checks/'+encodeURIComponent(id)+'/decision',{decision:b.dataset.checkReject?'rejected':'cancelled'});await manage();}
   }catch(x){ctx.error(x.message);}finally{if(b.isConnected)b.disabled=false;}
  };
 }
 host.addEventListener('click',e=>{if(e.target.closest('[data-check-open]'))manage().catch(e=>ctx.error(e.message));});
 setInterval(()=>{tick();if(!ctx.getUser())host.replaceChildren();},1000);
 setInterval(refresh,10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
 return {refresh};
}};

/* Same-app rehearsal namespace. Never calls production order or shift writes. */
(() => {
  'use strict';
  let timer=null;
  window.KPSMorningTrial={open:async function(ctx){
    clearInterval(timer);timer=null;
    const {api,mutation,tr,esc,datetime,user,authorize,getUser,onError}=ctx;
    const root=document.querySelector('#content');
    const owner=user.role==='owner';
    let trial=null,printing=null,busy=false,fallbackReason='';
    const requested=new URLSearchParams(location.search).get('trial_id');
    const notice=()=>`<section class="notice warn" role="note"><strong>${esc(tr('تجربة معزولة — ليست طلبات بيع ولا شفتات فعلية','Isolated rehearsal — no sales orders or actual shifts'))}</strong><p>${esc(tr('فرع 2 بحفر الباطن فقط. لا تتغير خدمة العملاء أو شفتات المساء، ولا تسجل مبالغ أو توصيلًا فعليًا.','Branch 2 in Hafar only. Customer service and evening shifts stay unchanged. No money or physical delivery is recorded.'))}</p></section>`;
    const title=tr('تجربة الصباح — فرع 2','Morning rehearsal — Branch 2');
    document.querySelector('#page-heading').innerHTML=`<div><h1>${esc(title)}</h1><p>${esc(tr('مدة التجربة 20 دقيقة. كل موظف يدخل بحسابه الموجود.','The rehearsal lasts 20 minutes. Each employee uses their existing account.'))}</p></div>`;
    document.querySelector('#navigation').innerHTML=`<a class="button quiet" href="/ops">${esc(tr('العودة للتشغيل','Return to operations'))}</a>`;
    document.querySelector('#scope-note').textContent=tr('هذه الشاشة للتجربة المعزولة فقط.','This screen is for the isolated rehearsal only.');
    document.querySelector('#session-label').textContent=`${user.display_name||''} · ${title}`;
    document.querySelector('#driver-checks')?.replaceChildren();
    document.querySelector('#operational-alerts')?.replaceChildren();
    const statusLabel=value=>({start:tr('بدء التجربة','Rehearsal started'),accept:tr('قبول تجريبي','Rehearsal accepted'),auto_assign:tr('أُسند تلقائيًا للسائق المحدد — محاكاة','Automatically assigned to selected driver — simulated'),assign:tr('إسناد تجريبي','Rehearsal assigned'),driver_accept:tr('قبول السائق التجريبي','Rehearsal driver acceptance'),close:tr('إقفال التجربة','Rehearsal closed'),stop:tr('إيقاف التجربة','Rehearsal stopped'),queued:tr('في انتظار الإرسال للطابعة','Queued for printer'),submitted:tr('أُرسل للطابعة — تأكد من الورقة','Sent to printer — verify paper'),failed:tr('تعذّر إرسال الطباعة','Print dispatch failed'),cancelled:tr('أُلغي أمر الاختبار','Test job cancelled'),new:tr('طلب وهمي جديد','New fictional order'),accepted:tr('مقبول — أُرسل أمر طباعة الاختبار','Accepted — test print queued'),preparing:tr('جارٍ تحضير الطلب التجريبي','Preparing fictional order'),picked_up:tr('استلم السائق الطلب التجريبي','Driver picked up fictional order'),ready:tr('تم التجهيز التجريبي','Rehearsal preparation ready'),assigned:tr('أُرسل للسائق تجريبيًا','Assigned to trial driver'),driver_accepted:tr('قبله السائق تجريبيًا','Driver accepted rehearsal'),delivered:tr('اكتمل التوصيل التجريبي','Rehearsal delivery complete'),open:tr('مفتوحة','Open'),closed:tr('مقفلة','Closed'),stopped:tr('متوقفة','Stopped'),expired:tr('انتهت المدة','Expired')})[value]||tr('حالة التجربة','Rehearsal state');
    const actorRole=()=>owner?'owner':Object.entries(trial?.participants||{}).find(([,person])=>person.id===user.id)?.[0];
    function active(){return trial?.status==='open'&&Date.parse(trial.expires_at)>Date.now();}
    function render(){
      if(!trial){root.innerHTML=notice()+`<section class="panel"><p>${esc(tr('لا توجد تجربة متاحة لحسابك. ينتظر الموظفون بدء المالك للتجربة.','No rehearsal is available to your account. Staff should wait for the owner to start it.'))}</p></section>`;return;}
      const role=actorRole(),state=trial.order?.status,actions=[];
      if(active()){
        if(state==='new'&&['manager','preparer'].includes(role))actions.push(['accept',role==='manager'?tr('قبول بدل الفرع — تجربة','Accept for branch — rehearsal'):tr('قبول الطلب — تجربة','Accept order — rehearsal')]);
        if(state==='driver_accepted'&&['manager','preparer'].includes(role))actions.push(['preparing',role==='manager'?tr('تحضير الطلب بدل المحضّر — تجربة','Prepare on behalf of preparer — rehearsal'):tr('تحضير الطلب — تجربة','Prepare order — rehearsal')]);
        if(state==='preparing'&&['manager','preparer'].includes(role))actions.push(['ready',role==='manager'?tr('تم التجهيز بدل المحضّر — تجربة','Mark ready on behalf of preparer — rehearsal'):tr('تم التجهيز — تجربة','Mark ready — rehearsal')]);
        if(state==='assigned'&&role==='driver')actions.push(['driver_accept',tr('قبول التوصيل التجريبي','Accept rehearsal delivery')]);
        if(state==='ready'&&role==='driver')actions.push(['picked_up',tr('استلام الطلب — محاكاة فقط','Pick up order — simulation only')]);
        if(state==='picked_up'&&role==='driver')actions.push(['delivered',tr('تم التوصيل — محاكاة فقط','Delivered — simulation only')]);
        if(state==='delivered'&&['manager','owner'].includes(role))actions.push(['close',tr('إقفال التجربة وعرض التقرير','Close rehearsal and view report')]);
      }
      if(owner&&trial.status==='open')actions.push(['stop',tr('إيقاف التجربة','Stop rehearsal')]);
      const label=active()?statusLabel(trial.status):statusLabel(trial.status==='open'?'expired':trial.status);
      const people=Object.entries(trial.participants||{}).map(([key,person])=>`<p>${esc(({manager:tr('المدير','Manager'),preparer:tr('المحضّر','Preparer'),driver:tr('السائق','Driver')})[key]||key)}: <strong>${esc(person.display_name)}</strong></p>`).join('');
      const link='/ops?morning_trial=1&trial_id='+encodeURIComponent(trial.id);
      const events=(trial.events||[]).map(event=>`<li>${esc(datetime(event.at||event.created_at))} · ${esc(statusLabel(event.action||event.type))}${event.actor_name?' · '+esc(event.actor_name):''}${event.reason?' · '+esc(tr('إنابة عن المحضّر: ','On behalf of preparer: '))+esc(event.reason):''}</li>`).join('');
      root.innerHTML=notice()+`<section class="panel"><h2>${esc(tr('حالة التجربة','Rehearsal status'))}: ${esc(label)}</h2><p>${esc(tr('تنتهي في','Expires at'))}: <time>${esc(datetime(trial.expires_at))}</time></p>${people}<p><a href="${esc(link)}" id="morning-trial-link">${esc(tr('رابط التجربة — كل موظف يدخل بحسابه','Rehearsal link — each employee signs in with their account'))}</a></p><p class="code" dir="ltr">${esc(trial.order?.id)}</p><h3 id="morning-trial-order-status" data-state="${esc(state)}">${esc(statusLabel(state))}</h3><p>${esc(tr('طلب تجريبي — لا يتم التجهيز الفعلي','Fictional trial order — do not physically prepare'))}</p>${active()&&role==='manager'&&['driver_accepted','preparing'].includes(state)?`<label>${esc(tr('سبب التجهيز بدل المحضّر المتأخر','Reason for preparing on behalf of delayed preparer'))}<input id="morning-trial-fallback-reason" minlength="3" maxlength="500" value="${esc(fallbackReason)}" ${busy?'disabled':''} required></label>`:''}<div class="actions">${actions.map(([action,label])=>`<button type="button" data-trial-action="${action}" ${busy?'disabled':''}>${esc(label)}</button>`).join('')}<button type="button" id="morning-trial-refresh">${esc(tr('تحديث','Refresh'))}</button></div><p class="form-error" role="alert" id="morning-trial-error"></p></section><section class="panel"><h2>${esc(tr('طباعة الاختبار','Test printing'))}</h2><p id="morning-trial-print">${esc(printing?statusLabel(printing.status):tr('لم يُرسل أمر طباعة بعد','No print job sent yet'))}</p><p>${esc(tr('الورقة تحمل علامة تجربة، وليست فاتورة بيع. حالة الإرسال لا تثبت خروج الورقة؛ تأكد من طابعة فرع 2.','The paper is marked as a rehearsal and is not a sales invoice. Dispatch status does not prove paper output; check the Branch 2 printer.'))}</p></section><section class="panel"><h2>${esc(tr('تقرير خطوات التجربة','Rehearsal event report'))}</h2><p>${esc(tr('الأثر المالي: لا يوجد','Financial effect: none'))}</p><ol>${events||`<li>${esc(tr('بانتظار أول إجراء','Waiting for the first action'))}</li>`}</ol></section>`;
      root.querySelector('#morning-trial-fallback-reason')?.addEventListener('input',event=>{fallbackReason=event.target.value;});
      root.querySelectorAll('[data-trial-action]').forEach(button=>button.addEventListener('click',()=>act(button.dataset.trialAction)));
      root.querySelector('#morning-trial-refresh').addEventListener('click',()=>refresh().catch(showError));
      if(owner&&!active()){const button=document.createElement('button');button.type='button';button.className='quiet';button.id='morning-trial-new';button.textContent=tr('تجربة جديدة','New rehearsal');button.onclick=()=>{clearInterval(timer);trial=null;setup().catch(showError);};root.querySelector('.actions').append(button);}
    }
    function showError(error){const host=root.querySelector('#morning-trial-error');if(host)host.textContent=error.message;else onError(error.message);}
    function adopt(data){if(trial?.id!==data.trial?.id)fallbackReason='';trial=data.trial||null;if(trial&&(trial.simulated!==true||trial.financial_effect!==false))throw new Error(tr('تعذر إثبات عزل هذه التجربة؛ لم يتم تنفيذ أي إجراء.','Rehearsal isolation could not be verified; no action was performed.'));printing=data.print_job||null;render();}
    async function refresh(){if(!getUser()||busy)return;const data=await api(trial?'/api/morning-trial/'+encodeURIComponent(trial.id):'/api/morning-trial/current');adopt(data);}
    async function act(action){const ownerStop=owner&&action==='stop'&&trial?.status==='open';if(busy||(!active()&&!ownerStop))return;let failure=null;const fallback=actorRole()==='manager'&&['preparing','ready'].includes(action);const reason=fallback?(root.querySelector('#morning-trial-fallback-reason')?.value||fallbackReason).trim():'';if(fallback)fallbackReason=reason;if(fallback&&reason.length<3){showError(new Error(tr('اكتب سبب التجهيز بدل المحضّر المتأخر.','Enter the reason for preparing on behalf of the delayed preparer.')));return;}busy=true;render();try{adopt(await mutation('/api/morning-trial/'+encodeURIComponent(trial.id)+'/action',{action,...(fallback?{reason}:{})}));}catch(error){failure=error;}finally{busy=false;render();if(failure)showError(failure);}}
    async function setup(){
      const data=await api('/api/morning-trial/setup');
      const branch=data.branch;
      if(!branch)throw new Error(tr('فرع 2 غير جاهز للتجربة.','Branch 2 is not ready for rehearsal.'));
      const options=rows=>(rows||[]).map(person=>`<option value="${esc(person.id)}">${esc(person.display_name)}${person.phone?' · '+esc(person.phone):''}</option>`).join('');
      root.innerHTML=notice()+`<form class="panel form-columns" id="morning-trial-start"><h2 class="full">${esc(tr('بدء تجربة جديدة','Start a rehearsal'))}</h2><p class="full">${esc(branch.name)} · ${esc(tr('فرع 2 — حفر الباطن','Branch 2 — Hafar'))}</p><label>${esc(tr('مدير التجربة','Rehearsal manager'))}<select name="manager_user_id" required>${options(data.managers)}</select></label><label>${esc(tr('محضّر فرع 2','Branch 2 preparer'))}<select name="preparer_user_id" required>${options(data.preparers)}</select></label><label>${esc(tr('سائق التجربة','Rehearsal driver'))}<select name="driver_user_id" required>${options(data.drivers)}</select></label><label class="full">${esc(tr('سبب التجربة','Rehearsal reason'))}<input name="reason" required minlength="3" maxlength="500" value="${esc(tr('تجربة صباحي فرع 2 معزولة','Isolated Branch 2 morning rehearsal'))}"></label><p class="form-error full" role="alert"></p><button type="submit">${esc(tr('اعتماد وبدء تجربة 20 دقيقة','Authorize and start a 20-minute rehearsal'))}</button></form>`;
      const form=root.querySelector('form');
      if(!(data.managers||[]).length||!(data.preparers||[]).length||!(data.drivers||[]).length){form.querySelector('[type=submit]').disabled=true;form.querySelector('.form-error').innerHTML=esc(tr('يلزم حساب مدير ومحضّر وسائق موجودون لفرع 2 يحددهم المالك لهذه التجربة. راجع الموظفين؛ لا تُضاف صلاحيات تشغيل فعلية تلقائيًا.','The owner selects existing Branch 2 manager, preparer and driver accounts for this rehearsal. Review staff; actual operating permissions are never added automatically.'))+' <a href="/ops">'+esc(tr('العودة للإدارة ثم الموظفين','Return to administration, then staff'))+'</a>';}
      form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('[type=submit]'),error=form.querySelector('.form-error');button.disabled=true;error.textContent='';try{const fields=new FormData(form);await authorize();const response=await mutation('/api/morning-trial/start',{branch_id:branch.id,manager_user_id:fields.get('manager_user_id'),preparer_user_id:fields.get('preparer_user_id'),driver_user_id:fields.get('driver_user_id'),reason:fields.get('reason')});adopt(response);startPolling();}catch(failure){error.textContent=failure.message;}finally{if(button.isConnected)button.disabled=false;}});
    }
    function startPolling(){clearInterval(timer);timer=setInterval(()=>{if(!document.hidden&&navigator.onLine&&getUser()&&!busy)refresh().catch(showError);},5000);}
    try{
      const data=await api(requested?'/api/morning-trial/'+encodeURIComponent(requested):'/api/morning-trial/current');
      adopt(data);
      if(!trial&&owner)await setup();
      if(trial||!owner)startPolling();
    }catch(error){root.innerHTML=notice()+`<p class="form-error" role="alert">${esc(error.message)}</p>`;}
  }};
})();

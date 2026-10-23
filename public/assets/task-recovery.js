/* Manager intervention desk. Reads never acknowledge or finish an order. */
(() => {
  'use strict';
  window.KPSTaskRecovery={open:async function({api,mutation,tr,esc,money,datetime,statusName,heading,active,reviewOrder,resolveDelivery}){
    heading(tr('معالجة الطلبات المتعثرة','Order intervention desk'),tr('ابحث بالطلب أو السائق، ثم افحص السبب قبل التدخل. تظهر المهام المفتوحة ضمن صلاحياتك.','Search by order or driver, then inspect before intervening. Open tasks are limited to your scope.'));
    const root=document.querySelector('#content');
    root.innerHTML=`<div class="stack"><form class="filters panel" data-recovery-search><label>${esc(tr('رقم الطلب أو اسم السائق','Order reference or driver name'))}<input name="search" type="search" maxlength="100"></label><button type="submit">${esc(tr('بحث وتحديث','Search and refresh'))}</button></form><p role="status" data-recovery-status></p><section data-recovery-list class="stack"></section><div class="actions" data-recovery-pages></div><section class="panel stack" data-recovery-detail hidden></section></div>`;
    const status=root.querySelector('[data-recovery-status]'),list=root.querySelector('[data-recovery-list]'),panel=root.querySelector('[data-recovery-detail]'),pages=root.querySelector('[data-recovery-pages]');
    let search='',offset=0,generation=0,detailGeneration=0;
    const live=()=>active()&&root.contains(list);
    const run=fn=>Promise.resolve().then(fn).catch(e=>{if(live())status.textContent=e.message;});
    const labels={closed:()=>tr('الطلب أو المهمة منتهية','Order or task has ended'),shift_closed:()=>tr('شفت السائق غير مفتوح','Driver shift is not open'),amendment_pending:()=>tr('طلب تعديل ينتظر القرار','An amendment is awaiting a decision'),branch_revision:()=>tr('الفرع لم يؤكد النسخة المعدلة','Branch has not acknowledged the revised copy'),driver_revision:()=>tr('مراجعة السائق للنسخة المعدلة غير مسجلة','Driver review of the revised copy is missing'),pickup_missing:()=>tr('استلام السائق من الفرع غير مسجل','Driver pickup has not been recorded'),payment_missing:()=>tr('التحصيل الفعلي غير مكتمل','Actual collection is incomplete')};
    async function load(){
      const ticket=++generation;status.textContent=tr('جاري تحميل المهام…','Loading tasks…');
      try{
        const data=await api('/api/task-recovery?'+new URLSearchParams({search,offset:String(offset)}));
        if(!live()||ticket!==generation)return;
        list.innerHTML=(data.tasks||[]).map(t=>`<article class="panel"><h2>${esc(t.order_no||t.order_id)}</h2><p>${esc(t.driver_name)} · ${esc(statusName(t.status))} · ${esc(datetime(t.created_at))}</p><button type="button" data-recovery-id="${esc(t.id)}">${esc(tr('فحص السبب والتدخل','Inspect and intervene'))}</button></article>`).join('');
        status.textContent=data.tasks.length?tr('اختر مهمة لفحص حالتها الحالية.','Choose a task to inspect its current state.'):tr('لا توجد مهام مفتوحة مطابقة.','No matching open tasks.');
        pages.innerHTML=(offset?`<button type="button" data-offset="${Math.max(0,offset-30)}">${esc(tr('السابق','Previous'))}</button>`:'')+(data.next_offset!=null?`<button type="button" data-offset="${data.next_offset}">${esc(tr('التالي','Next'))}</button>`:'');
        pages.querySelectorAll('button').forEach(b=>b.onclick=()=>{offset=Number(b.dataset.offset);void load();});
        list.querySelectorAll('[data-recovery-id]').forEach(b=>b.onclick=()=>void inspect(b.dataset.recoveryId));
      }catch(e){if(live()&&ticket===generation)status.textContent=e.message;}
    }
    async function inspect(tid){
      const ticket=++detailGeneration;panel.hidden=false;panel.textContent=tr('جاري فحص الحالة…','Inspecting current state…');
      try{
        const data=await api('/api/task-recovery/'+encodeURIComponent(tid));
        if(!live()||ticket!==detailGeneration)return;
        const o=data.order,t=data.task,c=o.customer||{},location=c.location;
        const map=location&&Number.isFinite(location.lat)&&Number.isFinite(location.lng)?`<a class="button quiet" target="_blank" rel="noopener" href="https://www.google.com/maps?q=${encodeURIComponent(location.lat+','+location.lng)}">${esc(tr('عرض موقع النسخة الحالية','View current delivery location'))}</a>`:'';
        panel.innerHTML=`<h2>${esc(o.order_no||o.id)}</h2><p>${esc(statusName(t.status))} · ${esc(tr('نسخة الطلب','Order copy'))} ${o.revision+1}</p><p>${esc(tr('العميل','Customer'))}: ${esc(c.name||'—')} · ${esc(c.neighborhood||'')} · ${esc(c.address||'')}</p>${map}<ul>${(o.snapshot.items||[]).map(i=>`<li>${esc(i.name)} × ${esc(i.quantity)}${(i.units||[]).map(u=>Object.values(u.option_snapshot||{}).map(g=>(g.choices||[]).map(v=>v.name||v.label||v.code).join(' / ')).filter(Boolean).map(s=>`<p>${esc(s)}</p>`).join('')).join('')}${i.note?`<p>${esc(i.note)}</p>`:''}</li>`).join('')}</ul><p>${esc(o.snapshot.notes||'')}</p><p>${esc(tr('الإجمالي','Total'))}: ${esc(money(o.total_halalas))} · ${esc(statusName(o.payment_status))}</p><h3>${esc(tr('نتيجة الفحص','Inspection result'))}</h3>${data.blockers.length?`<ul>${data.blockers.map(code=>`<li>${esc(labels[code]?.()||tr('تحتاج مراجعة','Review required'))}</li>`).join('')}</ul>`:`<p>${esc(tr('لا يوجد مانع مسجل لتوثيق التسليم؛ تحقق من الواقع قبل الحفظ.','No recorded blocker to verified delivery; check actual delivery before saving.'))}</p>`}<div class="actions"><button type="button" data-refresh-case>${esc(tr('إعادة فحص الحالة','Recheck state'))}</button><button type="button" data-review-order>${esc(tr('سجل الطلب والتعديلات','Order and amendment history'))}</button>${data.can_resolve_delivery?`<button type="button" data-resolve-delivery>${esc(tr('توثيق تسليم فعلي','Record verified delivery'))}</button>`:''}</div>${data.can_review_revision?`<form class="stack" data-revision-review><h3>${esc(tr('تدخل إداري لمراجعة النسخة الحالية','Administrative review of the current copy'))}</h3><p>${esc(tr('راجع الأصناف والموقع وأبلغ السائق بمحتوى النسخة الحالية، ثم وثّق تدخلك باسمك.','Review items and location, inform the driver of the current copy, then record the intervention in your name.'))}</p><label>${esc(tr('سبب التدخل','Intervention reason'))}<textarea name="reason" minlength="3" maxlength="2000" required></textarea></label><label>${esc(tr('نتيجة التواصل مع السائق أو مرجع الإثبات','Driver contact outcome or evidence reference'))}<textarea name="evidence" minlength="3" maxlength="2000" required></textarea></label><label class="check"><input name="confirmed" type="checkbox" required>${esc(tr('راجعت النسخة الحالية وتحققت من إبلاغ السائق بها','I reviewed the current copy and verified that the driver was informed'))}</label><p class="form-error" role="alert"></p><button type="submit">${esc(tr('تأكيد وحفظ التدخل الإداري','Confirm and save intervention'))}</button></form>`:''}<h3>${esc(tr('سجل التدخلات الإدارية','Administrative intervention history'))}</h3>${data.reviews.map(r=>`<article><strong>${esc(r.actor_name||'—')}</strong> · ${esc(datetime(r.created_at))}<p>${esc(r.details.reason)}</p><p>${esc(r.details.evidence)}</p><p>${esc(tr('نسخة الطلب','Order copy'))} ${r.details.revision+1}</p></article>`).join('')||`<p>${esc(tr('لا توجد تدخلات إدارية مسجلة.','No administrative interventions recorded.'))}</p>`}`;
        panel.querySelector('[data-refresh-case]').onclick=()=>void inspect(tid);
        panel.querySelector('[data-review-order]').onclick=()=>void run(()=>reviewOrder(o.id));
        const resolve=panel.querySelector('[data-resolve-delivery]');if(resolve)resolve.onclick=()=>void run(()=>resolveDelivery(o.id));
        const form=panel.querySelector('[data-revision-review]');
        if(form)form.onsubmit=async e=>{
          e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;
          try{
            const fd=new FormData(form);
            await mutation('/api/task-recovery/'+encodeURIComponent(tid)+'/review-revision',{revision:o.revision,version:o.version,reason:fd.get('reason'),evidence:fd.get('evidence'),confirmed:fd.has('confirmed')});
            if(live()&&ticket===detailGeneration){await inspect(tid);await load();}
          }catch(error){if(form.isConnected)form.querySelector('[role=alert]').textContent=error.message;}
          finally{if(button.isConnected)button.disabled=false;}
        };
        panel.scrollIntoView({block:'start',behavior:'smooth'});
      }catch(e){if(live()&&ticket===detailGeneration)panel.textContent=e.message;}
    }
    root.querySelector('[data-recovery-search]').onsubmit=e=>{e.preventDefault();search=String(new FormData(e.target).get('search')||'').trim();offset=0;void load();};
    await load();
  }};
})();

/* Explicit owner grants; amounts and actual times are never inferred. */
(() => {
  'use strict';
  window.KPSPriorDeliveries={open:async function(ctx){
    const {api,mutation,tr,esc,money,modal,closeModal,user,branches,onCreated}=ctx;
    const base='/api/prior-deliveries';
    const d=await api(base+'/options');
    const name=s=>s?`${s.display_name} · ${s.delivery_operational_date||''}`:'—';
    const input=(label,key,type='text',extra='')=>`<label>${esc(label)}<input name="${key}" type="${type}" required ${extra}></label>`;
    const end=label=>`<p class="form-error" role="alert"></p><button type="submit">${esc(label)}</button>`;
    const again=()=>window.KPSPriorDeliveries.open(ctx);
    function authorize(){
      modal(tr('فتح تسجيل الطلبات السابقة','Authorize prior deliveries'),`<form class="stack" id="prior-grant-form">
        <p>${esc(tr('فتح مؤقت لمدير محدد وسائقين محددين. يدخل المدير وقت التسليم والمبالغ الفعلية؛ لا يتغير استلام النقد أو أي تقرير أصلي.','Temporary access for one manager and selected drivers. The manager enters actual delivery time and payment amounts. Cash receipts and original reports stay intact.'))}</p>
        <label>${esc(tr('مدير التوصيل','Delivery manager'))}<select name="manager_shift_id" required>${d.managers.map(s=>`<option value="${esc(s.id)}">${esc(name(s))}</option>`).join('')}</select></label>
        <fieldset id="prior-drivers"><legend>${esc(tr('السائقون','Drivers'))}</legend></fieldset>
        ${input(tr('مدة الفتح بالدقائق','Access duration (minutes)'),'minutes','number','min="15" max="240" value="120"')}
        ${input(tr('سبب الاعتماد','Authorization reason'),'reason','text','minlength="5" maxlength="1000"')}
        ${end(tr('اعتماد وفتح التسجيل','Authorize and open'))}</form>`,async fd=>{
        const ids=fd.getAll('driver_shift_ids');if(!ids.length)throw new Error(tr('اختر سائقًا واحدًا على الأقل.','Select at least one driver.'));
        await mutation(base+'/grants',{manager_shift_id:fd.get('manager_shift_id'),driver_shift_ids:ids,minutes:Number(fd.get('minutes')),reason:fd.get('reason')});
        await again();
      });
      const form=document.querySelector('#prior-grant-form');
      const draw=()=>{form.querySelector('#prior-drivers').innerHTML=`<legend>${esc(tr('السائقون','Drivers'))}</legend>`+d.shifts.filter(s=>s.manager_shift_id===form.elements.manager_shift_id.value).map(s=>`<label><input type="checkbox" name="driver_shift_ids" value="${esc(s.id)}">${esc(name(s))} · ${esc(s.status)}</label>`).join('');};
      form.elements.manager_shift_id.addEventListener('change',draw);draw();
    }
    async function entry(g){
      const shifts=d.shifts.filter(s=>g.driver_shift_ids.includes(s.id));
      if(!shifts.length)throw new Error(tr('لا توجد وردية مخولة متاحة.','No authorized duty is available.'));
      modal(tr('تسجيل طلب سابق — عنبيشن','Record prior delivery — Enbishan'),`<form class="stack" id="prior-entry-form">
        <p class="notice">${esc(tr('يسجّل الطلب مسلّمًا ومحصّلًا دون إعادة إرساله للتحضير. اختر الطلب الموجود أولًا؛ أضف جديدًا فقط إذا لم يُسجّل.','Records an already delivered and collected order without sending it for preparation. Select an existing order first; create only a missing one.'))}</p>
        <p>${esc(tr('يوم التشغيل','Operating date'))}: <strong>${esc(g.operational_date)}</strong></p>
        <label>${esc(tr('السائق','Driver'))}<select name="driver_shift_id">${shifts.map(s=>`<option value="${esc(s.id)}">${esc(s.display_name)}</option>`).join('')}</select></label>
        <label>${esc(tr('الطلب','Order'))}<select name="order_id" required></select></label><p id="prior-order-total"></p>
        ${input(tr('وقت التسليم والتحصيل الفعلي — توقيت السعودية','Actual delivery and collection time — Saudi time'),'actual_delivered_at','datetime-local')}
        ${input(tr('مرجع الفاتورة أو التحصيل الأصلي','Original invoice or collection reference'),'source_reference','text','minlength="3" maxlength="150"')}
        <div class="form-columns">${input(tr('الكاش المحصل (ريال)','Cash collected (SAR)'),'cash','number','min="0" step="0.01"')}${input(tr('الشبكة المحصلة (ريال)','Card collected (SAR)'),'card','number','min="0" step="0.01"')}</div>
        ${input(tr('سبب تأخر التسجيل','Reason for late recording'),'reason','text','minlength="5" maxlength="1000"')}
        <label><input name="confirmed" type="checkbox" required>${esc(tr('أؤكد أن الطلب سُلّم وأن المبالغ المذكورة حُصّلت فعليًا من العميل','I confirm delivery and actual collection of these amounts from the customer'))}</label>
        ${end(tr('متابعة','Continue'))}</form>`,async fd=>{
        if(loading)throw new Error(tr('انتظر تحميل الطلبات.','Wait for orders to load.'));
        const shift=shifts.find(s=>s.id===fd.get('driver_shift_id'));
        const prior={driver_shift_id:shift.id,actual_delivered_at:String(fd.get('actual_delivered_at'))+':00+03:00',
          source_reference:fd.get('source_reference'),cash_halalas:Math.round(Number(fd.get('cash'))*100),card_halalas:Math.round(Number(fd.get('card'))*100),
          reason:fd.get('reason'),confirmed:fd.has('confirmed')};
        const selected=rows.find(o=>o.id===fd.get('order_id'));
        if(selected){
          await mutation(base+'/grants/'+g.id+'/record',{prior,order_id:selected.id,expected_version:selected.version});
          closeModal();await onCreated(selected.id);return;
        }
        if(fd.get('order_id')!=='new')throw new Error(tr('اختر الطلب.','Select an order.'));
        const staffMutation=(path,body,button)=>{
          if(path==='/api/orders/delivery-center/quote'||path==='/api/orders/delivery-center'){
            const method=prior.cash_halalas&&prior.card_halalas?'split':prior.cash_halalas?'cash':'card';
            return mutation(base+'/grants/'+g.id+(path.endsWith('/quote')?'/quote':'/record'),{prior,order:{...body,payment_method:method}},button);
          }
          return mutation(path,body,button);
        };
        await window.KPSCustomerOrder.open({...ctx,mutation:staffMutation,priorBranchIds:shift.branch_ids,priorDelivery:true});
      });
      const form=document.querySelector('#prior-entry-form');let rows=[],loading=true,seq=0;
      async function load(){
        loading=true;const n=++seq;form.elements.order_id.innerHTML='';
        try{const result=await api(base+'/grants/'+g.id+'/orders/'+form.elements.driver_shift_id.value);if(n!==seq||!form.isConnected)return;rows=result.orders;
          form.elements.order_id.innerHTML=`<option value="">${esc(tr('اختر طلبًا موجودًا أو أضف الناقص','Select an existing order or add a missing one'))}</option>`+rows.map(o=>`<option value="${esc(o.id)}">${esc(o.order_reference||o.id)} — ${esc(money(o.total_halalas))}</option>`).join('')+`<option value="new">${esc(tr('إضافة طلب ناقص غير مسجل','Add a missing, unrecorded order'))}</option>`;
          loading=false;
        }catch(e){if(form.isConnected)form.querySelector('.form-error').textContent=e.message;}
      }
      form.elements.driver_shift_id.addEventListener('change',load);
      form.elements.order_id.addEventListener('change',()=>{const o=rows.find(x=>x.id===form.elements.order_id.value);form.querySelector('#prior-order-total').textContent=o?tr('الإجمالي: ','Total: ')+money(o.total_halalas):'';});
      await load();
    }
    modal(tr('الطلبات السابقة — عنبيشن','Prior deliveries — Enbishan'),`<div class="stack" id="prior-home">
      <p>${esc(tr('تسجيل الطلبات المنفذة سابقًا بإذن المالك، مع منع تكرار المرجع وفصل إضافات التقفيلات المقفلة.','Owner-authorized recording of completed orders, with duplicate-reference protection and separate supplements for closed duties.'))}</p>
      ${d.grants.map(g=>`<section class="panel"><strong>${esc(name(d.managers.find(s=>s.id===g.manager_shift_id)))}</strong><p>${esc(g.reason)}</p><p>${esc(tr('ينتهي الفتح: ','Access expires: '))}${esc(new Intl.DateTimeFormat('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}).format(new Date(g.expires_at)))}</p><p>${esc(d.shifts.filter(s=>g.driver_shift_ids.includes(s.id)).map(s=>s.display_name).join('، '))}</p><button type="button" data-prior-entry="${esc(g.id)}">${esc(tr('تسجيل أو استكمال طلب سابق','Record or complete a prior order'))}</button>${user.role==='owner'?`<button class="quiet" type="button" data-prior-revoke="${esc(g.id)}">${esc(tr('إيقاف الفتح','Revoke access'))}</button>`:''}</section>`).join('')||`<p>${esc(tr('لا يوجد فتح ساري لحسابك.','No active authorization for your account.'))}</p>`}
      ${user.role==='owner'?`<button type="button" id="prior-authorize">${esc(tr('فتح التسجيل للسائقين','Authorize recording for drivers'))}</button>`:''}<p class="form-error" role="alert"></p></div>`);
    const home=document.querySelector('#prior-home');
    home.querySelector('#prior-authorize')?.addEventListener('click',authorize);
    home.querySelectorAll('[data-prior-entry]').forEach(b=>b.addEventListener('click',()=>entry(d.grants.find(g=>g.id===b.dataset.priorEntry)).catch(e=>{if(home.isConnected)home.querySelector('.form-error').textContent=e.message;})));
    home.querySelectorAll('[data-prior-revoke]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await mutation(base+'/grants/'+b.dataset.priorRevoke+'/revoke',{reason:tr('أوقف المالك إدخال الطلبات السابقة','Owner stopped prior delivery entry')});await again();}catch(e){home.querySelector('.form-error').textContent=e.message;b.disabled=false;}}));
  }};
})();

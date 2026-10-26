/* Explicit owner grants; amounts and actual times are never inferred. */
(() => {
  'use strict';
  window.KPSPriorDeliveries={open:async function(ctx){
    const {api,mutation,tr,esc,money,modal,closeModal,user,branches,onCreated}=ctx;
    const base='/api/prior-deliveries';
    const d=await api(base+'/options');
    const name=s=>s?`${s.display_name} · ${s.delivery_operational_date||''}${s.status==='closed'?' · '+tr('مقفلة','Closed'):''}`:'—';
    const input=(label,key,type='text',extra='')=>`<label>${esc(label)}<input name="${key}" type="${type}" required ${extra}></label>`;
    const end=label=>`<p class="form-error" role="alert"></p><button type="submit">${esc(label)}</button>`;
    const again=()=>window.KPSPriorDeliveries.open(ctx);
    function authorize(){
      modal(tr('فتح تسجيل الطلبات السابقة','Authorize prior deliveries'),`<form class="stack" id="prior-grant-form">
        <p>${esc(tr('فتح مؤقت لمدير محدد وسائقين محددين. يدخل المدير وقت التسليم والمبالغ الفعلية؛ لا يتغير استلام النقد أو أي تقرير أصلي.','Temporary access for one manager and selected drivers. The manager enters actual delivery time and payment amounts. Cash receipts and original reports stay intact.'))}</p>
        <label>${esc(tr('مدير التوصيل','Delivery manager'))}<select name="manager_shift_id" required>${d.managers.map(s=>`<option value="${esc(s.id)}">${esc(name(s))}</option>`).join('')}</select></label>
        <p id="prior-manager-state"></p><label id="prior-closed-confirm" hidden><input type="checkbox" name="closed_manager_supplement">${esc(tr('أعتمد ملحقًا للمناوبة المقفلة — إدخال المالك فقط، دون فتح المناوبة أو تغيير تقفيلتها','Authorize a closed-duty supplement — owner entry only; original duty stays closed and unchanged'))}</label>
        <fieldset id="prior-drivers"><legend>${esc(tr('السائقون','Drivers'))}</legend></fieldset>
        ${input(tr('مدة الفتح بالدقائق','Access duration (minutes)'),'minutes','number','min="15" max="240" value="120"')}
        ${input(tr('سبب الاعتماد','Authorization reason'),'reason','text','minlength="5" maxlength="1000"')}
        ${end(tr('اعتماد وفتح التسجيل','Authorize and open'))}</form>`,async fd=>{
        const ids=fd.getAll('driver_shift_ids');if(!ids.length)throw new Error(tr('اختر سائقًا واحدًا على الأقل.','Select at least one driver.'));
        await mutation(base+'/grants',{manager_shift_id:fd.get('manager_shift_id'),driver_shift_ids:ids,minutes:Number(fd.get('minutes')),reason:fd.get('reason'),closed_manager_supplement:fd.has('closed_manager_supplement')});
        await again();
      });
      const form=document.querySelector('#prior-grant-form');
      const draw=()=>{const closed=d.managers.find(s=>s.id===form.elements.manager_shift_id.value)?.status==='closed';form.querySelector('#prior-closed-confirm').hidden=!closed;form.elements.closed_manager_supplement.checked=false;form.elements.closed_manager_supplement.required=closed;form.querySelector('#prior-manager-state').textContent=closed?tr('هذه المناوبة مقفلة. الإذن للمالك لإضافة ملحق مرتبط باليوم والسائق الأصليين؛ لا يتيح إدخال مدير التوصيل.','This duty is closed. Only the owner can add a supplement tied to its original day and driver. Delivery-manager entry is unavailable.'):'';form.querySelector('#prior-drivers').innerHTML=`<legend>${esc(tr('السائقون','Drivers'))}</legend>`+d.shifts.filter(s=>s.manager_shift_id===form.elements.manager_shift_id.value&&(!closed||s.status==='closed')).map(s=>`<label><input type="checkbox" name="driver_shift_ids" value="${esc(s.id)}">${esc(name(s))} · ${esc(s.status)}</label>`).join('');};
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
    async function legacyInvoice(g){
      if(user.role!=='owner'||!g.closed_manager_supplement)throw new Error(tr('استيراد الفاتورة متاح للمالك بإذن ملحق مناوبة مقفلة.','Invoice import requires an owner-authorized closed-duty supplement.'));
      const shifts=d.shifts.filter(s=>g.driver_shift_ids.includes(s.id)&&s.status==='closed');
      if(!shifts.length)throw new Error(tr('لا توجد وردية مقفلة مخولة.','No authorized closed duty is available.'));
      const itemRow=()=>`<div class="panel prior-source-item"><div class="form-columns">${input(tr('اسم الصنف كما في الفاتورة','Item name on source invoice'),'source_item_name','text','maxlength="200"')}${input(tr('الكمية','Quantity'),'source_item_quantity','number','min="1" max="1000" step="1" value="1"')}${input(tr('سعر الوحدة شامل الضريبة (ريال)','Tax-inclusive unit price (SAR)'),'source_item_price','number','min="0.01" step="0.01"')}</div><button type="button" class="quiet" data-remove-source-item>${esc(tr('إزالة الصنف','Remove item'))}</button></div>`;
      modal(tr('إضافة فاتورة ساباد إلى ملحق التقفيلة','Add a Sabad invoice to the closure supplement'),`<form class="stack" id="prior-source-invoice-form">
        <p class="notice">${esc(tr('تُحفظ نسخة الفاتورة الأصلية في ملحق مستقل. تظل تقفيلة السائق والمدير المقفلة ومبالغها الأصلية محفوظة. لا تُصدر فاتورة ضريبية جديدة ولا يُرسل الطلب للتحضير.','The source invoice is recorded in a separate supplement. Original closed driver and manager reports and amounts are preserved. No new tax invoice or kitchen order is issued.'))}</p>
        <p>${esc(tr('يوم التشغيل','Operating date'))}: <strong>${esc(g.operational_date)}</strong></p>
        <label>${esc(tr('السائق','Driver'))}<select name="driver_shift_id" required>${shifts.map(s=>`<option value="${esc(s.id)}">${esc(s.display_name)}</option>`).join('')}</select></label>
        <label>${esc(tr('فرع الفاتورة الأصلية','Source invoice branch'))}<select name="branch_id" required></select></label>
        ${input(tr('رقم الفاتورة الأصلية في ساباد','Original Sabad invoice number'),'invoice_reference','text','minlength="3" maxlength="150"')}
        ${input(tr('وقت إصدار الفاتورة — توقيت السعودية','Invoice issue time — Saudi time'),'invoice_issued_at','datetime-local','step="1"')}
        <p class="small-text">${esc(tr('هذا وقت إصدار الفاتورة فقط؛ لا يُستخدم كوقت تسليم فعلي أو تحصيل.','This is the invoice issue time only; it is not used as the actual delivery or collection time.'))}</p>
        ${input(tr('اسم العميل كما في الفاتورة — دون إنشاء حساب أو جوال','Customer label on invoice — no account or phone'),'customer_label','text','maxlength="200" value="CUSTOMER0001"')}
        <h3>${esc(tr('أصناف الفاتورة الأصلية','Original invoice items'))}</h3>
        <div id="prior-source-items">${itemRow()}</div><button type="button" class="quiet" id="prior-source-add-item">${esc(tr('إضافة صنف','Add item'))}</button>
        <div class="form-columns">${input(tr('إجمالي الفاتورة شامل الضريبة (ريال)','Tax-inclusive invoice total (SAR)'),'source_total','number','min="0.01" step="0.01"')}${input(tr('الضريبة المضمنة حسب الفاتورة (ريال)','Included tax shown on invoice (SAR)'),'source_tax','number','min="0" step="0.01"')}${input(tr('الكاش حسب الفاتورة (ريال)','Cash shown on invoice (SAR)'),'source_cash','number','min="0" step="0.01"')}${input(tr('الشبكة حسب الفاتورة (ريال)','Card shown on invoice (SAR)'),'source_card','number','min="0" step="0.01"')}</div>
        ${input(tr('سبب الإضافة ومرجع المراجعة','Reason for import and review reference'),'reason','text','minlength="5" maxlength="1000"')}
        <label class="check"><input name="confirmed" type="checkbox" required>${esc(tr('راجعت الفاتورة الأصلية وأؤكد أنها طلب سابق مسلّم ومحصّل ولم يُسجّل هنا، وأعتمد إضافتها كملحق مستقل.','I reviewed the source invoice and attest it is an already delivered and collected order not previously recorded here, and approve its separate supplement.'))}</label>
        ${end(tr('حفظ الفاتورة الأصلية في الملحق','Save source invoice in supplement'))}</form>`,async fd=>{
          if(!fd.has('confirmed'))throw new Error(tr('أكد مراجعة الفاتورة الأصلية.','Confirm source invoice review.'));
          const amount=value=>{
            const raw=String(value??'').trim();
            if(!/^\d+(?:\.\d{1,2})?$/.test(raw))throw new Error(tr('أدخل المبالغ كما في الفاتورة حتى منزلتين عشريتين.','Enter source invoice amounts with at most two decimal places.'));
            const n=Math.round(Number(raw)*100);if(!Number.isSafeInteger(n))throw new Error(tr('المبلغ غير صالح.','Invalid amount.'));return n;
          };
          const names=fd.getAll('source_item_name'),quantities=fd.getAll('source_item_quantity'),prices=fd.getAll('source_item_price');
          if(!names.length||names.length>30||quantities.length!==names.length||prices.length!==names.length)throw new Error(tr('أدخل من صنف واحد إلى 30 صنفًا.','Enter between 1 and 30 items.'));
          const items=names.map((n,i)=>{
            const name=String(n).trim(),quantity=Number(quantities[i]),unit_price_halalas=amount(prices[i]);
            if(!name||!Number.isSafeInteger(quantity)||quantity<1||quantity>1000||unit_price_halalas<1)throw new Error(tr('راجع اسم الصنف والكمية وسعر الوحدة.','Check item name, quantity, and unit price.'));
            return {name,quantity,unit_price_halalas};
          });
          const total_halalas=amount(fd.get('source_total')),tax_halalas=amount(fd.get('source_tax')),cash_halalas=amount(fd.get('source_cash')),card_halalas=amount(fd.get('source_card'));
          if(items.reduce((sum,i)=>sum+i.quantity*i.unit_price_halalas,0)!==total_halalas||cash_halalas+card_halalas!==total_halalas||tax_halalas>total_halalas)throw new Error(tr('يجب أن تتطابق الأصناف والكاش والشبكة مع إجمالي الفاتورة، وتكون الضريبة ضمن الإجمالي.','Items and cash plus card must match the invoice total; included tax cannot exceed the total.'));
          const shift=shifts.find(s=>s.id===fd.get('driver_shift_id'));
          if(!shift||!shift.branch_ids.includes(fd.get('branch_id')))throw new Error(tr('اختر الفرع والسائق من الخيارات المخولة.','Choose an authorized driver and branch.'));
          const issued=String(fd.get('invoice_issued_at')||'');
          if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(issued))throw new Error(tr('أدخل وقت إصدار الفاتورة.','Enter the invoice issue time.'));
          const result=await mutation(base+'/grants/'+g.id+'/legacy-invoice',{driver_shift_id:shift.id,branch_id:fd.get('branch_id'),invoice_reference:fd.get('invoice_reference'),invoice_issued_at:issued+(issued.length===16?':00':'')+'+03:00',customer_label:fd.get('customer_label'),items,total_halalas,tax_halalas,cash_halalas,card_halalas,reason:fd.get('reason'),confirmed:true});
          closeModal();await onCreated(result.order.id);
        });
      const form=document.querySelector('#prior-source-invoice-form'),itemList=form.querySelector('#prior-source-items');
      const drawBranches=()=>{const shift=shifts.find(s=>s.id===form.elements.driver_shift_id.value);form.elements.branch_id.innerHTML=branches.filter(b=>shift?.branch_ids.includes(b.id)).map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('');};
      form.elements.driver_shift_id.addEventListener('change',drawBranches);drawBranches();
      form.querySelector('#prior-source-add-item').addEventListener('click',()=>{if(itemList.querySelectorAll('.prior-source-item').length<30)itemList.insertAdjacentHTML('beforeend',itemRow());});
      itemList.addEventListener('click',e=>{const button=e.target.closest('[data-remove-source-item]');if(button&&itemList.querySelectorAll('.prior-source-item').length>1)button.closest('.prior-source-item').remove();});
    }
    modal(tr('الطلبات السابقة — عنبيشن','Prior deliveries — Enbishan'),`<div class="stack" id="prior-home">
      <p>${esc(tr('تسجيل الطلبات المنفذة سابقًا بإذن المالك، مع منع تكرار المرجع وفصل إضافات التقفيلات المقفلة.','Owner-authorized recording of completed orders, with duplicate-reference protection and separate supplements for closed duties.'))}</p>
      ${d.grants.map(g=>`<section class="panel"><strong>${esc(name(d.managers.find(s=>s.id===g.manager_shift_id)))}</strong><p>${esc(g.reason)}</p>${g.closed_manager_supplement?`<p class="notice">${esc(tr('ملحق مناوبة مقفلة — إدخال المالك فقط، والتقفيلة الأصلية محفوظة.','Closed-duty supplement — owner entry only; original closure preserved.'))}</p>`:''}<p>${esc(tr('ينتهي الفتح: ','Access expires: '))}${esc(new Intl.DateTimeFormat('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}).format(new Date(g.expires_at)))}</p><p>${esc(d.shifts.filter(s=>g.driver_shift_ids.includes(s.id)).map(s=>s.display_name).join('، '))}</p><button type="button" data-prior-entry="${esc(g.id)}">${esc(tr('تسجيل أو استكمال طلب سابق','Record or complete a prior order'))}</button>${user.role==='owner'&&g.closed_manager_supplement?`<button class="quiet" type="button" data-prior-source-invoice="${esc(g.id)}">${esc(tr('إضافة فاتورة ساباد الأصلية','Add original Sabad invoice'))}</button>`:''}${user.role==='owner'?`<button class="quiet" type="button" data-prior-revoke="${esc(g.id)}">${esc(tr('إيقاف الفتح','Revoke access'))}</button>`:''}</section>`).join('')||`<p>${esc(tr('لا يوجد فتح ساري لحسابك.','No active authorization for your account.'))}</p>`}
      ${user.role==='owner'?`<button type="button" id="prior-authorize">${esc(tr('فتح التسجيل للسائقين','Authorize recording for drivers'))}</button>`:''}<p class="form-error" role="alert"></p></div>`);
    const home=document.querySelector('#prior-home');
    home.querySelector('#prior-authorize')?.addEventListener('click',authorize);
    home.querySelectorAll('[data-prior-entry]').forEach(b=>b.addEventListener('click',()=>entry(d.grants.find(g=>g.id===b.dataset.priorEntry)).catch(e=>{if(home.isConnected)home.querySelector('.form-error').textContent=e.message;})));
    home.querySelectorAll('[data-prior-source-invoice]').forEach(b=>b.addEventListener('click',()=>legacyInvoice(d.grants.find(g=>g.id===b.dataset.priorSourceInvoice)).catch(e=>{if(home.isConnected)home.querySelector('.form-error').textContent=e.message;})));
    home.querySelectorAll('[data-prior-revoke]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await mutation(base+'/grants/'+b.dataset.priorRevoke+'/revoke',{reason:tr('أوقف المالك إدخال الطلبات السابقة','Owner stopped prior delivery entry')});await again();}catch(e){home.querySelector('.form-error').textContent=e.message;b.disabled=false;}}));
  }};
})();

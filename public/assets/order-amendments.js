/* Shared customer/staff amendment flow. Drafts live in their own dialog, never polling markup. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tr=(ar,en)=>document.documentElement.lang==='en'?en:ar;
  const money=n=>`${(Number(n||0)/100).toFixed(2)} ${tr('ر.س.','SAR')}`;
  const field=(label,name,value='',extra='')=>`<label>${esc(label)}<input name="${name}" value="${esc(value)}" ${extra}></label>`;
  const button=(action,label)=>`<button type="button" data-amend="${action}">${esc(label)}</button>`;
  const states={requested:['بانتظار المعالجة','Awaiting processing'],branch_confirmed:['بانتظار المعالجة','Awaiting processing'],offered:['بانتظار موافقة العميل','Awaiting customer consent'],applied:['تم اعتماد التعديل','Amendment applied'],rejected:['مرفوض','Rejected'],withdrawn:['تم سحب التعديل','Withdrawn']};
  const requestKind=kind=>kind==='duplicate'?tr('طلب مكرر','Duplicate order'):tr('طلب إلغاء','Cancellation request');
  const itemList=items=>`<ul>${items.map(x=>`<li><b>${esc(x.name)} × ${x.quantity}</b>${x.note?` · ${esc(x.note)}`:''}${(x.units||[]).map(u=>Object.values(u.option_snapshot||{}).map(g=>(g.choices||[]).map(v=>v.name||v.label||v.code).join(' / ')).filter(Boolean).join(' · ')).filter(Boolean).map(s=>`<small>${esc(s)}</small>`).join('')}</li>`).join('')}</ul>`;
  window.KPSAdministrativeGuidance={render(g){
    if(!g)return '';
    const label=v=>v?tr(v.ar,v.en):'—';
    const row=(ar,en,v)=>v?`<div><dt>${esc(tr(ar,en))}</dt><dd>${esc(label(v))}</dd></div>`:'';
    const stages=g.stages||[];
    const status=g.operational_status?tr(g.operational_status==='completed'?'مكتمل تشغيليًا':g.operational_status==='cancelled'?'ملغي تشغيليًا':'جارٍ تشغيليًا',g.operational_status==='completed'?'Operationally complete':g.operational_status==='cancelled'?'Operationally cancelled':'Operationally active'):'';
    return `<section class="amend-note" data-administrative-guidance><h3>${esc(tr('تسلسل المعالجة والمسؤولية','Resolution sequence and responsibility'))}</h3>${status?`<p>${esc(status)}${g.financial_status==='pending'?' · '+esc(tr('المعالجة المالية معلقة لدى المحاسبة','Financial resolution pending with accounting')):''}</p>`:''}${g.state_note?`<p>${esc(label(g.state_note))}</p>`:''}${g.review_only?`<p>${esc(tr('مراجعة الطلب فقط — التنفيذ غير مثبت بهذه التذكرة','Request review only — this ticket does not establish execution'))}</p>`:''}${g.action?`<dl>${row('لدى الآن','Currently with',g.responsible)}${row('الإجراء المطلوب','Required action',g.action)}${row('الخطوة التالية','Next step',g.next_step)}${row('الاستكمال / البديل المتاح','Follow-up / available alternative',g.fallback)}</dl>`:''}${stages.map(stage=>`<dl>${row('لدى الآن','Currently with',stage.responsible)}${row('حالة الإسناد','Assignment status',stage.assignment_note)}${row('المسؤول التالي','Next responsible person',stage.next_responsible)}${row('الإجراء المطلوب من القسم','Department action required',stage.action)}${row('المطلوب لإكمال الحل','Evidence required',stage.evidence)}${row('بعد التنفيذ / الخطوة التالية','After execution / next step',stage.next_step)}${row('إذا تعذر الحل','If the action cannot proceed',stage.fallback)}${row('حدود الإرجاع / الاستكمال','Return / clarification limits',stage.limitation)}</dl>`).join('')}${!g.action&&!stages.length?`<p>${esc(tr('لا توجد خطوة إدارية مفتوحة مسجلة في هذا المصدر؛ مراجعة البنك والتقفيلات تُتابع من سجلاتها المستقلة.','No open administrative step is recorded in this source; bank and closure reviews remain in their independent records.'))}</p>`:''}</section>`;
  }};
  window.KPSAmendments={open:async function({oid,api,mutation,user,onChanged=()=>{}}){
    const dialog=document.createElement('dialog');dialog.className='amend-dialog';dialog.dir=document.documentElement.dir;
    dialog.innerHTML=`<header><h2>${esc(tr('تعديل وإلغاء الطلب','Order changes and cancellation'))}</h2><button type="button" data-close aria-label="${esc(tr('إغلاق','Close'))}">×</button></header><div class="amend-body"></div>`;
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{dialog.remove();onChanged();});
    const root=dialog.querySelector('.amend-body');let o,s,products=[],cart=[];
    const customer=user.role==='customer',staff=['owner','delivery_manager','branch_manager'].includes(user.role);
    const endpoint=`/api/order-amendments/orders/${encodeURIComponent(oid)}`;
    const fail=e=>{const target=root.querySelector('[role=alert]');if(target)target.textContent=e.message;};
    async function refresh(){
      const [detail,amend]=await Promise.all([api(`/api/orders/${encodeURIComponent(oid)}`),api(endpoint)]);o=detail.order;s=amend;
      if(!dialog.isConnected)return;
      const active=s.amendments.find(a=>a.id===s.active_id),closed=o.completed_at||['completed','cancelled','rejected'].includes(o.preparation_status)||['delivered','cancelled'].includes(o.delivery_status);
      const delegated=user.role==='delivery_manager'&&o.snapshot.delivery_workflow==='manager_dispatch';
      const reviewer=user.role==='owner'||user.role===(o.service==='delivery'?'delivery_manager':'branch_manager');
      root.innerHTML=`<p>${esc(o.order_no||oid)} · ${esc(tr('النسخة','Revision'))} ${s.revision+1}</p><div class="amend-totals"><span>${esc(tr('إجمالي الطلب الحالي','Current total'))}<b>${money(o.total_halalas)}</b></span><span>${esc(tr('المتبقي للتحصيل','Still to collect'))}<b>${money(s.due_halalas)}</b></span><span>${esc(tr('المستحق للعميل','Refund due'))}<b>${money(s.refund_due_halalas)}</b></span></div><p class="amend-note">${esc(tr('التعديل لا يطبق حتى مراجعته والموافقة على الإجمالي الجديد. بعد خروج السائق: تعديل العنوان فقط.','Changes apply only after review and consent to the new total. After pickup: address changes only.'))}</p><p role="alert"></p>`;
      if(!customer&&user.role!=='driver')root.insertAdjacentHTML('beforeend',window.KPSAdministrativeGuidance.render(s.guidance));
      renderRequests(closed,reviewer);
      if(active){
        root.insertAdjacentHTML('beforeend',`<section><h3>${esc(tr('طلب تعديل','Amendment request'))} — ${esc(tr(...states[active.status]))}</h3><p>${esc(tr('ينتهي العرض عند','Offer expires at'))} ${esc(new Date(active.expires_at).toLocaleTimeString())}</p><div class="amend-comparison"><div><h4>${esc(tr('الحالي','Current'))}</h4>${itemList(o.snapshot.items)}<p>${esc(o.customer.address)}</p></div><div><h4>${esc(tr('المقترح','Proposed'))}</h4>${itemList(active.quote.items)}<p>${esc(active.customer.address)}</p><p>${esc(active.quote.notes)}</p></div></div><div class="amend-totals"><span>${esc(tr('الأصناف','Items'))}<b>${money(active.quote.subtotal_halalas)}</b></span><span>${esc(tr('التوصيل','Delivery'))}<b>${money(active.quote.delivery_fee_halalas)}</b></span><span>${esc(tr('الإجمالي الجديد','New total'))}<b>${money(active.quote.total_halalas)}</b></span></div><p>${esc(tr('فرق السعر','Price difference'))}: ${money(active.quote.total_halalas-o.total_halalas)}</p><div class="amend-actions"></div></section>`);
        const actions=root.querySelector('.amend-actions');
        if(staff||customer)actions.insertAdjacentHTML('beforeend',button('withdraw',tr('سحب طلب التعديل','Withdraw request')));
        if(staff)actions.insertAdjacentHTML('beforeend',button('reject',tr('رفض مع السبب','Reject with reason')));
        if((['branch_manager','owner'].includes(user.role)||delegated)&&active.status==='requested')actions.insertAdjacentHTML('beforeend',button('confirm_branch',tr('الفرع يؤكد إمكانية التعديل','Confirm branch feasibility')));
        if(reviewer&&['requested','branch_confirmed'].includes(active.status))actions.insertAdjacentHTML('beforeend',button('offer',tr('مراجعة وإرسال الإجمالي للعميل','Review and offer new total')));
        if(active.status==='offered'&&(customer||reviewer)){
          if(!customer)actions.insertAdjacentHTML('beforeend',`<label>${esc(tr('موافقة العميل وصلت عبر','Customer consent received via'))}<select name="channel"><option value="whatsapp">${esc(tr('واتساب','WhatsApp'))}</option><option value="phone">${esc(tr('اتصال','Phone'))}</option></select></label>${field(tr('توثيق موافقة العميل على التعديل والإجمالي','Record consent to changes and total'),'consent_note','','maxlength="500" required')}`);
          actions.insertAdjacentHTML('beforeend',button('accept',customer?tr('أوافق على التعديل والإجمالي الجديد','Accept changes and new total'):tr('اعتماد التعديل بطلب العميل','Apply with recorded customer consent')));
        }
        root.querySelectorAll('[data-amend]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const action=b.dataset.amend;let note='';if(action==='confirm_branch'&&delegated){note=window.prompt(tr('وثّق تأكيد الفرع أن التعديل ممكن','Record branch confirmation that changes are feasible'))||'';if(!note.trim())return;}if(action==='reject'){note=window.prompt(tr('سبب الرفض','Rejection reason'))||'';if(!note.trim())return;}if(action==='accept'&&!customer){note=root.querySelector('[name=consent_note]').value.trim();if(!note)throw new Error(tr('وثّق موافقة العميل أولًا.','Record customer consent first.'));}await mutation(`/api/order-amendments/${encodeURIComponent(active.id)}/decision`,{action,channel:action==='accept'&&!customer?root.querySelector('[name=channel]').value:'customer',note});await refresh();}catch(e){fail(e);}finally{b.disabled=false;}});
      }else if(!closed&&(customer||reviewer)){
        root.insertAdjacentHTML('beforeend',button('edit',tr('طلب تعديل الأصناف أو العنوان','Request item or address changes')));
        root.querySelector('[data-amend=edit]').onclick=()=>edit().catch(fail);
      }
      if(!closed&&s.revision&&(['branch_manager','owner','driver'].includes(user.role)||delegated)){
        const kind=user.role==='driver'?'driver':'branch',received=s.receipts.some(r=>r.kind===kind&&(kind==='branch'||r.actor_id===user.id));
        if(!received){root.insertAdjacentHTML('beforeend',`<section class="amend-note"><h3>${esc(tr('نسخة معدلة — أوقف استخدام النسخة السابقة','Revised order — stop using the old copy'))}</h3>${itemList(o.snapshot.items)}<p>${esc(o.customer.address)}</p>${button('receipt',tr('راجعت النسخة المعدلة وأؤكد استلامها','Reviewed revised copy; acknowledge receipt'))}</section>`);root.querySelector('[data-amend=receipt]').onclick=async e=>{e.target.disabled=true;try{const note=delegated?(window.prompt(tr('أكد إبلاغ الفرع بالنسخة الجديدة وإلغاء النسخة القديمة','Confirm branch informed of new copy and old copy withdrawn'))||''):'';if(delegated&&!note.trim()){e.target.disabled=false;return;}await mutation(endpoint+'/receipt',{revision:s.revision,kind,note});await refresh();}catch(error){fail(error);e.target.disabled=false;}};}
      }
      if(staff&&s.refund_due_halalas>0&&user.role!=='delivery_manager'){
        const latest=s.amendments.find(a=>a.status==='applied');
        root.insertAdjacentHTML('beforeend',`<form id="amend-refund"><h3>${esc(tr('تسجيل رد فرق التعديل الفعلي','Record actual amendment refund'))}</h3><label>${esc(tr('وسيلة الرد الأصلية','Original payment method'))}<select name="method"><option value="cash">${esc(tr('كاش','Cash'))}</option><option value="card">${esc(tr('شبكة','Card'))}</option></select></label>${field(tr('المبلغ بالريال','Amount in SAR'),'amount',(s.refund_due_halalas/100).toFixed(2),'type="number" min="0.01" step="0.01" required')}${field(tr('مرجع الرد / ملاحظة','Refund reference / note'),'reason','','required maxlength="500"')}<button type="submit">${esc(tr('أؤكد رد المبلغ فعليًا','Confirm amount actually refunded'))}</button></form>`);
        root.querySelector('#amend-refund').onsubmit=async e=>{e.preventDefault();const form=e.target,b=form.querySelector('button');b.disabled=true;try{const f=new FormData(form);await mutation(`/api/orders/${encodeURIComponent(oid)}/refund`,{method:f.get('method'),amount_halalas:Math.round(Number(f.get('amount'))*100),reason:f.get('reason'),amendment_id:latest.id});await refresh();}catch(error){fail(error);b.disabled=false;}};
      }
      if(user.role==='delivery_manager'&&s.refund_due_halalas>0)root.insertAdjacentHTML('beforeend',`<p class="amend-note">${esc(tr('فرق الاسترداد للمحاسبة؛ لا تسجّل رد مبلغ نيابة عنها. وثّق المشكلة من تذكرة مشكلة دفع داخل الطلب.','Refund differences belong to accounting. Record a payment issue from the order support ticket.'))}</p>`);
      if(s.amendments.length)root.insertAdjacentHTML('beforeend',`<details><summary>${esc(tr('سجل التعديلات','Amendment history'))}</summary>${s.amendments.map(a=>`<p>${esc(tr(...states[a.status]))} · ${esc(new Date(a.created_at).toLocaleString())} · ${money(a.quote.total_halalas)}${a.consent_channel?` · ${esc(a.consent_channel==='customer'?tr('موافقة داخل النظام','In-app consent'):a.consent_channel)}`:''}</p>`).join('')}</details>`);
    }
    function renderRequests(closed,reviewer){
      if(s.journey_controls_enabled&&staff&&!closed){
        const open=document.createElement('button');open.type='button';open.textContent=tr('معالجة موثقة — إلغاء أو بلاغ','Documented cancellation or issue');
        open.onclick=()=>{dialog.close();window.KPSInterventions.open({oid,api,mutation,user,onChanged});};root.append(open);
      }
      const requests=s.requests||[],pending=requests.some(r=>!r.decision);
      requests.forEach(r=>{
        const section=document.createElement('section');section.className='amend-note service-request';
        const label=r.decision?(r.decision.status==='resolved'?tr('تمت المعالجة','Processed'):tr('مرفوض','Rejected')):tr('بانتظار المعالجة','Awaiting processing');
        section.innerHTML=`<h3>${esc(requestKind(r.details.kind))} — ${esc(label)}</h3><p>${esc(r.details.note)}</p><p>${esc(r.requester_name||'')}</p><p class="small muted">${esc(new Date(r.created_at).toLocaleString())}</p>${r.decision?`<p>${esc(r.decision.note)}</p><p>${esc(r.decider_name||'')}</p><p class="small muted">${esc(new Date(r.decided_at).toLocaleString())}</p>`:''}`;
        if(!r.decision&&reviewer&&!s.journey_controls_enabled){
          section.insertAdjacentHTML('beforeend',`<p>${esc(tr('رفض طلب الإلغاء يبقي الطلب قائمًا ولا يغيّر قيمة الفاتورة أو التحصيل. إرجاع صنف مجاني لا ينقص المستحق.','Rejecting cancellation keeps the order active and does not change the invoice or collection. Returning a free item does not reduce the amount due.'))}</p>${field(tr('نتيجة المعالجة / سبب الرفض','Outcome / rejection reason'),'request_decision','','required maxlength="500"')}<div class="amend-actions"><button type="button" data-request-decision="resolved">${esc(r.details.kind==='duplicate'?tr('إلغاء هذا الطلب المكرر','Cancel this duplicate order'):tr('تنفيذ الإلغاء','Cancel this order'))}</button><button type="button" data-request-decision="rejected">${esc(tr('رفض الطلب مع السبب','Reject with reason'))}</button></div>`);
          if(['picked_up','problem'].includes(o.delivery_status)||o.preparation_status==='handed_over')section.insertAdjacentHTML('beforeend',`<label>${esc(tr('عند تنفيذ الإلغاء: أين أصبح الطلب؟','When cancelling: where is the order now?'))}<select name="request_physical_resolution"><option value="">${esc(tr('اختر الوضع الفعلي','Select actual disposition'))}</option><option value="returned_to_branch">${esc(tr('أُعيد إلى الفرع','Returned to branch'))}</option><option value="customer_kept">${esc(tr('بقي مع العميل','Kept by customer'))}</option></select></label>`);
          section.querySelectorAll('[data-request-decision]').forEach(b=>b.onclick=async()=>{
            const note=section.querySelector('[name=request_decision]').value.trim();if(!note){fail(new Error(tr('اكتب نتيجة المعالجة أولًا.','Enter the outcome first.')));return;}
            section.querySelectorAll('button').forEach(x=>x.disabled=true);
            try{
              const action=b.dataset.requestDecision;
              if(action==='resolved'){
                const latest=(await api(`/api/orders/${encodeURIComponent(oid)}`)).order;
                if(latest.preparation_status!=='cancelled'&&latest.delivery_status!=='cancelled')await mutation(`/api/order-financial-cases/orders/${encodeURIComponent(oid)}/cancel`,{version:latest.version,reason:note,physical_resolution:section.querySelector('[name=request_physical_resolution]')?.value||null});else await mutation(`/api/order-requests/orders/${encodeURIComponent(oid)}/${encodeURIComponent(r.id)}/decision`,{action,note});
              }
              if(action==='rejected')await mutation(`/api/order-requests/orders/${encodeURIComponent(oid)}/${encodeURIComponent(r.id)}/decision`,{action,note});await refresh();
            }catch(e){fail(e);}finally{section.querySelectorAll('button').forEach(x=>x.disabled=false);}
          });
        }
        root.append(section);
      });
      if(customer&&!closed&&!pending){
        const actions=document.createElement('div');actions.className='amend-actions';
        actions.innerHTML=`<button type="button" data-request-kind="cancel">${esc(tr('طلب إلغاء','Request cancellation'))}</button><button type="button" data-request-kind="duplicate">${esc(tr('طلب مكرر','Duplicate order'))}</button>`;
        actions.querySelectorAll('button').forEach(b=>b.onclick=()=>requestForm(b.dataset.requestKind).catch(fail));root.append(actions);
      }
    }
    async function requestForm(kind){
      let options='';
      if(kind==='duplicate'){
        const result=await api('/api/orders?limit=100');
        options=(result.orders||[]).filter(x=>x.id!==oid).map(x=>`<option value="${esc(x.id)}">${esc(x.snapshot?.order_reference||x.order_no||x.id)}</option>`).join('');
      }
      root.innerHTML=`<form id="service-request-form"><h3>${esc(requestKind(kind))}</h3><p>${esc(tr('يُرسل للمسؤول للمعالجة، ويظل الطلب قائمًا حتى تأكيد الإلغاء.','Sent to the responsible team. Your order remains active until cancellation is confirmed.'))}</p>${kind==='duplicate'?`<label>${esc(tr('الطلب الآخر إن وجد','Other order, if available'))}<select name="related_order_id"><option value="">—</option>${options}</select></label>`:''}${field(tr('السبب','Reason'),'note','','required maxlength="500"')}<p role="alert"></p><button type="submit">${esc(tr('إرسال الطلب','Send request'))}</button></form>`;
      root.querySelector('form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const f=new FormData(e.target);await mutation(`/api/order-requests/orders/${encodeURIComponent(oid)}`,{kind,note:f.get('note'),related_order_id:f.get('related_order_id')||null});await refresh();}catch(error){fail(error);b.disabled=false;}};
    }
    async function edit(){
      const departed=o.preparation_status==='handed_over'||['picked_up','delivered'].includes(o.delivery_status);
      cart=o.snapshot.items.map(x=>({product_id:x.product_id,quantity:x.quantity,units:x.units?.length?x.units.map(v=>({options:v.options||{}})):null,note:x.note||'',label:x.name}));
      const c=o.customer;
      root.innerHTML=`<form id="amend-edit"><p class="amend-note">${esc(tr('غيّر المطلوب فقط. الأصناف التي بدأت بالتجهيز تحتاج تأكيد الفرع.','Change only what is needed. Items already in preparation require branch confirmation.'))}</p><div id="amend-cart"></div>${!departed?`<fieldset><legend>${esc(tr('إضافة صنف أو استبداله','Add or replace an item'))}</legend><select id="amend-product"></select><div id="amend-options"></div>${field(tr('الكمية','Quantity'),'quantity','1','type="number" min="1" max="50" step="1"')}${field(tr('ملاحظة الصنف','Item note'),'item_note','','maxlength="300"')}${button('add',tr('إضافة','Add'))}</fieldset>`:''}${o.service==='delivery'?`<div class="amend-fields">${field(tr('الحي — اختياري','Neighborhood — optional'),'neighborhood',c.neighborhood,'maxlength="120"')}${field(tr('العنوان / المدخل — اختياري','Address / entrance — optional'),'address',c.address,'maxlength="500"')}</div>${field(tr('رابط جوجل ماب الجديد — اتركه فارغًا للإبقاء على الموقع','New Google Maps link — leave blank to keep the location'),'map_url','','type="url" dir="ltr" maxlength="2000"')}<details><summary>${esc(tr('إحداثيات الموقع','Location coordinates'))}</summary><div class="amend-fields">${field(tr('خط العرض','Latitude'),'lat',c.location?.lat??'','type="number" step="any" min="-90" max="90"')}${field(tr('خط الطول','Longitude'),'lng',c.location?.lng??'','type="number" step="any" min="-180" max="180"')}</div></details>`:''}${field(tr('ملاحظة الطلب','Order note'),'notes',o.snapshot.notes||'',`maxlength="1000" ${departed?'readonly':''}`)}${field(tr('سبب التعديل / طلب العميل','Reason / customer request'),'reason','','required maxlength="500"')}<p role="alert"></p><div id="amend-review"></div><div class="amend-actions"><button type="submit">${esc(tr('مراجعة التعديل','Review changes'))}</button>${button('back',tr('رجوع','Back'))}</div></form>`;
      const form=root.querySelector('form');let reviewed=null;
      if(s.journey_controls_enabled&&staff){
        const evidence=await api('/api/order-interventions/orders/'+encodeURIComponent(oid));if(!form.isConnected)return;
        const proof=document.createElement('fieldset');proof.innerHTML=`<legend>توثيق طلب العميل</legend><label>طلب العميل داخل الموقع<select name="customer_request_id"><option value="">اختر طلب العميل الصريح</option>${evidence.customer_requests.filter(r=>r.details.kind==='amend').map(r=>`<option value="${esc(r.id)}">${esc(r.details.note)}</option>`).join('')}</select></label><label>صورة محادثة الواتساب<select name="evidence_attachment_id"><option value="">اختر صورة من محادثة الطلب</option>${evidence.attachments.map((a,i)=>`<option value="${esc(a.id)}">صورة ${i+1} · ${esc(new Date(a.created_at).toLocaleString())}</option>`).join('')}</select></label><p>طلبات الموقع: العميل يرسل التعديل من حسابه، ثم يراجعه المدير. لطلبات الواتساب أرفق صورة المحادثة داخل محادثة الطلب أولًا.</p>`;
        form.prepend(proof);
        const reason=form.querySelector('[name=reason]');reason.value='طلب العميل التعديل';reason.readOnly=true;
      }

      function drawCart(){root.querySelector('#amend-cart').innerHTML=cart.map((x,i)=>`<div class="amend-cart-line"><span>${esc(x.label)} × ${x.quantity}${x.note?' · '+esc(x.note):''}</span>${!departed?`<button type="button" data-remove="${i}">${esc(tr('حذف','Remove'))}</button>`:''}</div>`).join('');root.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{cart.splice(Number(b.dataset.remove),1);drawCart();invalidate();});}
      function invalidate(){reviewed=null;root.querySelector('#amend-review').textContent='';form.querySelector('[type=submit]').textContent=tr('مراجعة التعديل','Review changes');}
      drawCart();root.querySelector('[data-amend=back]').onclick=()=>refresh().catch(fail);form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
      if(!departed){
        const d=await api(`/api/catalog?brand_id=${encodeURIComponent(o.brand_id)}&branch_id=${encodeURIComponent(o.branch_id)}&service=${encodeURIComponent(o.service)}`);if(!form.isConnected)return;
        products=d.products.filter(p=>p.available);const select=root.querySelector('#amend-product');select.innerHTML=products.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} — ${money(p.price_halalas)}</option>`).join('');
        function options(){const p=products.find(p=>p.id===select.value);root.querySelector('#amend-options').innerHTML=(p?.option_groups||[]).map((g,i)=>`<fieldset><legend>${esc(g.name)}</legend>${(g.values||[]).filter(v=>v.available!==false&&v.enabled!==false).map(v=>`<label><input type="checkbox" data-group="${i}" value="${esc(v.code||v.id)}">${esc(v.name)} +${money(v.price_delta_halalas)}</label>`).join('')}</fieldset>`).join('');}
        select.onchange=options;options();root.querySelector('[data-amend=add]').onclick=()=>{try{const p=products.find(p=>p.id===select.value);if(!p)return;const qty=Number(form.elements.quantity.value);if(!Number.isInteger(qty)||qty<1||qty>50||cart.length>=50)throw new Error(tr('راجع الكمية وحد الأصناف.','Check quantity and item limit.'));const opts={};for(const [i,g]of(p.option_groups||[]).entries()){const v=[...root.querySelectorAll(`[data-group="${i}"]:checked`)].map(x=>x.value);if(v.length<(g.min_select??(g.required===false?0:1))||v.length>(g.max_select??1))throw new Error(tr('راجع خيارات ','Check options: ')+g.name);opts[g.code||g.id]=v;}cart.push({product_id:p.id,quantity:qty,units:Array.from({length:qty},()=>({options:opts})),note:form.elements.item_note.value,label:p.name});drawCart();invalidate();}catch(e){fail(e);}};
      }
      form.onsubmit=async e=>{e.preventDefault();const b=form.querySelector('[type=submit]');b.disabled=true;try{const f=new FormData(form),customerInfo={};for(const key of ['name','phone','neighborhood','address','location','car'])if(c[key]!=null)customerInfo[key]=c[key];if(o.service==='delivery'){customerInfo.neighborhood=f.get('neighborhood');customerInfo.address=f.get('address');if(f.get('map_url').trim())customerInfo.location=(await mutation('/api/location/parse-map-url',{url:f.get('map_url').trim()})).location;else if(f.get('lat').trim()&&f.get('lng').trim())customerInfo.location={lat:Number(f.get('lat')),lng:Number(f.get('lng'))};}
        const payload={version:o.version,items:cart.map(({label,...x})=>x),customer:customerInfo,notes:f.get('notes'),reason:f.get('reason'),...(s.journey_controls_enabled&&staff?{customer_request_id:f.get('customer_request_id')||null,evidence_attachment_id:f.get('evidence_attachment_id')||null}:{})},key=JSON.stringify(payload);
        if(reviewed!==key){const {quote}=await mutation(endpoint+'/preview',payload);root.querySelector('#amend-review').innerHTML=`${itemList(quote.items)}<p>${esc(tr('التوصيل','Delivery'))}: ${money(quote.delivery_fee_halalas)}</p><h3>${esc(tr('الإجمالي المقترح','Proposed total'))}: ${money(quote.total_halalas)}</h3>`;reviewed=key;b.textContent=tr('إرسال طلب التعديل','Submit amendment request');return;}
        await mutation(endpoint,payload);await refresh();
      }catch(error){fail(error);}finally{b.disabled=false;}};
    }
    try{await refresh();}catch(error){root.textContent=error.message;}
  }};
})();


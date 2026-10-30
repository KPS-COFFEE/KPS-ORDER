/* Evidence-bound decisions: selection lists only, no narrative required. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  window.KPSInterventions={async open({oid,api,mutation,user,onChanged=()=>{},initialReason}){
    const dialog=document.createElement('dialog');dialog.className='amend-dialog';dialog.dir='rtl';
    dialog.innerHTML='<header><h2>معالجة الطلب</h2><button type="button" data-close aria-label="إغلاق">×</button></header><div class="amend-body"></div>';
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{dialog.remove();onChanged();});
    const root=dialog.querySelector('.amend-body'),path='/api/order-interventions/orders/'+encodeURIComponent(oid);
    const options=map=>Object.entries(map).map(([v,label])=>`<option value="${esc(v)}">${esc(label)}</option>`).join('');
    const label=(text,html)=>`<label>${esc(text)}${html}</label>`;
    const physical=()=>label('مصير الطلب إذا استلمه السائق','<select name="physical_resolution"><option value="">لم يستلم من الفرع</option><option value="returned_to_branch">أعيد فعليًا للفرع</option><option value="customer_kept">بقي مع العميل بموافقة موثقة</option></select>');
    let d;
    async function refresh(){
      d=await api(path);if(!dialog.isConnected)return;
      if(!d.enabled){root.textContent='هذا الطلب يتبع مسار المعالجة السابق.';return;}
      const pending=d.requests.filter(r=>!r.decision),driver=user.role==='driver';
      root.innerHTML=`<p>${d.printed?'أرسلت الطباعة: الإلغاء باعتماد المالك أو المفوض فقط.':'الإلغاء قبل إرسال الطباعة يراجعه مدير التوصيل.'}</p><p role="alert"></p>`;
      for(const r of d.requests){
        const section=document.createElement('section');section.className='panel';const v=r.details||{};
        section.innerHTML=`<h3>${esc(v.kind==='cancel'?'طلب إلغاء':v.kind==='amend'?'طلب تعديل':'بلاغ سائق')}</h3><p>${esc(d.reasons[v.reason_code]||'')} · ${esc(new Date(r.created_at).toLocaleString('en-GB'))}</p><p>${esc(r.decision?d.statuses[r.decision.status]: 'بانتظار '+(d.pending_roles[v.pending_role]||v.pending_role))}</p>`;
        if(!r.decision&&!driver&&(!d.printed||v.kind!=='cancel'||d.high_authority)){
          section.insertAdjacentHTML('beforeend',`<form>${label('سبب القرار الموثق',`<input type="hidden" name="reason_code" value="${esc(v.reason_code)}"><span>${esc(d.reasons[v.reason_code])}</span>`)}${label('نتيجة التواصل',`<select name="contact_result">${options(Object.fromEntries(Object.entries(d.contact_results).filter(([k])=>k!=='customer_requested')))}</select>`)}${physical()}<div><button name="action" value="${v.kind==='cancel'?'approve':'resolve'}">${v.kind==='cancel'?'اعتماد وتنفيذ الإلغاء':'تسجيل المعالجة'}</button><button name="action" value="reject">رفض الطلب</button></div></form>`);
          const form=section.querySelector('form');form.elements.reason_code.value=v.reason_code||'no_answer';form.elements.contact_result.value='not_contacted';
          form.onsubmit=e=>submit(e,async f=>mutation(path+'/'+encodeURIComponent(r.id)+'/decision',{action:e.submitter.value,version:d.version,reason_code:f.get('reason_code'),contact_result:f.get('contact_result'),physical_resolution:f.get('physical_resolution')||null}));
        }
        root.append(section);
      }
      if(!pending.length&&user.role!=='admin'){
        const form=document.createElement('form');
        const kinds=driver?{problem:'رفع مشكلة'}:{cancel:'طلب إلغاء',problem:'متابعة مشكلة'};
        form.innerHTML=`${label('الإجراء',`<select name="kind">${options(kinds)}</select>`)}${label('السبب',`<select name="reason_code">${options(driver?Object.fromEntries(Object.entries(d.reasons).filter(([k])=>!['customer_cancel','customer_change','duplicate'].includes(k))):d.reasons)}</select>`)}${driver?'':label('التواصل مع العميل',`<select name="contact_result">${options(Object.fromEntries(Object.entries(d.contact_results).filter(([k])=>k!=='customer_requested')))}</select>`)}${!driver?label('طلب العميل داخل الموقع',`<select name="customer_request_id"><option value="">سبب تشغيلي / لا ينطبق</option>${d.customer_requests.map(r=>`<option value="${esc(r.id)}">${esc(r.details.note||r.details.kind)} · ${esc(new Date(r.created_at).toLocaleString('en-GB'))}</option>`).join('')}</select>`)+label('صورة المحادثة لطلب الواتساب',`<select name="evidence_attachment_id"><option value="">اختر من صور محادثة الطلب</option>${d.attachments.map((a,i)=>`<option value="${esc(a.id)}">صورة ${i+1} · ${esc(new Date(a.created_at).toLocaleString('en-GB'))}</option>`).join('')}</select>`):''}${driver?'':'<p>إرفاق صورة جديدة: افتح محادثة الطلب وأرفق الصورة، ثم ارجع لهذه الشاشة.</p>'}<button>رفع الطلب للمسؤول</button>`;
        if(initialReason&&form.elements.reason_code.querySelector(`option[value="${initialReason}"]`))form.elements.reason_code.value=initialReason;
        if(form.elements.contact_result)form.elements.contact_result.value='not_contacted';
        form.onsubmit=e=>submit(e,async f=>mutation(path,{version:d.version,kind:f.get('kind'),reason_code:f.get('reason_code'),contact_result:f.get('contact_result')||(f.get('reason_code')==='no_answer'?'no_answer':'not_contacted'),customer_request_id:f.get('customer_request_id')||null,evidence_attachment_id:f.get('evidence_attachment_id')||null}));root.append(form);
      }
    }
    async function submit(e,run){e.preventDefault();const form=e.target;form.querySelectorAll('button').forEach(b=>b.disabled=true);try{await run(new FormData(form));await refresh();}catch(error){root.querySelector('[role=alert]').textContent=error.message;form.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    try{await refresh();}catch(error){root.textContent=error.message;}
  }};
})();

/* A request keeps its original evidence; corrections and handoffs are events. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const states={requested:'بانتظار المراجعة',returned:'معاد لاستكمال مدير التوصيل',resubmitted:'أعيد رفعه للمراجعة',approved:'تم الاعتماد',rejected:'مرفوض',resolved:'تمت المعالجة'};
  const events={requested:'رفع المعاملة',returned:'إرجاع للتصحيح والاستكمال',resubmitted:'إعادة رفع للمراجعة',approved:'اعتماد القرار',rejected:'رفض الطلب',resolved:'تسجيل المعالجة','intervention.accounting_pending':'إحالة المعالجة المالية إلى المحاسبة','financial_case.resolved':'إقفال ملف المعالجة المحاسبية'};
  const clock=value=>value?new Date(value).toLocaleString('en-GB',{timeZone:'Asia/Riyadh'}):'—';
  const customerReasons=new Set(['customer_cancel','customer_change','duplicate']);
  const options=map=>Object.entries(map||{}).map(([v,label])=>`<option value="${esc(v)}">${esc(label)}</option>`).join('');
  const label=(text,html)=>`<label>${esc(text)}${html}</label>`;
  const fact=(name,value)=>`<div><dt>${esc(name)}</dt><dd>${esc(value||'—')}</dd></div>`;
  const physical=()=>label('مصير الطلب إذا استلمه السائق','<select name="physical_resolution"><option value="">لم يستلم من الفرع</option><option value="returned_to_branch">أعيد فعليًا للفرع</option><option value="customer_kept">بقي مع العميل بموافقة موثقة</option></select>');
  const contact=d=>label('نتيجة التواصل',`<select name="contact_result">${options(Object.fromEntries(Object.entries(d.contact_results||{}).filter(([k])=>k!=='customer_requested')))}</select>`);
  const evidence=d=>label('طلب العميل داخل الموقع',`<select name="customer_request_id"><option value="">لم يحدد طلب عميل</option>${(d.customer_requests||[]).map(r=>`<option value="${esc(r.id)}">${esc(r.details?.note||r.details?.kind)} · ${esc(clock(r.created_at))}</option>`).join('')}</select>`)+label('صورة المحادثة الموثقة',`<select name="evidence_attachment_id"><option value="">لم يحدد مرفق</option>${(d.attachments||[]).map((a,i)=>`<option value="${esc(a.id)}">صورة ${i+1} · ${esc(clock(a.created_at))}</option>`).join('')}</select>`);
  function timeline(r,d){
    const history=[{id:r.id,event_type:'requested',created_at:r.created_at,actor_name:r.requested_by_name,reason_code:r.details?.reason_code},...(r.workflow?.history||[]).filter(h=>h.id!==r.id)];
    return `<details class="intervention-history"><summary>تسلسل الإجراءات (${history.length})</summary><ol>${history.map(h=>{const type=String(h.event_type||'').split('.').at(-1);return `<li><div><strong>${esc(events[h.event_type]||events[type]||h.event_type)}</strong><time>${esc(clock(h.created_at))} · الرياض</time></div><p>${esc(h.actor_name||'المسؤول المسجل')}${h.target_manager_name?' ← '+esc(h.target_manager_name):''}</p>${h.reason_code?`<p>السبب: ${esc(d.reasons?.[h.reason_code]||h.reason_code)}</p>`:''}${h.note?`<p class="intervention-note">${esc(h.note)}</p>`:''}</li>`;}).join('')}</ol></details>`;
  }
  window.KPSInterventions={async open({oid,api,mutation,user,onChanged=()=>{},initialReason,uploadEvidence}){
    const dialog=document.createElement('dialog');dialog.className='amend-dialog intervention-dialog';dialog.dir='rtl';
    dialog.innerHTML='<header><h2>معالجة الطلب</h2><button type="button" data-close aria-label="إغلاق">×</button></header><div class="amend-body"></div>';
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{dialog.remove();onChanged();});
    const root=dialog.querySelector('.amend-body'),path='/api/order-interventions/orders/'+encodeURIComponent(oid);
    let d,legacyResult;
    const mayUploadEvidence=user.role!=='driver'&&typeof uploadEvidence==='function',evidenceUploads=new WeakMap();
    const newEvidence=()=>mayUploadEvidence?label('إرفاق صورة جديدة','<input name="evidence_image" type="file" accept="image/jpeg,image/png,image/webp">')+'<p>تُحفظ الصورة عند رفع المعاملة، دون إرسال رسالة للعميل.</p>':'';
    async function selectedEvidence(f,form){
      const file=mayUploadEvidence&&form.elements.evidence_image?.files?.[0];
      if(!file)return f.get('evidence_attachment_id')||null;
      let upload=evidenceUploads.get(file);
      if(!upload){
        upload=Promise.resolve().then(()=>uploadEvidence(file)).then(saved=>{
          if(typeof saved?.id!=='string'||!saved.id.trim())throw new Error('تعذر تأكيد حفظ الصورة. أعد المحاولة.');
          return saved.id;
        });
        evidenceUploads.set(file,upload);
      }
      try{return await upload;}
      catch(error){if(evidenceUploads.get(file)===upload)evidenceUploads.delete(file);throw error;}
    }
    const appendForm=(host,html,run)=>{const form=document.createElement('form');form.innerHTML=html;form.onsubmit=e=>submit(e,f=>run(f,e));host.append(form);return form;};
    async function refresh(){
      d=await api(path);if(!dialog.isConnected)return;
      if(!d.enabled){
        root.innerHTML=`<p>${legacyResult?'حُفظ الإلغاء التشغيلي. '+(legacyResult.financial_case?'أحيلت معالجة المبالغ إلى المحاسبة؛ لم يسجل استرداد.':'لم يسجل استرداد مالي.'):'هذا الطلب يتبع مسار الإلغاء التشغيلي المباشر.'}</p><p role="alert" aria-live="polite"></p>`;
        if(d.legacy_cancel_allowed)appendForm(root,`${label('سبب الإلغاء الموثق','<textarea name="reason" required minlength="3" maxlength="1000" rows="3"></textarea>')}${physical()}<p>يحفظ الطلب وتسلسل الإجراءات. معالجة المبالغ منفصلة لدى المحاسبة.</p><button>اعتماد الإلغاء التشغيلي</button>`,async f=>{
          const reason=String(f.get('reason')||'').trim();if(reason.length<3)throw new Error('اكتب سبب الإلغاء الموثق.');
          legacyResult=await mutation('/api/order-financial-cases/orders/'+encodeURIComponent(oid)+'/cancel',{version:d.version,reason,physical_resolution:f.get('physical_resolution')||null});
        });
        return;
      }
      const pending=d.requests.filter(r=>!r.decision&&!['approved','rejected','resolved'].includes(r.workflow?.status)),driver=user.role==='driver';
      const financialPending=d.requests.some(r=>r.workflow?.financial_pending),pendingCancellation=pending.some(r=>r.details?.kind==='cancel');
      const intro=financialPending?'الإلغاء التشغيلي معتمد؛ معالجة المبالغ ما زالت لدى المحاسبة. الاعتماد لا يسجل استردادًا للعميل.':pending.some(r=>r.workflow?.status==='returned')?'المعاملة معادة لمدير التوصيل المعيّن. يلزم استكمال التصحيح والأدلة ثم إعادة رفعها للمراجعة.':pendingCancellation&&d.has_recorded_collection?'هذا الطلب مسجل له تحصيل. اعتماد الإلغاء للمالك أو المفوض بإلغاء الطلبات المحصلة؛ معالجة المبالغ لدى المحاسبة.':pendingCancellation&&d.printed?'طلب الإلغاء بانتظار المراجع المخول بعد إرسال الطباعة. راجع سبب الإحالة والأدلة أدناه.':!pending.length&&d.requests.length?'القرارات المسجلة محفوظة أدناه. يوضح كل سجل الإجراء المنفذ والمسؤول عن أي متابعة متبقية.':'راجع سبب الإحالة والمطلوب من المسؤول الحالي قبل اتخاذ القرار.';
      root.innerHTML=`<p class="intervention-intro">${intro}</p><p role="alert" aria-live="polite"></p>`;
      for(const r of d.requests){
        const section=document.createElement('section');section.className='panel intervention-request';const v=r.details||{},w=r.workflow,currentReason=w?.reason_code||v.reason_code,status=w?.status||r.decision?.status||'requested';
        const actions=w?new Set(w.available_actions||[]):new Set(!r.decision&&!driver&&(!d.printed||v.kind!=='cancel'||d.high_authority)?[v.kind==='cancel'?'approve':'resolve','reject']:[]);
        const responsibleRole=w?w.current_responsible_role:r.decision?null:v.pending_role;
        const responsible=w?.current_responsible_name||d.pending_roles?.[responsibleRole]||responsibleRole||'لا يوجد إجراء معلق';
        const next=w?.next_action_label||(r.decision?'اكتمل القرار المسجل.':actions.size?'بانتظار مراجعتك واتخاذ القرار.':'بانتظار إجراء المسؤول المختص.');
        section.innerHTML=`<header class="intervention-request-heading"><h3>${esc(v.kind==='cancel'?'طلب إلغاء':v.kind==='amend'?'طلب تعديل':'بلاغ سائق')}</h3><span class="intervention-state">${esc(states[status]||d.statuses?.[status]||status)}</span></header><dl class="intervention-facts">${fact('رافع المعاملة',r.requested_by_name)}${fact('وقت الإحالة — الرياض',clock(r.created_at))}${fact('سبب الإحالة الأصلي',d.reasons?.[w?.original_reason_code||v.reason_code]||w?.original_reason_code||v.reason_code)}${currentReason!==(w?.original_reason_code||v.reason_code)?fact('السبب بعد التصحيح',d.reasons?.[currentReason]||currentReason):''}</dl><div class="intervention-next"><strong>المسؤول الحالي: ${esc(responsible)}</strong><p>${esc(next)}</p></div>${w?.return_note?`<div class="intervention-correction"><strong>توضيح التصحيح والاستكمال</strong><p class="intervention-note">${esc(w.return_note)}</p></div>`:''}${timeline(r,d)}`;
        const revision=w?{workflow_revision:w.revision}:{};
        const decisionActions=['approve','resolve','reject'].filter(a=>actions.has(a));
        if(decisionActions.length){
          const form=appendForm(section,`${label('سبب القرار الحالي',`<input type="hidden" name="reason_code" value="${esc(currentReason)}"><span>${esc(d.reasons?.[currentReason]||currentReason)}</span>`)}${contact(d)}${physical()}<div class="intervention-actions">${decisionActions.map(a=>`<button name="action" value="${a}"${a==='reject'?' class="quiet"':''}>${a==='approve'?'اعتماد وتنفيذ الإلغاء':a==='resolve'?'تسجيل المعالجة':'رفض الطلب'}</button>`).join('')}</div>`,(f,e)=>mutation(path+'/'+encodeURIComponent(r.id)+'/decision',{action:e.submitter.value,version:d.version,...revision,reason_code:f.get('reason_code'),contact_result:f.get('contact_result'),physical_resolution:f.get('physical_resolution')||null}));
          form.elements.reason_code.value=currentReason||'no_answer';form.elements.contact_result.value='not_contacted';
        }
        if(actions.has('return')){
          const box=document.createElement('details');box.className='intervention-return';box.innerHTML='<summary>إرجاع لمدير التوصيل للتصحيح والاستكمال</summary><p>يبقى السبب الأصلي محفوظًا. يسجل السبب المصحح والتوضيح والمسؤول في تسلسل المعاملة.</p>';
          const targets=d.return_targets||[],preferred=targets.some(t=>t.id===w.preferred_return_target_id)?w.preferred_return_target_id:'';
          const form=appendForm(box,`${label('السبب المصحح',`<select name="corrected_reason_code">${options(d.reasons)}</select>`)}${label('مدير التوصيل المطلوب منه الاستكمال',`<select name="target_manager_id"><option value="">اختر مدير التوصيل</option>${targets.map(t=>`<option value="${esc(t.id)}">${esc(t.display_name)}</option>`).join('')}</select>`)}${label('ما الخطأ؟ وما المطلوب تصحيحه؟','<textarea name="note" required minlength="3" maxlength="1500" rows="3"></textarea>')}<button>إرجاع المعاملة للاستكمال</button>`,f=>{
            if(!f.get('target_manager_id')||String(f.get('note')||'').trim().length<3)throw new Error('حدد مدير التوصيل واكتب توضيح التصحيح المطلوب.');
            return mutation(path+'/'+encodeURIComponent(r.id)+'/return',{version:d.version,...revision,corrected_reason_code:f.get('corrected_reason_code'),target_manager_id:f.get('target_manager_id'),note:String(f.get('note')).trim()});
          });form.elements.corrected_reason_code.value=currentReason;form.elements.target_manager_id.value=preferred;section.append(box);
        }
        if(actions.has('resubmit')){
          const form=appendForm(section,`<h4>استكمال المعاملة وإعادتها للمراجعة</h4><p>السبب الحالي: ${esc(d.reasons?.[currentReason]||currentReason)}</p>${contact(d)}${customerReasons.has(currentReason)?`<p>هذا السبب يحتاج طلب عميل أو دليل موثق قبل إعادة الرفع.</p>${evidence(d)}`:''}${newEvidence()}${label('ما الذي استُكمل؟','<textarea name="note" required minlength="3" maxlength="1500" rows="3"></textarea>')}<button>إعادة الرفع للمراجعة</button>`,async(f,e)=>mutation(path+'/'+encodeURIComponent(r.id)+'/resubmit',{version:d.version,...revision,contact_result:f.get('contact_result'),note:String(f.get('note')||'').trim(),customer_request_id:f.get('customer_request_id')||null,evidence_attachment_id:await selectedEvidence(f,e.target)}));form.elements.contact_result.value='not_contacted';
        }
        root.append(section);
      }
      const terminalCancellation=d.requests.some(r=>r.details?.kind==='cancel'&&(r.decision?.status==='approved'||r.workflow?.status==='approved'));
      const createKinds=Array.isArray(d.available_create_kinds)?d.available_create_kinds:!pending.length&&user.role!=='admin'&&!terminalCancellation&&!financialPending?(driver?['problem']:['cancel','problem']):[];
      const kinds=Object.fromEntries(Object.entries(driver?{problem:'رفع مشكلة'}:{cancel:'طلب إلغاء',problem:'متابعة مشكلة'}).filter(([kind])=>createKinds.includes(kind)));
      if(!pending.length&&Object.keys(kinds).length){
        const form=appendForm(root,`${label('الإجراء',`<select name="kind">${options(kinds)}</select>`)}${label('السبب',`<select name="reason_code">${options(driver?Object.fromEntries(Object.entries(d.reasons).filter(([k])=>!customerReasons.has(k))):d.reasons)}</select>`)}${driver?'':contact(d)+evidence(d)+(mayUploadEvidence?newEvidence():'<p>إرفاق صورة جديدة: افتح محادثة الطلب وأرفق الصورة، ثم ارجع لهذه الشاشة.</p>')}<button>رفع الطلب للمسؤول</button>`,async(f,e)=>mutation(path,{version:d.version,kind:f.get('kind'),reason_code:f.get('reason_code'),contact_result:f.get('contact_result')||(f.get('reason_code')==='no_answer'?'no_answer':'not_contacted'),customer_request_id:f.get('customer_request_id')||null,evidence_attachment_id:await selectedEvidence(f,e.target)}));
        if(initialReason&&form.elements.reason_code.querySelector(`option[value="${initialReason}"]`))form.elements.reason_code.value=initialReason;
        if(form.elements.contact_result)form.elements.contact_result.value='not_contacted';
      }
    }
    async function submit(e,run){
      e.preventDefault();const form=e.target,alert=root.querySelector('[role=alert]');alert.textContent='';form.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{await run(new FormData(form));await refresh();}
      catch(error){alert.textContent=error.message;form.querySelectorAll('button').forEach(b=>b.disabled=false);}
    }
    try{await refresh();}catch(error){root.textContent=error.message;}
  }};
})();

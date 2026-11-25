/* A request keeps its original evidence; corrections and handoffs are events. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tr=(ar,en)=>document.documentElement?.lang==='en'?en:ar;
  // Translate system labels only; names, customer messages and employee notes remain verbatim.
  const labels={
  "reasons": {
    "customer_cancel": [
      "طلب العميل الإلغاء",
      "Customer requested cancellation"
    ],
    "customer_change": [
      "طلب العميل التعديل",
      "Customer requested an amendment"
    ],
    "no_answer": [
      "العميل لا يرد",
      "Customer not answering"
    ],
    "late_complaint": [
      "شكوى تأخير",
      "Delay complaint"
    ],
    "wrong_address": [
      "مشكلة في العنوان",
      "Address problem"
    ],
    "damaged": [
      "مشكلة في الطلب",
      "Order problem"
    ],
    "customer_not_present": [
      "العميل غير موجود",
      "Customer not present"
    ],
    "amount_short": [
      "المبلغ ناقص",
      "Insufficient payment"
    ],
    "payment_failed": [
      "تعذر الدفع",
      "Payment failed"
    ],
    "missing_or_wrong_order": [
      "نقص أو خطأ بالطلب",
      "Missing or incorrect items"
    ],
    "other": [
      "مشكلة أخرى",
      "Other problem"
    ],
    "branch_unavailable": [
      "تعذر تنفيذ الفرع",
      "Branch unable to fulfil order"
    ],
    "duplicate": [
      "طلب مكرر",
      "Duplicate order"
    ]
  },
  "contact_results": {
    "customer_requested": [
      "طلب العميل داخل الموقع",
      "Customer requested on website"
    ],
    "agreed": [
      "تم التواصل ووافق",
      "Contacted and agreed"
    ],
    "declined": [
      "تم التواصل ولم يوافق",
      "Contacted and declined"
    ],
    "no_answer": [
      "لا يرد",
      "No answer"
    ],
    "not_contacted": [
      "لم يتم التواصل",
      "Not contacted"
    ]
  },
  "pending_roles": {
    "delivery_manager": [
      "مدير التوصيل",
      "Delivery manager"
    ],
    "owner_or_cancel_delegate": [
      "المالك أو المفوض بالإلغاء",
      "Owner or cancellation delegate"
    ],
    "owner_or_collected_cancel_delegate": [
      "المالك أو المفوض بإلغاء الطلبات المحصلة",
      "Owner or delegate for collected-order cancellation"
    ],
    "accounting": [
      "المحاسب",
      "Accountant"
    ],
    "owner": [
      "المالك",
      "Owner"
    ],
    "admin": [
      "المراجع المفوض",
      "Authorized reviewer"
    ],
    "reviewer": [
      "المراجع الذي أعاد المعاملة",
      "Reviewer who returned the request"
    ]
  }
};
  const systemText={
  "استكمال الملاحظة وإعادة رفع المعاملة للاعتماد": "Complete the note and resubmit for approval",
  "مراجعة سبب الإلغاء والأدلة واتخاذ قرار الاعتماد": "Review the cancellation reason and evidence, then decide approval",
  "استكمال معالجة التعديل ثم إقفال المتابعة": "Complete the amendment process, then close the follow-up",
  "معالجة البلاغ وتوثيق الإجراء المتخذ": "Resolve the reported problem and document the action taken",
  "المحاسبة: مطابقة التحصيل والاسترداد الفعلي وإقفال الملف المالي": "Accounting: reconcile actual collections and refunds, then close the financial case",
  "اكتملت المعالجة التشغيلية": "Operational treatment completed",
  "الطلب غير متاح ضمن الصلاحيات.": "This order is unavailable within your permissions.",
  "صلاحية اعتماد المعالجة متوقفة.": "Issue approval permission is disabled.",
  "تغيرت متابعة المعاملة؛ حدّث التفاصيل قبل الإجراء.": "The request has changed. Refresh its details before taking action.",
  "يلزم التفويض الصريح الكامل لإلغاء هذا الطلب.": "Explicit cancellation authorization is required for this order.",
  "صلاحية إلغاء الطلبات متوقفة.": "Order cancellation permission is disabled.",
  "بعد إرسال الطباعة، الإلغاء من المالك أو المفوض بالإلغاء فقط.": "After printing is sent, only the owner or cancellation delegate can cancel.",
  "ارفع طلب إلغاء موثق ثم اعتمده من مسار المعالجة.": "Submit a documented cancellation request for approval through the resolution process.",
  "وضح سبب تعذر إرفاق المحادثة.": "Explain why the conversation cannot be attached.",
  "أكد أن رسالة العميل تطلب الإجراء المختار صراحة.": "Confirm that the customer message explicitly requests the selected action.",
  "اختر رسالة العميل من محادثة هذا الطلب المتاحة ضمن صلاحياتك.": "Select the customer message from this order conversation within your permissions.",
  "أرفق صورة المحادثة من محادثة هذا الطلب لإثبات طلب العميل.": "Attach a conversation image from this order to document the customer request.",
  "يلزم طلب العميل الصريح المسجل داخل محادثة الموقع.": "An explicit customer request recorded in the website conversation is required.",
  "طلب العميل يخص نسخة سابقة؛ يلزم طلب جديد.": "The customer request refers to an earlier version. A new request is required.",
  "تمت معالجة طلب العميل سابقًا؛ يلزم طلب جديد.": "The customer request was already processed. A new request is required.",
  "محادثة طلب العميل غير متاحة.": "The customer request conversation is unavailable.",
  "قائمة المعالجة للمراجع المخول أو مدير التوصيل المكلف بالاستكمال.": "The resolution inbox is for the authorized reviewer or assigned delivery manager.",
  "صلاحية المعالجة غير متاحة.": "Issue resolution permission is unavailable.",
  "رفع المعالجة من مسؤول الخدمة أو السائق المكلف فقط.": "Only the service employee or assigned driver can submit this request.",
  "هذا الطلب يتبع مسار المعالجة السابق.": "This order follows the previous resolution process.",
  "تغير الطلب؛ حدّث التفاصيل.": "The order has changed. Refresh its details.",
  "الطلب منتهٍ؛ استخدم المعالجة المالية المعتمدة.": "The order has ended. Use the approved financial resolution process.",
  "يوجد إجراء مفتوح؛ أكمل متابعته أولًا.": "An action is already open. Complete its follow-up first.",
  "السائق يرفع بلاغًا؛ لا يلغي أو يعدل الطلب.": "Drivers can report a problem; they cannot cancel or amend the order.",
  "اربط طلب التعديل بتعديل الأصناف أو العنوان المطلوب.": "Link the amendment request to the required item or address change.",
  "طلب المعالجة غير موجود.": "The resolution request was not found.",
  "المعاملة مصعدة للمالك أو المفوض؛ يلزم اعتماده.": "The request was escalated to the owner or delegate and requires their approval.",
  "الإلغاء بعد إرسال الطباعة للمالك أو المفوض فقط.": "After printing is sent, cancellation is restricted to the owner or delegate.",
  "الإجراء معالج سابقًا.": "This action has already been processed.",
  "اختر مدير توصيل فعالًا ومخولًا ضمن فرع الطلب.": "Choose an active, authorized delivery manager within the order branch.",
  "استكمال المعاملة لمدير التوصيل المكلف بها أو المالك فقط.": "Only the assigned delivery manager or owner can complete this request.",
  "المعاملة ليست معادة للاستكمال.": "This request has not been returned for completion.",
  "المعاملة لدى مدير التوصيل للاستكمال؛ يجب إعادة رفعها قبل القرار.": "The request is with the delivery manager for completion and must be resubmitted before a decision.",
  "تغير الطلب؛ راجع المعالجة قبل الاعتماد.": "The order has changed. Review its resolution before approval.",
  "تغير المبلغ؛ يلزم طلب اعتماد جديد.": "The amount has changed. A new approval request is required.",
  "سبب القرار يجب أن يطابق السبب الحالي الموثق؛ أعد المعاملة للاستكمال عند تغير السبب.": "The decision reason must match the current documented reason. Return the request for completion if the reason has changed.",
  "اعتماد التعديل من مسار التعديل؛ البلاغ يحال أو يقفل بعد المعالجة.": "Approve amendments through the amendment process. Refer or close the report after resolution.",
  "أكمل مراجعة التعديل واعتماد نسخته أو رفضه من مسار التعديل أولًا.": "First review and approve or reject the amendment through the amendment process.",
  "اكتب سبب رفض واضحا.": "Enter a clear rejection reason.",
  "طلب الإلغاء لا يقفل دون تنفيذ الإلغاء المعتمد.": "A cancellation request cannot be closed before the approved cancellation is executed.",
  "استثناء المرفق يحتاج اعتماد المدير أو المالك.": "An attachment exception requires manager or owner approval.",
  "صلاحية عرض المعالجة متوقفة.": "Resolution viewing permission is disabled.",
  "مهمة الإلغاء خارج نطاق التفويض.": "The cancellation task is outside the delegated scope."
};
  const systemLabel=value=>tr(value,systemText[value]||value);
  const codedLabel=(kind,code,map)=>document.documentElement?.lang==='en'&&labels[kind]?.[code]?labels[kind][code][1]:map?.[code]||code;
  const reasonLabel=(code,d)=>codedLabel('reasons',code,d.reasons);
  const localizedMap=(kind,map)=>Object.fromEntries(Object.entries(map||{}).map(([code,value])=>[code,codedLabel(kind,code,map)]));
  const states=()=>({requested:tr('بانتظار المراجعة','Awaiting review'),returned:tr('معاد لاستكمال مدير التوصيل','Returned to the delivery manager for completion'),resubmitted:tr('أعيد رفعه للمراجعة','Resubmitted for review'),approved:tr('تم الاعتماد','Approved'),rejected:tr('مرفوض','Rejected'),resolved:tr('تمت المعالجة','Resolved')});
  const events=()=>({requested:tr('رفع المعاملة','Request submitted'),returned:tr('إرجاع للتصحيح والاستكمال','Returned for correction and completion'),resubmitted:tr('إعادة رفع للمراجعة','Resubmitted for review'),approved:tr('اعتماد القرار','Decision approved'),rejected:tr('رفض الطلب','Request rejected'),resolved:tr('تسجيل المعالجة','Record resolution'),'intervention.accounting_pending':tr('إحالة المعالجة المالية إلى المحاسبة','Financial treatment referred to accounting'),'financial_case.resolved':tr('إقفال ملف المعالجة المحاسبية','Accounting case closed')});
  const clock=value=>value?new Date(value).toLocaleString('en-GB',{timeZone:'Asia/Riyadh'}):'—';
  const customerReasons=new Set(['customer_cancel','customer_change','duplicate']);
  const options=map=>Object.entries(map||{}).map(([v,label])=>`<option value="${esc(v)}">${esc(label)}</option>`).join('');
  const label=(text,html)=>`<label>${esc(text)}${html}</label>`;
  const fact=(name,value)=>`<div><dt>${esc(name)}</dt><dd>${esc(value||'—')}</dd></div>`;
  const physical=()=>label(tr('مصير الطلب إذا استلمه السائق','Item disposition after driver pickup'),tr('<select name="physical_resolution"><option value="">لم يستلم من الفرع</option><option value="returned_to_branch">أعيد فعليًا للفرع</option><option value="customer_kept">بقي مع العميل بموافقة موثقة</option></select>','<select name="physical_resolution"><option value="">Not picked up from branch</option><option value="returned_to_branch">Physically returned to branch</option><option value="customer_kept">Kept by customer with documented approval</option></select>'));
  const contact=d=>label(tr('نتيجة التواصل','Contact outcome'),`<select name="contact_result">${options(Object.fromEntries(Object.entries(localizedMap('contact_results',d.contact_results)).filter(([k])=>k!=='customer_requested')))}</select>`);
  const evidence=d=>label(tr('طلب العميل داخل الموقع','Customer request on the website'),`<select name="customer_request_id"><option value="">${tr("لم يحدد طلب عميل","No customer request selected")}</option>${(d.customer_requests||[]).map(r=>`<option value="${esc(r.id)}">${esc(r.details?.note||r.details?.kind)} · ${esc(clock(r.created_at))}</option>`).join('')}</select>`)+label(tr('صورة المحادثة الموثقة','Documented conversation image'),`<select name="evidence_attachment_id"><option value="">${tr("لم يحدد مرفق","No attachment selected")}</option>${(d.attachments||[]).map((a,i)=>`<option value="${esc(a.id)}">${tr("صورة","Image")} ${i+1} · ${esc(clock(a.created_at))}</option>`).join('')}</select>`);
  const messageProof=d=>(d.customer_messages||[]).length?label(tr('رسالة العميل في محادثة الطلب','Customer message in the order conversation'),`<select name="evidence_message_id"><option value="">${tr("اختر رسالة العميل إذا لم يوجد طلب صالح أعلاه","Select the customer message if no valid request is available above")}</option>${d.customer_messages.map(m=>`<option value="${esc(m.id)}">${esc(m.text)} · ${esc(clock(m.created_at))}</option>`).join('')}</select>`)+label(tr('تأكيد طلب العميل','Confirm customer request'),tr('<input type="checkbox" name="customer_message_confirmed"> أؤكد أن الرسالة المختارة تطلب الإجراء المحدد صراحة، وأنه ما زال مطلوبا للطلب الحالي','<input type="checkbox" name="customer_message_confirmed"> I confirm that the selected message explicitly requests this action and that it is still required for the current order'))+tr('<p>محادثة الموقع تكفي ولا تحتاج صورة مكررة. طلب التعديل لا يعد طلب إلغاء.</p>','<p>The website conversation is sufficient; no duplicate image is needed. An amendment request is not a cancellation request.</p>'):'';
  const exceptionFields=(f,e)=>e?.submitter?.value==='attachment_unavailable'?{attachment_unavailable:true,attachment_unavailable_reason:String(f.get('attachment_unavailable_reason')||'').trim()}:{};
  const exceptionForm=()=>label(tr('سبب تعذر الإرفاق','Reason the attachment is unavailable'),tr('<input name="attachment_unavailable_reason" maxlength="1000" placeholder="مثال: فشل رفع الصورة أو المحادثة غير متوفرة الآن">','<input name="attachment_unavailable_reason" maxlength="1000" placeholder="Example: image upload failed or the conversation is currently unavailable">'))+tr('<button class="quiet" name="action" value="attachment_unavailable">تعذر الإرفاق — تصعيد للمدير</button><p>يراجع محمد أو المالك الإجراء ويتخذ القرار دون انتظار الصورة. يمكن إضافتها لاحقًا.</p>','<button class="quiet" name="action" value="attachment_unavailable">Attachment unavailable — escalate to manager</button><p>Mohammed or the owner reviews and decides without waiting for the image. It can be added later.</p>');
  const proofFields=f=>f.get('evidence_message_id')?{evidence_message_id:f.get('evidence_message_id'),customer_message_confirmed:f.get('customer_message_confirmed')==='on'}:{};
  function timeline(r,d){
    const history=[{id:r.id,event_type:'requested',created_at:r.created_at,actor_name:r.requested_by_name,reason_code:r.details?.reason_code},...(r.workflow?.history||[]).filter(h=>h.id!==r.id)];
    return `<details class="intervention-history"><summary>${tr("تسلسل الإجراءات (","Action history (")}${history.length})</summary><ol>${history.map(h=>{const type=String(h.event_type||'').split('.').at(-1);return `<li><div><strong>${esc(events()[h.event_type]||events()[type]||h.event_type)}</strong><time>${esc(clock(h.created_at))} · ${tr("الرياض","Riyadh")}</time></div><p>${esc(h.actor_name||tr('المسؤول المسجل','Recorded responsible employee'))}${h.target_manager_name?' ← '+esc(h.target_manager_name):''}</p>${h.reason_code?`<p>${tr("السبب:","Reason:")} ${esc(reasonLabel(h.reason_code,d))}</p>`:''}${h.note?`<p class="intervention-note">${esc(h.note)}</p>`:''}</li>`;}).join('')}</ol></details>`;
  }
  window.KPSInterventions={async open({oid,api,mutation,user,onChanged=()=>{},initialReason,uploadEvidence}){
    const dialog=document.createElement('dialog');dialog.className='amend-dialog intervention-dialog';dialog.dir=tr('rtl','ltr');dialog.lang=tr('ar','en');
    dialog.innerHTML=tr('<header><h2>معالجة الطلب</h2><button type="button" data-close aria-label="إغلاق">×</button></header><div class="amend-body"></div>','<header><h2>Order issue resolution</h2><button type="button" data-close aria-label="Close">×</button></header><div class="amend-body"></div>');
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{dialog.remove();onChanged();});
    const root=dialog.querySelector('.amend-body'),path='/api/order-interventions/orders/'+encodeURIComponent(oid);
    let d,legacyResult;
    const mayUploadEvidence=user.role!=='driver'&&typeof uploadEvidence==='function',evidenceUploads=new WeakMap();
    const newEvidence=()=>mayUploadEvidence?label(tr('إرفاق صورة جديدة','Attach a new image'),'<input name="evidence_image" type="file" accept="image/jpeg,image/png,image/webp">')+tr('<p>تُحفظ الصورة عند رفع المعاملة، دون إرسال رسالة للعميل.</p>','<p>The image is saved when the request is submitted, without sending a customer message.</p>'):'';
    async function selectedEvidence(f,form){
      const file=mayUploadEvidence&&form.elements.evidence_image?.files?.[0];
      if(!file)return f.get('evidence_attachment_id')||null;
      let upload=evidenceUploads.get(file);
      if(!upload){
        upload=Promise.resolve().then(()=>uploadEvidence(file)).then(saved=>{
          if(typeof saved?.id!=='string'||!saved.id.trim())throw new Error(tr('تعذر تأكيد حفظ الصورة. أعد المحاولة.','Could not confirm that the image was saved. Try again.'));
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
        root.innerHTML=`<p>${legacyResult?tr('حُفظ الإلغاء التشغيلي. ','Operational cancellation recorded. ')+(legacyResult.financial_case?tr('أحيلت معالجة المبالغ إلى المحاسبة؛ لم يسجل استرداد.','Financial treatment was referred to accounting; no refund was recorded.'):tr('لم يسجل استرداد مالي.','No refund was recorded.')):tr('هذا الطلب يتبع مسار الإلغاء التشغيلي المباشر.','This order follows the direct operational cancellation process.')}</p><p role="alert" aria-live="polite"></p>`;
        if(d.legacy_cancel_allowed)appendForm(root,`${label(tr('سبب الإلغاء الموثق','Documented cancellation reason'),'<textarea name="reason" required minlength="3" maxlength="1000" rows="3"></textarea>')}${physical()}<p>${tr("يحفظ الطلب وتسلسل الإجراءات. معالجة المبالغ منفصلة لدى المحاسبة.","The order and its history are preserved. Financial treatment is handled separately by accounting.")}</p><button>${tr("اعتماد الإلغاء التشغيلي","Approve operational cancellation")}</button>`,async f=>{
          const reason=String(f.get('reason')||'').trim();if(reason.length<3)throw new Error(tr('اكتب سبب الإلغاء الموثق.','Enter the documented cancellation reason.'));
          legacyResult=await mutation('/api/order-financial-cases/orders/'+encodeURIComponent(oid)+'/cancel',{version:d.version,reason,physical_resolution:f.get('physical_resolution')||null});
        });
        return;
      }
      const pending=d.requests.filter(r=>!r.decision&&!['approved','rejected','resolved'].includes(r.workflow?.status)),driver=user.role==='driver';
      const financialPending=d.requests.some(r=>r.workflow?.financial_pending),pendingCancellation=pending.some(r=>r.details?.kind==='cancel');
      const intro=financialPending?tr('الإلغاء التشغيلي معتمد؛ معالجة المبالغ ما زالت لدى المحاسبة. الاعتماد لا يسجل استردادًا للعميل.','Operational cancellation is approved; financial treatment remains with accounting. Approval does not record a customer refund.'):pending.some(r=>r.workflow?.status==='returned')?tr('المعاملة معادة لمدير التوصيل المعيّن. يلزم استكمال التصحيح والأدلة ثم إعادة رفعها للمراجعة.','The request was returned to the assigned delivery manager. Complete the corrections and evidence, then resubmit for review.'):pendingCancellation&&d.has_recorded_collection?tr('هذا الطلب مسجل له تحصيل. اعتماد الإلغاء للمالك أو المفوض بإلغاء الطلبات المحصلة؛ معالجة المبالغ لدى المحاسبة.','This order has a recorded collection. Cancellation requires the owner or a delegate authorized to cancel collected orders; accounting handles the amounts.'):pendingCancellation&&d.printed?tr('طلب الإلغاء بانتظار المراجع المخول بعد إرسال الطباعة. راجع سبب الإحالة والأدلة أدناه.','Cancellation is awaiting an authorized reviewer after printing was sent. Review the referral reason and evidence below.'):!pending.length&&d.requests.length?tr('القرارات المسجلة محفوظة أدناه. يوضح كل سجل الإجراء المنفذ والمسؤول عن أي متابعة متبقية.','Recorded decisions are preserved below. Each entry shows the action taken and who is responsible for any remaining follow-up.'):tr('راجع سبب الإحالة والمطلوب من المسؤول الحالي قبل اتخاذ القرار.','Review the referral reason and the action required from the current responsible employee before deciding.');
      root.innerHTML=`<p class="intervention-intro">${intro}</p><p role="alert" aria-live="polite"></p>`;
      for(const r of d.requests){
        const section=document.createElement('section');section.className='panel intervention-request';const v=r.details||{},w=r.workflow,currentReason=w?.reason_code||v.reason_code,status=w?.status||r.decision?.status||'requested';
        const actions=w?new Set(w.available_actions||[]):new Set(!r.decision&&!driver&&(!d.printed||v.kind!=='cancel'||d.high_authority)?[v.kind==='cancel'?'approve':'resolve','reject']:[]);
        const responsibleRole=w?w.current_responsible_role:r.decision?null:v.pending_role;
        const responsible=w?.current_responsible_name||(responsibleRole?codedLabel('pending_roles',responsibleRole,d.pending_roles):null)||responsibleRole||tr('لا يوجد إجراء معلق','No pending action');
        const next=(w?.next_action_label?(r.decision?.note===w.next_action_label?w.next_action_label:systemLabel(w.next_action_label)):null)||(r.decision?tr('اكتمل القرار المسجل.','The recorded decision is complete.'):actions.size?tr('بانتظار مراجعتك واتخاذ القرار.','Awaiting your review and decision.'):tr('بانتظار إجراء المسؤول المختص.','Awaiting action from the responsible employee.'));
        section.innerHTML=`<header class="intervention-request-heading"><h3>${esc(v.kind==='cancel'?tr('طلب إلغاء','Cancellation request'):v.kind==='amend'?tr('طلب تعديل','Amendment request'):tr('متابعة مشكلة','Issue follow-up'))}</h3><span class="intervention-state">${esc(states()[status]||d.statuses?.[status]||status)}</span></header><dl class="intervention-facts">${fact(tr('رافع المعاملة','Submitted by'),r.requested_by_name)}${fact(tr('وقت الإحالة — الرياض','Referral time — Riyadh'),clock(r.created_at))}${fact(tr('سبب الإحالة الأصلي','Original referral reason'),reasonLabel(w?.original_reason_code||v.reason_code,d))}${currentReason!==(w?.original_reason_code||v.reason_code)?fact(tr('السبب بعد التصحيح','Corrected reason'),reasonLabel(currentReason,d)):''}</dl><div class="intervention-next"><strong>${tr("المسؤول الحالي:","Currently responsible:")} ${esc(responsible)}</strong><p>${esc(next)}</p></div>${w?.return_note?`<div class="intervention-correction"><strong>${tr("توضيح التصحيح والاستكمال","Correction and completion details")}</strong><p class="intervention-note">${esc(w.return_note)}</p></div>`:''}${v.note?`<p class="intervention-note">${esc(v.note)}</p>`:''}${(w?.attachment_unavailable??v.attachment_unavailable)?`<div class="intervention-correction"><strong>${tr("استثناء مرفق لهذه المعاملة","Attachment exception for this request")}</strong><p>${esc(w?.attachment_unavailable_reason||v.attachment_unavailable_reason)}</p><p>${tr("القرار متاح دون صورة، ويمكن استكمال المرفق لاحقًا.","A decision can be made without an image; the attachment can be added later.")}</p></div>`:''}${timeline(r,d)}`;
        const revision=w?{workflow_revision:w.revision}:{};
        const decisionActions=['approve','resolve','reject'].filter(a=>actions.has(a));
        if(decisionActions.length){
          const form=appendForm(section,`${label(tr('سبب القرار الحالي','Current decision reason'),`<input type="hidden" name="reason_code" value="${esc(currentReason)}"><span>${esc(reasonLabel(currentReason,d))}</span>`)}${contact(d)}${physical()}${label(tr('ملاحظات القرار / سبب الرفض','Decision notes / rejection reason'),'<textarea name="note" maxlength="1500" rows="2"></textarea>')}<div class="intervention-actions">${decisionActions.map(a=>`<button name="action" value="${a}"${a==='reject'?' class="quiet"':''}>${a==='approve'?tr('اعتماد وتنفيذ الإلغاء','Approve and execute cancellation'):a==='resolve'?tr('تسجيل المعالجة','Record resolution'):tr('رفض الطلب','Request rejected')}</button>`).join('')}</div>`,(f,e)=>{const note=String(f.get('note')||'').trim();if(e.submitter.value==='reject'&&note.length<3)throw new Error(tr('اكتب سبب رفض الطلب.','Enter the reason for rejecting the request.'));return mutation(path+'/'+encodeURIComponent(r.id)+'/decision',{action:e.submitter.value,version:d.version,...revision,reason_code:f.get('reason_code'),contact_result:f.get('contact_result'),physical_resolution:f.get('physical_resolution')||null,note});});
          form.elements.reason_code.value=currentReason||'no_answer';form.elements.contact_result.value='not_contacted';
        }
        if(actions.has('return')){
          const box=document.createElement('details');box.className='intervention-return';box.innerHTML=tr('<summary>إرجاع لمدير التوصيل للتصحيح والاستكمال</summary><p>يبقى السبب الأصلي محفوظًا. يسجل السبب المصحح والتوضيح والمسؤول في تسلسل المعاملة.</p>','<summary>Return to delivery manager for correction and completion</summary><p>The original reason remains preserved. The corrected reason, explanation and responsible employee are recorded in the request history.</p>');
          const targets=d.return_targets||[],preferred=targets.some(t=>t.id===w.preferred_return_target_id)?w.preferred_return_target_id:'';
          const form=appendForm(box,`${label(tr('السبب المصحح','Corrected reason'),`<select name="corrected_reason_code">${options(localizedMap('reasons',d.reasons))}</select>`)}${label(tr('مدير التوصيل المطلوب منه الاستكمال','Delivery manager responsible for completion'),`<select name="target_manager_id"><option value="">${tr("اختر مدير التوصيل","Choose delivery manager")}</option>${targets.map(t=>`<option value="${esc(t.id)}">${esc(t.display_name)}</option>`).join('')}</select>`)}${label(tr('ما الخطأ؟ وما المطلوب تصحيحه؟','What is wrong and what needs to be corrected?'),'<textarea name="note" required minlength="3" maxlength="1500" rows="3"></textarea>')}<button>${tr("إرجاع المعاملة للاستكمال","Return request for completion")}</button>`,f=>{
            if(!f.get('target_manager_id')||String(f.get('note')||'').trim().length<3)throw new Error(tr('حدد مدير التوصيل واكتب توضيح التصحيح المطلوب.','Choose a delivery manager and explain the required correction.'));
            return mutation(path+'/'+encodeURIComponent(r.id)+'/return',{version:d.version,...revision,corrected_reason_code:f.get('corrected_reason_code'),target_manager_id:f.get('target_manager_id'),note:String(f.get('note')).trim()});
          });form.elements.corrected_reason_code.value=currentReason;form.elements.target_manager_id.value=preferred;section.append(box);
        }
        if(actions.has('resubmit')){
          const form=appendForm(section,`<h4>${tr("استكمال المعاملة وإعادتها للمراجعة","Complete request and resubmit for review")}</h4><p>${tr("السبب الحالي:","Current reason:")} ${esc(reasonLabel(currentReason,d))}</p>${contact(d)}${customerReasons.has(currentReason)?`<p>${tr("هذا السبب يحتاج طلب عميل أو دليل موثق قبل إعادة الرفع.","This reason requires a customer request or documented evidence before resubmission.")}</p>${evidence(d)}${messageProof(d)}`:''}${newEvidence()}${label(tr('ما الذي استُكمل؟','What has been completed?'),'<textarea name="note" required minlength="3" maxlength="1500" rows="3"></textarea>')}<button>${tr("إعادة الرفع للمراجعة","Resubmit for review")}</button>${exceptionForm()}`,async(f,e)=>mutation(path+'/'+encodeURIComponent(r.id)+'/resubmit',{version:d.version,...revision,contact_result:f.get('contact_result'),note:String(f.get('note')||'').trim(),customer_request_id:f.get('customer_request_id')||null,...proofFields(f),evidence_attachment_id:e.submitter?.value==='attachment_unavailable'?null:await selectedEvidence(f,e.target),...exceptionFields(f,e)}));form.elements.contact_result.value='not_contacted';
        }
        root.append(section);
      }
      const terminalCancellation=d.requests.some(r=>r.details?.kind==='cancel'&&(r.decision?.status==='approved'||r.workflow?.status==='approved'));
      const createKinds=Array.isArray(d.available_create_kinds)?d.available_create_kinds:!pending.length&&user.role!=='admin'&&!terminalCancellation&&!financialPending?(driver?['problem']:['cancel','problem']):[];
      const kinds=Object.fromEntries(Object.entries(driver?{problem:tr('رفع مشكلة','Report a problem')}:{cancel:tr('طلب إلغاء','Cancellation request'),problem:tr('متابعة مشكلة','Issue follow-up')}).filter(([kind])=>createKinds.includes(kind)));
      if(!pending.length&&Object.keys(kinds).length){
        const form=appendForm(root,`${label(tr('الإجراء','Action'),`<select name="kind">${options(kinds)}</select>`)}${label(tr('السبب','Reason'),`<select name="reason_code">${options(localizedMap('reasons',d.reasons))}</select>`)}${driver?'':contact(d)+evidence(d)+messageProof(d)+(mayUploadEvidence?newEvidence():tr('<p>إرفاق صورة جديدة: افتح محادثة الطلب وأرفق الصورة، ثم ارجع لهذه الشاشة.</p>','<p>To attach a new image, open the order conversation, attach the image, then return to this screen.</p>'))}${driver||!Array.isArray(d.customer_messages)?'':label(tr('التصعيد للمسؤول الأعلى','Escalate to higher authority'),tr('<input type="checkbox" name="escalate"> إحالة المشكلة للمالك أو محمد عبدالسميع المفوض','<input type="checkbox" name="escalate"> Refer the issue to the owner or authorized delegate Mohammed Abdelsamie'))}${label(tr('تفاصيل المطلوب','Required action details'),driver?'<input name="note" maxlength="1500">':'<textarea name="note" maxlength="1500" rows="3"></textarea>')}<button>${tr("رفع الطلب للمسؤول","Submit to responsible employee")}</button>${driver?'':exceptionForm()}`,async(f,e)=>mutation(path,{version:d.version,kind:f.get('kind'),...(Array.isArray(d.customer_messages)?{escalate:f.get('escalate')==='on'}:{}),reason_code:f.get('reason_code'),contact_result:f.get('contact_result')||(f.get('reason_code')==='no_answer'?'no_answer':'not_contacted'),customer_request_id:f.get('customer_request_id')||null,...proofFields(f),evidence_attachment_id:e.submitter?.value==='attachment_unavailable'?null:await selectedEvidence(f,e.target),note:String(f.get('note')||'').trim(),...exceptionFields(f,e)}));
        if(initialReason&&form.elements.reason_code.querySelector(`option[value="${initialReason}"]`))form.elements.reason_code.value=initialReason;
        if(form.elements.contact_result)form.elements.contact_result.value='not_contacted';
      }
    }
    async function submit(e,run){
      e.preventDefault();const form=e.target;if(form.dataset?.submitting==='1')return;const exceptional=e.submitter?.value==='attachment_unavailable';if(exceptional&&String(form.elements.attachment_unavailable_reason?.value||'').trim().length<3){root.querySelector('[role=alert]').textContent=tr('اكتب سبب تعذر الإرفاق قبل التصعيد.','Enter why the attachment is unavailable before escalating.');return;}if(form.dataset)form.dataset.submitting='1';const alert=root.querySelector('[role=alert]');alert.textContent='';form.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{await run(new FormData(form));await refresh();}
      catch(error){alert.textContent=systemLabel(error.message);form.querySelectorAll('button').forEach(b=>b.disabled=false);}finally{if(form.dataset)delete form.dataset.submitting;}
    }
    try{await refresh();}catch(error){root.textContent=systemLabel(error.message);}
  }};
})();


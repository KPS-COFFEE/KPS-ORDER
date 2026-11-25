/* Recorded owner investigation; no attendance, live presence or duration inference. */
(()=>{'use strict';
window.KPSOwnerEmployeeActivity={async open(ctx){
 const {api,mutation,tr,esc,heading,modal,closeModal,stepUp,view,user,active}=ctx;
 const root=document.querySelector('#content');let version=0,options=null;
 view.kind??='login';view.offset??=0;
 const role=r=>({owner:tr('مالك','Owner'),admin:tr('إداري','Administrator'),delivery_manager:tr('مدير توصيل','Delivery manager'),delivery_staff:tr('موظف توصيل','Delivery staff'),driver:tr('سائق','Driver'),branch_manager:tr('مسؤول فرع','Branch manager')})[r]||tr('موظف','Employee');
 const actions={
 'order.created':['إنشاء طلب','Create order'],'order.manager_accepted':['قبول الطلب','Accept order'],'order.preparation':['تحديث تحضير الطلب','Update preparation'],
 'order.collect':['تسجيل تحصيل','Record collection'],'order.refund':['تسجيل استرداد','Record refund'],'order.delivery_location.changed':['تعديل موقع التوصيل','Change delivery location'],
 'task.assign':['إسناد مهمة','Assign task'],'task.transition':['تحديث خطوة التوصيل','Update delivery stage'],'task.reassigned':['إعادة إسناد','Reassign task'],
 'task.handover.request':['طلب تسليم مهمة','Request task handover'],'task.handover.accept':['قبول تسليم مهمة','Accept task handover'],
 'shift.open':['فتح شفت','Open shift'],'shift.closing':['بدء التقفيلة','Start closure'],'shift.submit':['إرسال التقفيلة','Submit closure'],
 'shift.approve':['اعتماد التقفيلة','Approve closure'],'shift.return':['إرجاع التقفيلة','Return closure'],'shift.refuel':['تسجيل بنزين','Record refuelling'],
 'shift.closure_draft.saved':['حفظ مسودة تقفيلة','Save closure draft'],'custody.authorized_event':['تسجيل قيد عهدة','Record custody entry'],
 'custody.confirm':['تأكيد استلام عهدة','Confirm custody receipt'],'custody.request':['طلب تسليم عهدة','Request custody handover'],
 'accounting.review':['مراجعة محاسبية','Accounting review'],'accounting.manager_match':['مطابقة تقفيلة مدير','Match manager closure'],
 'session.created':['دخول مسجل','Recorded sign-in'],'session.revoked':['خروج مسجل','Recorded sign-out'],'employee.sessions.ended':['إنهاء جلسات بأمر المالك','Sessions ended by owner']};
 const action=r=>actions[r.action]?tr(...actions[r.action]):tr('إجراء مسجل','Recorded action')+' ('+r.action+')';
 const stamp=value=>new Date(value).toLocaleString(ctx.lang==='en'?'en-GB':'ar-SA',{timeZone:'Asia/Riyadh',calendar:'gregory',dateStyle:'short',timeStyle:'medium'});
 heading(tr('تقارير دخول الموظفين وأعمالهم','Employee sign-in and work reports'),tr('للمالك فقط — الأحداث المسجلة ضمن النطاق المسموح','Owner only — recorded events within authorized scope'));
 root.innerHTML=`<section class="panel stack" id="employee-activity"><div class="actions"><button type="button" data-activity-kind="login">${esc(tr('دخول الموظفين','Employee sign-ins'))}</button><button type="button" data-activity-kind="work">${esc(tr('أعمال الموظفين','Employee work'))}</button></div><p class="notice">${esc(tr('أحداث مسجلة وليست حضورا أو اتصالا حاليا. أحداث الدخول القديمة غير محددة البراند مستبعدة. الأعمال المعروضة تخص الطلبات والشفتات والتقارير والمهام المصرح بها فقط؛ الأسماء والأدوار الحالية.','Recorded events, not attendance or online presence. Legacy sign-ins without recorded brand are excluded. Work covers authorized orders, shifts, reports and tasks only; names and roles are current.'))}</p><p id="employee-options-note" class="muted"></p><form class="filters" id="employee-activity-filters"><label>${esc(tr('من','From'))}<input type="date" name="start" required></label><label>${esc(tr('إلى','Until'))}<input type="date" name="end" required></label><label>${esc(tr('الموظف','Employee'))}<select name="employee_id"><option value="">${esc(tr('كل الموظفين في النطاق','All employees in scope'))}</option></select></label><button type="submit">${esc(tr('عرض التقرير','Show report'))}</button></form><div class="actions"><button type="button" id="employee-end-sessions" disabled>${esc(tr('إنهاء جلسات الموظف المحدد','End selected employee sessions'))}</button></div><p role="alert"></p><div id="employee-activity-rows"></div><div class="actions"><button type="button" data-activity-page="previous">${esc(tr('السابق','Previous'))}</button><button type="button" data-activity-page="next">${esc(tr('التالي','Next'))}</button></div></section>`;
 const host=root.querySelector('#employee-activity'),form=host.querySelector('form'),error=host.querySelector('[role=alert]'),rows=host.querySelector('#employee-activity-rows'),end=host.querySelector('#employee-end-sessions');
 function current(){return active()&&host.isConnected;}
 function endState(){const person=options?.employees.find(p=>p.id===form.elements.employee_id.value);end.disabled=!options?.can_end_sessions||!person||person.role==='owner'||person.id===user.id;}
 async function load(){const token=++version;error.textContent='';
  try{
   if(!options){options=await api('/api/owner-employee-activity/options');if(!current()||token!==version)return;
    form.elements.employee_id.innerHTML+=options.employees.map(p=>`<option value="${esc(p.id)}">${esc(p.display_name)} · ${esc(role(p.role))}</option>`).join('');
    if(options.options_truncated)host.querySelector('#employee-options-note').textContent=tr('قائمة الاختيار تعرض أول 500 حساب؛ التقرير العام يشمل بقية الحسابات في النطاق.','The picker shows the first 500 accounts; the unfiltered report includes other scoped accounts.');
    view.start??=options.today;view.end??=options.today;
   }
   form.elements.start.value=view.start;form.elements.end.value=view.end;form.elements.employee_id.value=view.employee_id||'';endState();
   const query=new URLSearchParams({kind:view.kind,start:view.start,end:view.end,employee_id:view.employee_id||'',offset:String(view.offset),limit:'50'});
   const report=await api('/api/owner-employee-activity?'+query);if(!current()||token!==version)return;
   host.querySelectorAll('[data-activity-kind]').forEach(b=>{const selected=b.dataset.activityKind===view.kind;b.setAttribute('aria-pressed',String(selected));b.classList.toggle('quiet',!selected);});
   rows.innerHTML=report.rows.map(r=>`<article class="panel"><strong>${esc(r.employee_name)}</strong> · ${esc(role(r.employee_role))}<p>${esc(action(r))} · <time>${esc(stamp(r.created_at))}</time></p>${view.kind==='work'?`<small>${esc(tr('المرجع','Reference'))}: ${esc(r.reference)}</small>`:''}</article>`).join('')||`<p>${esc(tr('لا توجد أحداث موثقة في الفترة والنطاق المحددين. لا يعني ذلك عدم دخول الموظف أو عدم عمله.','No documented events in the selected period and scope. This does not mean the employee did not sign in or work.'))}</p>`;
   host.querySelector('[data-activity-page=previous]').disabled=view.offset===0;
   host.querySelector('[data-activity-page=next]').disabled=report.next_offset==null;
   if(report.page_limit_reached)error.textContent=tr('وصلت إلى حد الصفحات؛ اختر فترة أقصر أو موظفا محددا.','Page limit reached; choose a shorter period or an employee.');
  }catch(e){if(current()&&token===version)error.textContent=e.message;}
 }
 form.onsubmit=e=>{e.preventDefault();view.start=form.elements.start.value;view.end=form.elements.end.value;view.employee_id=form.elements.employee_id.value;view.offset=0;void load();};
 const remember=()=>{view.start=form.elements.start.value;view.end=form.elements.end.value;view.employee_id=form.elements.employee_id.value;};
 form.onchange=()=>{remember();endState();};form.oninput=remember;
 host.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
  if(b.dataset.activityKind){view.kind=b.dataset.activityKind;view.offset=0;void load();}
  if(b.dataset.activityPage){view.offset=Math.max(0,view.offset+(b.dataset.activityPage==='next'?50:-50));void load();}
 });
 end.onclick=()=>{const person=options.employees.find(p=>p.id===form.elements.employee_id.value);if(!person||end.disabled)return;
  modal(tr('إنهاء جلسات الموظف','End employee sessions'),`<form><p><strong>${esc(person.display_name)}</strong></p><p>${esc(tr('ينهي جلسات هذا الحساب في جميع البراندات مع بقاء الموظف مفعلا. تلغى رموز الدخول القديمة؛ يستطيع الدخول برمز جديد. لا يغير الشفتات أو الأموال أو الصلاحيات.','Ends this account’s sessions across all brands while keeping it active. Old sign-in codes are invalidated; a new code permits fresh sign-in. Shifts, funds and permissions stay unchanged.'))}</p><label>${esc(tr('سبب الإنهاء','Reason'))}<textarea name="reason" minlength="3" maxlength="500" required></textarea></label><button type="submit">${esc(tr('تأكيد وإنهاء الجلسات','Confirm and end sessions'))}</button><p class="form-error" role="alert"></p></form>`,async fd=>{await stepUp();await mutation('/api/owner-employee-activity/'+encodeURIComponent(person.id)+'/end-sessions',{reason:fd.get('reason')});closeModal();await load();});
 };
 await load();
}};
})();

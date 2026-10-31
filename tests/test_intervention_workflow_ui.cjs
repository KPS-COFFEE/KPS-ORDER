const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const widget=fs.readFileSync('public/assets/order-interventions.js','utf8');
const base=()=>({enabled:true,printed:true,high_authority:true,version:7,
 reasons:{late_complaint:'شكوى تأخير',no_answer:'العميل لا يرد',customer_cancel:'طلب العميل الإلغاء'},
 contact_results:{not_contacted:'لم يتم التواصل',no_answer:'لا يرد',agreed:'تم التواصل ووافق'},
 pending_roles:{owner_or_cancel_delegate:'المراجع المخول',delivery_manager:'مدير التوصيل'},statuses:{},
 customer_requests:[{id:'customer-proof',created_at:'2026-10-29T12:00:00Z',details:{note:'طلب العميل الموثق'}}],attachments:[{id:'image-proof',created_at:'2026-10-29T12:00:00Z'}],return_targets:[{id:'manager-1',display_name:'مدير التوصيل الأول'}],
 requests:[{id:'request/1',created_at:'2026-10-28T12:00:00Z',requested_by_name:'رافع المعاملة',details:{kind:'cancel',reason_code:'late_complaint',pending_role:'owner_or_cancel_delegate'},decision:null,
 workflow:{revision:0,status:'requested',original_reason_code:'late_complaint',reason_code:'late_complaint',current_responsible_role:'owner_or_cancel_delegate',current_responsible_name:null,next_action_label:'بانتظار اعتمادك أو إرجاعها للاستكمال',preferred_return_target_id:'manager-1',available_actions:['approve','reject','return'],history:[]}}]});
function harness(initial,role='admin'){
 let data=structuredClone(initial),writes=[],reads=[],failure=null,afterWrite=null,dialog;
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.elements={};this.isConnected=true;this.alert={textContent:''};this.buttons=[];}
  set innerHTML(html){this.html=html;this.children=[];this.elements={};this.buttons=[];
   for(const m of html.matchAll(/<input\b([^>]*)>/g)){const name=m[1].match(/name="([^"]+)"/),v=m[1].match(/value="([^"]*)"/);if(name)this.elements[name[1]]={value:v?.[1]||''};}
   for(const m of html.matchAll(/<select name="([^"]+)">([\s\S]*?)<\/select>/g)){const values=[...m[2].matchAll(/<option value="([^"]*)"/g)].map(v=>v[1]);this.elements[m[1]]={value:values[0]||'',querySelector:q=>values.includes(q.match(/value="([^"]*)"/)?.[1])?{}:null};}
   for(const m of html.matchAll(/<textarea name="([^"]+)"[^>]*>([\s\S]*?)<\/textarea>/g))this.elements[m[1]]={value:m[2]};
   for(const _ of html.matchAll(/<button\b/g))this.buttons.push({disabled:false});
  }
  get innerHTML(){return this.html||'';}append(x){this.children.push(x);}showModal(){}addEventListener(){}remove(){this.isConnected=false;}
  querySelector(q){if(q==='[data-close]')return {};if(q==='.amend-body')return this.body||(this.body=new Element('body'));if(q==='[role=alert]')return this.alert;return null;}
  querySelectorAll(q){return q==='button'?this.buttons:[];}
 }
 const ctx={window:{},document:{createElement:tag=>new Element(tag),body:{append:d=>dialog=d}},FormData:class{constructor(form){this.form=form;}get(key){return this.form.elements[key]?.value??null;}}};vm.createContext(ctx);vm.runInContext(widget,ctx);
 const all=root=>[root,...root.children.flatMap(all)];
 return {writes,reads,get data(){return data;},set data(value){data=structuredClone(value);},set failure(value){failure=value;},set afterWrite(value){afterWrite=value;},get text(){return all(dialog.body).map(n=>n.html||'').join('');},get alert(){return dialog.body.alert.textContent;},forms:()=>all(dialog.body).filter(n=>n.tag==='form'),
  open:()=>ctx.window.KPSInterventions.open({oid:'order/1',user:{id:role==='admin'?'delegate':'manager-1',role},api:async path=>{reads.push(path);return structuredClone(data);},mutation:async(path,body)=>{if(failure)throw failure;writes.push({path,body});if(afterWrite)afterWrite(path,body);return {status:'returned'};}}),
  submit:(form,action)=>form.onsubmit({target:form,preventDefault(){},submitter:action?{value:action}:undefined})};
}
test('return preserves the original reason, records correction and named responsibility, and refreshes from the server',async()=>{
 const original=base(),h=harness(original);await h.open();assert.equal(h.writes.length,0);assert.match(h.text,/رافع المعاملة/);assert.match(h.text,/بانتظار اعتمادك/);
 const form=h.forms().find(f=>f.elements.corrected_reason_code);assert.equal(form.elements.target_manager_id.value,'manager-1');
 form.elements.corrected_reason_code.value='no_answer';form.elements.note.value='صحح سبب التأخير: تحقق من نتيجة الاتصال أولًا';
 h.afterWrite=()=>{const d=base(),r=d.requests[0];r.workflow={...r.workflow,revision:1,status:'returned',reason_code:'no_answer',return_note:form.elements.note.value,current_responsible_role:'delivery_manager',current_responsible_name:'مدير التوصيل الأول',next_action_label:'بانتظار استكمال مدير التوصيل وإعادة الرفع',available_actions:[],history:[{id:'return-1',event_type:'intervention.returned',created_at:'2026-10-29T12:00:00Z',actor_name:'المراجع المخول',target_manager_name:'مدير التوصيل الأول',reason_code:'no_answer',note:form.elements.note.value}]};h.data=d;};
 await h.submit(form);assert.equal(h.writes.length,1);const w=h.writes[0];assert.equal(w.path,'/api/order-interventions/orders/order%2F1/request%2F1/return');assert.equal(w.body.version,7);assert.equal(w.body.workflow_revision,0);assert.equal(w.body.corrected_reason_code,'no_answer');assert.equal(w.body.target_manager_id,'manager-1');
 assert.equal(original.requests[0].details.reason_code,'late_complaint');assert.equal(h.data.requests[0].details.reason_code,'late_complaint');assert.match(h.text,/سبب الإحالة الأصلي/);assert.match(h.text,/السبب بعد التصحيح/);assert.match(h.text,/مدير التوصيل الأول/);assert.match(h.text,/بانتظار استكمال/);assert.equal(h.forms().length,0);assert.equal(h.reads.length,2);
});
test('missing or revoked server actions are never reconstructed from a broad admin role',async()=>{
 const d=base();d.requests[0].workflow.available_actions=[];const h=harness(d);await h.open();assert.equal(h.forms().length,0);assert.equal(h.writes.length,0);assert.doesNotMatch(h.text,/name="action"|name="target_manager_id"/);
});
test('return requires a real allowed manager selection and retains the typed explanation after conflict',async()=>{
 const d=base();d.requests[0].workflow.preferred_return_target_id='inactive-manager';const h=harness(d);await h.open();const f=h.forms().find(x=>x.elements.corrected_reason_code);assert.equal(f.elements.target_manager_id.value,'');f.elements.note.value='توضيح تصحيح مسجل';await h.submit(f);assert.equal(h.writes.length,0);assert.match(h.alert,/حدد مدير التوصيل/);
 f.elements.target_manager_id.value='manager-1';h.failure=new Error('تغيرت المعاملة؛ حدّث السجل');await h.submit(f);assert.equal(h.writes.length,0);assert.equal(f.elements.note.value,'توضيح تصحيح مسجل');assert.ok(f.buttons.every(b=>!b.disabled));assert.match(h.alert,/تغيرت المعاملة/);assert.equal(h.reads.length,1);
});
test('assigned manager resubmits the corrected reason with explicit evidence and current workflow revision',async()=>{
 const d=base();d.requests[0].workflow={...d.requests[0].workflow,revision:3,status:'returned',reason_code:'customer_cancel',current_responsible_name:'مدير التوصيل الأول',available_actions:['resubmit'],return_note:'أرفق طلب العميل قبل إعادة الرفع'};const h=harness(d,'delivery_manager');await h.open();const f=h.forms().find(x=>x.elements.note);assert.ok(f.elements.customer_request_id);assert.ok(f.elements.evidence_attachment_id);assert.doesNotMatch(h.text,/اعتماد وتنفيذ الإلغاء/);f.elements.note.value='استكمل طلب العميل الموجود في سجل المحادثة';f.elements.contact_result.value='agreed';f.elements.customer_request_id.value='customer-proof';
 await h.submit(f);const w=h.writes[0];assert.equal(w.path,'/api/order-interventions/orders/order%2F1/request%2F1/resubmit');assert.equal(w.body.workflow_revision,3);assert.equal(w.body.customer_request_id,'customer-proof');assert.equal(w.body.evidence_attachment_id,null);assert.equal(w.body.reason_code,undefined);assert.equal(h.data.requests[0].details.reason_code,'late_complaint');
});
test('decision uses the corrected current reason and revision, while history is escaped',async()=>{
 const d=base();d.requests[0].workflow={...d.requests[0].workflow,revision:4,status:'resubmitted',reason_code:'no_answer',available_actions:['approve'],return_note:'<img src=x onerror=alert(1)>'};const h=harness(d);await h.open();const f=h.forms()[0];f.elements.contact_result.value='no_answer';f.elements.physical_resolution.value='returned_to_branch';await h.submit(f,'approve');assert.equal(h.writes[0].body.reason_code,'no_answer');assert.equal(h.writes[0].body.workflow_revision,4);assert.match(h.text,/&lt;img/);assert.doesNotMatch(h.text,/<img src=x/);
});
test('legacy authority opens only the canonical cancellation form and read-only cannot execute it',async()=>{
 const d={...base(),enabled:false,legacy_cancel_allowed:true};const h=harness(d);await h.open();const f=h.forms()[0];assert.ok(f.elements.reason);assert.equal(f.elements.corrected_reason_code,undefined);f.elements.reason.value='إلغاء موثق بعد مراجعة طلب العميل';f.elements.physical_resolution.value='returned_to_branch';await h.submit(f);assert.equal(h.writes[0].path,'/api/order-financial-cases/orders/order%2F1/cancel');assert.equal(h.writes[0].body.version,7);assert.equal(h.writes[0].body.physical_resolution,'returned_to_branch');
 const readonly=harness({...d,legacy_cancel_allowed:false});await readonly.open();assert.equal(readonly.forms().length,0);assert.equal(readonly.writes.length,0);
});
test('approved operational cancellation retains accounting responsibility until the linked financial case closes',async()=>{
 const d=base();d.pending_roles.accounting='المحاسبة';d.requests[0].decision={status:'approved'};d.requests[0].workflow={...d.requests[0].workflow,status:'approved',financial_pending:true,current_responsible_role:'accounting',next_action_label:'المحاسبة: مطابقة التحصيل والاسترداد الفعلي وإقفال الملف المالي',available_actions:[],history:[{id:'accounting-1',event_type:'intervention.accounting_pending',created_at:'2026-10-30T12:00:00Z',actor_name:'المراجع المخول'}]};
 const h=harness(d);await h.open();assert.equal(h.forms().length,0);assert.match(h.text,/المسؤول الحالي: المحاسبة/);assert.match(h.text,/مطابقة التحصيل والاسترداد الفعلي/);assert.match(h.text,/إحالة المعالجة المالية إلى المحاسبة/);assert.doesNotMatch(h.text,/اكتملت المعالجة التشغيلية/);d.requests[0].workflow.financial_pending=false;d.requests[0].workflow.current_responsible_role=null;d.requests[0].workflow.next_action_label='اكتملت المعالجة التشغيلية';d.requests[0].workflow.history.push({id:'financial-done',event_type:'financial_case.resolved',created_at:'2026-10-31T12:00:00Z',actor_name:'المحاسب',note:'مطابقة فعلية موثقة'});const closed=harness(d);await closed.open();assert.match(closed.text,/إقفال ملف المعالجة المحاسبية/);assert.match(closed.text,/المسؤول الحالي: لا يوجد إجراء معلق/);assert.doesNotMatch(closed.text,/المسؤول الحالي: المراجع المخول/);
});
test('terminal delivery manager never gets a new request form and accounting intro does not ask for a second approval',async()=>{
 const d=base();d.available_create_kinds=[];d.has_recorded_collection=true;d.requests[0].decision={status:'approved'};d.requests[0].workflow={...d.requests[0].workflow,status:'approved',financial_pending:true,current_responsible_role:'accounting',next_action_label:'المحاسبة: مطابقة المبالغ وإقفال الملف',available_actions:[]};d.pending_roles.accounting='المحاسبة';const h=harness(d,'delivery_manager');await h.open();assert.equal(h.forms().length,0);assert.match(h.text,/الإلغاء التشغيلي معتمد/);assert.match(h.text,/المبالغ ما زالت لدى المحاسبة/);assert.doesNotMatch(h.text,/name="kind"|الإلغاء يحتاج اعتماد|طلب الإلغاء بانتظار المراجع/);
 // Safe mixed-version fallback when the previous backend omits creation policy.
 delete d.available_create_kinds;const previous=harness(d,'delivery_manager');await previous.open();assert.equal(previous.forms().length,0);
});
test('server creation policy wins even when a driver cannot see another employee’s pending request',async()=>{
 const d=base();d.requests=[];d.available_create_kinds=[];const h=harness(d,'driver');await h.open();assert.equal(h.forms().length,0);const allowed=base();allowed.requests=[];allowed.available_create_kinds=['problem'];const manager=harness(allowed,'delivery_manager');await manager.open();const f=manager.forms()[0];assert.match(f.html,/value="problem"/);assert.doesNotMatch(f.html,/value="cancel"/);
});

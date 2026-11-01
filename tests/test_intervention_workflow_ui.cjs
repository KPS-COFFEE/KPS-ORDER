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
function harness(initial,role='admin',{uploadEvidence}={}){
 let data=structuredClone(initial),writes=[],attempts=[],reads=[],failure=null,afterWrite=null,dialog;
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.elements={};this.isConnected=true;this.alert={textContent:''};this.buttons=[];}
  set innerHTML(html){this.html=html;this.children=[];this.elements={};this.buttons=[];
   for(const m of html.matchAll(/<input\b([^>]*)>/g)){const name=m[1].match(/name="([^"]+)"/),v=m[1].match(/value="([^"]*)"/);if(name)this.elements[name[1]]={value:v?.[1]||'',...(/type="file"/.test(m[1])?{files:[]}:{} )};}
   for(const m of html.matchAll(/<select name="([^"]+)">([\s\S]*?)<\/select>/g)){const values=[...m[2].matchAll(/<option value="([^"]*)"/g)].map(v=>v[1]);this.elements[m[1]]={value:values[0]||'',querySelector:q=>values.includes(q.match(/value="([^"]*)"/)?.[1])?{}:null};}
   for(const m of html.matchAll(/<textarea name="([^"]+)"[^>]*>([\s\S]*?)<\/textarea>/g))this.elements[m[1]]={value:m[2]};
   for(const _ of html.matchAll(/<button\b/g))this.buttons.push({disabled:false});
  }
  get innerHTML(){return this.html||'';}append(x){this.children.push(x);}showModal(){}addEventListener(){}remove(){this.isConnected=false;}
  querySelector(q){if(q==='[data-close]')return {};if(q==='.amend-body')return this.body||(this.body=new Element('body'));if(q==='[role=alert]')return this.alert;return null;}
  querySelectorAll(q){return q==='button'?this.buttons:[];}
 }
 const ctx={window:{},document:{createElement:tag=>new Element(tag),body:{append:d=>dialog=d}},FormData:class{constructor(form){this.values=new Map(Object.entries(form.elements).map(([key,field])=>[key,field.files?field.files[0]||null:field.value]));}get(key){return this.values.get(key)??null;}}};vm.createContext(ctx);vm.runInContext(widget,ctx);
 const all=root=>[root,...root.children.flatMap(all)];
 return {writes,attempts,reads,get data(){return data;},set data(value){data=structuredClone(value);},set failure(value){failure=value;},set afterWrite(value){afterWrite=value;},get text(){return all(dialog.body).map(n=>n.html||'').join('');},get alert(){return dialog.body.alert.textContent;},forms:()=>all(dialog.body).filter(n=>n.tag==='form'),
  open:()=>ctx.window.KPSInterventions.open({oid:'order/1',user:{id:role==='admin'?'delegate':'manager-1',role},uploadEvidence,api:async path=>{reads.push(path);return structuredClone(data);},mutation:async(path,body)=>{attempts.push({path,body});if(failure)throw failure;writes.push({path,body});if(afterWrite)afterWrite(path,body);return {status:'returned'};}}),
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

function evidenceFormData(mode){
 const d=base();
 if(mode==='create'){d.requests=[];d.available_create_kinds=['cancel','problem'];}
 else d.requests[0].workflow={...d.requests[0].workflow,revision:3,status:'returned',reason_code:'customer_cancel',available_actions:['resubmit']};
 return d;
}
function fillEvidenceForm(form,mode,file){
 form.elements.contact_result.value='agreed';form.elements.customer_request_id.value='customer-proof';form.elements.evidence_attachment_id.value='image-proof';
 if(mode==='create'){form.elements.kind.value='cancel';form.elements.reason_code.value='customer_cancel';}
 else form.elements.note.value='استكملت الدليل المطلوب مع الاحتفاظ بالسبب الأصلي';
 if(file)form.elements.evidence_image.files=[file];
}
const picture=()=>new File(['synthetic evidence'],'evidence.png',{type:'image/png',lastModified:1});
for(const mode of ['create','resubmit']){
 test(`${mode} uploads selected evidence only on submit and uses it instead of an existing attachment`,async()=>{
  const uploads=[],h=harness(evidenceFormData(mode),'delivery_manager',{uploadEvidence:async file=>{uploads.push(file);return {id:'uploaded-proof'};}});
  await h.open();const form=h.forms()[0],file=picture();fillEvidenceForm(form,mode,file);
  assert.equal(uploads.length,0);assert.equal(h.writes.length,0);assert.match(form.html,/name="evidence_image" type="file"/);
  assert.doesNotMatch(form.html,/افتح محادثة الطلب وأرفق الصورة/);
  await h.submit(form);
  assert.equal(uploads.length,1);assert.equal(uploads[0],file);assert.equal(h.writes.length,1);
  const saved=h.writes[0];assert.equal(saved.path,'/api/order-interventions/orders/order%2F1'+(mode==='resubmit'?'/request%2F1/resubmit':''));
  assert.equal(saved.body.version,7);assert.equal(saved.body.evidence_attachment_id,'uploaded-proof');assert.equal(saved.body.customer_request_id,'customer-proof');assert.equal(saved.body.contact_result,'agreed');
  if(mode==='resubmit'){assert.equal(saved.body.workflow_revision,3);assert.equal(saved.body.note,'استكملت الدليل المطلوب مع الاحتفاظ بالسبب الأصلي');assert.equal(saved.body.reason_code,undefined);}
  else{assert.equal(saved.body.kind,'cancel');assert.equal(saved.body.reason_code,'customer_cancel');}
  assert.equal(h.reads.length,2);assert.ok(h.writes.every(write=>!write.path.includes('/messages')));
 });
 test(`${mode} preserves the complete draft after a mutation failure and reuses only the same selected File`,async()=>{
  const uploads=[],h=harness(evidenceFormData(mode),'delivery_manager',{uploadEvidence:async file=>{uploads.push(file);return {id:'uploaded-'+uploads.length};}});
  await h.open();const form=h.forms()[0],file=picture();fillEvidenceForm(form,mode,file);
  const fields=Object.fromEntries(Object.entries(form.elements).filter(([,field])=>!field.files).map(([name,field])=>[name,field.value]));
  h.failure=new Error('تعذر تأكيد حفظ المعاملة؛ أعد المحاولة');await h.submit(form);
  assert.equal(uploads.length,1);assert.equal(h.writes.length,0);assert.equal(h.forms()[0],form);assert.equal(h.reads.length,1);assert.equal(form.elements.evidence_image.files[0],file);
  for(const [name,value] of Object.entries(fields))assert.equal(form.elements[name].value,value);
  assert.ok(form.buttons.every(button=>!button.disabled));assert.match(h.alert,/تعذر تأكيد حفظ المعاملة/);
  await h.submit(form);assert.equal(uploads.length,1);assert.equal(h.attempts[1].body.evidence_attachment_id,'uploaded-1');
  const replacement=picture();assert.notEqual(replacement,file);form.elements.evidence_image.files=[replacement];
  await h.submit(form);assert.equal(uploads.length,2);assert.equal(uploads[1],replacement);assert.equal(h.attempts[2].body.evidence_attachment_id,'uploaded-2');
  h.failure=null;await h.submit(form);assert.equal(uploads.length,2);assert.equal(h.writes.length,1);assert.equal(h.writes[0].body.evidence_attachment_id,'uploaded-2');
 });
 test(`${mode} keeps the existing attachment selector usable without an upload callback`,async()=>{
  const h=harness(evidenceFormData(mode),'delivery_manager');await h.open();const form=h.forms()[0];fillEvidenceForm(form,mode);
  assert.equal(form.elements.evidence_image,undefined);await h.submit(form);assert.equal(h.writes.length,1);assert.equal(h.writes[0].body.evidence_attachment_id,'image-proof');
 });
}
test('failed or unconfirmed image uploads preserve the draft and retry before any treatment mutation',async()=>{
 let count=0;const h=harness(evidenceFormData('create'),'delivery_manager',{uploadEvidence:async()=>{count++;if(count===1)throw new Error('تعذر رفع الصورة');return count===2?{}:{id:'confirmed-proof'};}});
 await h.open();const form=h.forms()[0],file=picture();fillEvidenceForm(form,'create',file);
 await h.submit(form);assert.equal(h.attempts.length,0);assert.equal(form.elements.evidence_image.files[0],file);assert.equal(form.elements.reason_code.value,'customer_cancel');assert.match(h.alert,/تعذر رفع الصورة/);assert.ok(form.buttons.every(button=>!button.disabled));
 await h.submit(form);assert.equal(count,2);assert.equal(h.attempts.length,0);assert.equal(h.forms()[0],form);assert.match(h.alert,/تعذر تأكيد حفظ الصورة/);
 await h.submit(form);assert.equal(count,3);assert.equal(h.writes.length,1);assert.equal(h.writes[0].body.evidence_attachment_id,'confirmed-proof');
});
test('clearing a selected image restores the chosen existing attachment without another upload',async()=>{
 let uploads=0;const h=harness(evidenceFormData('create'),'delivery_manager',{uploadEvidence:async()=>{uploads++;return {id:'uploaded-proof'};}});
 await h.open();const form=h.forms()[0];fillEvidenceForm(form,'create',picture());h.failure=new Error('أعد المحاولة');await h.submit(form);
 form.elements.evidence_image.files=[];h.failure=null;await h.submit(form);
 assert.equal(uploads,1);assert.equal(h.writes[0].body.evidence_attachment_id,'image-proof');
});
test('a non-customer returned reason can carry new evidence without inventing a customer request',async()=>{
 const d=evidenceFormData('resubmit');d.requests[0].workflow.reason_code='no_answer';const h=harness(d,'delivery_manager',{uploadEvidence:async()=>({id:'contact-proof'})});
 await h.open();const form=h.forms()[0];assert.equal(form.elements.customer_request_id,undefined);assert.ok(form.elements.evidence_image);form.elements.note.value='استكملت محاولة التواصل وأرفقت الإثبات';form.elements.contact_result.value='no_answer';form.elements.evidence_image.files=[picture()];
 await h.submit(form);assert.equal(h.writes[0].body.evidence_attachment_id,'contact-proof');assert.equal(h.writes[0].body.customer_request_id,null);assert.equal(h.writes[0].body.workflow_revision,3);
});
test('the optional image callback grants no driver, read-only, terminal or legacy action',async()=>{
 let uploads=0;const callback={uploadEvidence:async()=>{uploads++;return {id:'unexpected'};}};
 const driverData=evidenceFormData('create');driverData.available_create_kinds=['problem'];const driver=harness(driverData,'driver',callback);await driver.open();assert.equal(driver.forms()[0].elements.evidence_image,undefined);
 const readonly=base();readonly.requests[0].workflow.available_actions=[];const noActions=harness(readonly,'delivery_manager',callback);await noActions.open();assert.equal(noActions.forms().length,0);
 const terminal=base();terminal.available_create_kinds=[];terminal.requests[0].decision={status:'approved'};terminal.requests[0].workflow.available_actions=[];terminal.requests[0].workflow.status='approved';const closed=harness(terminal,'delivery_manager',callback);await closed.open();assert.equal(closed.forms().length,0);
 const legacy=harness({...base(),enabled:false,legacy_cancel_allowed:true},'delivery_manager',callback);await legacy.open();assert.equal(legacy.forms()[0].elements.evidence_image,undefined);
 assert.equal(uploads,0);
});

test('manager selects customer chat proof without duplicate image and can escalate a problem',async()=>{
 const d=base();d.requests=[];d.available_create_kinds=['cancel','problem'];d.customer_messages=[{id:'message-1',text:'أطلب إلغاء طلبي <script>',created_at:'2026-11-01T15:00:00Z'}];
 const h=harness(d,'delivery_manager');await h.open();assert.match(h.text,/محادثة الموقع تكفي/);assert.match(h.text,/&lt;script&gt;/);const f=h.forms()[0];
 f.elements.kind.value='problem';f.elements.reason_code.value='wrong_address';f.elements.evidence_message_id.value='message-1';f.elements.customer_message_confirmed.value='on';f.elements.escalate.value='on';await h.submit(f);
 assert.equal(h.writes[0].body.evidence_message_id,'message-1');assert.equal(h.writes[0].body.customer_message_confirmed,true);assert.equal(h.writes[0].body.evidence_attachment_id,null);assert.equal(h.writes[0].body.escalate,true);
});

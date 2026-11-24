'use strict';
(()=>{
const $=q=>document.querySelector(q),all=q=>Array.from(document.querySelectorAll(q));
const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const opts=(list,empty)=> (empty===undefined?'':'<option value="">'+safe(empty)+'</option>')+Object.entries(list).map(([v,t])=>'<option value="'+safe(v)+'">'+safe(t)+'</option>').join('');
const names={requested:'بانتظار الموافقة',approved:'معتمد',issued:'جاهز للاستلام',active:'على الطريق',completed:'مكتمل',cancelled:'ملغى',idle:'بلا أمر'};
const actions={approve:'موافقة',issue:'تجهيز المفتاح',start:'استلام وبدء',finish:'إنهاء المهمة',arrive:'تمت زيارة الفرع',miss:'تعذر التوريد',request_return:'طلب تسليم المفتاح',return:'تأكيد استلام الإدارة',cancel:'إلغاء الأمر'};
const lines=['local','riyadh','east','selected'];
const C=$('#create').elements,F=$('#filters').elements,R=$('#routeForm').elements;
let data,csrf,lead,director,selection,reportSeq=0,workTab,report,picks=[],routePicks=[];
const dateAt=(t)=>new Date(t+10800000).toISOString();
const picture=c=>Number.isInteger(c.art)?'<img alt="" src="/assets/fleet-models/'+c.art+'.jpg">':'';
const plate=p=>window.KPSSaudiPlate.render(p,'ar',true);
const error=e=>{$('#msg').textContent=e.message;};
const buttons=(action,id)=>'<button type="button" data-act="'+action+'" data-id="'+id+'">'+actions[action]+'</button>';
async function api(path,{method='GET',body,format,key}={}){
 const headers={'X-KPS-Context':'work'};
 if(body){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf;headers['Idempotency-Key']=key||crypto.randomUUID();}
 const res=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined,cache:'no-store'});
 if(!res.ok){let j={};try{j=await res.json();}catch{}throw Error(j.detail?.message||j.error?.message||'تعذر إتمام الإجراء. حدّث الصفحة وتحقق من تسجيل الدخول.');}
 return format?res.blob():res.json();
}
function formGuard(form,cb,id){
 form.onsubmit=async e=>{e.preventDefault();const b=form.querySelector('button:last-of-type[type=submit]')||form.lastElementChild;b.disabled=true;
 try{if(id)$(id).textContent='';await cb();}catch(e){if(id)$(id).textContent=e.message;else error(e);}finally{b.disabled=false;}};
}
async function reload(){
 data=await api('/api/fleet-missions/workspace');lead=['owner','director','fleet_manager'].includes(data.role);director=['owner','director'].includes(data.role);
 const branches=$('#supervisorBranches');
 branches.hidden=!['supervisor_a','supervisor_b'].includes(data.role);
 if(!branches.hidden)branches.textContent='الفروع تحت إشرافك · '+data.branches.map(b=>b.branch_no+' '+b.name).join('، ');
 $('[data-tab=reports]').hidden=!lead;$('[data-tab=routes]').hidden=!director;
 $('#carCards').innerHTML=data.cars.map(c=>'<article>'+picture(c)+'<h3>'+safe(c.model||'سيارة')+'</h3>'+plate(c.plate)+'<p>'+safe(c.holder?'المفتاح مع '+c.holder:'المفتاح لدى الإدارة')+'</p></article>').join('')||'لا توجد سيارات متاحة.';
 $('#orderCards').innerHTML=data.missions.map(m=>{
 const mine=m.employee_id===data.self,own=m.key_holder===m.employee_id,acts=[];
 if(lead&&m.status==='requested')acts.push('approve');
 if(lead&&m.status==='approved'&&!own&&!m.parent_id)acts.push('issue');
 if(mine&&(m.status==='issued'||m.status==='approved'&&own&&(m.category==='permanent'||m.parent_id)))acts.push('start');
 if(mine&&m.status==='active'){if(m.stops.some(s=>s.state==='pending'))acts.push('arrive','miss');else acts.push('finish');}
 if(m.status==='completed'&&own&&!m.parent_id&&!m.returned_at&&m.category!=='permanent'){
 if(mine&&!m.return_requested_at)acts.push('request_return');if(lead&&m.return_requested_at)acts.push('return');}
 if(['approved','requested'].includes(m.status)||lead&&m.status==='issued')acts.push('cancel');
 const minutes=m.started_at?Math.max(0,Math.floor(((m.ended_at?Date.parse(m.ended_at):Date.now())-Date.parse(m.started_at))/60000)):null;
 return '<article><small>أمر '+safe(m.id)+' · '+safe(names[m.status])+'</small><h3>'+safe(data.kinds[m.kind])+'</h3><p>'+safe(m.display_name)+' · '+safe(m.destination)+'</p>'+plate(m.plate)+'<p>'+safe(new Date(m.scheduled_at).toLocaleString('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}))+'</p><small>المخطط '+m.planned_minutes+' د'+(minutes===null?'':' · الفعلي '+minutes+' د')+'</small><div class="actions">'+acts.map(a=>buttons(a,m.id)).join('')+'</div></article>';
 }).join('')||'لا توجد أوامر حركة.';
}
function tab(name){
 workTab=name;all('main section').forEach(s=>s.hidden=s.id!==name);
 all('[data-tab]').forEach(b=>b.setAttribute('aria-current',String(b.dataset.tab===name)));
 if(name==='reports')loadReport().catch(error);
 if(name==='routes'){R.kind.innerHTML=opts(Object.fromEntries(lines.map(k=>[k,data.kinds[k]])));initRoute();}
}
function stopEditor(el,arr){
 const branch=Object.fromEntries(data.branches.map(b=>[b.id,b.name]));
 const draw=()=>{
 el.innerHTML='<label>أضف الفروع بترتيب المرور<select>'+opts(Object.fromEntries(Object.entries(branch).filter(([v])=>!arr.includes(v))),'اختر فرعًا')+'</select></label><ol>'+arr.map((v,i)=>'<li>'+safe(branch[v])+' <button type="button" data-remove="'+i+'">حذف</button></li>').join('')+'</ol>';
 el.querySelector('select').onchange=e=>{if(e.target.value)arr.push(e.target.value);draw();};
 el.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{arr.splice(+b.dataset.remove,1);draw();});};draw();
}
function initRoute(){routePicks=(data.routes.find(r=>r.kind===R.kind.value)?.stops||[]).map(s=>s.id);stopEditor($('#routeStops'),routePicks);}
function kinds(){
 const possible=new Set(data.cars.flatMap(c=>c.options[C.employee_id.value]||[]));
 C.kind.innerHTML=opts(Object.fromEntries(Object.entries(data.kinds).filter(([k])=>possible.has(k)&&(director||k!=='assignment'))));
 if(data.staff.find(p=>p.id===C.employee_id.value)?.profile==='supplier_bus'&&possible.has('bus'))C.kind.value='bus';
 if(data.staff.find(p=>p.id===C.employee_id.value)?.profile.startsWith('supervisor_')&&possible.has('supervision'))C.kind.value='supervision';
 cars();
}
function cars(){
 const user=data.staff.find(p=>p.id===C.employee_id.value),kind=C.kind.value;
 const available=data.cars.filter(c=>c.options[C.employee_id.value]?.includes(kind)).sort((a,b)=>Number(b.home_profile===user?.profile||b.category==='shared_camry')-Number(a.home_profile===user?.profile||a.category==='shared_camry'));
 C.vehicle_id.innerHTML=opts(Object.fromEntries(available.map(c=>[c.vehicle_id,(c.model||'مركبة')+' · '+c.plate+(c.holder?' · بعهدة '+c.holder:'')])));
 const route=lines.includes(kind);$('#destinationLabel').hidden=route||kind==='supervision';C.destination.required=!route&&kind!=='supervision';
 $('#destinationLabel span').textContent=kind==='other'?'سبب المهمة (إلزامي)':'الوجهة أو الغرض';picks=[];
 const el=$('#chosenStops');if(kind==='selected'&&director)stopEditor(el,picks);
 else el.textContent=route?(data.routes.find(r=>r.kind===kind)?.stops.map(s=>s.name).join(' ← ')||'بانتظار تحديد فروع الخط من المدير العام'):'';
}
function updateFrequency(){
 const recurring=lead && C.frequency.value!=='once';
 $('#frequencyField').hidden=!lead;
 $('#occurrencesField').hidden=!recurring;
 C.occurrences.disabled=!recurring;
}
C.frequency.onchange=updateFrequency;
function newOrder(){
 $('#create').reset();updateFrequency();$('#editorError').textContent='';
 C.employee_id.innerHTML=opts(Object.fromEntries(data.staff.map(u=>[u.id,u.display_name])));if(data.staff.some(s=>s.id===data.self))C.employee_id.value=data.self;
 C.time.value=dateAt(Date.now()).slice(0,16);kinds();$('#editor').showModal();
}
let submitKey;
$('#create').addEventListener('input',()=>submitKey=null);
formGuard($('#create'),async()=>{
 submitKey||=crypto.randomUUID();
 await api('/api/fleet-missions/orders',{method:'POST',key:submitKey,body:{employee_id:C.employee_id.value,vehicle_id:C.vehicle_id.value,kind:C.kind.value,
 destination:C.destination.value,planned_minutes:+C.planned_minutes.value,scheduled_at:C.time.value+':00+03:00',stops:picks,...(lead && C.frequency.value!=='once'?{frequency:C.frequency.value,occurrences:Number(C.occurrences.value)}:{})}});
 $('#editor').close();await reload();},'#editorError');
formGuard($('#routeForm'),async()=>{await api('/api/fleet-missions/routes/'+R.kind.value,{method:'PUT',body:{stops:routePicks}});await reload();$('#msg').textContent='حُفظت فروع الخط وترتيبها.';});
function action(a,id){
 const m=data.missions.find(v=>v.id===id);selection={a,m};const car=data.cars.find(c=>c.vehicle_id===m.vehicle_id);
 $('#actionTitle').textContent=actions[a]+' · أمر '+id;$('#actionError').textContent='';
 const meter=['start','finish'].includes(a);
 $('#actionFields').innerHTML=(meter?'<label>قراءة العداد (كم)<input type="number" name="km" min="0" max="10000000" required></label><label><span><input type="checkbox" name="broken"> العداد معطل</span></label><label>مستوى البنزين<select name="fuel" required>'+opts({0:'فارغ',25:'ربع',50:'نصف',75:'ثلاثة أرباع',100:'ممتلئ'},'اختر المستوى')+'</select></label>':'')+
 (a==='start'?'<label>فحص المركبة<select name="inspection" required>'+opts({'سليمة':'سليمة','ملاحظة':'توجد ملاحظة'},'اختر نتيجة الفحص')+'</select></label>':'')+
 (a==='arrive'||a==='miss'?'<p>'+safe(m.stops.find(s=>s.state==='pending')?.name)+'</p>':'')+
 (['finish','start','miss','cancel'].includes(a)?'<label>'+(m.kind==='supervision'&&a==='finish'?'تقرير نهاية اليوم والفروع التي زرتها':'الملاحظات / السبب')+'<textarea maxlength="400" name="note" '+(a==='miss'||m.kind==='supervision'&&a==='finish'?'required':'')+'></textarea></label>':'');
 const f=$('#actionForm').elements;
 if(meter){f.broken.checked=Boolean(car?.broken_meter);const flip=()=>{f.km.required=!f.broken.checked;f.km.disabled=f.broken.checked;};f.broken.onchange=flip;flip();}
 if(a==='start')f.inspection.onchange=()=>{f.note.required=f.inspection.value==='ملاحظة';};
 $('#act').showModal();
}
formGuard($('#actionForm'),async()=>{
 const {a,m}=selection,f=$('#actionForm').elements;
 await api('/api/fleet-missions/orders/'+m.id,{method:'POST',body:{action:a,km:f.km?.value?+f.km.value:null,broken:!!f.broken?.checked,
 fuel:f.fuel?+f.fuel.value:null,inspection:f.inspection?f.inspection.value+(f.note?.value?' · '+f.note.value:''):'',note:f.note?.value||'',stop_id:m.stops.find(s=>s.state==='pending')?.id||''}});
 $('#act').close();await reload();},'#actionError');
function filterUrl(fmt){const q=new URLSearchParams(new FormData($('#filters')));q.delete('group');q.set('fmt',fmt);return '/api/fleet-missions/reports/'+F.group.value+'?'+q;}
function choices(el,list){const old=el.value;el.innerHTML=opts(list,'الكل');if(Object.hasOwn(list,old))el.value=old;}
async function loadReport(){
 const seq=++reportSeq;const r=await api(filterUrl('json'));if(seq!==reportSeq)return;report=r;
 choices(F.employee,r.employees);choices(F.vehicle,r.vehicles);$('#asof').textContent='آخر قراءة '+new Date(r.as_of).toLocaleString('ar-SA',{timeZone:'Asia/Riyadh'});
 const measured=r.rows.filter(row=>!row.parent&&typeof row.distance==='number');
 $('#totals').innerHTML=[[r.rows.length,'السجلات'],[new Set(r.rows.flatMap(row=>row.vehicle_ids)).size,'السيارات'],[measured.reduce((n,row)=>n+row.distance,0),'كم مسجلة'],[r.rows.filter(row=>row.code==='active').length,'على الطريق']].map(([v,t])=>'<div><b>'+safe(v)+'</b>'+t+'</div>').join('');
 const keys=Object.keys(r.columns);
 $('#reportRows').innerHTML='<table><thead><tr><th>الشكل</th>'+keys.map(k=>'<th>'+safe(r.columns[k])+'</th>').join('')+'</tr></thead><tbody>'+r.rows.map(row=>'<tr><td>'+picture(row)+'</td>'+keys.map(k=>'<td>'+(k==='plate'?row.plate.split(' / ').map(p=>plate(p)).join(' '):safe(row[k]??'—'))+'</td>').join('')+'</tr>').join('')+'</tbody></table>'+(!r.rows.length?'<p>لا توجد حركة مطابقة لهذا الاختيار.</p>':'');
}
function resetFilters(){choices(F.employee,{});choices(F.vehicle,{});choices(F.kind,F.group.value==='delivery'?{}:data.kinds);F.kind.value='';F.kind.disabled=F.group.value==='delivery';choices(F.status,F.group.value==='delivery'?{active:'مفتوح',completed:'مكتمل'}:names);F.status.value='';}
async function download(mode){const fmt=mode==='share'?'pdf':mode,blob=await api(filterUrl(fmt),{format:fmt});const filename=report.title+'.'+fmt;
 if(mode==='share'&&navigator.canShare){const file=new File([blob],filename,{type:blob.type});if(navigator.canShare({files:[file]})){await navigator.share({files:[file],title:report.title});return;}}
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),15000);}
C.employee_id.onchange=kinds;C.kind.onchange=cars;R.kind.onchange=initRoute;
$('#newOrder').onclick=()=>{submitKey=null;newOrder();};
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 if(b.hasAttribute('data-close'))b.closest('dialog').close();if(b.dataset.tab)tab(b.dataset.tab);if(b.dataset.act)action(b.dataset.act,+b.dataset.id);if(b.dataset.export)download(b.dataset.export).catch(error);});
$('#filters').onchange=e=>{if([F.group,F.day].includes(e.target))resetFilters();if(F.day.value)loadReport().catch(error);};
(async()=>{const s=await api('/api/session');csrf=s.csrf_token;
 if(s.user?.role==='fleet_employee'){
   $('#backToOps').hidden=true;
   const logout=$('#fleetLogout');
   logout.hidden=false;
   logout.onclick=async()=>{try{await api('/api/auth/logout',{method:'POST',body:{}});location.replace('/ops');}catch(e){error(e);}};
 }
 await reload();$('#msg').textContent='';$('#content').hidden=false;
 F.day.value=dateAt(Date.now()-86400000).slice(0,10);resetFilters();tab(lead?'reports':'orders');})().catch(error);
})();

'use strict';
(() => {
let en=false, next=null, rows=[], overview=null;
const brand=new URLSearchParams(location.search).get('operations_brand');
const headers={'X-KPS-Context':'work'};if(brand)headers['X-Operations-Brand']=brand;
function filters(){const q=new URLSearchParams(new FormData(el('filters')));for(const [k,v] of [...q])if(!v)q.delete(k);if(next)q.set('after',next);return q.toString();}
const el=id=>document.getElementById(id),tr=(ar,english)=>en?english:ar;
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function table(headings,values){const t=node('table'),h=node('tr');headings.forEach(x=>h.append(node('th',x)));const head=node('thead');head.append(h);t.append(head);const body=node('tbody');values.forEach(row=>{const r=node('tr');row.forEach(value=>{const d=node('td');d.append(value instanceof Node?value:document.createTextNode(value??'—'));r.append(d);});body.append(r);});t.append(body);return t;}
const date=v=>v?new Intl.DateTimeFormat(en?'en-SA':'ar-SA',{dateStyle:'medium',timeZone:'Asia/Riyadh'}).format(new Date(v)):'—';
const money=v=>new Intl.NumberFormat(en?'en-SA':'ar-SA',{style:'currency',currency:'SAR'}).format(Number(v)/100);
async function api(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers});if(!r.ok)throw new Error(tr('تعذر الوصول. يلزم حساب مالك نشط.','Access failed. An active owner account is required.'));return r.json();}
function render(){el('crm-title').textContent=(overview?.brand_id||brand||'KPS').toUpperCase()+' · '+tr('العملاء','Customers');document.documentElement.lang=en?'en':'ar';document.documentElement.dir=en?'ltr':'rtl';el('language').textContent=en?'العربية':'English';el('definition').textContent=tr('سجل العملاء الموثقين فقط. الإنفاق = التحصيل الفعلي بعد المرتجعات. الطلبات الملغاة والمستردة بالكامل لا تحتسب. تُستبعد الحسابات التجريبية المعلمة والطلبات التجريبية وفترة ما قبل بداية التشغيل المعتمدة. غياب اعتماد الفترة يظهر تنبيهًا. الاسم مدخل من العميل؛ رمز الجوال لا يثبت صحة الاسم. تخفى المناطق التي تقل عن ٥ عملاء. لا يتضمن التقرير موافقة تسويقية.','Verified customers only. Spending is actual collection net of refunds. Cancelled and fully refunded orders are excluded. Marked test accounts, test orders and orders before the certified operating period are excluded. A missing period is flagged. Names are self-reported; mobile OTP does not prove the name. Areas with fewer than 5 customers are suppressed. Registration does not imply marketing consent.');el('totals').replaceChildren();if(overview){Object.entries({registered_customers:tr('المسجلون الموثقون','Verified registrations'),purchasing_customers:tr('العملاء المشترون','Purchasing customers'),qualifying_orders:tr('طلبات مؤهلة','Qualifying orders'),new_purchasing_customers:tr('عملاء اشتروا مرة واحدة','One-time buyers'),repeat_customers:tr('عملاء متكررون','Repeat customers'),net_spending_halalas:tr('صافي الإنفاق','Net spending')}).forEach(([k,label])=>{const a=node('article',label);a.append(node('strong',k.endsWith('halalas')?money(overview.totals[k]):overview.totals[k]));el('totals').append(a);});if(!overview.operating_period?.configured||!overview.operating_period?.period_valid){el('totals').append(node('p',tr('لم تعتمد فترة التشغيل الجديدة بعد؛ قد تبقى بيانات تجريبية تاريخية غير مصنفة.','The new operating period has not been certified; unmarked historical test records may remain.')));}el('areas').replaceChildren(table([tr('المدينة','City'),tr('الحي','Neighborhood'),tr('العملاء','Customers'),tr('الطلبات','Orders'),tr('الفرع الأكثر طلبًا','Most requested branch')],overview.areas.map(a=>[a.city,a.neighborhood,a.customers,a.completed_orders,a.most_requested_branch_id])));}el('branch-title').textContent=tr('طلبات حسب فرع التنفيذ','Orders by fulfilling branch');if(overview)el('branches').replaceChildren(table([tr('فرع التنفيذ','Fulfilling branch'),tr('الطلبات','Orders'),tr('العملاء','Customers'),tr('صافي الإنفاق','Net spending')],overview.branches.map(b=>[b.branch_id,b.completed_orders,b.customers,money(b.net_spending_halalas)])));el('area-title').textContent=tr('مناطق الطلب المجمعة','Aggregated demand areas');el('customer-title').textContent=tr('العملاء الموثقون','Verified customers');el('customers').replaceChildren(table([tr('الاسم','Name'),tr('رقم العميل','Customer ID'),tr('الجوال الموثق','Verified mobile'),tr('تاريخ التسجيل','Registered'),tr('أول طلب','First order'),tr('آخر طلب','Latest order'),tr('الطلبات','Orders'),tr('الإنفاق','Spending')],rows.map(r=>{const b=node('button',r.display_name||r.id);b.addEventListener('click',()=>detail(r.id));return[b,r.id,r.phone,date(r.created_at),date(r.first_order_at),date(r.latest_order_at),r.qualifying_orders,money(r.net_spending_halalas)];})));el('more').hidden=!next;}
async function detail(id,after='',append=false){try{const d=await api('/api/crm/customers/'+encodeURIComponent(id)+'?orders_after='+encodeURIComponent(after));
if(!append){el('detail').replaceChildren(node('h2',d.customer.display_name||id),node('p',d.customer.phone),node('p',tr('تاريخ التسجيل: ','Registered: ')+date(d.customer.created_at)),node('p',tr('صافي الإنفاق: ','Net spending: ')+money(d.summary.net_spending_halalas)),
node('h3',tr('العناوين','Addresses')),table([tr('الاسم','Label'),tr('العنوان','Address'),tr('الحي','Neighborhood')],d.addresses.map(a=>[a.label,a.address,a.neighborhood])),
node('h3',tr('المحادثات والشكاوى','Conversations and complaints')),table([tr('المرجع','Reference'),tr('السبب','Reason'),tr('الحالة','Status'),tr('الفريق','Team'),tr('التاريخ','Date')],d.service_history.map(c=>[c.id,c.category,c.status,c.route,date(c.created_at)])),
node('h3',tr('الفروع والخدمات','Branches and services')),table([tr('الفرع المختار','Selected branch'),tr('الفرع المنفذ','Fulfilling branch'),tr('الخدمة','Service'),tr('الطلبات','Orders'),tr('الإنفاق','Spending')],d.branch_channels.map(b=>[b.selected_branch_id,b.fulfilled_branch_id,b.service,b.qualifying_orders,money(b.net_spending_halalas)])),node('h3',tr('سجل الطلبات','Order history')));}
el('detail-more')?.remove();el('detail').append(table([tr('الطلب','Order'),tr('التاريخ','Date'),tr('الخدمة','Service'),tr('الفرع','Branch'),tr('التجهيز','Preparation'),tr('التوصيل','Delivery'),tr('القيمة','Total'),tr('صافي التحصيل','Net collected')],d.orders.map(o=>[o.id,date(o.created_at),o.service,o.branch_id,o.preparation_status,o.delivery_status,money(o.total_halalas),money(o.net_spending_halalas)])));
if(d.orders_next_after){const button=node('button',tr('طلبات أخرى','More orders'));button.id='detail-more';button.addEventListener('click',()=>detail(id,d.orders_next_after,true));el('detail').append(button);}
}catch(e){el('status').textContent=e.message;}}
async function load(more=false){try{el('status').textContent=tr('جار التحميل…','Loading…');if(!more){overview=await api('/api/crm/overview');rows=[];next=null;}const data=await api('/api/crm/customers?'+filters());rows.push(...data.customers);next=data.next_after;render();el('status').textContent='';}catch(e){el('status').textContent=e.message;}}
let exportChallenge=null;
async function protectedPost(url,body){
 const session=await api('/api/session');
 if(!session.user||!session.csrf_token)throw new Error(tr('أعد تسجيل الدخول.','Sign in again.'));
 return fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',
   headers:{...headers,'X-CSRF-Token':session.csrf_token,'Content-Type':'application/json'},body:JSON.stringify(body||{})});
}
async function exportCustomers(){
 const button=el('export');button.disabled=true;
 try{const query=new URLSearchParams(filters());query.delete('after');
  const response=await protectedPost('/api/crm/export?'+query);
  if(!response.ok){const problem=await response.json();
   if(response.status===403&&problem.detail?.code==='OWNER_STEP_UP_REQUIRED'){el('export-proof').showModal();return;}
   throw new Error(problem.detail?.message||tr('تعذر التصدير.','Export failed.'));}
  const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download='customers.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(e){el('status').textContent=e.message;}finally{button.disabled=false;}
}
el('export').addEventListener('click',exportCustomers);
el('send-export-code').addEventListener('click',async()=>{const button=el('send-export-code');button.disabled=true;try{
 const response=await protectedPost('/api/identity/owner/sms/request'),data=await response.json();
 if(!response.ok)throw new Error(data.detail?.message||tr('تعذر إرسال الرمز.','Code request failed.'));
 exportChallenge=data.challenge_id;el('export-proof-status').textContent=tr('أدخل الرمز المرسل إلى جوالك المسجل.','Enter the code sent to your registered mobile.');
 }catch(e){el('export-proof-status').textContent=e.message;}finally{button.disabled=false;}});
el('verify-export-code').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{
 if(!exportChallenge)throw new Error(tr('اطلب الرمز أولًا.','Request a code first.'));
 const code=new FormData(e.currentTarget).get('code'),response=await protectedPost('/api/identity/owner/sms/verify',{challenge_id:exportChallenge,code}),data=await response.json();
 if(!response.ok)throw new Error(data.detail?.message||tr('تعذر التحقق.','Verification failed.'));
 exportChallenge=null;e.currentTarget.reset();el('export-proof').close();await exportCustomers();
 }catch(error){el('export-proof-status').textContent=error.message;}finally{button.disabled=false;}});
el('filters').addEventListener('submit',e=>{e.preventDefault();load();});
el('language').addEventListener('click',()=>{en=!en;render();});el('refresh').addEventListener('click',()=>load());el('more').addEventListener('click',()=>load(true));load();
})();

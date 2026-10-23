/* Staff-only display of canonical online orders and permanent gift claims. */
(() => {
  'use strict';
  const PAGE_SIZE=50;
  function validDate(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value||'')||value.startsWith('0000-'))return false;const d=new Date(value+'T00:00:00Z');return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value&&value!=='9999-12-31';}
  function queryFor(values,offset=0){
    if(!validDate(values.start)||!validDate(values.end))throw new Error('dates');
    const days=(new Date(values.end+'T00:00:00Z')-new Date(values.start+'T00:00:00Z'))/86400000;
    if(days<0||days>92)throw new Error('dates');
    const q=new URLSearchParams({start:values.start,end:values.end});
    if(values.branch_id)q.set('branch_id',values.branch_id);
    if(values.policy_version){if(!/^[1-9]\d*$/.test(values.policy_version)||Number(values.policy_version)>2147483647)throw new Error('version');q.set('policy_version',values.policy_version);}
    if(!Number.isInteger(offset)||offset<0||offset>100000)throw new Error('offset');
    return {daily:q.toString(),beneficiaries:new URLSearchParams([...q,['offset',String(offset)],['limit',String(PAGE_SIZE+1)]]).toString()};
  }
  function hasReportAccess(user){return Boolean(user&&['owner','delivery_manager'].includes(user.role)&&!user.delivery_context&&(user.role==='owner'||user.effective_permissions?.includes('reports_view')));}
  function grantedBrand(user,requested){return Boolean(requested&&requested!==user?.brand_id&&user?.operations_brands?.some(b=>b.id===requested));}
  function finalizeBody(values,reason){queryFor(values);if(typeof reason!=='string'||reason.trim().length<3||reason.length>500||reason.includes('\0'))throw new Error('reason');return {start:values.start,end:values.end,branch_id:values.branch_id||null,policy_version:values.policy_version?Number(values.policy_version):null,reason:reason.trim()};}
  const helpers={PAGE_SIZE,validDate,queryFor,hasReportAccess,grantedBrand,finalizeBody};
  if(typeof module==='object'&&module.exports)module.exports=helpers;
  if(typeof document==='undefined')return;

  const $=s=>document.querySelector(s),form=$('#report-filters');
  let lang=localStorage.getItem('kps-ops-language')==='en'?'en':'ar';
  let user=null,brandHeader='',report=null,beneficiaries=[],offset=0,hasNext=false,version=0,controller=null,lastValues=null,branches=[],statusKey='loading',statusError=false,csrf='',snapshotId='',snapshotMeta=null,finalizeBusy=false;
  const pendingFinalizations=new Map();
  const requested=new URLSearchParams(location.search).get('operations_brand')||'';
  const labels={
    saved:['التقارير النهائية المحفوظة','Saved final reports'],saved_latest:['آخر 50 تقريرًا ضمن صلاحياتك','Latest 50 reports within your permissions'],
    choose_saved:['اختر تقريرًا محفوظًا','Choose a saved report'],saved_empty:['لا توجد تقارير نهائية محفوظة.','No saved final reports.'],
    finalize_title:['حفظ تقرير نهائي للفترة','Save a final period report'],finalize:['تأكيد وحفظ التقرير النهائي','Confirm and save final report'],
    finalize_note:['يحفظ جميع صفحات المستفيدين وملخص الأيام كما هي وقت الحفظ. لا يوقف الهدية أو الإعلان ولا يغيّر التقفيلة المحاسبية.','Saves every beneficiary page and daily totals as observed now. This does not stop the gift or advertisement or change accounting closure.'],
    reason:['سبب حفظ التقرير','Reason for saving'],reason_error:['أدخل سببًا من 3 إلى 500 حرف.','Enter a reason of 3 to 500 characters.'],
    confirm_final:['حفظ تقرير نهائي ثابت لجميع صفحات الفترة المعروضة؟ سيبقى العرض والإعلان بحالتهما الحالية.','Save a fixed final report of all pages in the displayed period? The offer and advertisement keep their current state.'],
    final_ready:['تقرير نهائي محفوظ','Saved final report'],saved_at:['وقت الحفظ','Saved at'],full_count:['إجمالي المستفيدين المحفوظين','All saved beneficiaries'],
    download:['تنزيل التقرير النهائي كاملًا','Download the complete final report'],too_large:['الفترة أكبر من حد التقرير الكامل. اختر فترة أقصر؛ لم يتم حفظ تقرير جزئي.','The complete report exceeds the size limit. Choose a shorter period; no partial report was saved.'],
    integrity:['تعذر التحقق من سلامة التقرير النهائي.','The final report could not be verified.'],
    skip:['انتقل للتقرير','Skip to report'],back:['التشغيل','Operations'],title:['تقارير الطلبات والعروض','Online orders and offers'],
    subtitle:['الطلبات الأونلاين والعملاء المستفيدون، ضمن فروع حسابك.','Online orders and offer beneficiaries within your authorized branches.'],
    basis:['التواريخ أيام تقويمية بتوقيت الرياض، وليست أيام التقفيلة المحاسبية.','Dates are Riyadh calendar days, separate from accounting closure days.'],
    start:['من تاريخ','From date'],end:['إلى تاريخ','To date'],branch:['الفرع','Branch'],all:['جميع الفروع المخولة','All authorized branches'],
    version:['إصدار إعداد العرض (اختياري)','Offer policy revision (optional)'],show:['عرض التقرير','Show report'],retry:['إعادة المحاولة','Retry'],
    daily:['الطلبات حسب اليوم','Orders by day'],beneficiaries:['المستفيدون من الهدية','Gift beneficiaries'],print:['طباعة الصفحة المعروضة','Print the displayed page'],
    previous:['السابق','Previous'],next:['التالي','Next'],day:['اليوم','Day'],online_orders:['طلبات أونلاين','Online orders'],
    non_cancelled_orders:['طلبات غير ملغاة','Non-cancelled orders'],cancelled_orders:['طلبات ملغاة','Cancelled orders'],completed_orders:['طلبات مكتملة','Completed orders'],
    unique_customers:['عملاء فريدون','Unique customers'],first_time_customers:['عملاء جدد أونلاين','First-time online customers'],repeat_customers:['عملاء متكررون','Repeat customers'],
    gift_claims:['استفادات مسجلة من الهدية','Recorded gift claims'],non_cancelled_gift_orders:['هدايا بطلبات غير ملغاة','Non-cancelled gift orders'],cancelled_gift_orders:['هدايا بطلبات ملغاة','Cancelled gift orders'],
    customer_note:['العميل الجديد هنا هو صاحب أول طلب أونلاين غير ملغى ضمن سجل الفروع المختارة. إذا عاد وطلب خلال الفترة نفسها فقد يُحسب جديدًا ومتكررًا، ويظهر مرة واحدة في إجمالي العملاء.','A first-time customer placed their first non-cancelled online order in the selected branch history. A customer may be first-time and repeat in one period; unique customers counts them once.'],
    gift_note:['يُسجَّل الاستفادة عند تأكيد الطلب. إلغاء الطلب لا يعيد استحقاق الهدية. أرقام الجوال معروضة بآخر أربعة أرقام فقط.','A gift claim is recorded when an order is confirmed. Cancellation does not restore eligibility. Only the last four phone digits are shown.'],
    version_note:['اختيار إصدار العرض يفلتر الهدايا فقط؛ إجمالي طلبات الأونلاين يبقى لجميع الطلبات في الفترة.','The offer revision filters gift metrics only; online-order totals still include all orders in the period.'],
    loading:['جاري تحميل التقرير…','Loading report…'],empty:['لا توجد سجلات ضمن الفترة والنطاق المختارين.','No records in the selected period and scope.'],
    dates:['اختر تواريخ صحيحة، بفترة لا تتجاوز 93 يومًا.','Choose valid dates covering no more than 93 days.'],
    invalid_version:['أدخل رقم إصدار صحيحًا أكبر من صفر، أو اتركه فارغًا.','Enter a positive whole revision number, or leave it blank.'],
    forbidden:['التقرير غير متاح لصلاحيات حسابك الحالية.','This report is unavailable with your current permissions.'],
    error:['تعذر تحميل التقرير. تحقق من الاتصال وأعد المحاولة.','Unable to load the report. Check your connection and retry.'],
    expired:['انتهت الجلسة. جارٍ فتح صفحة الدخول…','Session expired. Opening sign-in…'],
    phone:['آخر أرقام الجوال','Phone suffix'],claim:['تسجيل الهدية','Gift claimed'],order:['الطلب','Order'],status:['حالة الطلب','Order status'],
    cancelled:['ملغى — الاستفادة محفوظة','Cancelled — claim retained'],confirmed:['طلب غير ملغى','Non-cancelled order'],
    customer_kind:['سجل العميل','Customer history'],first_order:['أول طلب أونلاين','First online order'],repeat_order:['طلب متكرر','Repeat order'],unknown:['غير متاح','Unavailable'],
    page:['الصفحة','Page'],scope:['النطاق المعروض','Displayed scope'],all_versions:['جميع إصدارات العرض','All offer revisions'],ready:['تم تحميل التقرير.','Report loaded.']
  };
  const t=k=>(labels[k]||['',''])[lang==='ar'?0:1];
  const count=n=>new Intl.NumberFormat(lang==='ar'?'ar-SA':'en-GB').format(Number(n||0));
  const date=v=>{const d=new Date(v);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat(lang==='ar'?'ar-SA-u-ca-gregory':'en-GB',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}).format(d):t('unknown');};
  function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e;}
  function status(key,error=false){statusKey=key;statusError=error;$('#report-status').textContent=t(key);$('#report-status').classList.toggle('error',error);}
  function clearReport(){report=null;beneficiaries=[];snapshotId='';snapshotMeta=null;$('#report-finalize-form').hidden=true;$('#report-final-evidence').hidden=true;$('#report-download').hidden=true;$('#report-content').hidden=true;$('#report-summary').replaceChildren();$('#report-daily tbody').replaceChildren();$('#report-beneficiaries').replaceChildren();}
  function translate(){document.documentElement.lang=lang;document.documentElement.dir=lang==='ar'?'rtl':'ltr';document.title=t('title');document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n));$('#report-language').textContent=lang==='ar'?'English':'العربية';$('#report-summary').setAttribute('aria-label',t('title'));$('.pagination').setAttribute('aria-label',t('beneficiaries'));options();if(report)render();status(statusKey,statusError);}
  function options(){const select=form.elements.branch_id,current=select.value;select.replaceChildren(node('option','',t('all')));select.options[0].value='';for(const b of branches){const o=node('option','',b.name);o.value=b.id;select.append(o);}select.value=branches.some(b=>b.id===current)?current:'';}
  function branchName(id){return (snapshotId?report?.branches:branches)?.find(b=>b.id===id)?.name||id||t('unknown');}
  function render(){
    $('#report-content').hidden=false;
    $('#report-finalize-form').hidden=user?.role!=='owner'||Boolean(snapshotId);
    $('#report-final-evidence').hidden=!snapshotId;$('#report-download').hidden=!snapshotId;
    if(snapshotMeta)$('#report-final-evidence').textContent=`${t('final_ready')} · ${t('saved_at')}: ${date(snapshotMeta.created_at)} · ${t('full_count')}: ${count(snapshotMeta.beneficiary_count)} · ${snapshotMeta.id}`;
    const selected=lastValues?.branch_id?branchName(lastValues.branch_id):t('all');
    $('#report-applied').textContent=`${report.start} → ${report.end} · ${t('scope')}: ${selected} · ${lastValues?.policy_version?t('version')+': '+lastValues.policy_version:t('all_versions')}`;
    const summary=$('#report-summary');summary.replaceChildren();
    for(const key of ['online_orders','unique_customers','first_time_customers','repeat_customers','gift_claims','completed_orders','cancelled_orders','cancelled_gift_orders']){const card=node('article','panel');card.append(node('h2','',t(key)),node('strong','',count(report.summary[key])));summary.append(card);}
    const keys=['online_orders','non_cancelled_orders','cancelled_orders','unique_customers','first_time_customers','repeat_customers','gift_claims','cancelled_gift_orders'];
    const head=node('tr');[t('day'),...keys.map(t)].forEach(x=>head.append(node('th','',x)));$('#report-daily thead').replaceChildren(head);
    const body=$('#report-daily tbody');body.replaceChildren();
    for(const row of report.daily){const tr=node('tr');tr.append(node('th','',row.report_date));keys.forEach(k=>tr.append(node('td','',count(row[k]))));body.append(tr);}
    if(!report.daily.length){const tr=node('tr'),td=node('td','empty',t('empty'));td.colSpan=keys.length+1;tr.append(td);body.append(tr);}
    const list=$('#report-beneficiaries');list.replaceChildren();
    for(const row of beneficiaries){const card=node('article','beneficiary-card');card.append(node('h3','',`${t('order')} ${row.order_no||row.order_id}`));const dl=node('dl');
      const fields=[[t('branch'),branchName(row.branch_id)],[t('phone'),row.phone_suffix||t('unknown'),'phone-suffix'],[t('claim'),date(row.claimed_at)],[t('version'),row.policy_version],[t('status'),row.cancelled?t('cancelled'):t('confirmed'),row.cancelled?'cancelled':''],[t('customer_kind'),row.first_online_order===true?t('first_order'):row.first_online_order===false?t('repeat_order'):t('unknown')]];
      fields.forEach(([label,value,cls])=>{const div=node('div');div.append(node('dt','',label),node('dd',cls,value));dl.append(div);});card.append(dl);list.append(card);}
    if(!beneficiaries.length)list.append(node('p','empty',t('empty')));
    $('#report-page').textContent=`${t('page')} ${count(offset/PAGE_SIZE+1)}`;$('#report-previous').disabled=offset===0;$('#report-next').disabled=!hasNext||offset+PAGE_SIZE>100000;
  }
  async function api(path,signal,selected=true,options={}){const headers={Accept:'application/json','X-KPS-Context':'work'};if(selected&&brandHeader)headers['X-Operations-Brand']=brandHeader;if(options.body){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf;headers['Idempotency-Key']=options.key;}const r=await fetch(path,{method:options.method||'GET',body:options.body?JSON.stringify(options.body):undefined,credentials:'same-origin',cache:'no-store',headers,signal});if(r.status===401){status('expired',true);location.assign('/ops');throw Object.assign(new Error('auth'),{status:401});}if(!r.ok){const data=await r.json().catch(()=>({}));throw Object.assign(new Error('request'),{status:r.status,code:data?.detail?.code});}return options.raw?r:r.json();}
  async function validateSession(signal){const result=await api('/api/session',signal);if(!result.user||result.reauthentication_required){status('expired',true);location.assign('/ops');throw Object.assign(new Error('auth'),{status:401});}if(!hasReportAccess(result.user))throw Object.assign(new Error('forbidden'),{status:403});if(brandHeader&&result.user.brand_id!==brandHeader)throw Object.assign(new Error('forbidden'),{status:403});user=result.user;csrf=result.csrf_token||'';$('#report-saved-panel').hidden=false;}
  function values(){return Object.fromEntries(['start','end','branch_id','policy_version'].map(k=>[k,form.elements[k].value]));}
  async function load(nextOffset=0,useLast=false){if(useLast&&snapshotId)return openClosure(snapshotId,nextOffset);const token=++version;controller?.abort();controller=new AbortController();const signal=controller.signal;$('#report-retry').hidden=true;status('loading');$('#report-content').hidden=true;$('#report-previous').disabled=$('#report-next').disabled=true;
    try{const applied=useLast&&lastValues?lastValues:values(),q=queryFor(applied,nextOffset);await validateSession(signal);const [daily,claims]=await Promise.all([api('/api/reports/promotions/daily?'+q.daily,signal),api('/api/reports/promotions/beneficiaries?'+q.beneficiaries,signal)]);if(token!==version)return;if(daily.brand_id!==user.brand_id||claims.brand_id!==user.brand_id)throw Object.assign(new Error('scope'),{status:403});if(!applied.branch_id)branches=daily.branches||[];else if(user.role!=='owner')branches=branches.filter(b=>user.branch_ids?.includes(b.id));snapshotId='';snapshotMeta=null;lastValues={...applied};report=daily;offset=nextOffset;hasNext=claims.beneficiaries.length>PAGE_SIZE;beneficiaries=claims.beneficiaries.slice(0,PAGE_SIZE);options();render();status('ready');}
    catch(e){if(token!==version||e.name==='AbortError')return;clearReport();if(e.status===403||e.status===404){$('#report-saved-panel').hidden=true;$('#report-saved').replaceChildren();}status(e.message==='dates'?'dates':e.message==='version'?'invalid_version':e.status===401?'expired':e.status===403||e.status===404?'forbidden':'error',true);$('#report-retry').hidden=e.status===401||e.status===403;}
  }
  function failure(e){clearReport();if(e.status===403||e.status===404){$('#report-saved-panel').hidden=true;$('#report-saved').replaceChildren();}status(e.message==='reason'?'reason_error':e.code==='REPORT_TOO_LARGE'?'too_large':e.code==='REPORT_INTEGRITY_INVALID'?'integrity':e.status===401?'expired':e.status===403||e.status===404?'forbidden':'error',true);}
  async function savedReports(){try{await validateSession();const data=await api('/api/reports/promotions/closures');if(data.brand_id!==user.brand_id)throw Object.assign(new Error('scope'),{status:403});const select=$('#report-saved');select.replaceChildren(node('option','',data.closures?.length?t('choose_saved'):t('saved_empty')));select.options[0].value='';for(const row of data.closures||[]){const option=node('option','',`${row.start_date} → ${row.end_date} · ${date(row.created_at)}`);option.value=row.id;select.append(option);}}catch(e){failure(e);}}
  async function openClosure(id,nextOffset=0){const token=++version;controller?.abort();controller=new AbortController();const signal=controller.signal;status('loading');$('#report-content').hidden=true;
    try{await validateSession(signal);const data=await api('/api/reports/promotions/closures/'+encodeURIComponent(id)+'?'+new URLSearchParams({offset:String(nextOffset),limit:String(PAGE_SIZE)}),signal);if(token!==version)return;if(data.closure.brand_id!==user.brand_id||data.report.brand_id!==user.brand_id)throw Object.assign(new Error('scope'),{status:403});snapshotId=id;snapshotMeta={...data.closure,beneficiary_count:data.beneficiary_count};report=data.report;lastValues={start:report.start,end:report.end,branch_id:report.selected_branch_id||'',policy_version:report.policy_version?String(report.policy_version):''};offset=nextOffset;hasNext=offset+data.beneficiaries.length<data.beneficiary_count;beneficiaries=data.beneficiaries;options();render();status('ready');}
    catch(e){if(token!==version||e.name==='AbortError')return;failure(e);}
  }
  async function finalize(event){event.preventDefault();if(finalizeBusy||!report||snapshotId||user?.role!=='owner')return;let body;try{body=finalizeBody(lastValues,$('#report-finalize-reason').value);}catch(e){status('reason_error',true);return;}if(!window.confirm(t('confirm_final')))return;finalizeBusy=true;$('#report-finalize').disabled=true;const actor=user.id,brand=user.brand_id,signature=JSON.stringify([actor,brand,body]);if(!pendingFinalizations.has(signature))pendingFinalizations.set(signature,crypto.randomUUID());
    try{await validateSession();if(user.id!==actor||user.brand_id!==brand||user.role!=='owner')throw Object.assign(new Error('scope'),{status:403});const data=await api('/api/reports/promotions/closures',undefined,true,{method:'POST',body,key:pendingFinalizations.get(signature)});if(data.brand_id!==brand)throw Object.assign(new Error('scope'),{status:403});pendingFinalizations.delete(signature);await openClosure(data.closure.id);}
    catch(e){failure(e);}finally{finalizeBusy=false;$('#report-finalize').disabled=false;}
  }
  async function downloadFinal(){if(!snapshotId)return;const id=snapshotId;try{await validateSession();const response=await api('/api/reports/promotions/closures/'+encodeURIComponent(id)+'/download?lang='+lang,undefined,true,{raw:true});const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download='online-gift-report-'+id+'-'+lang+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){failure(e);}}
  async function init(){translate();const todayParts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Riyadh',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()),part=k=>todayParts.find(x=>x.type===k).value,today=`${part('year')}-${part('month')}-${part('day')}`;form.elements.start.value=form.elements.end.value=today;status('loading');
    try{const session=await api('/api/session',undefined,false);if(!session.user||session.reauthentication_required){location.assign('/ops');return;}if(requested&&requested!==session.user.brand_id){if(!grantedBrand(session.user,requested))throw Object.assign(new Error('forbidden'),{status:403});brandHeader=requested;}await validateSession();$('#report-brand').textContent=user.operations_brands?.find(b=>b.id===user.brand_id)?.name||({kps:'KPS COFFEE',enbishan:'ENBISHAN'}[user.brand_id]||user.brand_id);document.body.dataset.brand=user.brand_id;$('#report-back').href='/ops?'+new URLSearchParams({operations_brand:user.brand_id});form.hidden=false;await load();}
    catch(e){clearReport();status(e.status===401?'expired':e.status===403?'forbidden':'error',true);$('#report-retry').hidden=e.status===401||e.status===403;}
  }
  $('#report-finalize-form').addEventListener('submit',finalize);$('#report-saved-refresh').addEventListener('click',savedReports);$('#report-saved').addEventListener('change',()=>{if($('#report-saved').value)openClosure($('#report-saved').value);});$('#report-download').addEventListener('click',downloadFinal);
  form.addEventListener('submit',event=>{event.preventDefault();load();});$('#report-retry').addEventListener('click',()=>user?load():init());$('#report-previous').addEventListener('click',()=>load(Math.max(0,offset-PAGE_SIZE),true));$('#report-next').addEventListener('click',()=>{if(hasNext)load(offset+PAGE_SIZE,true);});$('#report-language').addEventListener('click',()=>{lang=lang==='ar'?'en':'ar';localStorage.setItem('kps-ops-language',lang);translate();if(report)status('ready');});$('#report-print').addEventListener('click',()=>window.print());init();
})();

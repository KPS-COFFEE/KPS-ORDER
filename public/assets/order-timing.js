'use strict';
(() => {
  const terminal = o => ['cancelled','rejected','completed'].includes(o.preparation_status) || ['cancelled','delivered'].includes(o.delivery_status);
  const seconds = (phase, elapsed = 0) => Number.isFinite(phase?.seconds) ? phase.seconds + (phase.state === 'running' ? elapsed : 0) : null;
  function preparation(o, elapsed = 0) {
    const wait=o.timings?.acceptance_wait, prep=o.timings?.preparation;
    if(wait?.state==='cancelled'||prep?.state==='cancelled')return {seconds:null,state:'cancelled'};
    if(wait?.state==='running')return {...wait,seconds:seconds(wait,elapsed)};
    if(!Number.isFinite(wait?.seconds)||!Number.isFinite(prep?.seconds))return {seconds:null,state:'missing'};
    return {...prep,seconds:wait.seconds+seconds(prep,elapsed)};
  }
  function classification(o, metric, elapsed = 0) {
    const phase=o.timings?.[metric], value=seconds(phase,elapsed);
    if(phase?.state==='cancelled')return 'excluded';
    if(value===null)return phase?.state==='not_started'?'not_started':'missing';
    const policy=o.performance_snapshot?.policies?.[metric];
    if(!Number.isFinite(policy?.t1_seconds)||!Number.isFinite(policy?.t2_seconds))return 'unconfigured';
    return value<=policy.t1_seconds?'excellent':value<=policy.t2_seconds?'good':'late';
  }
  function duration(phase, tr) {
    if(!Number.isFinite(phase?.seconds))return tr(({cancelled:'ملغي — لا يحتسب',not_started:'لم تبدأ',not_applicable:'لا ينطبق'})[phase?.state]||'توقيت غير مكتمل',({cancelled:'Cancelled — excluded',not_started:'Not started',not_applicable:'Not applicable'})[phase?.state]||'Incomplete timing');
    const value=Math.max(0,Math.floor(phase.seconds));
    return `${Math.floor(value/60)}:${String(value%60).padStart(2,'0')}`;
  }
  async function open(ctx) {
    const {api,tr,esc,heading,view,user,branches,branchLabel,orderLabel,statusName,datetime}=ctx;
    view.filters??={period:'today'};view.offset??=0;
    heading(tr('متابعة أوقات الطلبات','Order timing monitor'),tr('التجهيز من وصول الطلب إلى الجاهزية؛ التوصيل من استلام السائق إلى التسليم؛ الإجمالي يشمل كل الانتظار.','Preparation: arrival to ready. Delivery: driver pickup to handoff. Total includes all waiting.'),`<button class="quiet" data-action="refresh">${esc(tr('تحديث','Refresh'))}</button>`);
    const q=new URLSearchParams({service:'delivery',limit:'50',offset:String(view.offset),sort:'active_oldest'});
    for(const [key,value] of Object.entries(view.filters))if(value&&key!=='view')q.set(key,value);
    if(user.role==='delivery_manager')q.set('manager_view',view.filters.view==='history'?'history':'current');
    const data=await api('/api/orders?'+q);
    const rows=data.orders||[], received=performance.now();
    const drivers=[...new Map(rows.filter(o=>o.driver_id).map(o=>[o.driver_id,o.driver_name])).entries()];
    const option=(value,label,selected)=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`;
    const filters=`<form id="timing-filters" class="timing-filters">${user.role==='delivery_manager'?`<label>${esc(tr('الشفت','Shift'))}<select name="view">${option('current',tr('الشفت الحالي','Current shift'),view.filters.view||'current')}${option('history',tr('الشفتات السابقة','Previous shifts'),view.filters.view)}</select></label>`:''}<label>${esc(tr('الفترة','Period'))}<select name="period">${option('today',tr('اليوم التشغيلي','Operational day'),view.filters.period)}${option('yesterday',tr('أمس التشغيلي','Previous operational day'),view.filters.period)}</select></label><label>${esc(tr('الفرع','Branch'))}<select name="branch_id">${option('',tr('كل الفروع المسموحة','All permitted branches'),view.filters.branch_id||'')}${branches.map(b=>option(b.id,branchLabel(b),view.filters.branch_id)).join('')}</select></label><label>${esc(tr('السائق','Driver'))}<select name="driver_id">${option('',tr('كل السائقين','All drivers'),view.filters.driver_id||'')}${drivers.map(([id,name])=>option(id,name,view.filters.driver_id)).join('')}${view.filters.driver_id&&!drivers.some(([id])=>id===view.filters.driver_id)?option(view.filters.driver_id,view.driverLabel||tr('السائق المختار','Selected driver'),view.filters.driver_id):''}</select></label><label>${esc(tr('الحالة','Status'))}<select name="status">${[['',tr('كل الحالات','All statuses')],['active',tr('الجارية','Active')],['ready',tr('جاهزة','Ready')],['picked_up',tr('مع السائق','With driver')],['delivered',tr('المكتملة','Completed')],['cancelled',tr('الملغاة','Cancelled')]].map(([v,l])=>option(v,l,view.filters.status||'')).join('')}</select></label><label>${esc(tr('الترتيب داخل الصفحة','Order within page'))}<select name="priority">${option('',tr('الجاري الأقدم','Oldest active'),view.priority||'')}${option('late',tr('المتأخر أولًا','Late first'),view.priority)}</select></label><button type="submit">${esc(tr('تطبيق','Apply'))}</button></form>`;
    const headers=[tr('الطلب / وقت الوصول','Order / arrival'),tr('الفرع','Branch'),tr('الحي','Neighborhood'),tr('السائق','Driver'),tr('التجهيز','Preparation'),tr('انتظار السائق','Driver wait'),tr('التوصيل','Delivery'),tr('إجمالي المدة','Total duration'),tr('الحالة','Status'),tr('الملاحظات','Notes')];
    const root=document.querySelector('#content');
    root.innerHTML=`<section class="timing-screen">${filters}<p class="muted timing-scope">${esc(tr('ملخص الطلبات المعروضة في هذه الصفحة — 50 طلبًا كحد أقصى. اختيار السائق من النتائج المعروضة.','Summary of this page — up to 50 orders. Driver choices come from displayed results.'))}${user.role==='delivery_manager'&&view.filters.view!=='history'?' · '+esc(tr('الفترة تتبع الشفت الحالي حتى لو تجاوز منتصف الليل.','Period follows the current shift across midnight.')):''}</p><div class="timing-summary" aria-label="${esc(tr('ملخص النتائج','Results summary'))}"></div><p class="muted small-text">${esc(tr('الأوقات بالدقائق:الثواني، وتتحدث أثناء سير الطلب. الألوان تتبع معيار المحافظة المحفوظ؛ المعيار غير المحدد لا يعني أداءً جيدًا. التجهيز يشمل انتظار قبول الفرع.','Times are minutes:seconds and advance for active stages. Colors use the frozen province policy; an unset policy is not good performance. Preparation includes branch acceptance wait.'))}</p><div class="timing-scroll" tabindex="0" role="region" aria-label="${esc(tr('جدول متابعة أوقات الطلبات — مرر أفقيًا لعرض كل الأعمدة','Order timing table — scroll horizontally for all columns'))}"><table class="timing-table"><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody></tbody></table></div><div class="timing-pagination"><button class="quiet" id="timing-prev" ${view.offset===0?'disabled':''}>${esc(tr('السابق','Previous'))}</button><span>${esc(tr('الصفحة','Page'))} ${Math.floor(view.offset/50)+1}</span><button class="quiet" id="timing-next" ${data.next_offset==null?'disabled':''}>${esc(tr('التالي','Next'))}</button></div></section>`;
    const screen=root.querySelector('.timing-screen');
    for(const select of screen.querySelectorAll('select[name=branch_id],select[name=driver_id]')){
      const full=document.createElement('small');full.className='timing-choice';select.after(full);
      const show=()=>{full.textContent=select.selectedOptions[0]?.textContent||'';};select.addEventListener('change',show);show();
    }
    function render() {
      if(root.querySelector('.timing-screen')!==screen||!root.isConnected)return;
      const elapsed=Math.max(0,(performance.now()-received)/1000);
      const labels={excellent:tr('ممتاز','Excellent'),good:tr('قريب من الحد','Approaching limit'),late:tr('متأخر','Late'),unconfigured:tr('المعيار غير محدد','Policy not configured'),not_started:tr('لم تبدأ','Not started'),missing:tr('توقيت غير مكتمل','Incomplete timing'),excluded:tr('ملغي — لا يحتسب','Cancelled — excluded')};
      const cell=(o,key)=>{const phase=key==='preparation'?preparation(o,elapsed):{...o.timings?.[key],seconds:seconds(o.timings?.[key],elapsed)};const code=['delivery','total'].includes(key)?classification(o,key,elapsed):'neutral';return `<td class="timing-duration timing-${code}"><span class="${Number.isFinite(phase.seconds)?'timing-value':'timing-unavailable'}" dir="${Number.isFinite(phase.seconds)?'ltr':'auto'}">${esc(duration(phase,tr))}</span>${phase.state==='running'?`<small>${esc(tr('جارٍ','Running'))}</small>`:''}${['delivery','total'].includes(key)?`<small>${esc(labels[code])}</small>`:''}</td>`;};
      const sorted=[...rows];if(view.priority==='late')sorted.sort((a,b)=>Number(classification(b,'delivery',elapsed)==='late'||classification(b,'total',elapsed)==='late')-Number(classification(a,'delivery',elapsed)==='late'||classification(a,'total',elapsed)==='late'));
      root.querySelector('tbody').innerHTML=sorted.map(o=>{const notes=[o.snapshot?.notes,...(o.snapshot?.items||[]).filter(i=>i.note||i.notes).map(i=>`${i.name}: ${i.note||i.notes}`)].filter(Boolean).join('\n');return `<tr data-timing-order="${esc(o.id)}"><th scope="row"><strong>${esc(orderLabel(o))}</strong><small>${esc(datetime(o.created_at))}</small></th><td>${esc(branchLabel(o))}</td><td>${esc(o.neighborhood||o.customer?.neighborhood||tr('الحي غير مسجل','Neighborhood not recorded'))}</td><td>${esc(o.driver_name||tr('غير مسند','Unassigned'))}</td>${cell(o,'preparation')}${cell(o,'driver_wait')}${cell(o,'delivery')}${cell(o,'total')}<td><span class="timing-status">${esc(statusName(['picked_up','problem','delivered','cancelled'].includes(o.delivery_status)?o.delivery_status:o.preparation_status))}</span>${classification(o,'delivery',elapsed)==='late'?`<small class="timing-late">${esc(tr('تأخر توصيل','Delivery delay'))}</small>`:classification(o,'total',elapsed)==='late'?`<small class="timing-late">${esc(tr('تجاوز إجمالي المدة','Total duration exceeded'))}</small>`:''}</td><td class="timing-notes">${esc(notes||tr('لا توجد ملاحظات','No notes'))}</td></tr>`;}).join('')||`<tr><td colspan="10">${esc(tr('لا توجد طلبات ضمن هذه الفلاتر.','No matching orders.'))}</td></tr>`;
      const count=test=>rows.filter(test).length;const completed=rows.filter(o=>o.delivery_status==='delivered'&&Number.isFinite(o.timings?.delivery?.seconds));const average=completed.length?duration({seconds:completed.reduce((n,o)=>n+o.timings.delivery.seconds,0)/completed.length},tr):'—';
      root.querySelector('.timing-summary').innerHTML=[[tr('جارية','Active'),count(o=>!terminal(o))],[tr('بانتظار التجهيز','Awaiting preparation'),count(o=>!terminal(o)&&!['ready','handed_over'].includes(o.preparation_status))],[tr('مع السائق','With driver'),count(o=>!terminal(o)&&['running'].includes(o.timings?.delivery?.state))],[tr('متأخرة','Late'),count(o=>classification(o,'delivery',elapsed)==='late'||classification(o,'total',elapsed)==='late')],[tr('مكتملة','Completed'),count(o=>o.delivery_status==='delivered')],[tr('متوسط التوصيل المكتمل','Completed delivery average'),average]].map(([label,value])=>`<div><span>${esc(label)}</span><strong dir="ltr">${esc(value)}</strong></div>`).join('');
    }
    render();
    root.querySelector('#timing-filters').addEventListener('submit',e=>{e.preventDefault();const form=e.currentTarget;const values=Object.fromEntries(new FormData(form));view.driverLabel=form.driver_id.selectedOptions[0]?.textContent;view.priority=values.priority;delete values.priority;view.filters=values;view.offset=0;ctx.reload();});
    root.querySelector('#timing-prev').addEventListener('click',()=>{view.offset=Math.max(0,view.offset-50);ctx.reload();});
    root.querySelector('#timing-next').addEventListener('click',()=>{view.offset=data.next_offset;ctx.reload();});
    const timer=setInterval(()=>{if(root.querySelector('.timing-screen')!==screen||!ctx.active()){clearInterval(timer);return;}if(!document.hidden)render();},1000);
  }
  window.KPSOrderTiming={open,preparation,seconds,classification,duration};
})();

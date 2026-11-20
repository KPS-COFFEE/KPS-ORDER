/* Daily geographic oversight; has no writes or dispatch gates. */
(() => {
  'use strict';
  async function open(h) {
    const {api, modal, state, tr, esc} = h;
    const brands = state.user.central_operations?.brands || [{id:state.user.brand_id,name:state.user.brand_id}];
    modal(tr('تقرير الفروع والأحياء اليومي','Daily branches and neighborhoods'), `<form id="proximity-filter" class="filter-form">
      <label>${esc(tr('اليوم التشغيلي','Operational day'))}<input name="day" type="date"></label>
      <label>${esc(tr('البراند','Brand'))}<select name="brand">${brands.map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label>
      <button type="submit">${esc(tr('عرض التقرير','Show report'))}</button></form><div id="proximity-results" aria-live="polite"></div>`);
    const form=document.querySelector('#proximity-filter'),host=document.querySelector('#proximity-results');
    let generation=0;
    const distance = value => typeof value==='number' ? (value/1000).toFixed(2)+' km' : '—';
    async function load(){
      const current=++generation, button=form.querySelector('button');button.disabled=true;
      try{
        const data=await api('/api/branch-proximity'+(form.elements.day.value?'?day='+encodeURIComponent(form.elements.day.value):''),{brand:form.elements.brand.value});
        if(!host.isConnected||generation!==current)return;
        form.elements.day.value=data.day;
        const labels=[tr('الطلب','Order'),tr('الحي','Neighborhood'),tr('المدينة','City'),tr('فرع الطلب','Order branch'),tr('فرع التحضير / الاستلام','Preparation / pickup branch'),tr('آخر وجهة طباعة الفاتورة','Latest invoice print destination'),tr('حالة الطباعة','Print status'),tr('المسافة للعميل','Distance to customer'),tr('الفرع الأقرب جغرافيًا','Geographically nearest branch'),tr('مسافة الأقرب','Nearest distance')];
        const statuses={queued:tr('بانتظار الطباعة','Queued'),claimed:tr('قيد الإرسال','Sending'),submitted:tr('أُرسلت للطابعة','Sent to spooler'),uncertain:tr('غير مؤكدة','Uncertain')};
        host.innerHTML=`<p class="notice">${esc(data.note)}</p><p>${esc(tr('عدد الطلبات','Order count'))}: ${data.rows.length}</p><div class="table-scroll"><table><thead><tr>${labels.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(r=>`<tr class="${r.is_nearest===false?'proximity-farther':''}">${[r.reference,r.neighborhood||'—',r.city,r.order_branch_name,r.preparation_branch_name||tr('خارج النطاق','Outside scope'),r.print_branch_name||'—',statuses[r.print_status]||r.print_status||'—',distance(r.distance_to_customer_meters),r.nearest_branch_name||'—',distance(r.nearest_distance_meters)].map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${!data.rows.length?`<p>${esc(tr('لا توجد طلبات توصيل لهذا اليوم ضمن الصلاحيات.','No delivery orders for this day in your scope.'))}</p>`:''}`;
      }catch(error){if(host.isConnected)host.textContent=error.message;}
      finally{if(button.isConnected)button.disabled=false;}
    }
    form.addEventListener('submit',event=>{event.preventDefault();void load();});
    await load();
  }
  window.KPSBranchProximity={open};
})();

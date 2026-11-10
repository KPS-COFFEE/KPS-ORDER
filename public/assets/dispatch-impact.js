/* Prospective dispatch evidence: no clock, coordinates or override claims are invented. */
(() => {
  'use strict';
  const create=({root,select,api,orderId,taskId,policy,tr})=>{
    let generation=0,quote=null,closed=false;
    const text=(tag,value)=>{const e=document.createElement(tag);e.textContent=value;return e;};
    const status=text('p',tr('اختر السائق لحساب أثر الإسناد.','Choose a driver to calculate dispatch impact.'));
    const results=document.createElement('div');results.setAttribute('aria-live','polite');
    const warning=text('p',tr('مدة الطريق مع سماح تجهيز وتوقف تقديري؛ جاهزية الفرع غير المسجلة ليست مؤكدة.','Road times include explicit preparation and stop allowances; unrecorded branch readiness is not confirmed.'));
    const choices=document.createElement('div');choices.hidden=true;
    function picker(name,label,values){const wrap=text('label',label),input=document.createElement('select');input.name=name;const empty=text('option',tr('اختر','Choose'));empty.value='';input.append(empty);for(const [value,label] of Object.entries(values||{})){const option=text('option',label);option.value=value;input.append(option);}wrap.append(input);choices.append(wrap);return input;}
    const defaults={all_drivers_busy:tr('جميع السائقين مشغولون','All drivers busy'),branch_preparation:tr('انتظار تجهيز الفرع','Branch preparation'),same_route_batch:tr('تجميع طلبات على نفس المسار','Same-route batch'),vehicle_or_driver_issue:tr('مشكلة بالسائق أو المركبة','Driver or vehicle issue'),reduce_delay:tr('إعادة توزيع لتقليل التأخير','Redistribute to reduce delay'),customer_location_change:tr('تغيير موقع العميل','Customer location change'),estimate_unavailable:tr('تقدير المسار غير متاح','Estimate unavailable')};
    const contactDefaults={agreed:tr('تم التواصل ووافق','Contacted and agreed'),disagreed:tr('تم التواصل ولم يوافق','Contacted and disagreed'),no_answer:tr('لا يرد','No answer'),not_contacted:tr('لم يتم التواصل','Not contacted')};
    const reason=picker('impact_reason',tr('سبب الإسناد رغم التأخير أو عدم توفر تقدير','Reason for late or unverified assignment'),policy.reason_options||defaults);
    const contact=picker('impact_contact',tr('التواصل مع العميل','Customer contact'),policy.contact_options||contactDefaults);
    const refresh=text('button',tr('تحديث التقدير','Refresh estimate'));refresh.type='button';
    root.replaceChildren(status,warning,results,choices,refresh);
    function showUnavailable(){quote=null;choices.hidden=false;status.textContent=tr('التقدير غير متاح؛ يمكنك الاستمرار بتوثيق السبب وحالة التواصل، دون وقت وصول مؤكد.','Estimate unavailable; record a reason and contact status to continue without a claimed arrival time.');}
    async function update(){
      const driver=select.value,own=++generation;quote=null;reason.value='';contact.value='';results.replaceChildren();
      choices.hidden=!driver;if(!driver){status.textContent=tr('اختر السائق لحساب أثر الإسناد.','Choose a driver to calculate dispatch impact.');return;}
      status.textContent=tr('جارٍ حساب المسار؛ الاستمرار دون تقدير يتطلب اختيار السبب وحالة التواصل.','Calculating; proceeding without an estimate requires reason and contact choices.');
      try{
        const data=await api('/api/operations/dispatch-impact',{method:'POST',body:{...(taskId?{task_id:taskId}:{order_id:orderId}),driver_shift_id:driver}});
        if(closed||own!==generation||!root.isConnected||select.value!==driver)return;
        if(!data.enabled){root.hidden=true;return;}
        quote={id:data.quote_id,driver,receivedAt:Date.now(),ttl:data.expires_in_seconds};
        const labels={within_target:tr('ضمن الوقت المستهدف بالتقدير الحالي','Within target under current estimates'),late:tr('متوقع تأخير — راجع الطلبات أدناه','Delay expected — review orders below'),unverified:tr('بعض المواعيد غير متاحة؛ لا يوجد تقييم مكتمل','Some deadlines are unavailable; assessment incomplete'),unavailable:tr('حساب الطريق غير متاح','Road estimate unavailable')};
        status.textContent=labels[data.status]||labels.unavailable;choices.hidden=data.status==='within_target';
        if(data.reason)results.append(text('p',tr('لا يوجد وقت وصول مؤكد لهذه الحالة.','No arrival time is claimed for this case.')));
        if(data.orders?.length){
          const table=document.createElement('table'),head=document.createElement('tr');
          for(const label of [tr('الطلب','Order'),tr('الموعد الأصلي','Deadline'),tr('التسليم المتوقع','Expected completion'),tr('التأخير المتوقع','Expected lateness'),tr('تأخير إضافي بسبب الإضافة','Added lateness')])head.append(text('th',label));table.append(head);
          for(const row of data.orders){const line=document.createElement('tr');const clock=value=>value?new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'—';
            for(const value of [row.order_no||row.order_id,clock(row.deadline_at),clock(row.completion_at),row.late_seconds===null?'—':Math.ceil(row.late_seconds/60)+' min',Math.ceil(row.added_lateness_seconds/60)+' min'])line.append(text('td',value));table.append(line);}
          results.append(table);
        }
      }catch{if(!closed&&own===generation&&root.isConnected)showUnavailable();}
    }
    select.addEventListener('change',update);refresh.addEventListener('click',update);
    return {payload(){
      if(closed||root.hidden)return {};
      const fresh=quote&&quote.driver===select.value&&Date.now()-quote.receivedAt<=Math.min(quote.ttl||0,40)*1000;
      if(!fresh)showUnavailable();
      if(!choices.hidden&&(!reason.value||!contact.value))throw Error(tr('اختر سبب الإسناد وحالة التواصل من القائمتين.','Select assignment reason and customer contact status.'));
      return {impact_quote_id:fresh?quote.id:null,impact_reason:reason.value||null,impact_contact:contact.value||null};
    },close(){closed=true;generation++;select.removeEventListener('change',update);}};
  };
  window.KPSDispatchImpact={create};
})();

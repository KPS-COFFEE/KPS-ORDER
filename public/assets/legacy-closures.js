/* Attest historical delivery only; shift closure and payment remain independent. */
(() => {
  'use strict';
  const base='/api/legacy-deliveries';
  window.KPSLegacyClosures={
    async open(ctx,sid){
      const {api,mutation,tr,esc,money,modal,closeModal,onChanged}=ctx;
      const data=await api(base+'/shifts/'+encodeURIComponent(sid)+'/preview');
      const orders=data.orders||[];
      if(!orders.length)throw new Error(tr('لا توجد طلبات سابقة مؤهلة لهذا الإجراء.','There are no eligible legacy orders for this action.'));
      modal(tr('اعتماد تسليم الطلبات السابقة','Attest delivery of prior orders'),`<form class="stack" id="legacy-delivery-form">
        <p><strong>${esc(data.driver_name)}</strong> · ${esc(tr('يوم التشغيل','Operating day'))}: <bdi dir="ltr">${esc(data.operational_date)}</bdi></p>
        <p class="notice">${esc(tr('يُثبت أن الطلبات المدرجة سُلّمت سابقًا في ساباد. يقفل مدير التوصيل الوردية ويستكمل التحصيل عبر الإجراءات المعتادة.','Attests that the listed orders were delivered previously in Sabad. The delivery manager closes the workday and completes collections through the usual procedures.'))}</p>
        <h3>${esc(tr('الطلبات السابقة المشمولة','Included prior orders'))}</h3>
        ${orders.map(o=>`<section class="panel"><strong><bdi dir="ltr">${esc(o.order_reference||o.order_id)}</bdi></strong> · ${esc(money(o.total_halalas))}</section>`).join('')}
        <p>${esc(tr('وقت التسليم الفعلي غير موثق بهذا الإجراء. تُحفظ موافقة المالك ووقت تسجيلها في سجل الطلب.','This action does not establish the actual delivery time. The owner attestation and its recording time are saved in the order history.'))}</p>
        <label>${esc(tr('سبب الاعتماد ومرجع الطلبات السابقة','Attestation reason and prior-order reference'))}<textarea name="reason" minlength="5" maxlength="2000" required></textarea></label>
        <label class="check"><input name="confirmed" type="checkbox" required>${esc(tr('أؤكد أن جميع الطلبات المدرجة سُلّمت سابقًا في ساباد، وأعتمد تسجيلها مسلّمة في هذا النظام.','I attest that every listed order was delivered previously in Sabad and approve recording it as delivered in this system.'))}</label>
        <p class="form-error" role="alert"></p><button type="submit">${esc(tr('اعتماد تسليم الطلبات السابقة','Attest delivery of prior orders'))}</button></form>`,async fd=>{
          if(!fd.has('confirmed'))throw new Error(tr('أكد تسليم جميع الطلبات المدرجة.','Confirm delivery of every listed order.'));
          const reason=String(fd.get('reason')||'').trim();
          if(reason.length<5)throw new Error(tr('اكتب سببًا واضحًا للاعتماد.','Enter a clear attestation reason.'));
          const result=await mutation(base+'/shifts/'+encodeURIComponent(data.anchor_shift_id||sid)+'/complete',{
            operational_date:data.operational_date,
            orders:orders.map(o=>({order_id:o.order_id,task_id:o.task_id,expected_version:o.expected_version})),
            reason,confirmed:true
          });
          closeModal();await onChanged(result);
        });
    }
  };
})();

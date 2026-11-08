/* Owner configuration only; no setting is activated by loading this module. */
(() => {
  'use strict';
  async function open(ctx) {
    const {api,tr,esc,modal,closeModal,user}=ctx;
    if(user.role!=='owner')throw new Error(tr('للمالك فقط','Owner only'));
    const data=await api('/api/morning-delivery/policy'),p=data.policy;
    const enbishan=user.brand_id==='enbishan';
    const branchIds=new Set(data.branches.map(branch=>branch.id));
    const managers=data.users.filter(u=>u.role==='delivery_manager'&&(!enbishan||u.branch_ids.some(id=>branchIds.has(id))));
    const choices=(name,rows,selected,label)=>rows.map(row=>`<label><input type="checkbox" name="${name}" value="${esc(row.id)}" ${selected.includes(row.id)?'checked':''}> ${esc(label(row))}</label>`).join('');
    const employeeLabel=u=>`${u.display_name}${u.phone?' · '+u.phone:''}`;
    const ordered=[...data.branches].sort((a,b)=>{
      const ai=p.execution_branch_ids.indexOf(a.id),bi=p.execution_branch_ids.indexOf(b.id);
      return (ai<0?10000+Number(a.branch_no):ai)-(bi<0?10000+Number(b.branch_no):bi);
    });
    modal(tr('إعدادات التوصيل الصباحي','Morning delivery settings'),`<form id="morning-policy-form" class="stack">
      <p>${esc(enbishan?tr('عنبيشن 1، من 09:00 إلى 12:00 بتوقيت السعودية، عدا الجمعة. يلزم فتح مناوبة المدير لاستقبال الطلبات.','Enbishan 1, 09:00–12:00 Saudi time, except Friday. An approved manager must open morning duty before orders can be placed.'):tr('حفر الباطن فقط، من 04:00 إلى 16:00 بتوقيت السعودية. الزبون يشوف فرعه المختار؛ التنفيذ داخلي على الفروع المعتمدة.','Hafar only, 04:00–16:00 Saudi time. Customers retain their selected branch; fulfillment is routed internally.'))}</p>
      <label><input type="checkbox" name="enabled" ${p.enabled?'checked':''}> ${esc(tr('تفعيل استقبال الطلبات الصباحية','Enable morning order admission'))}</label>
      <fieldset><legend>${esc(tr('فروع التنفيذ — الأول المتاح حسب الترتيب الظاهر','Execution branches — first available in displayed order'))}</legend>${choices('branches',ordered,p.execution_branch_ids,b=>`${b.branch_no} · ${b.name}`)}</fieldset>
      ${enbishan?'':`<p>${esc(tr('الطلب الذي يحتوي خلية ينفذ كاملًا بفرع 2، بشرط اختياره ضمن فروع التنفيذ. لا يتم تقسيم الطلب.','An order containing khaliya is fulfilled entirely by branch 2, which must be selected. Orders are never split.'))}</p>
      <fieldset><legend>${esc(tr('محضّرو الفروع','Branch preparers'))}</legend>${choices('preparers',data.users.filter(u=>u.role==='branch_manager'),p.preparer_user_ids,employeeLabel)}</fieldset>`}
      <fieldset><legend>${esc(tr('مديرو التوصيل الصباحي','Morning delivery managers'))}</legend>${choices('managers',managers,p.manager_user_ids,employeeLabel)}</fieldset>
      <p>${esc(enbishan?tr('اختر مديرًا مخولًا أصلًا على عنبيشن 1. الحفظ لا يوسع صلاحياته.','Select a manager who already has access to Enbishan 1. Saving does not expand staff access.'):tr('كل فرع يحتاج محضّرًا ومديرًا لديهم صلاحية عليه أصلًا. الحفظ لا يوسّع صلاحيات الموظفين. إيقاف الاستقبال لا يلغي الطلبات القائمة.','Every branch needs an already-authorized preparer and manager. Saving does not expand staff access. Disabling admission preserves existing orders.'))}</p>
      <label>${esc(tr('سبب التعديل','Reason for change'))}<textarea name="reason" required minlength="3" maxlength="500"></textarea></label>
      <p class="form-error" role="alert"></p><button type="submit">${esc(tr('حفظ إعدادات الصباح','Save morning settings'))}</button>
    </form>`,async fd=>{
      const next={enabled:fd.has('enabled'),execution_branch_ids:fd.getAll('branches'),preparer_user_ids:fd.getAll('preparers'),manager_user_ids:fd.getAll('managers')};
      if(next.enabled&&(!next.execution_branch_ids.length||!next.manager_user_ids.length||(!enbishan&&!next.preparer_user_ids.length)))throw new Error(tr('اختر فرع التنفيذ والموظفين المخولين قبل التفعيل.','Select the execution branch and authorized staff before enabling.'));
      await ctx.authorize();
      await api('/api/morning-delivery/policy',{method:'PUT',body:{policy:next,expected_version:p.version,reason:fd.get('reason')}});
      closeModal();await ctx.onSaved();
    });
  }
  window.KPSMorningDelivery={open};
})();

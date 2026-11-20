(() => {
  'use strict';
  async function open(ctx){
    const {api,tr,esc,modal,closeModal,user}=ctx;
    if(user.role!=='owner')throw new Error(tr('للمالك فقط','Owner only'));
    const {policy}=await api('/api/advertising/policy');
    modal(tr('ظهور الإعلانات للعملاء','Customer advertising visibility'),`<form class="stack">
      <p>${esc(tr('الإعلانات والشريط والعبارة الترويجية مخفية افتراضيًا. لا تظهر إلا بعد تفعيلك هنا، ثم تفعيل الإعلان وجدوله. المعاينة في الإدارة تبقى متاحة.','Banners, the announcement strip and promotional copy are hidden by default. Enable visibility here, then activate the relevant scheduled content. Admin previews remain available.'))}</p>
      <label><input name="advertising_enabled" type="checkbox" ${policy.advertising_enabled?'checked':''}> ${esc(tr('السماح بظهور الإعلانات للعملاء','Allow customer-facing advertising'))}</label>
      <p>${esc(tr('هذا الخيار مستقل عن عرض الهدية؛ إيقاف الإعلان لا يغيّر استحقاق الهدايا.','This setting is independent of gifts; disabling ads does not change gift eligibility.'))}</p>
      <label>${esc(tr('سبب التعديل','Reason for change'))}<textarea name="reason" required minlength="3" maxlength="500"></textarea></label>
      <p class="form-error" role="alert"></p><button type="submit">${esc(tr('حفظ قرار المالك','Save owner decision'))}</button></form>`,async fd=>{
        await ctx.authorize();
        await api('/api/advertising/policy',{method:'PUT',body:{advertising_enabled:fd.has('advertising_enabled'),expected_version:policy.version,reason:fd.get('reason')}});
        closeModal();await ctx.onSaved();
      });
  }
  window.KPSAdvertisingPolicy={open};
})();

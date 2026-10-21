/* Owner drafts use the real catalog; preview does not grant a gift. */
(() => {
  'use strict';
  async function open(ctx){
    const {api,tr,esc,modal,closeModal,user}=ctx;
    if(user.role!=='owner')throw new Error(tr('للمالك فقط','Owner only'));
    const [{policy},config]=await Promise.all([api('/api/gifts/policy'),api('/api/admin/config')]);
    const products=config.products||[];
    const localStart=policy.starts_at?new Date(new Date(policy.starts_at).getTime()+10800000).toISOString().slice(0,16):'';
    modal(tr('هدية أول طلب مؤهل','First qualifying order gift'),`<form id="first-gift-owner" class="stack">
      <p>${esc(tr('متاحة للعملاء القدامى والجدد، مرة واحدة فقط لكل جوال موثق في هذا البراند. الحد الأدنى المعتمد للفرع يُحسب من الأصناف المدفوعة دون رسوم التوصيل أو الهدية.','Available to existing and new customers once per verified mobile in this brand. The approved branch minimum counts paid items only, excluding delivery and the gift.'))}</p>
      <label><input type="checkbox" name="enabled" ${policy.enabled?'checked':''}>${esc(tr('تفعيل العرض','Enable offer'))}</label>
      <label>${esc(tr('موعد البداية — توقيت الرياض','Start date and time — Riyadh'))}<input type="datetime-local" name="starts_at" value="${esc(localStart)}"></label>
      ${[0,1].map(i=>`<fieldset><legend>${esc(i===0?tr('القهوة المجانية','Free coffee'):tr('الكوكيز المجاني','Free cookie'))}</legend><label>${esc(tr('الصنف المعتمد','Approved item'))}<select name="product_${i}" data-gift-slot="${i}"><option value="">${esc(tr('اختر الصنف','Choose item'))}</option>${products.map(p=>`<option value="${esc(p.id)}" ${policy.items[i]?.product_id===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><div id="gift-options-${i}"></div></fieldset>`).join('')}
      <section class="notice" id="gift-owner-preview" aria-live="polite"></section>
      <p>${esc(tr('تشغيل العرض أو إيقافه لا يغير الإعلان. إعادة تشغيله أو تغيير موعده لا يعيد الاستحقاق لمن سبق أن استفاد.','Enabling or pausing this offer does not change the advertisement. Restarting or rescheduling never resets previous claims.'))}</p>
      <label>${esc(tr('سبب الاعتماد أو التغيير','Approval or change reason'))}<textarea name="reason" required minlength="3" maxlength="500"></textarea></label>
      ${!user.employee_sms_email_retired?`<label>${esc(tr('كلمة مرور المالك','Owner password'))}<input name="step_up_password" type="password" autocomplete="current-password" required></label>`:''}
      <p class="form-error" role="alert"></p><button type="submit">${esc(tr('اعتماد إعدادات الهدية','Approve gift settings'))}</button>
    </form>`,async(fd)=>{
      const enabled=fd.has('enabled'),starts=String(fd.get('starts_at')||'');
      const items=selections();
      if(enabled&&(!starts||items.length!==2))throw new Error(tr('حدد الموعد والصنفين قبل التفعيل','Choose the start time and both items before enabling'));
      await ctx.authorize(fd.get('step_up_password')||undefined);
      await api('/api/gifts/policy',{method:'PUT',body:{policy:{enabled,starts_at:starts?`${starts}:00+03:00`:null,items},expected_version:policy.version,reason:fd.get('reason')}});
      closeModal();await ctx.onSaved();
    });
    const form=document.querySelector('#first-gift-owner');
    function drawOptions(slot,existing={}){
      const product=products.find(p=>p.id===form.elements['product_'+slot].value);
      const groups=product?.settings?.option_groups||product?.option_groups||[];
      form.querySelector('#gift-options-'+slot).innerHTML=groups.map((g,index)=>{const key=g.code||g.id,selected=existing[key]||[];return `<fieldset><legend>${esc(g.name)} (${g.min_select??(g.required===false?0:1)}–${g.max_select??1})</legend>${(g.values||[]).filter(v=>v.enabled!==false).map(v=>{const code=v.code||v.id;return `<label><input type="checkbox" data-gift-group="${index}" value="${esc(code)}" ${[].concat(selected).includes(code)?'checked':''}>${esc(v.name)}</label>`;}).join('')}</fieldset>`;}).join('');
    }
    function selections(){return [0,1].flatMap(slot=>{const product=products.find(p=>p.id===form.elements['product_'+slot].value);if(!product)return [];const options={};for(const [index,g] of (product.settings?.option_groups||product.option_groups||[]).entries()){options[g.code||g.id]=[...form.querySelectorAll(`#gift-options-${slot} [data-gift-group="${index}"]:checked`)].map(x=>x.value);}return [{product_id:product.id,options}];});}
    function preview(){const names=selections().map(s=>products.find(p=>p.id===s.product_id)?.name).filter(Boolean);const root=form.querySelector('#gift-owner-preview');root.textContent=tr('معاينة الهدية: ','Gift preview: ')+(names.length?names.join(' + ')+' · '+tr('مجانًا مع طلب مؤهل','Free with a qualifying order'):tr('اختر الصنفين المعتمدين','Choose both approved items'));}
    for(const select of form.querySelectorAll('[data-gift-slot]')){const i=Number(select.dataset.giftSlot);drawOptions(i,policy.items[i]?.options||{});select.addEventListener('change',()=>{drawOptions(i);preview();});}
    form.addEventListener('change',preview);preview();
  }
  window.KPSFirstOrderGift={open};
})();

/* Real owner-only API. Draft preview performs no writes. */
(() => {
  'use strict';
  const labels={
    manager_wait:['انتظار قبول مدير التوصيل','Manager acceptance','من إنشاء الطلب','From order creation'],
    assignment:['الإسناد وتعيين السواق','Driver assignment','من البداية المختارة','From the selected start'],
    driver_acceptance:['قبول السواق','Driver acceptance','من الإسناد','From assignment'],
    delivery:['التوصيل','Delivery','من استلام السواق إلى التسليم','From driver pickup to delivery'],
    preparer_wait:['انتظار قبول المحضّر','Preparer acceptance','من وصول الطلب للمحضّر','From notification to the preparer'],
    preparation:['التحضير','Preparation','من بداية تحضير الفرع إلى الجاهزية','From branch preparation to readiness']
  };
  const level=(seconds,limits)=>seconds>=limits.red?'red':limits.orange!==null&&seconds>=limits.orange?'orange':'normal';
  async function open(ctx){
    const {api,mutation,tr,esc,modal,closeModal,user}=ctx;
    if(user.role!=='owner')throw new Error(tr('للمالك فقط','Owner only'));
    const {policy}=await api('/api/experience/policy');
    const draft=JSON.parse(JSON.stringify(policy));delete draft.version;
    const limit=(name,value)=>`<input type="number" name="${name}" min="0.1" max="240" step="0.1" value="${value===null?'':value/60}" inputmode="decimal">`;
    modal(tr('رحلة الزبون ومعايير المراحل','Customer journey and stage targets'),`<form id="experience-policy-form" class="stack">
      <p>${esc(tr('تعديلات هذه الشاشة خاصة بالبراند الحالي. تُحفظ المعايير مع الطلب الجديد لحماية التقارير السابقة.','Changes apply to the current brand. New orders capture the targets to preserve historical reports.'))}</p>
      <label><input type="checkbox" name="customer_entry_enabled" ${draft.customer_entry_enabled?'checked':''}>${esc(tr('تفعيل رحلة الزبون: الاسم والجوال ثم الموقع والفروع المؤهلة','Enable customer journey: name and mobile, then location and eligible branches'))}</label>
      <label><input type="checkbox" name="timing_enabled" ${draft.timing_enabled?'checked':''}>${esc(tr('تفعيل معايير المراحل للطلبات الجديدة','Enable stage targets for new orders'))}</label>
      <div class="form-columns"><label>${esc(tr('إجمالي حفر الباطن — أحمر عند، بالدقائق','Hafar total — red at, minutes'))}${limit('hafar',draft.total_seconds.hafar)}</label><label>${esc(tr('خارج حفر الباطن — أحمر عند، بالدقائق','Outside Hafar total — red at, minutes'))}${limit('outside',draft.total_seconds.outside)}</label></div>
      <div class="table-scroll"><table><thead><tr><th>${esc(tr('المرحلة','Stage'))}</th><th>${esc(tr('برتقالي عند، دقيقة','Orange at, minutes'))}</th><th>${esc(tr('أحمر عند، دقيقة','Red at, minutes'))}</th></tr></thead><tbody>${Object.entries(labels).map(([key,l])=>`<tr><td>${esc(tr(l[0],l[1]))}<small>${esc(tr(l[2],l[3]))}</small></td><td>${limit(key+'_orange',draft.stages[key].orange)}</td><td>${limit(key+'_red',draft.stages[key].red)}</td></tr>`).join('')}</tbody></table></div>
      <p>${esc(tr('1.5 = دقيقة ونصف. اترك البرتقالي فارغًا إذا تبي الانتقال مباشرة للأحمر.','1.5 = one and a half minutes. Leave orange blank to go directly to red.'))}</p>
      <label>${esc(tr('بداية عداد الإسناد','Assignment clock starts'))}<select name="assignment_start"><option value="manager_accepted" ${draft.assignment_start==='manager_accepted'?'selected':''}>${esc(tr('من قبول المدير','From manager acceptance'))}</option><option value="created" ${draft.assignment_start==='created'?'selected':''}>${esc(tr('من إنشاء الطلب','From order creation'))}</option></select></label>
      <fieldset><legend>${esc(tr('اختبار المسودة قبل الحفظ','Test draft before saving'))}</legend><div class="form-columns"><select name="test_stage">${Object.entries(labels).map(([k,l])=>`<option value="${k}">${esc(tr(l[0],l[1]))}</option>`).join('')}</select><label>${esc(tr('الوقت المنقضي بالدقائق','Elapsed minutes'))}<input type="number" name="test_minutes" value="1.5" min="0" step="0.1"></label></div><output id="experience-test" aria-live="polite"></output></fieldset>
      <label>${esc(tr('سبب التغيير','Reason for change'))}<textarea name="reason" required minlength="3" maxlength="500"></textarea></label><p class="form-error" role="alert"></p><button type="submit">${esc(tr('حفظ واعتماد من المالك','Save and approve as owner'))}</button>
      ${!user.employee_sms_email_retired?`<label>${esc(tr('كلمة مرور المالك لتأكيد الاعتماد','Owner password to confirm approval'))}<input name="step_up_password" type="password" autocomplete="current-password" required></label>`:''}
    </form>`,async(fd)=>{
      const next=read(fd);
      await ctx.authorize(fd.get('step_up_password')||undefined);
      await api('/api/experience/policy',{method:'PUT',body:{policy:next,expected_version:policy.version,reason:fd.get('reason')}});
      closeModal();await ctx.onSaved();
    });
    function read(fd){
      const seconds=(name,nullable=false)=>{const value=String(fd.get(name)||'').trim();if(!value&&nullable)return null;if(!value||!Number.isFinite(Number(value)))throw new Error(tr('أدخل مدة صحيحة','Enter a valid duration'));return Math.round(Number(value)*60);};
      const next={...draft,customer_entry_enabled:fd.has('customer_entry_enabled'),timing_enabled:fd.has('timing_enabled'),assignment_start:fd.get('assignment_start'),total_seconds:{hafar:seconds('hafar'),outside:seconds('outside')},stages:{}};
      for(const key of Object.keys(labels)){const orange=seconds(key+'_orange',true),red=seconds(key+'_red');if(red<=0||red>14400||orange!==null&&(orange<=0||orange>=red))throw new Error(tr('البرتقالي يجب أن يكون أقل من الأحمر','Orange must be earlier than red'));next.stages[key]={orange,red};}
      return next;
    }
    const form=document.querySelector('#experience-policy-form'),output=form.querySelector('#experience-test');
    function preview(){try{const fd=new FormData(form),p=read(fd),color=level(Number(fd.get('test_minutes'))*60,p.stages[fd.get('test_stage')]);output.className='pill '+({normal:'good',orange:'warn',red:'bad'}[color]);output.textContent=tr({normal:'ضمن المعيار',orange:'برتقالي — تنبيه',red:'أحمر — متأخر'}[color],{normal:'Within target',orange:'Orange — warning',red:'Red — late'}[color]);}catch(error){output.className='form-error';output.textContent=error.message;}}
    form.addEventListener('input',preview);form.addEventListener('change',preview);preview();
  }
  window.KPSExperiencePolicy={open,level};
})();

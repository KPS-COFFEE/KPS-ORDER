/* Operational presentation only. Approval/late status comes from the server. */
(() => {
  'use strict';
  window.KPSOperatingReports = {mount};
  const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const amount = n => n == null ? '—' : new Intl.NumberFormat('ar-SA',{maximumFractionDigits:2}).format(n/100);
  function timingGroup(group, detailed=false){
    const duration=(seconds, late=false)=>seconds==null?'—':
      `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}${late?' · متأخر':''}`;
    const cell=(value,late=false)=>`<td${late?' class="or-timing-late"':''}>${esc(value)}</td>`;
    const clock=value=>value?new Date(value).toLocaleTimeString('en-GB',{timeZone:'Asia/Riyadh'}):'—';
    const workload=o=>{
      const evidence=o.workload||{};
      if(evidence.state==='assigned')return `${evidence.driver} · مسند ${evidence.assigned} · معه ${evidence.onboard}`;
      if(evidence.state==='unassigned')return 'غير مسند عند التأخير';
      return 'السجل غير كافٍ'+(evidence.driver?' · '+evidence.driver:'');
    };
    const label=`وصل متأخرًا ${group.late} · متأخر ولم يسلّم ${group.open_late}`;
    const details=(detailed?group.delayed_orders:[]).map(o=>`<tr>${cell(o.reference)}${cell(o.brand==='kps'?'كبز':'عنبيشن')}${cell(o.branch)}${cell(o.driver||'—')}${cell(duration(o.preparation_seconds,o.preparation_late),o.preparation_late)}${cell(o.pickup_late?'متأخر':'—',o.pickup_late)}${cell(duration(o.delivery_seconds,o.delivery_late),o.delivery_late)}${cell(duration(o.elapsed_seconds,['late','open_late'].includes(o.status)),['late','open_late'].includes(o.status))}${cell(clock(o.delay_at))}${cell(workload(o))}</tr>`).join('');
    return `<div><strong>${esc(group.city)}</strong><p>الحد بالدقائق ${esc(group.limits.map(x=>x/60).join(' / ')||'—')} · ضمن الوقت ${group.on_time} · الالتزام ${group.compliance==null?'—':group.compliance+'%'} · مفتوح ضمن الوقت ${group.open} · ملغى ${group.cancelled} · غير قابل للتقييم ${group.unavailable}</p>${details?`<details data-or-timing-city="${esc(group.city)}"><summary class="or-timing-late">${esc(label)} · تفاصيل المراحل (${group.delayed_orders.length})</summary><div class="or-timing-table"><table><thead><tr><th>الطلب</th><th>البراند</th><th>الفرع</th><th>السائق</th><th>مدة التجهيز</th><th>موعد الاستلام</th><th>مدة التوصيل</th><th>إجمالي المدة</th><th>وقت التأخير</th><th>حمل السائق وقت التأخير</th></tr></thead><tbody>${details}</tbody></table></div></details>`:`<p>${esc(label)}</p>`}</div>`;
  }
  async function mount(host,{api,morning=false,initialDay='',managerShiftId='',allowDay=true,lockContext=false,initialScope='shift',brand='',daily=false}) {
    let report,day=initialDay,scope=initialScope==='day'&&allowDay&&!morning?'day':'shift',generation=0,rolling=daily&&!initialDay?'previous':null;
    host.classList.add('operating-report');
    const query=(exporting=false)=>new URLSearchParams({
      ...(rolling&&!exporting?(rolling==='previous'?{previous_day:'true'}:{}):day?{day}:{}),
      scope,...managerShiftId?{manager_shift_id:managerShiftId}:{}
    }).toString();
    const error=text=>{const el=host.querySelector('[role="alert"]');if(el)el.textContent=text;};
    async function load(){
      const current=++generation;
      try{const data=await api('/api/operating-reports?'+query());if(current!==generation||!host.isConnected)return;report=data;day=data.day;render();}
      catch(e){if(current===generation){host.innerHTML='<p role="alert">'+esc(e.message)+'</p><button type="button" data-or-retry>إعادة المحاولة</button>';}}
    }
    function render(){
      const a=report.accounting;
      const brands = r => Object.entries(r.brands).map(([brand,b])=>`<span class="or-brand or-${brand}"><span><i></i>${brand==='kps'?'KPS':'ENBISHAN'}</span><span>${b.count} طلب · ${amount(b.amount)} ر.س</span></span>`).join('');
      const cities = report.rows.reduce((groups,r)=>{const key=r.city||'غير مسجل';(groups[key]??=[]).push(r);return groups;},Object.create(null));
      const cityTotal=(items,k)=>items.every(r=>r[k]!=null)?items.reduce((s,r)=>s+r[k],0):null;
      const dateLabel=new Date(day+'T12:00:00+03:00').toLocaleDateString('ar-SA',{calendar:'gregory',weekday:'long',year:'numeric',month:'long',day:'numeric'});
      host.innerHTML=`<header class="or-heading"><div class="or-logos"><img src="/assets/operating-report/kps.jpeg" alt="كبز"><img src="/assets/operating-report/enbishan.jpeg" alt="عنبيشن"></div><div><h2>التقرير التشغيلي</h2><p>${esc(dateLabel)}</p></div><button type="button" data-or-retry class="or-refresh" aria-label="تحديث">تحديث</button></header>
      <div class="or-toolbar"><label>اليوم التشغيلي<input type="date" data-or-day value="${esc(day)}" ${lockContext?'disabled':''}></label><div class="or-scopes"><button type="button" data-or-scope="shift" aria-pressed="${scope==='shift'}">شفتي</button>${allowDay&&report.can_day?`<button type="button" data-or-scope="day" aria-pressed="${scope==='day'}">اليوم التشغيلي</button>`:''}</div>${daily?`<button type="button" data-or-rolling="previous" aria-pressed="${rolling==='previous'}">آخر يوم تشغيلي</button><button type="button" data-or-rolling="current" aria-pressed="${rolling==='current'}">اليوم الحالي</button>`:''}</div>
      <p role="alert" aria-live="polite"></p><p class="or-context">${scope==='day'?'الصباحي والمسائي':'طلبات مناوبتك'} · ${report.operational_complete?'مكتمل تشغيليًا':'التقفيلات التشغيلية لم تكتمل'}</p>
      <div class="or-summary"><div><strong>${report.count}</strong><span>طلب</span></div><div><strong>${report.rows.length}</strong><span>عدد السائقين</span></div><div><strong>${amount(report.net)} <small>ر.س</small></strong><span>صافي الطلبات</span></div></div>
      <section class="or-accounting ${a.approved?'or-approved':''}" aria-label="متابعة المراجعة المحاسبية"><div class="or-progress-heading"><h3>المراجعة المحاسبية</h3><strong>${a.percent}٪</strong><span class="or-review-state ${a.overdue?'or-late':''}">${a.approved?'معتمد':a.overdue?'متأخر':'قيد المراجعة'}</span></div><progress max="100" value="${a.percent}" aria-label="اكتمال المراجعة المحاسبية"></progress><p>${a.completed_steps} من ${a.total_steps} خطوة مكتملة${a.overdue?` · ${a.overdue} تقفيلة تحتاج متابعة`:''}</p>${a.late?`<p>سجل التأخير: ${a.late} تقفيلة أُنجزت بعد الموعد</p>`:''}${a.approved?a.approvals.map(v=>`<div class="or-stamp"><strong>✓ معتمد محاسبيًا</strong><span>المعتمد: ${esc(v.approved_name)}</span><span>${esc(new Date(v.created_at).toLocaleString('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}))}</span><small>مرجع الاعتماد: ${esc(v.id)}</small></div>`).join(''):'<p>بانتظار اكتمال المراجعة واعتماد المحاسب</p>'}</section>
      <section class="or-drivers"><h3>السائقون</h3>${report.rows.map(r=>`<details><summary><span class="or-driver-name"><strong>${esc(r.name)}</strong><small class="or-period ${r.period==='morning'?'or-morning':''}">${r.period==='morning'?'صباحي':'مسائي'}</small></span><span class="or-driver-brands">${brands(r)}</span><span class="or-driver-net"><small>الصافي</small><strong>${amount(r.net)} <small>ر.س</small></strong></span><span class="or-status ${r.closed?'or-good':''}">${r.closed?'تقفيلة مكتملة':'تقفيلة معلّقة'} ‹</span></summary><div class="or-detail"><p>${esc(r.city||'غير مسجل')}</p><dl class="or-payment-grid"><div><dt>شبكة</dt><dd>${amount(r.network)}</dd></div><div><dt>كاش</dt><dd>${amount(r.cash)}</dd></div><div><dt>العجز</dt><dd>${amount(r.shortage)}</dd></div><div><dt>الفائض</dt><dd>${amount(r.surplus)}</dd></div><div><dt>المطابقة</dt><dd>${esc(r.status||'بانتظار البيانات')}</dd></div></dl><table><thead><tr><th>البراند</th><th>الطلبات</th><th>القيمة</th></tr></thead><tbody>${Object.entries(r.brands).map(([brand,b])=>`<tr><td>${brand==='kps'?'كبز':'عنبيشن'}</td><td>${b.count}</td><td>${amount(b.amount)} ر.س</td></tr>`).join('')}</tbody></table></div></details>`).join('')||'<p>لا توجد طلبات أو تقفيلات في هذا النطاق.</p>'}</section>
      <section class="or-city-summary"><h3>ملخص المدن / المحافظات</h3>${Object.entries(cities).map(([city,items])=>`<div><strong>${esc(city)}</strong><p>قيمة الطلبات ${amount(items.reduce((s,r)=>s+r.net,0))} ر.س · شبكة ${amount(cityTotal(items,'network'))} · كاش ${amount(cityTotal(items,'cash'))} · العجز ${amount(cityTotal(items,'shortage'))} · الفائض ${amount(cityTotal(items,'surplus'))}</p></div>`).join('')}<p>المطابقة: قيمة الطلبات = شبكة + كاش. النقص عجز والزيادة فائض.</p></section>
      <section class="or-city-summary"><h3>الالتزام بوقت التوصيل</h3>${(report.delivery_timing||[]).map(group=>timingGroup(group)).join('')||'<p>لا توجد طلبات توصيل مرتبطة بهذه المناوبة.</p>'}<p>نسبة الالتزام من الطلبات المسلّمة القابلة للتقييم.</p></section>
      ${report.cases.length?`<section class="or-cases"><h3>حالات التشغيل</h3>${report.cases.map(v=>`<details><summary><span><strong>${esc(v.kind)}</strong><small>${esc(v.driver)} · ${v.brand==='kps'?'كبز':'عنبيشن'}</small></span><span>${amount(v.amount)} ر.س ‹</span></summary><div class="or-case-detail"><p>${esc(v.reason)}</p><p>${esc(v.approval)} · المرجع: ${esc(v.reference)}</p></div></details>`).join('')}</section>`:''}
      ${daily&&report.can_view_delay_details?`<button type="button" data-or-delay-report>تقرير تفاصيل التأخير اليومي</button>`:''}
      <footer class="or-actions"><button type="button" data-or-export="share">حفظ أو مشاركة PDF</button><button type="button" data-or-export="print">طباعة</button></footer><p class="or-note">تقرير تشغيلي. المراجعة والختم يعكسان سجل المحاسبة. قيم الضيافة والتعويضات مرجعية ولا تخصم مرة أخرى.</p>`;
    }
    let delayPanel;
    async function showDelayReport(){
      if(delayPanel){delayPanel.scrollIntoView({block:'start'});return;}
      delayPanel=document.createElement('section');
      delayPanel.className='operating-report';
      delayPanel.innerHTML='<h2>تقرير تفاصيل التأخير اليومي</h2><label>اليوم التشغيلي <input type="date"></label><div aria-live="polite"></div>';
      host.after(delayPanel);
      const input=delayPanel.querySelector('input'),output=delayPanel.querySelector('[aria-live]');
      if(!rolling)input.value=day;
      let revision=0;
      async function loadDetails(){
        const request=++revision;
        output.textContent='جارٍ تحميل التقرير…';
        const params=new URLSearchParams({delay_details:'true'});
        if(input.value)params.set('day',input.value);
        else if(rolling!=='current')params.set('previous_day','true');
        try{
          const data=await api('/api/operating-reports?'+params);
          if(request!==revision||!delayPanel.isConnected)return;
          input.value=data.day;
          output.innerHTML=data.delivery_timing.map(group=>timingGroup(group,true)).join('')||'<p>لا توجد طلبات توصيل في هذا اليوم.</p>';
        }catch(e){if(request===revision)output.textContent=e.message;}
      }
      input.addEventListener('change',loadDetails);
      delayPanel.scrollIntoView({block:'start'});
      await loadDetails();
    }
    host.addEventListener('change',e=>{if(e.target.matches('[data-or-day]')){day=e.target.value;rolling=null;scope='shift';void load();}});
    host.addEventListener('click',async e=>{
      const button=e.target.closest('button');if(!button)return;
      if(button.hasAttribute('data-or-scope')){if(button.dataset.orScope==='day'&&(!allowDay||!report?.can_day))return;scope=button.dataset.orScope;void load();return;}
      if(button.dataset.orRolling){rolling=button.dataset.orRolling;scope='shift';void load();return;}
      if(button.hasAttribute('data-or-retry')){void load();return;}
      if(button.hasAttribute('data-or-delay-report')){await showDelayReport();return;}
      if(!button.dataset.orExport||!report)return;
      const printing=button.dataset.orExport==='print';
      const exported=report,exportQuery=query(true);
      if(printing){
        const params=new URLSearchParams(exportQuery);
        if(brand)params.set('operations_brand',brand);
        if(morning)params.set('delivery_period','morning');
        window.open('/api/operating-reports/export/print?'+params,'_blank','noopener');
        return;
      }
      button.disabled=true;error('');
      try{
        const headers={'X-KPS-Context':'work'};if(brand)headers['X-Operations-Brand']=brand;if(morning)headers['X-Delivery-Period']='morning';
        const response=await fetch('/api/operating-reports/export/pdf?'+exportQuery,{credentials:'same-origin',cache:'no-store',headers});
        if(!response.ok){const data=await response.json();throw new Error(data.detail?.message||'تعذر تجهيز التقرير');}
        const blob=await response.blob(),file=new File([blob],exported.filename,{type:'application/pdf'});
        if(navigator.canShare?.({files:[file]})){await navigator.share({files:[file],title:exported.filename.replace(/\.pdf$/,'')});return;}
        const url=URL.createObjectURL(blob);
        const a=document.createElement('a');a.href=url;a.download=exported.filename;document.body.append(a);a.click();a.remove();
        setTimeout(()=>URL.revokeObjectURL(url),60000);
      }catch(ex){if(ex.name!=='AbortError')error(ex.message);}finally{button.disabled=false;}
    });
    await load();
    const refresh=setInterval(()=>{if(!host.isConnected){clearInterval(refresh);return;}if(!document.hidden&&!host.querySelector('button:disabled'))void load();},30000);
  }
})();

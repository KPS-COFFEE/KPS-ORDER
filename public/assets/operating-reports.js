/* Operational presentation only. Approval/late status comes from the server. */
(() => {
  'use strict';
  window.KPSOperatingReports = {mount};
  const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const amount = n => n == null ? 'غير موثق' : new Intl.NumberFormat('ar-SA',{maximumFractionDigits:2}).format(n/100);
  async function mount(host,{api,morning=false}) {
    let report,day='',scope='shift',generation=0;
    host.classList.add('operating-report');
    const query=()=>new URLSearchParams({...day?{day}:{},scope}).toString();
    const error=text=>{const el=host.querySelector('[role="alert"]');if(el)el.textContent=text;};
    async function load(){
      const current=++generation;
      try{const data=await api('/api/operating-reports?'+query());if(current!==generation||!host.isConnected)return;report=data;day=data.day;render();}
      catch(e){if(current===generation){host.innerHTML='<p role="alert">'+esc(e.message)+'</p><button type="button" data-or-retry>إعادة المحاولة</button>';}}
    }
    function render(){
      const a=report.accounting;
      const brands = r => Object.entries(r.brands).map(([brand,b])=>`<span class="or-brand or-${brand}"><span><i></i>${brand==='kps'?'KPS':'ENBISHAN'}</span><span>${b.count} طلب · ${amount(b.amount)} ر.س</span></span>`).join('');
      const dateLabel=new Date(day+'T12:00:00+03:00').toLocaleDateString('ar-SA',{calendar:'gregory',weekday:'long',year:'numeric',month:'long',day:'numeric'});
      host.innerHTML=`<header class="or-heading"><div class="or-logos"><img src="/assets/operating-report/kps.jpeg" alt="كبز"><img src="/assets/operating-report/enbishan.jpeg" alt="عنبيشن"></div><div><h2>التقرير التشغيلي</h2><p>${esc(dateLabel)}</p></div><button type="button" data-or-retry class="or-refresh" aria-label="تحديث">تحديث</button></header>
      <div class="or-toolbar"><label>اليوم التشغيلي<input type="date" data-or-day value="${esc(day)}"></label><div class="or-scopes"><button type="button" data-or-scope="shift" aria-pressed="${scope==='shift'}">شفتي</button>${report.can_day?`<button type="button" data-or-scope="day" aria-pressed="${scope==='day'}">اليوم التشغيلي</button>`:''}</div></div>
      <p role="alert" aria-live="polite"></p><p class="or-context">${scope==='day'?'الصباحي والمسائي':'طلبات مناوبتك'} · ${report.operational_complete?'مكتمل تشغيليًا':'التقفيلات التشغيلية لم تكتمل'}</p>
      <div class="or-summary"><div><strong>${report.count}</strong><span>طلب</span></div><div><strong>${report.rows.length}</strong><span>عدد السائقين</span></div><div><strong>${amount(report.net)} <small>ر.س</small></strong><span>صافي الطلبات</span></div></div>
      <section class="or-accounting ${a.approved?'or-approved':''}" aria-label="متابعة المراجعة المحاسبية"><div class="or-progress-heading"><h3>المراجعة المحاسبية</h3><strong>${a.percent}٪</strong><span class="or-review-state ${a.overdue?'or-late':''}">${a.approved?'معتمد':a.overdue?'متأخر':'قيد المراجعة'}</span></div><progress max="100" value="${a.percent}" aria-label="اكتمال المراجعة المحاسبية"></progress><p>${a.completed_steps} من ${a.total_steps} خطوة مكتملة${a.overdue?` · ${a.overdue} تقفيلة تحتاج متابعة`:''}</p>${a.late?`<p>سجل التأخير: ${a.late} تقفيلة أُنجزت بعد الموعد</p>`:''}${a.approved?a.approvals.map(v=>`<div class="or-stamp"><strong>✓ معتمد محاسبيًا</strong><span>المعتمد: ${esc(v.approved_name)}</span><span>${esc(new Date(v.created_at).toLocaleString('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}))}</span><small>مرجع الاعتماد: ${esc(v.id)}</small></div>`).join(''):'<p>بانتظار اكتمال المراجعة واعتماد المحاسب</p>'}</section>
      <section class="or-drivers"><h3>السائقون</h3>${report.rows.map(r=>`<details><summary><span class="or-driver-name"><strong>${esc(r.name)}</strong><small class="or-period ${r.period==='morning'?'or-morning':''}">${r.period==='morning'?'صباحي':'مسائي'}</small></span><span class="or-driver-brands">${brands(r)}</span><span class="or-driver-net"><small>الصافي</small><strong>${amount(r.net)} <small>ر.س</small></strong></span><span class="or-status ${r.closed?'or-good':''}">${r.closed?'تقفيلة مكتملة':'تقفيلة معلّقة'} ‹</span></summary><div class="or-detail"><table><thead><tr><th>البراند</th><th>الطلبات</th><th>القيمة</th></tr></thead><tbody>${Object.entries(r.brands).map(([brand,b])=>`<tr><td>${brand==='kps'?'كبز':'عنبيشن'}</td><td>${b.count}</td><td>${amount(b.amount)} ر.س</td></tr>`).join('')}</tbody></table></div></details>`).join('')||'<p>لا توجد طلبات أو تقفيلات في هذا النطاق.</p>'}</section>
      ${report.cases.length?`<section class="or-cases"><h3>حالات التشغيل</h3>${report.cases.map(v=>`<details><summary><span><strong>${esc(v.kind)}</strong><small>${esc(v.driver)} · ${v.brand==='kps'?'كبز':'عنبيشن'}</small></span><span>${amount(v.amount)} ر.س ‹</span></summary><div class="or-case-detail"><p>${esc(v.reason)}</p><p>${esc(v.approval)} · المرجع: ${esc(v.reference)}</p></div></details>`).join('')}</section>`:''}
      <footer class="or-actions"><button type="button" data-or-export="share">حفظ أو مشاركة PDF</button><button type="button" data-or-export="print">طباعة</button></footer><p class="or-note">تقرير تشغيلي. المراجعة والختم يعكسان سجل المحاسبة. قيم الضيافة والتعويضات مرجعية ولا تخصم مرة أخرى.</p>`;
    }
    host.addEventListener('change',e=>{if(e.target.matches('[data-or-day]')){day=e.target.value;scope='shift';void load();}});
    host.addEventListener('click',async e=>{
      const button=e.target.closest('button');if(!button)return;
      if(button.hasAttribute('data-or-scope')){scope=button.dataset.orScope;void load();return;}
      if(button.hasAttribute('data-or-retry')){void load();return;}
      if(!button.dataset.orExport||!report)return;
      const printing=button.dataset.orExport==='print';
      const exported=report,exportQuery=query();
      const tab=printing?window.open('about:blank','_blank'):null;
      button.disabled=true;error('');
      try{
        const headers={'X-KPS-Context':'work'};if(morning)headers['X-Delivery-Period']='morning';
        const response=await fetch('/api/operating-reports/export/pdf?'+exportQuery,{credentials:'same-origin',cache:'no-store',headers});
        if(!response.ok){const data=await response.json();throw new Error(data.detail?.message||'تعذر تجهيز التقرير');}
        const blob=await response.blob(),file=new File([blob],exported.filename,{type:'application/pdf'});
        if(!printing&&navigator.canShare?.({files:[file]})){await navigator.share({files:[file],title:exported.filename.replace(/\.pdf$/,'')});return;}
        const url=URL.createObjectURL(blob);
        if(tab){tab.location.href=url;}else{const a=document.createElement('a');a.href=url;a.download=exported.filename;document.body.append(a);a.click();a.remove();}
        setTimeout(()=>URL.revokeObjectURL(url),60000);
      }catch(ex){if(tab)tab.close();if(ex.name!=='AbortError')error(ex.message);}finally{button.disabled=false;}
    });
    await load();
    const refresh=setInterval(()=>{if(!host.isConnected){clearInterval(refresh);return;}if(!document.hidden&&!host.querySelector('button:disabled'))void load();},30000);
  }
})();

'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const form = $('report-filters');
  let en = (localStorage.getItem('kps_ops_lang') || localStorage.getItem('kps-ops-language')) === 'en';
  let brand = new URLSearchParams(location.search).get('operations_brand');
  let data = null, applied = null, offset = 0, requestId = 0, controller = null;
  let stopped=false, accessAllowed=false, accessController=null, accessPromise=null, accessGeneration=0, actor=null, accessFingerprint=null, reportPending=true;
  const headers = {'X-KPS-Context': 'work'};
  if (brand) headers['X-Operations-Brand'] = brand;
  const tr = (ar, english) => en ? english : ar;
  const node = (tag, text, cls) => {const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n;};
  const number = n => new Intl.NumberFormat(en ? 'en-SA' : 'ar-SA').format(n || 0);
  const money = n => new Intl.NumberFormat(en ? 'en-SA' : 'ar-SA', {style:'currency', currency:'SAR', maximumFractionDigits:2}).format(Number(n || 0) / 100);
  const day = v => new Intl.DateTimeFormat(en ? 'en-GB' : 'ar-SA', {calendar:'gregory', dateStyle:'medium', timeZone:'Asia/Riyadh'}).format(new Date(v.length === 10 ? v + 'T12:00:00+03:00' : v));
  const today = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Riyadh', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
  const iso = d => d.toISOString().slice(0,10);
  function preset(kind) {
    const end = today(), d = new Date(end + 'T12:00:00Z');
    let start = end, finish = end;
    if (kind === 'month') start = end.slice(0,8) + '01';
    if (kind === 'previous') {d.setUTCDate(0); finish = iso(d); start = finish.slice(0,8)+'01';}
    if (kind === 'two') {const date=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-2);const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(Math.min(date,last));start = iso(d);}
    form.elements.start.value = start; form.elements.end.value = finish;
    document.querySelectorAll('[data-preset]').forEach(b => {b.classList.toggle('active', b.dataset.preset === kind); b.setAttribute('aria-pressed', String(b.dataset.preset === kind));});
    $('preset-note').textContent = kind === 'two' ? tr('شهران متحركان حتى اليوم؛ التواريخ ظاهرة أعلاه.', 'Rolling two months through today; exact dates shown above.') : '';
  }
  function localize() {
    document.documentElement.lang = en ? 'en' : 'ar'; document.documentElement.dir = en ? 'ltr' : 'rtl';
    document.title = tr('تقرير العملاء', 'Customer report');
    document.querySelectorAll('[data-ar]').forEach(n => n.textContent = en ? n.dataset.en : n.dataset.ar);
    $('language').textContent = en ? 'العربية' : 'English';
    form.elements.q.placeholder = tr('بحث عن عميل', 'Find a customer');
    $('summary').setAttribute('aria-label', tr('ملخص الفترة', 'Period summary'));
    if (data) render();
  }
  function badge(value, type) {
    const labels = {new:tr('جديد خلال الفترة','New in period'), repeat:tr('متكرر خلال الفترة','Returning in period'), inactive:tr('بدون طلب خلال الفترة','No orders in period'),
      regular:tr('منتظم','Regular'), late:tr('متأخر عن عادته','Overdue'), lapsed:tr('يُحتمل انقطاعه','Possibly lapsed'),
      insufficient:tr('بيانات غير كافية','Insufficient history'), irregular:tr('عادة غير منتظمة','Irregular pattern'), no_repeat:tr('طلب مرة ولم يكرر','One purchase only')};
    return node('span', labels[value] || value, 'badge ' + (type || value));
  }
  function table(headings, rows) {
    const t = node('table'), head = node('thead'), h = node('tr'), body = node('tbody');
    headings.forEach(label => {const th = node('th',label); th.scope='col'; h.append(th);}); head.append(h); t.append(head);
    rows.forEach(values => {const row = node('tr'); values.forEach((value,i) => {const cell = node('td'); cell.dataset.label=headings[i]; cell.append(value instanceof Node ? value : document.createTextNode(value ?? '—')); row.append(cell);}); body.append(row);});
    t.append(body); return t;
  }
  function metric(label, value, note, highlight) {
    const a = node('article',null,'metric'+(highlight?' highlight':''));
    a.append(node('span',label),node('strong',value)); if (note) a.append(node('small',note)); return a;
  }
  function render() {
    const s=data.summary;
    $('brand-label').textContent=data.brand_id === 'enbishan' ? 'ENBISHAN' : 'KPS COFFEE';
    document.body.dataset.brand=data.brand_id;
    $('applied-period').textContent=day(data.start)+' — '+day(data.end);
    const cohorts={all:tr('جميع العملاء','All customers'),new:tr('الجدد خلال الفترة','New in period'),repeat:tr('المتكررون خلال الفترة','Returning in period'),inactive:tr('بدون طلب خلال الفترة','No orders in period')};
    $('applied-filters').replaceChildren(document.createTextNode(cohorts[applied.segment]+' · '));
    if(applied.activity!=='all')$('applied-filters').append(badge(applied.activity),document.createTextNode(' · '));
    $('applied-filters').append(document.createTextNode(applied.q ? tr('البحث: ','Search: ')+applied.q : tr('جميع الأسماء','All names')));
    $('summary').replaceChildren(
      metric(tr('قيمة المشتريات','Purchase value'),money(s.value_halalas),tr('صافي التحصيل بعد الاستردادات','Net collections after refunds'),true),
      metric(tr('الطلبات المكتملة','Completed orders'),number(s.orders),tr('خلال الفترة المحددة','Within selected period')),
      metric(tr('العملاء المشترون','Purchasing customers'),number(s.purchasing_customers),tr('كل عميل يُحسب مرة خلال الفترة','Each buyer counted once per period')),
      metric(tr('متوسط قيمة الطلب','Average order value'),money(s.orders ? s.value_halalas/s.orders : 0),tr('القيمة ÷ عدد الطلبات','Value ÷ order count')),
      metric(tr('عملاء جدد','New customers'),number(s.new_customers),tr('أول شراء لهم خلال الفترة','First purchase in this period')),
      metric(tr('عملاء متكررون','Returning customers'),number(s.repeat_customers),tr('اشتروا قبل الفترة ورجعوا خلالها','Purchased before and during the period')),
      metric(tr('متأخرون عن عادتهم','Overdue customers'),number(s.late_customers),tr('حالة متابعة حسب العادة','Indicative follow-up status')),
      metric(tr('يُحتمل انقطاعهم','Possibly lapsed'),number(s.lapsed_customers),tr('تحتاج متابعة؛ لا تثبت سبب الغياب','Needs follow-up; cause is unknown'))
    );
    const sorts={orders:tr('ترتيب العملاء حسب عدد الطلبات','Customers ranked by order count'),value:tr('ترتيب العملاء حسب قيمة المشتريات','Customers ranked by purchase value'),absence:tr('ترتيب العملاء حسب مدة الغياب','Customers ranked by absence')};
    $('ranking-title').textContent=sorts[applied.sort];
    $('ranking-count').textContent=number(s.known_customers)+' '+tr('عميل مطابق','matching customers')+' · '+number(s.inactive_customers)+' '+tr('بدون طلب في الفترة','without orders in period');
    const headings=[tr('الترتيب','Rank'),tr('العميل والجوال','Customer and mobile'),tr('الطلبات','Orders'),tr('قيمة المشتريات','Purchase value'),tr('أول طلب','First purchase'),tr('آخر طلب','Last purchase'),tr('مدة الغياب','Days absent'),tr('تصنيف الفترة','Period cohort'),tr('المتابعة','Follow-up')];
    const rows=data.customers.map(r=>{
      const profile=node('div'); profile.append(node('strong',r.display_name||tr('عميل','Customer')),node('bdi',r.phone,'phone'));
      const activity=node('div');activity.append(badge(r.activity));
      if(r.usual_gap_days && !['no_repeat','insufficient','irregular'].includes(r.activity)) activity.append(node('small',tr('عادته: كل ','Usually every ')+number(Math.round(r.usual_gap_days*10)/10)+tr(' يوم',' days'),'muted'));
      return [node('span',number(r.rank),'rank'+(r.rank<=3?' top':'')),profile,node('strong',number(r.orders),'number'),node('strong',money(r.value_halalas),'number'),day(r.first_order_at),day(r.latest_order_at),number(r.days_absent)+' '+tr('يوم','days'),badge(r.segment),activity];
    });
    $('ranking').replaceChildren(rows.length?table(headings,rows):node('p',tr('لا يوجد عملاء مطابقون لهذه الفترة والفلاتر. جرّب فترة أوسع أو أعد ضبط الفلاتر.','No matching customers. Try a wider period or reset the filters.'),'empty'));
    $('previous').disabled=offset===0; $('next').disabled=data.next_offset===null;
    $('page-label').textContent=rows.length?number(offset+1)+'–'+number(offset+rows.length)+' '+tr('من','of')+' '+number(s.known_customers):tr('لا توجد نتائج','No results');
    $('daily-count').textContent=number(data.daily.length)+' '+tr('يوم','days');
    $('daily').replaceChildren(table([tr('التاريخ','Date'),tr('العملاء','Customers'),tr('جدد','New'),tr('متكررون','Returning'),tr('الطلبات','Orders'),tr('منها طلبات متكررة','Of which repeat orders'),tr('قيمة المشتريات','Purchase value')],[...data.daily].reverse().map(d=>[day(d.day),number(d.purchasing_customers),number(d.new_customers),number(d.repeat_customers),number(d.orders),number(d.repeat_orders),money(d.value_halalas)])));
    const p=data.operating_period;
    $('coverage-warning').hidden=Boolean(p?.configured&&p?.period_valid);
    $('coverage-warning').textContent=tr('فترة التشغيل المعتمدة غير مكتملة أو غير صالحة. راجع الإعداد قبل الاعتماد على تصنيف الجديد والمتكرر.','The certified operating period is missing or invalid. Review configuration before relying on new/returning cohorts.');
    const lines=[
      tr('الهوية: العملاء ذوو الجوال الموثق وروابط الحساب المثبتة. لا تُدمج الحسابات لمجرد كتابة رقم متشابه. طلبات الضيف غير المرتبطة لا تدخل هذا التحليل.','Identity: verified mobile accounts and proven account links. Typed matching numbers never merge identities. Unlinked guest orders are outside this analysis.'),
      tr('المشتريات: طلبات مكتملة ذات تحصيل فعلي موجب بعد الاستردادات. تُستبعد الطلبات الملغاة والتجريبية والمستردة بالكامل. القيمة تشمل الرسوم والضريبة ضمن إجمالي الطلب المحصل.','Purchases: completed orders with positive actual net collections. Cancelled, test and fully refunded orders are excluded. Value includes collected fees and tax within the order total.'),
      tr('الجديد والمتكرر يُحسبان من سجل الشراء المعروف منذ بداية التشغيل المعتمدة؛ التسجيل وحده لا يُعد شراء. مجموع العملاء اليومي لا يساوي عدد العملاء الفريد خلال الشهر.','New/returning uses known purchase history since the certified operating start; registration alone is not a purchase. Daily customer totals cannot be summed into monthly unique customers.'),
      tr('الغياب يُحسب حتى نهاية الفترة، وليس حتى اليوم إذا اخترت فترة قديمة. يلزم شراء في خمسة أيام مختلفة لتقدير العادة. نستخدم وسيط آخر ١٢ فاصلًا بين أيام الشراء.','Absence is measured through period end, not today for historical periods. A pattern requires five distinct purchase days. Cadence uses the median of up to 12 recent gaps.'),
      tr('متأخر: مضى ضعف عادته وبحد أدنى ٧ أيام. يُحتمل انقطاعه: ثلاثة أضعاف عادته وبحد أدنى ١٤ يومًا. التفاوت الكبير في العادة يُظهر «عادة غير منتظمة». هذه قواعد أولية قابلة للمراجعة بعد التحقق من النتائج.','Overdue: at least twice usual cadence and 7 days. Possibly lapsed: at least three times cadence and 14 days. Widely varying gaps show an irregular pattern. These initial rules should be reviewed against observed results.'),
      tr('التقرير للبراند المحدد فقط. الأرقام تُحدّث من أدلة الإكمال والاسترداد الحالية، وليست تقفيلة محاسبية مجمدة. لا تُستنتج موافقة تسويقية من التسجيل.','This report covers the selected brand only. Current completion and refund evidence updates these figures; this is not a frozen accounting close. Registration does not imply marketing consent.')
    ];
    $('definitions').replaceChildren(...lines.map(x=>node('li',x)));
    $('generated').textContent=tr('آخر تحديث: ','Updated: ')+new Intl.DateTimeFormat(en?'en-GB':'ar-SA',{calendar:'gregory',dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Riyadh'}).format(new Date(data.generated_at))+' · '+tr('الطباعة تشمل الصفحة المعروضة من ترتيب العملاء وجميع أيام الفترة.','Print includes the displayed ranking page and all days in the period.');
  }
  function maskAccess() { document.documentElement.setAttribute('data-customer-access-pending',''); }
  function clearReport() {
    data=null;applied=null;offset=0;$('report-content').hidden=true;
    for(const id of ['ranking','daily','summary','definitions','applied-period','applied-filters','ranking-count','page-label','daily-count','generated','coverage-warning'])$(id).replaceChildren();
    $('print').disabled=true;$('previous').disabled=true;$('next').disabled=true;
  }
  function revokeAccess(message) {
    if(stopped)return;
    stopped=true;accessAllowed=false;requestId++;accessGeneration++;
    controller?.abort();accessController?.abort();controller=accessController=null;
    clearInterval(accessTimer);clearReport();form.reset();form.hidden=true;
    actor=accessFingerprint=null;
    $('report-status').className='error';
    $('report-status').textContent=message||tr('انتهى تصريح الدخول. سجّل الدخول بحساب مالك نشط.','Access ended. Sign in with an active owner account.');
    $('report-signin').hidden=false;
    document.documentElement.removeAttribute('data-customer-access-pending');
  }
  async function checkAccess() {
    if(stopped||document.hidden||!navigator.onLine)return false;
    if(accessPromise)return accessPromise;
    const generation=++accessGeneration,proofController=new AbortController();accessController=proofController;
    const timeout=setTimeout(()=>proofController.abort(),5000);
    accessPromise=(async()=>{
      try {
        const response=await fetch('/api/session',{headers,cache:'no-store',credentials:'same-origin',signal:proofController.signal});
        if(stopped||generation!==accessGeneration||proofController.signal.aborted)return false;
        if([401,403].includes(response.status)){revokeAccess();return false;}
        if(!response.ok)throw new Error(tr('تعذر التحقق من صلاحية الوصول. أعد الاتصال للمحاولة.','Unable to verify access. Reconnect to retry.'));
        const session=await response.json();
        if(stopped||generation!==accessGeneration||proofController.signal.aborted)return false;
        const user=session.user;
        if(!user?.id||user.role!=='owner'||user.active===false||!session.csrf_token||session.reauthentication_required||
           (actor&&actor!==user.id)||(brand&&brand!==user.brand_id)){
          revokeAccess(tr('هذا التقرير للمالك. سجّل دخولك بحساب مالك نشط.','An active owner account is required for this report.'));return false;
        }
        const fingerprint=JSON.stringify([user.id,user.brand_id,user.home_brand_id,user.branch_ids,user.effective_permissions]);
        const scopeChanged=accessFingerprint&&accessFingerprint!==fingerprint;
        if(scopeChanged){requestId++;controller?.abort();controller=null;clearReport();}
        actor=user.id;accessFingerprint=fingerprint;accessAllowed=true;
        brand=user.brand_id;headers['X-Operations-Brand']=brand;
        $('back-link').href='/ops?operations_brand='+encodeURIComponent(brand);
        $('crm-link').href='/customer-crm?operations_brand='+encodeURIComponent(brand);
        $('report-signin').href='/ops?operations_brand='+encodeURIComponent(brand);
        form.hidden=false;
        document.documentElement.removeAttribute('data-customer-access-pending');
        if(scopeChanged)void load();
        return true;
      } catch(error) {
        if(!stopped&&generation===accessGeneration){
          accessAllowed=false;maskAccess();$('report-status').className='error';
          $('report-status').textContent=tr('تعذر التحقق من صلاحية الوصول. أعد الاتصال للمحاولة.','Unable to verify access. Reconnect to retry.');
        }
        return false;
      } finally {clearTimeout(timeout);if(accessController===proofController){accessController=null;accessPromise=null;}}
    })();
    return accessPromise;
  }
  function pauseAccess() {
    if(stopped)return;
    maskAccess();accessAllowed=false;accessGeneration++;accessController?.abort();accessController=null;accessPromise=null;
    if(controller)reportPending=true;requestId++;controller?.abort();controller=null;
  }
  async function resumeAccess() {
    if(stopped)return;
    if(document.hidden||!navigator.onLine){pauseAccess();return;}
    maskAccess();if(await checkAccess())if(!data&&!controller)void load();
  }
  async function load(pageOffset=0, existing=null) {
    if(stopped||!accessAllowed||document.hidden||!navigator.onLine)return;
    reportPending=false;
    const id=++requestId; controller?.abort();controller=new AbortController();
    const query=existing||Object.fromEntries(new FormData(form));
    query.offset=String(pageOffset);query.limit='50';
    $('report-status').className='';$('report-status').textContent=tr('جارٍ إعداد التقرير…','Preparing report…');
    $('apply').disabled=true;$('print').disabled=true;$('previous').disabled=true;$('next').disabled=true;
    $('report-content').hidden=true; data=null;
    try {
      const response=await fetch('/api/reports/customers?'+new URLSearchParams(query),{headers,cache:'no-store',credentials:'same-origin',signal:controller.signal});
      const result=await response.json();if(stopped||id!==requestId)return;
      if([401,403].includes(response.status)){revokeAccess(result.detail?.message);return;}
      if(!response.ok)throw new Error(result.detail?.message||tr('تعذر عرض التقرير. تحقق من الجلسة والفترة المحددة.','Unable to show report. Check the session and dates.'));
      if(result.brand_id!==brand){revokeAccess();return;}
      data=result;applied={...query};offset=pageOffset;render();$('report-content').hidden=false;$('report-status').textContent='';
    } catch(error) {
      if(stopped||id!==requestId||error.name==='AbortError')return;
      clearReport();
      $('report-status').className='error';$('report-status').textContent=error.message;
    } finally {if(id===requestId&&!stopped){controller=null;$('apply').disabled=false;$('print').disabled=!data;}}
  }
  form.addEventListener('submit',event=>{event.preventDefault();if(form.reportValidity())load();});
  form.addEventListener('change',event=>{if(['start','end'].includes(event.target.name)){document.querySelectorAll('[data-preset]').forEach(b=>{b.classList.remove('active');b.setAttribute('aria-pressed','false');});$('preset-note').textContent='';}});
  $('presets').addEventListener('click',event=>{const button=event.target.closest('[data-preset]');if(button){preset(button.dataset.preset);load();}});
  $('reset').addEventListener('click',()=>{form.reset();preset('month');load();});
  $('previous').addEventListener('click',()=>load(Math.max(0,offset-50),{...applied}));
  $('next').addEventListener('click',()=>{if(data && data.next_offset!==null)load(data.next_offset,{...applied});});
  $('print').addEventListener('click',()=>{if(data&&accessAllowed&&!stopped){const open=$('daily-details').open;$('daily-details').open=true;window.print();$('daily-details').open=open;}});
  $('language').addEventListener('click',()=>{en=!en;localStorage.setItem('kps_ops_lang',en?'en':'ar');localize();});
  async function init() {
    localize();form.elements.start.max=today();form.elements.end.max=today();preset('month');maskAccess();
    if(await checkAccess())if(!controller)await load();
  }
  const accessTimer=setInterval(async()=>{if(await checkAccess())if(reportPending&&!controller)void load();},2000);
  document.addEventListener('visibilitychange',resumeAccess);
  for(const event of ['online','focus'])window.addEventListener(event,resumeAccess);
  window.addEventListener('offline',pauseAccess);
  window.addEventListener('pagehide',()=>{pauseAccess();stopped=true;clearInterval(accessTimer);});
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();else void resumeAccess();});
  init();
})();

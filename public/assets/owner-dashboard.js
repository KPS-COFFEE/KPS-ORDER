/* Owner overview. Every value and operational-date boundary comes from the server. */
(() => {
  'use strict';

  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const integer = new Intl.NumberFormat('en-US', {maximumFractionDigits:0});
  const decimal = new Intl.NumberFormat('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = value => finite(value) ? integer.format(value) : '—';
  const amount = value => finite(value) ? decimal.format(value / 100) : '—';
  const presets = ['today','yesterday','last7','month','custom'];
  const sorts = ['created_desc','created_asc','value_desc','value_asc'];

  function create({api,getUser,getScope,getLang,isActive,onOrder,onAccessDenied}) {
    let host = null, scope = null, data = null, dataKey = '', sequence = 0;
    let controller = null, searchTimer = null, requestPromise = null, lastFetch = 0;
    let filters = null, options = {brands:[],cities:[],branches:[],drivers:[]};
    let language = '', dateDraft = false, loading = false, error = '', stale = false;
    const renderedHTML = new WeakMap();
    const tr = (ar,en) => getLang() === 'en' ? en : ar;
    const $ = selector => host?.querySelector(selector);
    const active = () => !!host?.isConnected && getUser()?.role === 'owner' && (!isActive || isActive());
    const key = () => JSON.stringify(filters);
    const initialFilters = () => {
      const user = getUser();
      const brands = user?.central_operations?.brands || [];
      return {brand_id:brands.length > 1 ? 'all' : user?.brand_id || '',period:'today',start:'',end:'',city:'',branch_id:'',driver_id:'',search:'',sort:'created_desc',offset:0};
    };
    const availableBrands = () => options.brands.length ? options.brands : (getUser()?.central_operations?.brands || []).map(brand => ({id:brand.id,name:brand.name}));
    const date = value => {
      if (!value) return '—';
      const parsed = new Date(value);
      if (Number.isNaN(parsed.valueOf())) return '—';
      return new Intl.DateTimeFormat(getLang() === 'en' ? 'en-GB' : 'ar-SA-u-ca-gregory-nu-latn',{timeZone:data?.period?.timezone || 'Asia/Riyadh',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(parsed);
    };
    const money = value => `<span class="owner-money"><bdi class="owner-number">${amount(value)}</bdi><span class="owner-currency">SAR</span></span>`;
    const setHTML = (selector,html) => {
      const node = $(selector); if (!node || renderedHTML.get(node) === html) return;
      const scroller = node.querySelector('.owner-table-scroll');
      const scroll = scroller ? {left:scroller.scrollLeft,top:scroller.scrollTop} : null;
      const focused = node.contains(document.activeElement) ? document.activeElement : null;
      const focusKey = focused?.dataset?.ownerAction || focused?.dataset?.ownerOrder;
      node.innerHTML = html;
      renderedHTML.set(node,html);
      const nextScroller = node.querySelector('.owner-table-scroll');
      if (scroll && nextScroller) { nextScroller.scrollLeft = scroll.left; nextScroller.scrollTop = scroll.top; }
      if (focusKey) [...node.querySelectorAll('button')].find(button => (button.dataset.ownerAction || button.dataset.ownerOrder) === focusKey)?.focus({preventScroll:true});
    };
    const statusName = status => ({completed:tr('مكتمل','Completed'),active:tr('قيد التنفيذ','In progress'),cancelled:tr('ملغي','Cancelled'),new:tr('جديد','New'),accepted:tr('مقبول','Accepted'),preparing:tr('قيد التجهيز','Preparing'),ready:tr('جاهز','Ready'),handed_over:tr('تم التسليم','Handed over'),delivered:tr('تم التوصيل','Delivered'),rejected:tr('مرفوض','Rejected'),assigned:tr('مسند','Assigned'),picked_up:tr('استلم من الفرع','Picked up'),problem:tr('بلاغ مشكلة','Problem'),paid:tr('محصل','Paid'),unpaid:tr('غير محصل','Unpaid'),partially_refunded:tr('استرداد جزئي','Partly refunded'),refunded:tr('مسترد','Refunded'),pending:tr('منتظر','Pending')})[status] || status || '—';
    const presetName = preset => ({today:tr('اليوم التشغيلي الحالي','Current operational day'),yesterday:tr('اليوم التشغيلي السابق','Previous operational day'),last7:tr('آخر 7 أيام تشغيلية','Last 7 operational days'),month:tr('هذا الشهر','This month'),custom:tr('فترة مخصصة','Custom period')})[preset] || '';
    const optionHTML = (rows,selected,placeholder,label = row => row.name) => `${placeholder === null ? '' : `<option value="">${escape(placeholder)}</option>`}${rows.map(row => `<option value="${escape(row.id)}"${String(row.id) === String(selected) ? ' selected' : ''}>${escape(label(row))}</option>`).join('')}`;

    function pause() {
      sequence++;
      controller?.abort(); controller = null;
      clearTimeout(searchTimer); searchTimer = null;
      requestPromise = null; setBusy(false);
    }

    function reset() {
      pause(); data = null; dataKey = ''; filters = null; scope = null; lastFetch = 0;
      options = {brands:[],cities:[],branches:[],drivers:[]}; error = ''; stale = false; dateDraft = false;
      if (host) { host.replaceChildren(); host.removeAttribute('aria-busy'); }
    }

    function shell() {
      language = getLang();
      host.className = 'owner-dashboard';
      host.setAttribute('dir',language === 'en' ? 'ltr' : 'rtl');
      host.innerHTML = `
        <section class="owner-intro" aria-label="${escape(tr('ملخص الملاك','Owner overview'))}">
          <div><span class="owner-eyebrow">${escape(tr('رؤية موحّدة لأعمالك','YOUR BUSINESS AT A GLANCE'))}</span><h2 id="owner-period-title">${escape(presetName(filters.period))}</h2><div id="owner-brand-context" class="owner-brand-context"></div></div>
          <div class="owner-freshness"><span class="owner-access-badge">${escape(tr('خاص بالملاك','Owners only'))}</span><span id="owner-updated">${escape(tr('بانتظار البيانات','Waiting for data'))}</span><button type="button" class="quiet owner-refresh" data-owner-action="refresh"><span aria-hidden="true">↻</span> ${escape(tr('تحديث','Refresh'))}</button></div>
        </section>
        <form id="owner-filters" class="owner-filters" aria-label="${escape(tr('تصفية جميع مؤشرات اللوحة','Filter all dashboard values'))}">
          <label for="owner-brand"><span>${escape(tr('البراند','Brand'))}</span><select id="owner-brand" name="brand_id"></select></label>
          <label for="owner-period"><span>${escape(tr('الفترة','Period'))}</span><select id="owner-period" name="period">${presets.map(preset => `<option value="${preset}"${preset === filters.period ? ' selected' : ''}>${escape(presetName(preset))}</option>`).join('')}</select></label>
          <label for="owner-city"><span>${escape(tr('المدينة','City'))}</span><select id="owner-city" name="city"></select></label>
          <label for="owner-branch"><span>${escape(tr('الفرع','Branch'))}</span><select id="owner-branch" name="branch_id"></select></label>
          <label for="owner-driver"><span>${escape(tr('السائق','Driver'))}</span><select id="owner-driver" name="driver_id"></select></label>
          <button type="button" class="quiet owner-reset" data-owner-action="reset">${escape(tr('مسح الفلاتر','Reset filters'))}</button>
          <div id="owner-custom-dates" class="owner-custom-dates"${filters.period === 'custom' ? '' : ' hidden'}><label for="owner-start"><span>${escape(tr('من يوم تشغيلي','From operational date'))}</span><input id="owner-start" name="start" type="date" value="${escape(filters.start)}"></label><label for="owner-end"><span>${escape(tr('إلى يوم تشغيلي','Through operational date'))}</span><input id="owner-end" name="end" type="date" value="${escape(filters.end)}"></label><button type="submit">${escape(tr('تطبيق الفترة','Apply period'))}</button><p class="owner-date-hint">${escape(tr('يشمل تاريخ البداية والنهاية.','Includes both start and end dates.'))}</p></div>
        </form>
        <div id="owner-period-basis" class="owner-period-basis"></div>
        <div id="owner-notice" class="owner-notice" role="status" aria-live="polite" hidden></div>
        <div id="owner-results" aria-busy="false">
          <section id="owner-metrics" class="owner-metrics" aria-label="${escape(tr('المؤشرات الرئيسية','Key metrics'))}"></section>
          <section id="owner-finance-context" class="owner-finance-context" aria-label="${escape(tr('تفاصيل التحصيل والضيافة','Collections and complimentary orders'))}"></section>
          <div class="owner-analysis"><section id="owner-status-chart" class="owner-panel" aria-label="${escape(tr('توزيع حالات الطلبات','Order status distribution'))}"></section><section id="owner-brand-breakdown" class="owner-panel" aria-label="${escape(tr('الأرقام حسب البراند','Values by brand'))}"></section></div>
        </div>
        <section id="owner-details" class="owner-panel owner-details" aria-labelledby="owner-orders-title">
          <div class="owner-section-heading"><div><span class="owner-eyebrow">${escape(tr('من المؤشرات إلى التفاصيل','FROM OVERVIEW TO DETAIL'))}</span><h2 id="owner-orders-title">${escape(tr('الطلبات والحركات','Orders and movements'))}</h2><p id="owner-rows-note">${escape(tr('نفس البراند والفترة والفلاتر المختارة أعلاه.','The same brand, period and filters selected above.'))}</p></div><span id="owner-record-count" class="owner-record-count"></span></div>
          <div class="owner-table-tools"><label for="owner-search"><span>${escape(tr('البحث في جميع النتائج','Search all results'))}</span><input id="owner-search" type="search" maxlength="120" autocomplete="off" placeholder="${escape(tr('رقم الطلب أو المرجع','Order number or reference'))}" value="${escape(filters.search)}" aria-describedby="owner-search-note"></label><label for="owner-sort"><span>${escape(tr('ترتيب النتائج','Sort results'))}</span><select id="owner-sort">${[[sorts[0],tr('الأحدث أولًا','Newest first')],[sorts[1],tr('الأقدم أولًا','Oldest first')],[sorts[2],tr('الأعلى قيمة','Highest value')],[sorts[3],tr('الأقل قيمة','Lowest value')]].map(([value,label]) => `<option value="${value}"${filters.sort === value ? ' selected' : ''}>${escape(label)}</option>`).join('')}</select></label><p id="owner-search-note">${escape(tr('يُطبّق البحث على المؤشرات والتفاصيل معًا.','Search applies to both the metrics and details.'))}</p></div>
          <div id="owner-orders" class="owner-orders"></div>
          <div id="owner-pagination" class="owner-pagination"></div>
        </section>`;
      host.onchange = handleChange;
      host.oninput = handleInput;
      host.onclick = handleClick;
      $('#owner-filters').onsubmit = event => { event.preventDefault(); applyDates(); };
      $('#owner-search').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(searchTimer); void refresh({force:true}); } };
      syncControls();
      if (data && dataKey === key()) renderData(); else renderLoading();
    }

    function syncSelect(id,rows,value,placeholder,label) {
      const select = $(id);
      if (!select) return;
      const markup = optionHTML(rows,value,placeholder,label);
      if (select.innerHTML !== markup) select.innerHTML = markup;
      select.value = value;
    }

    function syncControls() {
      let brands = availableBrands();
      if (!brands.length) brands = [{id:getUser()?.brand_id || '',name:getUser()?.brand_name || getUser()?.brand_id || tr('البراند الحالي','Current brand')}];
      if (brands.length > 1) brands = [{id:'all',name:tr('جميع البراندات المخولة','All authorized brands')},...brands];
      syncSelect('#owner-brand',brands,filters.brand_id,null);
      if ($('#owner-brand') && !$('#owner-brand').value && brands.length === 1) $('#owner-brand').value = brands[0].id;
      syncSelect('#owner-city',options.cities,filters.city,tr('كل المدن','All cities'));
      syncSelect('#owner-branch',options.branches,filters.branch_id,tr('كل الفروع','All branches'),row => [row.brand_name,row.branch_no,row.name].filter(value => value !== undefined && value !== null && value !== '').join(' · '));
      syncSelect('#owner-driver',options.drivers,filters.driver_id,tr('كل السائقين','All drivers'),row => row.name + (row.active === false ? tr(' · غير نشط',' · Inactive') : ''));
      $('#owner-period').value = filters.period;
      $('#owner-custom-dates').hidden = filters.period !== 'custom';
      if (!dateDraft) { $('#owner-start').value = filters.start || data?.period?.start || ''; $('#owner-end').value = filters.end || data?.period?.end || ''; }
      $('#owner-sort').value = filters.sort;
      if (document.activeElement !== $('#owner-search')) $('#owner-search').value = filters.search;
    }

    function showNotice(message = '',tone = '') {
      const node = $('#owner-notice');
      if (!node) return;
      node.hidden = !message; node.className = 'owner-notice' + (tone ? ` owner-notice-${tone}` : '');
      node.textContent = message;
    }

    function renderLoading() {
      setHTML('#owner-metrics',Array.from({length:6},(_,index) => `<div class="owner-metric owner-skeleton${index < 2 ? ' owner-metric-featured' : ''}" aria-hidden="true"><span></span><strong></strong><span></span></div>`).join(''));
      setHTML('#owner-finance-context','');
      setHTML('#owner-status-chart',`<h3>${escape(tr('حالات الطلبات','Order status'))}</h3><div class="owner-chart-placeholder" aria-hidden="true"></div>`);
      setHTML('#owner-brand-breakdown',`<h3>${escape(tr('حسب البراند','By brand'))}</h3><div class="owner-chart-placeholder" aria-hidden="true"></div>`);
      setHTML('#owner-orders',`<div class="owner-empty owner-loading" role="status">${escape(tr('جاري تحميل النتائج…','Loading results…'))}</div>`);
      setHTML('#owner-pagination',''); setHTML('#owner-record-count','');
    }

    function renderError() {
      if (data && dataKey === key()) return;
      setHTML('#owner-metrics',''); setHTML('#owner-finance-context',''); setHTML('#owner-status-chart',''); setHTML('#owner-brand-breakdown','');
      setHTML('#owner-orders',`<div class="owner-empty owner-error"><strong>${escape(tr('تعذر تحميل البيانات','Could not load data'))}</strong><p>${escape(tr('أعد المحاولة لعرض أرقام مؤكدة من النظام.','Retry to load confirmed values from the system.'))}</p><button type="button" class="quiet" data-owner-action="refresh">${escape(tr('إعادة المحاولة','Try again'))}</button></div>`);
      setHTML('#owner-pagination',''); setHTML('#owner-record-count','');
    }

    function renderMeta() {
      const period = data?.period;
      const shownBrands = data?.brand?.id === 'all' ? availableBrands() : [data?.brand].filter(Boolean);
      setHTML('#owner-brand-context',shownBrands.map(brand => `<span class="owner-brand-badge">${escape(brand.name)}</span>`).join(''));
      setHTML('#owner-period-title',escape(presetName(period?.preset || filters.period)));
      const generated = data?.generated_at;
      setHTML('#owner-updated',generated ? `${escape(tr('آخر تحديث','Updated'))} <time datetime="${escape(generated)}">${escape(date(generated))}</time>${stale ? ` · ${escape(tr('بيانات سابقة','Previous data'))}` : ''}` : escape(tr('بانتظار البيانات','Waiting for data')));
      if (period) {
        const definition = typeof period.definition === 'object' ? (period.definition[getLang()] || period.definition.ar || period.definition.en || '') : period.definition;
        const fallback = period.fallback_start || '';
        setHTML('#owner-period-basis',`<div class="owner-basis-summary"><span class="owner-basis-dot" aria-hidden="true"></span><strong>${escape(tr('اليوم المحاسبي حسب شفت المدير','Accounting day follows the manager shift'))}</strong><bdi>${escape(period.start)}${period.end !== period.start ? ` — ${escape(period.end)}` : ''}</bdi><span>${escape(tr('بتوقيت الرياض','Riyadh time'))}</span></div><details><summary>${escape(tr('كيف تُحسب الفترة؟','How is this period calculated?'))}</summary><p>${escape(definition || tr('تُنسب حركات الشفت إلى يومه التشغيلي المعتمد، حتى لو امتد بعد منتصف الليل.','Shift movements remain assigned to their approved operational date, including after midnight.'))}</p>${fallback ? `<p>${escape(tr('بداية اليوم المرجعية عند عدم وجود شفت مرتبط: ','Fallback day boundary when there is no linked shift: '))}<bdi>${escape(fallback)}</bdi> ${escape(tr('بتوقيت الرياض. لا تقسم شفت المدير.','Riyadh time. This does not split a manager shift.'))}</p>` : ''}${data.scope_note ? `<p>${escape(typeof data.scope_note === 'object' ? data.scope_note[getLang()] || data.scope_note.ar || data.scope_note.en : data.scope_note)}</p>` : ''}</details>`);
      }
    }

    function renderMetrics() {
      const summary = data.summary || {};
      const values = [
        {key:'net_sales_halalas',label:tr('صافي المبيعات','Net sales'),note:tr('بعد المرتجعات وتصحيحات السعر؛ دون الضيافة والتعويض والاستكمال','After returns and price adjustments; excludes complimentary orders'),financial:true},
        {key:'net_collected_halalas',label:tr('صافي التحصيل','Net collected'),note:tr('النقد والشبكة ناقص الاسترداد في الفترة','Period cash + card, less refunds'),financial:true},
        {key:'orders',label:tr('إجمالي الطلبات','Total orders'),note:tr('الطلبات المنسوبة للفترة المختارة','Orders attributed to the selected period')},
        {key:'completed',label:tr('طلبات مكتملة','Completed'),note:tr('اكتمل تنفيذها','Fulfilled orders'),tone:'completed'},
        {key:'active',label:tr('قيد التنفيذ','In progress'),note:tr('تحتاج متابعة التشغيل','Being processed'),tone:'active'},
        {key:'cancelled',label:tr('طلبات ملغاة','Cancelled'),note:tr('الملغي والمرفوض','Cancelled and rejected'),tone:'cancelled'}
      ];
      setHTML('#owner-metrics',values.map(item => `<article class="owner-metric${item.financial ? ' owner-metric-featured' : ''}${item.tone ? ` owner-metric-${item.tone}` : ''}"><h3>${escape(item.label)}</h3><p class="owner-metric-value">${item.financial ? money(summary[item.key]) : `<bdi class="owner-number">${number(summary[item.key])}</bdi>`}</p>${item.key === 'net_sales_halalas' ? `<p class="owner-metric-secondary"><span>${escape(tr('إجمالي المكتمل قبل التسويات:','Completed value before adjustments:'))}</span><strong>${money(summary.gross_completed_halalas)}</strong></p>` : ''}<p class="owner-metric-note">${escape(item.note)}</p></article>`).join(''));
      const finance = [['cash_halalas',tr('نقد','Cash')],['card_halalas',tr('شبكة','Card')],['refunds_halalas',tr('استردادات الفترة','Period refunds')]];
      setHTML('#owner-finance-context',finance.map(([field,label]) => `<div><span>${escape(label)}</span><strong>${money(summary[field])}</strong></div>`).join('') + `<div class="owner-complimentary"><span>${escape(tr('طلبات ضيافة / تعويض / استكمال','Complimentary / compensation / completion'))}</span><strong><bdi class="owner-number">${number(summary.complimentary_orders)}</bdi><small>${escape(tr('طلب','orders'))}</small></strong><span class="owner-complimentary-value">${finite(summary.complimentary_value_halalas) ? `${escape(tr('القيمة الاسمية','Face value'))} ${money(summary.complimentary_value_halalas)}` : escape(tr('القيمة الاسمية غير متاحة','Face value unavailable'))}</span></div>`);
    }

    function renderCharts() {
      const summary = data.summary || {};
      const statuses = ['completed','active','cancelled'].map(status => ({status,count:Number((data.status_breakdown || []).find(row => row.status === status)?.count ?? summary[status] ?? 0)}));
      const total = statuses.reduce((sum,row) => sum + Math.max(0,row.count),0);
      const percentage = value => total ? Math.round(value / total * 100) : 0;
      setHTML('#owner-status-chart',`<div class="owner-section-heading"><div><h3>${escape(tr('حالات الطلبات','Order status'))}</h3><p>${escape(tr('توزيع جميع الطلبات المطابقة للفلاتر','All orders matching your filters'))}</p></div><span class="owner-mini-total"><bdi>${number(total)}</bdi> ${escape(tr('طلب','orders'))}</span></div>${total ? `<div class="owner-status-bar" aria-hidden="true">${statuses.map(row => row.count > 0 ? `<span class="owner-status-${row.status}" style="flex-grow:${row.count}"></span>` : '').join('')}</div><ul class="owner-status-legend">${statuses.map(row => `<li><span class="owner-legend-label"><i class="owner-status-${row.status}" aria-hidden="true"></i>${escape(statusName(row.status))}</span><bdi>${number(row.count)}</bdi><span class="owner-percent"><bdi>${percentage(row.count)}%</bdi></span></li>`).join('')}</ul>` : `<div class="owner-chart-empty">${escape(tr('لا توجد طلبات ضمن الفلاتر الحالية.','No orders match these filters.'))}</div>`}`);
      const brands = data.brand_breakdown || [];
      setHTML('#owner-brand-breakdown',`<div class="owner-section-heading"><div><h3>${escape(tr('الأداء حسب البراند','Performance by brand'))}</h3><p>${escape(tr('نفس الفترة والفلاتر','Same period and filters'))}</p></div></div>${brands.length ? `<div class="owner-brand-table"><div class="owner-brand-table-head"><span>${escape(tr('البراند / الطلبات','Brand / orders'))}</span><span>${escape(tr('صافي المبيعات','Net sales'))}</span><span>${escape(tr('صافي التحصيل','Net collected'))}</span></div>${brands.map(brand => `<div class="owner-brand-line"><div><strong>${escape(brand.brand_name)}</strong><span><bdi>${number(brand.orders)}</bdi> ${escape(tr('طلب','orders'))}</span></div><strong>${money(brand.net_sales_halalas)}</strong><strong>${money(brand.net_collected_halalas)}</strong></div>`).join('')}</div>` : `<div class="owner-chart-empty">${escape(tr('لا توجد نتائج للبراندات المختارة.','No results for the selected brands.'))}</div>`}`);
    }

    function renderRows() {
      const rows = data.rows || [], pagination = data.pagination || {};
      const columns = [tr('الطلب','Order'),tr('اليوم التشغيلي','Operational date'),tr('الفرع / المدينة','Branch / city'),tr('السائق','Driver'),tr('الحالة','Status'),tr('قيمة الفاتورة','Invoice value'),tr('صافي تحصيل الفترة','Period net collected'),tr('استرداد الفترة','Period refunds')];
      setHTML('#owner-record-count',`${number(pagination.total)} ${escape(tr('سجل','records'))}`);
      const td = (index,content,className = '') => `<td data-label="${escape(columns[index])}"${className ? ` class="${className}"` : ''}>${content}</td>`;
      setHTML('#owner-orders',rows.length ? `<div class="owner-table-scroll" tabindex="0" role="region" aria-label="${escape(tr('جدول الطلبات والحركات','Orders and movements table'))}"><table class="owner-table"><caption class="owner-visually-hidden">${escape(tr('الطلبات والحركات المرتبطة بالفترة المحاسبية والفلاتر الحالية','Orders and movements in the selected accounting period and filters'))}</caption><thead><tr>${columns.map(column => `<th scope="col">${escape(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => {
        const status = row.status || row.delivery_status || row.preparation_status;
        const tone = ['completed','delivered','handed_over'].includes(status) ? 'completed' : ['cancelled','rejected'].includes(status) ? 'cancelled' : 'active';
        return `<tr>${td(0,`<button type="button" class="owner-order-link" data-owner-order="${escape(row.id)}" data-owner-brand="${escape(row.brand_id)}"><bdi>${escape(row.order_no || row.reference || '—')}</bdi><span class="owner-visually-hidden"> ${escape(tr('فتح تفاصيل الطلب','Open order details'))}</span></button><span class="owner-row-brand">${escape(row.brand_name || data.brand?.name || '')}</span>${row.record_kind === 'movement' ? `<span class="owner-row-note">${escape(tr('حركة لطلب سابق','Earlier order movement'))}</span>` : ''}${row.is_complimentary ? `<span class="owner-row-note">${escape(tr('ضيافة / تعويض / استكمال','Complimentary order'))}</span>` : ''}`,'owner-order-cell')}${td(1,`<bdi>${escape(row.operational_date || '—')}</bdi><small><time datetime="${escape(row.created_at || '')}">${escape(date(row.created_at))}</time></small>`)}${td(2,`<strong>${escape(row.branch_name || '—')}</strong><small>${escape(row.city || '—')}</small>`)}${td(3,escape(row.driver_name || tr('غير مسند','Unassigned')))}${td(4,`<span class="owner-status-pill owner-pill-${tone}">${escape(statusName(status))}</span><small>${escape(statusName(row.payment_status))}</small>`)}${td(5,money(row.value_halalas),'owner-amount-cell')}${td(6,money(row.net_collected_halalas),'owner-amount-cell')}${td(7,money(row.refunds_halalas),'owner-amount-cell')}</tr>`;
      }).join('')}</tbody></table></div>` : `<div class="owner-empty"><span class="owner-empty-symbol" aria-hidden="true">⌕</span><strong>${escape(tr('لا توجد نتائج مطابقة','No matching results'))}</strong><p>${escape(tr('جرّب فترة أخرى أو وسّع المدينة أو الفرع أو السائق.','Try another period, city, branch or driver.'))}</p><button type="button" class="quiet" data-owner-action="reset">${escape(tr('مسح الفلاتر','Reset filters'))}</button></div>`);
      const offset = Number(pagination.offset || 0), limit = Number(pagination.limit || 25), total = Number(pagination.total || 0);
      setHTML('#owner-pagination',`<p>${total ? `<bdi>${number(offset + 1)}–${number(Math.min(offset + rows.length,total))}</bdi> ${escape(tr('من','of'))} <bdi>${number(total)}</bdi>` : escape(tr('0 سجلات','0 records'))}</p><div><button type="button" class="quiet" data-owner-action="prev"${offset <= 0 ? ' disabled' : ''}>${escape(tr('السابق','Previous'))}</button><span class="owner-page-number"><bdi>${number(Math.floor(offset / limit) + 1)}</bdi></span><button type="button" class="quiet" data-owner-action="next"${pagination.next_offset == null ? ' disabled' : ''}>${escape(tr('التالي','Next'))}</button></div>`);
      setHTML('#owner-rows-note',escape(tr('حركات التحصيل والاسترداد لطلبات سابقة قد تظهر كسجل إضافي؛ قيمة الفاتورة لا تعني أنها مبيعات هذه الفترة.','Collections or refunds for earlier orders may appear as additional records; invoice value does not mean a sale in this period.')));
    }

    function renderData() {
      renderMeta(); renderMetrics(); renderCharts(); renderRows();
      if (data.financial_completion_blocked) showNotice(tr('توجد تقفيلات مشتركة معلّقة. الأرقام المعروضة لا تعني اكتمال الإقفال المالي.','Shared closures are pending. These values do not confirm final financial closure.'),'warning');
      else showNotice();
    }

    function setBusy(busy) {
      loading = busy;
      host?.setAttribute('aria-busy',String(busy));
      $('#owner-results')?.setAttribute('aria-busy',String(busy));
      const button = $('[data-owner-action="refresh"]');
      if (button) { button.disabled = busy; button.classList.toggle('owner-refreshing',busy); }
    }

    function validateDates() {
      const start = $('#owner-start').value, end = $('#owner-end').value;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) {
        showNotice(tr('اختر تاريخ بداية ونهاية صحيحين، بحيث لا تسبق النهاية البداية. لم تُطبّق الفترة المعدلة.','Choose valid start and end dates, with the end on or after the start. Your date change has not been applied.'),'error');
        return false;
      }
      filters.start = start; filters.end = end; dateDraft = false; return true;
    }

    function applyDates() {
      if (filters.period !== 'custom' || !validateDates()) return;
      filters.offset = 0; void refresh({force:true});
    }

    function handleChange(event) {
      const id = event.target.id;
      if (id === 'owner-start' || id === 'owner-end') return;
      const fields = {'owner-brand':'brand_id','owner-period':'period','owner-city':'city','owner-branch':'branch_id','owner-driver':'driver_id','owner-sort':'sort'};
      const field = fields[id]; if (!field) return;
      if (field === 'period' && !presets.includes(event.target.value)) return;
      if (field === 'sort' && !sorts.includes(event.target.value)) return;
      filters[field] = event.target.value; filters.offset = 0;
      if (field === 'brand_id') { filters.city = ''; filters.branch_id = ''; filters.driver_id = ''; options.cities = []; options.branches = []; options.drivers = []; }
      if (field === 'city') { filters.branch_id = ''; filters.driver_id = ''; options.branches = options.branches.filter(row => !filters.city || row.city === filters.city); options.drivers = []; }
      if (field === 'branch_id') { filters.driver_id = ''; options.drivers = options.drivers.filter(row => !filters.branch_id || row.branch_ids?.includes(filters.branch_id)); }
      if (field === 'period') {
        dateDraft = false;
        if (filters.period === 'custom') { filters.start = data?.period?.start || filters.start; filters.end = data?.period?.end || filters.end; }
        else { filters.start = ''; filters.end = ''; }
      }
      clearTimeout(searchTimer); syncControls();
      if (filters.period === 'custom' && (!filters.start || !filters.end)) { pause(); renderLoading(); showNotice(tr('اختر الفترة ثم اضغط تطبيق الفترة.','Choose dates and select Apply period.')); return; }
      void refresh({force:true});
    }

    function handleInput(event) {
      if (event.target.id === 'owner-start' || event.target.id === 'owner-end') {
        dateDraft = true;
        showNotice(tr('التواريخ المعدلة لم تُطبّق بعد. اضغط تطبيق الفترة لتحديث المؤشرات.','Date changes are not applied yet. Select Apply period to update the metrics.'),'warning');
      }
      if (event.target.id !== 'owner-search') return;
      filters.search = event.target.value.trim(); filters.offset = 0;
      pause(); renderLoading(); showNotice(tr('جاري البحث في جميع النتائج…','Searching all results…'));
      searchTimer = setTimeout(() => { searchTimer = null; void refresh({force:true}); },350);
    }

    function handleClick(event) {
      const button = event.target.closest('button');
      if (!button || !host.contains(button) || button.disabled) return;
      if (button.hasAttribute('data-owner-order')) {
        const row = data?.rows?.find(item => String(item.id) === button.dataset.ownerOrder && String(item.brand_id) === button.dataset.ownerBrand);
        if (row && active() && getScope() === scope && !loading) Promise.resolve(onOrder(row.id,row.brand_id)).catch(failure => { if (active()) showNotice(failure.message || tr('تعذر فتح الطلب.','Could not open the order.'),'error'); });
        return;
      }
      const action = button.dataset.ownerAction;
      if (action === 'refresh') { clearTimeout(searchTimer); void refresh({force:true}); }
      if (action === 'reset') {
        const brand = filters.brand_id; filters = {...initialFilters(),brand_id:brand}; dateDraft = false; syncControls(); void refresh({force:true});
      }
      if (action === 'prev' || action === 'next') {
        const page = data?.pagination; if (!page) return;
        filters.offset = action === 'prev' ? Math.max(0,Number(page.offset || 0) - Number(page.limit || 25)) : Number(page.next_offset);
        void refresh({force:true});
      }
    }

    async function refresh({force = false} = {}) {
      if (!active()) return false;
      if (scope !== getScope()) { reset(); scope = getScope(); filters = initialFilters(); shell(); }
      if (!force && (loading || dateDraft || Date.now() - lastFetch < 30000)) return requestPromise || false;
      if (filters.period === 'custom' && (!filters.start || !filters.end)) return false;
      pause();
      const requestSequence = ++sequence, requestScope = scope, requestedKey = key();
      const abort = controller = new AbortController();
      const keepData = !!data && dataKey === requestedKey;
      if (!keepData) renderLoading();
      error = ''; stale = false; setBusy(true);
      // Keep successful content at the same position during background reads.
      // The refresh button and aria-busy communicate a refresh without adding
      // a banner above the cards on every poll.
      if (!keepData) showNotice(tr('جاري تحميل البيانات…','Loading data…'));
      const query = new URLSearchParams();
      for (const [field,value] of Object.entries(filters)) if (value !== '' && value != null) query.set(field,String(value));
      const current = () => !abort.signal.aborted && requestSequence === sequence && active() && getScope() === requestScope && key() === requestedKey;
      const task = (async () => {
        try {
          const response = await api('/api/owner-dashboard?' + query,{signal:abort.signal});
          if (!current()) return false;
          if (!response?.summary || !response.period || !Array.isArray(response.rows)) throw new Error(tr('استجابة البيانات غير مكتملة. أعد المحاولة.','The data response was incomplete. Please retry.'));
          options = {brands:response.options?.brands || [],cities:response.options?.cities || [],branches:response.options?.branches || [],drivers:response.options?.drivers || []};
          // A central staff context can contain a brand with a lower role.
          // Only the endpoint's owner-authorized options permit aggregation.
          if (filters.brand_id === 'all' && options.brands.length === 1) {
            filters.brand_id = options.brands[0].id; filters.offset = 0; syncControls();
            return refresh({force:true});
          }
          // Options are canonical, server-authorized scopes. If access or the
          // period removed a dependent choice, fetch its reset cohort first.
          let resetDependent = false;
          for (const [field,list] of [['city','cities'],['branch_id','branches'],['driver_id','drivers']]) {
            if (filters[field] && !options[list].some(row => String(row.id) === filters[field])) { filters[field] = ''; resetDependent = true; }
          }
          if (resetDependent) { filters.offset = 0; syncControls(); return refresh({force:true}); }
          const page = response.pagination;
          if (page && filters.offset > 0 && filters.offset >= page.total) {
            filters.offset = page.total > 0 ? Math.floor((page.total - 1) / (page.limit || 25)) * (page.limit || 25) : 0;
            return refresh({force:true});
          }
          if (response.filters?.brand_id) filters.brand_id = response.filters.brand_id;
          data = response; dataKey = key(); lastFetch = Date.now(); stale = false;
          syncControls(); renderData(); return true;
        } catch (failure) {
          if (!current()) return false;
          if ([401,403].includes(failure.status)) { reset(); onAccessDenied?.(failure); return false; }
          if (['PAGE_REFRESH_SUPERSEDED','WORK_SESSION_CHANGED'].includes(failure.code)) { reset(); return false; }
          error = failure.message || tr('تعذر تحميل البيانات.','Could not load data.');
          stale = keepData;
          if (keepData) renderMeta(); else renderError();
          showNotice(keepData ? tr('تعذر التحديث؛ تظهر آخر بيانات ناجحة لنفس الفلاتر. يمكنك إعادة المحاولة.','Refresh failed. The last successful data for these filters is shown. You can retry.') : error,'error');
          return false;
        } finally {
          if (requestSequence === sequence) { controller = null; requestPromise = null; setBusy(false); }
        }
      })();
      requestPromise = task; return task;
    }

    async function mount(nextHost) {
      if (getUser()?.role !== 'owner') { reset(); return false; }
      const changed = scope !== getScope();
      if (changed) reset();
      const newHost = host !== nextHost; host = nextHost; scope = getScope();
      if (!filters) filters = initialFilters();
      if (changed || newHost || language !== getLang() || !$('#owner-filters')) shell();
      return refresh({force:true});
    }

    return {mount,refresh,pause,reset};
  }

  window.KPSOwnerDashboard = Object.freeze({create});
})();

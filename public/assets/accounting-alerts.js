'use strict';
(() => {
  // Only canonical scoped read endpoints supply tasks. Opening this inbox never
  // receives a bag, acknowledges a discrepancy, posts money or closes a period.
  function accountingOnly(user, can) {
    return user?.role === 'admin'
      && ['accounting_reports', 'accounting_review'].some(can)
      && !['orders_cancel_after_print', 'orders_cancel_collected', 'delivery_assign',
        'orders_print', 'shifts_approve', 'support_manage'].some(can);
  }
  async function refresh(host, {api, can, user, tr, esc, isCurrent = () => true}) {
    const brands = user.operations_brands?.length ? user.operations_brands : [{id:user.brand_id, name:user.brand_id}];
    const sources = [];
    for (const brand of brands) {
      if (can('accounting_reports')) sources.push([brand, 'work', '/api/accounting/work-inbox']);
      if (can('accounting_review')) {
        sources.push([brand, 'cases', '/api/order-financial-cases'], [brand, 'floats', '/api/driver-floats']);
      }
    }
    if (user.central_operations && can('accounting_review')) {
      sources.push([null, 'bags', '/api/cash-bags?pending_only=true'],
        [null, 'drivers', '/api/shared-driver-closures'], [null, 'managers', '/api/shared-manager-closures']);
    }
    const results = await Promise.allSettled(sources.map(([brand,,path]) => api(path, brand?.id)));
    if (!isCurrent()) return;
    const groups = new Map(), failures = [];
    const names = {work:['الشفتات والموازنات والبنك','Shifts, balances and bank'],cases:['معالجات الطلبات','Order cases'],
      floats:['العهد','Floats'],bags:['الأكياس','Bags'],drivers:['تقفيلات السائقين','Driver closures'],managers:['تقفيلات المديرين','Manager closures']};
    for (const [i,result] of results.entries()) {
      const [brand,key] = sources[i], label = brand?.name || brand?.id || tr('الأعمال المشتركة','Shared work');
      const list = groups.get(label) || [];
      groups.set(label,list);
      if (result.status === 'rejected') { failures.push(label+' · '+tr(...names[key])); continue; }
      const d = result.value;
      const target = (action,id='') => `data-action="accounting-work" data-work="${esc(action)}" data-brand="${esc(brand?.id||'')}" data-id="${esc(id)}"`;
      const counted = (action,title,count) => {
        if (count) list.push(`<button class="quiet" ${target(action)}>${esc(title)} (${esc(count)})</button>`);
      };
      const choices = (action,title,rows,describe) => {
        if (!rows.length) return;
        list.push(`<details data-group="${esc((brand?.id||'')+'|'+title)}"><summary>${esc(title)} (${esc(rows.length)})</summary><ul class="ac-work-list">${rows.map(row =>
          `<li><button class="quiet" ${target(action,row.id)}>${esc(describe(row))}</button></li>`).join('')}</ul></details>`);
      };
      if (key === 'work') {
        choices('shift',tr('شفتات وموازنات تحتاج استكمال أو اعتماد','Closures needing completion or approval'),
          d.shifts, r => [r.day,r.manager,'#'+(r.shift_no||r.id)].join(' · '));
        choices('shift',tr('ذمم باقية للتحصيل أو السداد','Outstanding receivables or payables'),d.outstanding,
          r => [r.driver,r.day,r.due].filter(Boolean).join(' · '));
        choices('incident',tr('ملاحظات مفتوحة للمتابعة','Open incidents for follow-up'),d.incidents,
          r => [r.occurred_on,'#'+r.case_no].join(' · '));
        for(const row of d.months) {
          const month=row.month.slice(0,7);
          list.push(`<button class="quiet" ${target('month',month)} data-branches="${esc(JSON.stringify(row.branch_ids))}">${esc(tr('مراجعة وإقفال الشهر','Review and close month'))} · ${esc(month)}</button>`);
        }
        const labels={receipts:['إثباتات وصول بنك تنتظر قرارًا مستقلًا','Bank receipts awaiting independent review'],collection:['تحصيلات شبكة تحتاج إسنادًا لدفعة','Card collections awaiting allocation'],
          refund:['مرتجعات شبكة تحتاج إسنادًا لدفعة','Card refunds awaiting allocation'],
          batches:['دفعات تنتظر اعتمادًا أو وصول البنك','Batches awaiting approval or bank receipt'],
          lines:['حركات بنك تنتظر المراجعة','Bank lines awaiting review']};
        for(const [kind,row] of Object.entries(d.bank||{})) {
          if(row.count) list.push(`<button class="quiet" ${target('bank',row.oldest_day||'')}>${esc(tr(...labels[kind]))} (${esc(row.count)})</button>`);
        }
      } else if (key === 'cases') {
        choices('order',tr('طلبات تحتاج معالجة مالية أو استرداد','Orders needing financial treatment or refund'),
          d.cases.filter(r=>r.financial_case && r.financial_case.status!=='resolved'),r=>r.order_no||r.id);
      } else if (key === 'floats') {
        counted('float',tr('عهد تنتظر قرار المحاسبة','Driver floats awaiting accounting decision'),d.requests.filter(r=>r.approved==null).length);
      } else if (key === 'bags') {
        const bagLabel=r=>[r.shift_date,r.driver_name,r.reference||r.id].filter(Boolean).join(' · ');
        choices('bag-count',tr('أكياس تنتظر السحب والجرد','Bags awaiting physical collection and count'),
          d.bags.filter(r=>!r.count_id&&r.ready_to_count),bagLabel);
        choices('bags',tr('أكياس بانتظار استلام مدير التوصيل','Bags awaiting manager receipt'),
          d.bags.filter(r=>!r.count_id&&!r.ready_to_count),bagLabel);
        choices('bag-review',tr('فروق جرد تحتاج معالجة أو متابعة','Custody differences needing follow-up'),
          d.bags.filter(r=>r.count_id),bagLabel);
      } else {
        counted('shared',tr(...names[key]),d.workdays.length);
      }
    }
    const work = [...groups].filter(([,rows])=>rows.length);
    const heading = tr('شغلي المحاسبي','My accounting work');
    const html = `<section class="notice ${work.length||failures.length?'warn':''}" aria-label="${esc(heading)}"><strong>${esc(heading)}</strong>
      ${work.map(([label,rows])=>`<section><h3>${esc(label)}</h3>${rows.join(' ')}</section>`).join('')}
      ${failures.length?`<p role="status">${esc(tr('تعذر التحقق من','Could not verify'))} ${esc(failures.join('، '))}</p>`:''}
      ${!work.length&&!failures.length?`<p>${esc(tr('لا توجد إجراءات محاسبية معلقة في المصادر المعروضة.','No pending accounting actions in the displayed sources.'))}</p>`:''}</section>`;
    if (host.dataset.workHtml !== html) {
      const opened = new Set([...host.querySelectorAll('details[open]')].map(el=>el.dataset.group));
      host.innerHTML = html;
      host.dataset.workHtml = html;
      for (const el of host.querySelectorAll('details')) el.open = opened.has(el.dataset.group);
    }
    host.hidden = false;
  }
  window.KPSAccountingAlerts = {accountingOnly, refresh};
})();

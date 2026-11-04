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
    const sources = [];
    if (can('accounting_reports')) sources.push({key:'closures', read:() => api('/api/accounting/manager-cards?page_size=20&offset=0')});
    if (can('accounting_review')) {
      sources.push({key:'cases', read:() => api('/api/order-financial-cases')});
      sources.push({key:'floats', read:() => api('/api/driver-floats')});
      if (user.central_operations)
        sources.push({key:'bags', read:() => api('/api/cash-bags')});
    }
    const results = await Promise.allSettled(sources.map(source => source.read()));
    if (!isCurrent()) return;
    const tasks = [], failures = [];
    function task(label, count, action, page = '') {
      if (count) tasks.push(`<li><button class="quiet" ${page ? `data-page="${esc(page)}"` : `data-action="${esc(action)}"`}>${esc(label)} (${esc(count)})</button></li>`);
    }
    results.forEach((result, i) => {
      if (result.status !== 'fulfilled') { failures.push(sources[i].key); return; }
      const data = result.value;
      switch (sources[i].key) {
        case 'closures': {
          const cards = (data.cards || []).filter(card => !card.approved_day);
          task(tr('تقفيلات تحتاج استكمال أو اعتماد', 'Closures needing completion or approval'), cards.length, '', 'accounting');
          if (data.has_more) tasks.push(`<li>${esc(tr('توجد تقفيلات إضافية؛ افتح المحاسبة لعرضها.', 'More closures are available; open accounting to view them.'))}</li>`);
          break;
        }
        case 'cases': task(tr('طلبات تحتاج معالجة مالية أو استرداد', 'Orders needing financial treatment or refund'), (data.cases || []).filter(row => row.financial_case && row.financial_case.status !== 'resolved').length, 'financial-cases'); break;
        case 'floats': task(tr('عهد تنتظر قرار المحاسبة', 'Driver floats awaiting accounting decision'), (data.requests || []).filter(row => row.approved == null).length, 'float-register'); break;
        case 'bags': task(tr('أكياس تنتظر السحب والجرد الفعلي', 'Bags awaiting physical collection and count'), (data.bags || []).filter(row => !row.count_id && row.ready_to_count).length, 'cash-bags'); break;
      }
    });
    const heading = tr('إشعارات المحاسبة والإجراء المطلوب', 'Accounting notifications and required action');
    const body = tasks.length ? `<ul>${tasks.join('')}</ul>` : failures.length ? '' : `<p>${esc(tr('لا توجد إجراءات محاسبية معلقة في المصادر المعروضة.', 'No pending accounting actions in the displayed sources.'))}</p>`;
    const failure = failures.length ? `<p class="notice warn" role="status">${esc(tr('تعذر تحديث بعض الإجراءات؛ افتح المحاسبة وتحقق قبل الإقفال. عدم ظهور التنبيه لا يعني اكتمال المعالجة.', 'Some actions could not refresh; check accounting before closure. A missing alert does not mean work is complete.'))}</p>` : '';
    host.innerHTML = `<details class="notice" data-alert-group="accounting" open><summary>${esc(heading)}</summary>${body}${failure}<button class="quiet" data-page="accounting">${esc(tr('فتح إجراءات المحاسبة', 'Open accounting workspace'))}</button></details>`;
    host.hidden = false;
  }
  window.KPSAccountingAlerts = {accountingOnly, refresh};
})();

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
  // A work item is a projection of existing evidence, never a second ledger.
  // Bags join by manager AND child shift IDs; dates/names are not join keys.
  function closureTasks(cards, bags = []) {
    const tasks = [];
    for (const card of cards) {
      if (card.approved_day) continue;
      const linked = bags.filter(b => (b.manager_shift_ids || []).includes(card.id));
      const add = (kind, row = {}, bag = null, responsible = 'accounting') => {
        tasks.push({kind, card_id:card.id, manager:card.manager_name, day:card.day,
          shift_no:card.shift_no, driver:row.driver_name || '', driver_shift_id:row.shift_id || row.id || '',
          report_id:row.shift_id ? row.id : '', bag_id:bag?.id || '', responsible,
          counted:!!bag?.count_id, received_cash_halalas:bag?.count_id ? bag.received_cash_halalas : row.cash_recorded ? row.cash_received_halalas : null,
          cash_halalas:bag ? bag.declared_cash_halalas : row.cash_due_halalas ?? null,
          card_halalas:bag ? bag.declared_card_halalas : row.bank_halalas ?? null,
          due_at:row.due_at || card.due_at || null, overdue:!!(row.overdue ?? card.overdue)});
      };
      if (card.operationally_closed === false) { add('manager_end', {}, null, 'delivery_manager'); continue; }
      for (const row of card.pending_drivers || []) {
        if ((card.drivers || []).some(d => d.shift_id === row.id)) continue;
        if (row.responsible !== 'accounting') { add('driver_end', row, null, 'delivery_manager'); continue; }
        const matches = linked.filter(b => (b.shift_ids || [b.anchor_shift_id]).includes(row.id));
        if (!matches.length) { add('source_review', row); continue; }
        for (const bag of matches) {
          const custodyPending = (bag.accounting_custody_difference_halalas || bag.accounting_custody_card_difference_halalas)
            && !bag.custody_review?.financially_complete;
          add(!bag.count_id ? (bag.ready_to_count ? 'bag_count' : 'manager_receipt') : custodyPending ? 'custody' : 'allocate', row, bag,
            !bag.count_id && !bag.ready_to_count ? 'delivery_manager' : 'accounting');
        }
      }
      for (const row of card.drivers || []) {
        if (row.period_approval_allowed) continue;
        const bag = linked.find(b => (b.shift_ids || [b.anchor_shift_id]).includes(row.shift_id));
        if (!row.cash_recorded) add(bag?.count_id ? 'receipt_link' : 'cash_receipt', row, bag);
        if (!row.balance_approved) add('balance', row, bag);
        if (row.variance_pending) add('variance', row, bag);
        if (row.cash_recorded && row.balance_approved && !row.variance_pending) add('source_review', row, bag);
      }
      if (card.ready) add('approve');
      else if (!tasks.some(t => t.card_id === card.id)) add('source_review');
    }
    // One physical bag may span multiple legs of the SAME manager shift.
    return tasks.filter((task, i) => !tasks.slice(0, i).some(t => t.card_id === task.card_id && t.kind === task.kind
      && (task.bag_id ? t.bag_id === task.bag_id : t.driver_shift_id === task.driver_shift_id)));
  }
  function taskLabel(kind, tr) {
    const labels = {
      manager_end:['إنهاء مناوبة المدير أولًا','Complete manager duty first'],
      driver_end:['استكمال نهاية مناوبة السائق وسجلاتها','Complete driver end-of-duty records'],
      source_review:['مراجعة أصل التقفيلة والأدلة والموانع','Review closure evidence and blockers'],
      bag_count:['استلام الكيس وجرده فعليًا','Physically receive and count bag'],
      manager_receipt:['بانتظار استلام مدير التوصيل للكيس','Awaiting delivery manager bag receipt'],
      custody:['معالجة فرق عهدة الكيس واعتماده مستقلاً','Document bag custody difference and obtain independent approval'],
      allocate:['الكيس مجرود؛ استكمال الإسناد والمطابقة','Bag counted; complete attribution and reconciliation'],
      receipt_link:['الكيس مجرود؛ مراجعة ربط الاستلام بالتقفيلة دون تكرار الكاش','Bag counted; review receipt linkage without recording cash twice'],
      cash_receipt:['توثيق استلام الكاش الفعلي','Document actual cash receipt'],
      balance:['مراجعة واعتماد موازنة الكاش والشبكة','Review and approve cash/card balance'],
      variance:['معالجة الفروق بالأدلة والاعتماد المستقل','Document variances and obtain independent approval'],
      approve:['اعتماد مراجعة الشفت وإصدار التقرير','Approve shift review and issue report']
    };
    return tr(...(labels[kind] || labels.source_review));
  }
  function renderTasks(tasks, {tr, esc, money = v => (v / 100).toFixed(2), local = false, lang = 'ar'}) {
    if (!tasks.length) return '';
    const amount = value => Number.isSafeInteger(value) ? esc(money(value)) : '—';
    return `<section class="ac-pending-work notice warn" role="region" aria-label="${esc(tr('إجراءات محاسبية غير مكتملة','Incomplete accounting actions'))}"><strong>${esc(tr('عندك إجراءات محاسبية غير مكتملة','You have incomplete accounting actions'))}</strong><p>${esc(tr('يبقى الإجراء ظاهرًا حتى اكتماله في السجل. الجرد لا يعني اعتماد التقفيلة، ومطابقة البنك مستقلة.','An action stays visible until completed in its record. Counting is not closure approval; bank matching is separate.'))}</p><ul>${tasks.map(t => {
      const date = t.due_at ? new Date(t.due_at) : null;
      const due = date && Number.isFinite(date.getTime()) ? date.toLocaleString(lang === 'en' ? 'en-GB' : 'ar-SA-u-ca-gregory', {timeZone:'Asia/Riyadh'}) : tr('موعد غير موثق؛ يلزم المراجعة','Undocumented deadline; review required');
      return `<li class="${t.overdue ? 'ac-work-overdue' : ''}"><strong>${esc(t.day || '—')} · ${esc(t.manager || '—')}${t.shift_no ? ' · '+esc(t.shift_no) : ''}${t.driver ? ' · '+esc(t.driver) : ''}</strong><p>${esc(taskLabel(t.kind, tr))} — ${esc(t.responsible === 'accounting' ? tr('المسؤول: المحاسبة','Responsible: accounting') : tr('المسؤول: مدير التوصيل','Responsible: delivery manager'))}</p><p>${esc(tr('الكاش المعلن/المطلوب','Declared/due cash'))}: <bdi>${amount(t.cash_halalas)}</bdi> · ${esc(tr('المستلم المسجل','Recorded receipt'))}: <bdi>${amount(t.received_cash_halalas)}</bdi> · ${esc(tr('الشبكة المسجلة','Recorded card'))}: <bdi>${amount(t.card_halalas)}</bdi></p><small>${esc(tr('الموعد بتوقيت الرياض','Due in Riyadh time'))}: ${esc(due)}${t.overdue ? ' · '+esc(tr('متأخر','Overdue')) : ''}</small><button type="button" ${local ? 'data-ac-work-card' : 'data-accounting-focus'}="${esc(t.card_id)}" data-work-driver="${esc(t.driver_shift_id)}" data-work-report="${esc(t.report_id)}">${esc(tr('استكمال الإجراء','Complete action'))}</button></li>`;
    }).join('')}</ul></section>`;
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
    const bagData = results.find((r, i) => sources[i].key === 'bags' && r.status === 'fulfilled')?.value?.bags || [];
    results.forEach((result, i) => {
      if (result.status !== 'fulfilled') { failures.push(sources[i].key); return; }
      const data = result.value;
      switch (sources[i].key) {
        case 'closures': {
          const cards = (data.cards || []).filter(card => !card.approved_day);
          task(tr('تقفيلات تحتاج استكمال أو اعتماد', 'Closures needing completion or approval'), cards.length, '', 'accounting');
          const detail = renderTasks(closureTasks(cards, bagData), {tr, esc});
          if (detail) tasks.push(`<li>${detail}</li>`);
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
  window.KPSAccountingAlerts = {accountingOnly, refresh, closureTasks, renderTasks};
})();

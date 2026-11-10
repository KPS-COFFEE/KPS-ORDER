/* Physical preparation selection; every write is validated by the server. */
(() => {
  'use strict';
  let helpers = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const en = () => helpers?.state?.lang === 'en';
  const tr = (ar, english) => en() ? english : ar;
  const distance = value => typeof value === 'number' && Number.isFinite(value)
    ? (value < 1000 ? Math.round(value) + tr(' م', ' m') : (value / 1000).toFixed(1) + tr(' كم', ' km'))
    : tr('غير متاحة', 'Unavailable');
  function init(options) { helpers = options; return window.KPSPreparationRouting; }
  function ready() { if (!helpers) throw new Error('Preparation routing is not initialized.'); return helpers; }
  async function open(orderId, brandId) {
    const h = ready();
    let brand = brandId || (h.state.orders || []).find(o => o.id === orderId)?.brand_id;
    if (!brand) {
      const canonical = await h.api('/api/orders/' + encodeURIComponent(orderId));
      brand = canonical.order?.brand_id;
    }
    if (!brand) throw new Error(tr('تعذر تحديد براند الطلب.', 'Order brand is unavailable.'));
    const data = await h.api('/api/preparation-routing/orders/' + encodeURIComponent(orderId), {brand});
    const writable = !!data.can_choose && h.can('orders_route_preparation') && h.can('orders_view') && h.can('delivery_assign');
    const options = (data.branches || []).map(branch => `<label class="prep-route-choice${branch.available ? '' : ' prep-route-unavailable'}">
      <input type="radio" name="target_branch_id" value="${esc(branch.id)}" ${!writable || !branch.available || branch.current ? 'disabled' : ''} required>
      <span><strong>${esc(branch.name)}</strong>${branch.current ? `<small class="prep-route-current">${esc(tr('فرع التحضير الحالي', 'Current preparation branch'))}</small>` : ''}
      <span class="prep-route-distances"><span>${esc(tr('من فرع التحضير الحالي: ', 'From current preparation branch: '))}${esc(distance(branch.distance_from_branch_meters))}</span>
      <span>${esc(tr('إلى العميل: ', 'To customer: '))}${esc(distance(branch.distance_to_customer_meters))}</span></span>
      ${branch.reason ? `<span class="prep-route-reason">${esc(branch.reason)}</span>` : ''}</span>
    </label>`).join('');
    const canSelect = writable && (data.branches || []).some(b => b.available && !b.current);
    h.modal(tr('اختيار فرع التحضير', 'Choose preparation branch'), `<form class="prep-route-panel">
      <p>${esc(tr('فرع الطلب: ', 'Original order branch: '))}<strong>${esc(data.source_branch?.name)}</strong></p>
      <p class="prep-route-notice">${esc(tr('يغيّر هذا الاختيار مكان التحضير والاستلام فقط. تبقى أسعار العميل ورسوم التوصيل والفاتورة والمستفيد من التسوية كما هي.', 'This choice changes preparation and pickup location. Customer prices, delivery fee, invoice and settlement beneficiary remain unchanged.'))}</p>
      <p class="prep-route-notice">${esc(tr('المسافات مباشرة تقريبية، وليست مسافة طريق أو وقت وصول.', 'Distances are approximate straight-line distances, not road distances or arrival estimates.'))}</p>
      ${data.blocked_reason ? `<p role="status" class="prep-route-blocked">${esc(data.blocked_reason)}</p>` : ''}
      <div class="prep-route-choices">${options || `<p>${esc(tr('لا توجد فروع ضمن الصلاحيات الحالية.', 'No branches in your current scope.'))}</p>`}</div>
      ${canSelect ? `<label>${esc(tr('سبب اختيار الفرع', 'Reason for branch choice'))}<textarea name="reason" required minlength="3" maxlength="500" rows="3"></textarea></label><button type="submit">${esc(tr('اعتماد فرع التحضير', 'Confirm preparation branch'))}</button>` : ''}
      <p class="form-error" role="alert"></p>
    </form>`, canSelect ? async (form, element, button) => {
      const target = String(form.get('target_branch_id') || '');
      const selected = (data.branches || []).find(b => b.id === target && b.available && !b.current);
      if (!selected) throw new Error(tr('اختر فرعًا مؤهلًا من القائمة.', 'Choose an eligible branch.'));
      await h.mutation('/api/preparation-routing/orders/' + encodeURIComponent(orderId), {
        target_branch_id: target, expected_preparation_branch_id: data.preparation_branch.id,
        version: data.version, reason: String(form.get('reason') || '').trim()
      }, button, {brand});
      h.closeModal?.();
      await h.refresh();
      h.notify(tr('تم اختيار فرع التحضير؛ أرسل الطباعة إلى طابعته المعتمدة.', 'Preparation branch selected. Send printing to its approved printer.'));
    } : undefined);
    return data;
  }
  function optionsText(item) {
    return (item.options || []).flatMap(unit => Object.values(unit).map(group =>
      [group.name, ...(group.choices || [])].filter(Boolean).join(': '))).join('، ');
  }
  async function renderQueue(container) {
    const h = ready();
    const data = await h.api('/api/preparation-routing/tasks');
    const tasks = data.preparation_tasks || [];
    const next = {new:['accepted','قبول التحضير','Accept preparation'], accepted:['preparing','بدأ التحضير','Start preparing'],
      preparing:['ready','جاهز للاستلام','Ready for pickup'], ready:['handed_over','تسليم للسائق','Hand over to driver']};
    container.innerHTML = `<section class="prep-target-queue"><h2>${esc(tr('طلبات التحضير المسندة لفرعك', 'Preparation assigned to your branch'))}</h2>
      <p>${esc(tr('تحديث التحضير والتسليم فقط؛ المحاسبة ومعالجة الطلب لدى الفريق المخول.', 'Update preparation and handover. Authorized staff handle accounting and order interventions.'))}</p>
      ${tasks.length ? tasks.map(task => {
        const transition = task.next_status==='reviewed' ? ['reviewed', task.next_status_label||'تأكيد مراجعة التعديل المعتمد', 'Acknowledge approved revision'] : next[task.preparation_status];
        return `<article class="prep-target-card"><header><strong>${esc(task.order_reference || task.order_no)}</strong><span>${esc(task.preparation_branch?.name)}</span></header>
          <ul>${(task.items || []).map(item => `<li><strong>${esc(item.quantity)} × ${esc(item.name)}</strong>${optionsText(item) ? `<span>${esc(optionsText(item))}</span>` : ''}${item.note ? `<span>${esc(item.note)}</span>` : ''}</li>`).join('')}</ul>
          ${task.notes ? `<p>${esc(task.notes)}</p>` : ''}
          ${task.operational_instructions ? `<p><strong>${esc(tr('تعليمات المدير المعتمدة', 'Approved manager instructions'))}</strong>: ${esc(task.operational_instructions)}</p>` : ''}
          ${transition && h.can('orders_prepare') && (task.workflow !== 'manager_dispatch'||transition[0]==='reviewed') ? `<button type="button" data-prep-task="${esc(task.id)}" data-prep-status="${esc(transition[0])}">${esc(tr(transition[1], transition[2]))}</button>` : ''}
          ${task.workflow === 'manager_dispatch' ? `<p>${esc(tr('الاستلام يؤكده السائق المكلف.', 'The assigned driver confirms pickup.'))}</p>` : ''}</article>`;
      }).join('') : `<p>${esc(tr('لا توجد طلبات تحضير محولة حاليًا.', 'No redirected preparation tasks.'))}</p>`}</section>`;
    if (container._preparationHandler) container.removeEventListener('click', container._preparationHandler);
    const handler = async event => {
      const button = event.target.closest('[data-prep-task]');
      if (!button || !container.contains(button) || button.disabled) return;
      const task = tasks.find(row => row.id === button.dataset.prepTask);
      if (!task || !h.can('orders_prepare')) return;
      try {
        await h.mutation('/api/preparation-routing/tasks/' + encodeURIComponent(task.id) + '/prepare',
          {version:task.version, status:button.dataset.prepStatus}, button, {brand:task.brand_id});
        await renderQueue(container);
      } catch (error) { h.notify(error.message, true); }
    };
    container._preparationHandler = handler;
    container.addEventListener('click', handler);
    return data;
  }
  window.KPSPreparationRouting = {init, open, renderQueue};
})();

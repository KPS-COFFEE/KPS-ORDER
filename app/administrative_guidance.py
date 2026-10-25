"""Read-only adapters over canonical states; descriptors never authorize mutations.

Only caller-authorized source rows may be passed. Missing assignment/return routes
stay explicit; this module does not invent department handoffs or execution.
"""

from datetime import datetime, timezone


def text(ar, en):
    return {'ar': ar, 'en': en}


ROLES = {
    'delivery_manager': text('مدير التوصيل', 'Delivery manager'),
    'branch_manager': text('مسؤول الفرع', 'Branch manager'),
    'accounting': text('المحاسبة', 'Accounting'),
    'customer': text('العميل', 'Customer'),
    'driver': text('السائق', 'Driver'),
    'owner': text('المالك', 'Owner'),
}


def step(code, role, action, evidence, following, fallback, **extra):
    return dict(code=code, responsible_role=role, responsible=ROLES[role],
                action=text(*action), evidence=text(*evidence),
                next_step=text(*following), fallback=text(*fallback), **extra)


def order(order, *, financial_case=None, requests=(), amendments=(), viewer_role=None, events=()):
    # Do not add administrative metadata to customer or blind-driver responses.
    if viewer_role in ('customer', 'driver'):
        return None
    reviewer = 'delivery_manager' if order['service'] == 'delivery' else 'branch_manager'
    cancelled = order['preparation_status'] in ('cancelled', 'rejected') or order['delivery_status'] == 'cancelled'
    delivered = order['delivery_status'] == 'delivered' or order['preparation_status'] == 'completed'
    stages=[]
    for request in requests:
        if request.get('decision'):
            continue
        stages.append(step('cancellation_review', reviewer,
            ('راجع طلب الإلغاء وسجّل التنفيذ أو الرفض من نفس النافذة.', 'Review the cancellation request and record cancellation or rejection in this dialog.'),
            ('سبب القرار، ومصير الطلب الفعلي إذا استلمه السائق.', 'Decision reason and actual disposition if the driver picked up the order.'),
            ('عند الإلغاء: الاسترداد للمحاسبة إن وُجد تحصيل. عند الرفض: يستمر الطلب وأي فرق تحصيل قائم.', 'After cancellation: accounting handles any collected funds. After rejection: the order and any collection difference remain active.'),
            ('إذا تغير الطلب حدّث النافذة وأعد المراجعة؛ الرفض أو الملاحظة لا يسوّيان فرق التحصيل.', 'Refresh and review again if the order changed; rejection or a note does not settle a collection difference.'),
            source_id=request.get('id'), return_supported=False))
    active=next((a for a in amendments if a['status'] in ('requested','branch_confirmed','offered')),None)
    if active and active.get('expires_at') and active['expires_at'] <= datetime.now(timezone.utc):
        stages.append(step('amendment_expired',reviewer,
            ('انتهت مهلة عرض التعديل؛ ارفضه مع السبب أو اسحبه قبل طلب تعديل جديد.', 'The amendment offer expired; reject with a reason or withdraw it before requesting a new amendment.'),
            ('تحقق من حالة الطلب الحالية وطلب العميل.', 'Verify the current order state and customer request.'),
            ('بعد السحب أو الرفض: يمكن طلب عرض جديد إذا كانت حالة الطلب تسمح.', 'After withdrawal or rejection: request a fresh offer if the order state permits.'),
            ('إذا تغيرت حالة الطلب حدّث النافذة؛ لا تعتمد العرض المنتهي.', 'Refresh if the order state changed; do not apply an expired offer.'),source_id=active.get('id'),return_supported=False))
        active=None
    if active:
        offered=active['status']=='offered'
        stages.append(step('amendment_consent' if offered else 'amendment_review', 'customer' if offered else reviewer,
            ('الحصول على موافقة العميل على الأصناف والإجمالي الجديد قبل الاعتماد.' if offered else 'تحقق من إمكانية التعديل ثم راجع الإجمالي وأرسل العرض للعميل.', 'Obtain customer consent to the items and new total before applying.' if offered else 'Verify feasibility, review the total, and offer the changes to the customer.'),
            ('موافقة داخل النظام أو توثيق قناة التواصل وموافقة العميل.' if offered else 'الأصناف والعنوان المطلوبان وتأكيد الفرع عند الحاجة.', 'In-app consent or the contact channel and recorded customer consent.' if offered else 'Requested items/address and branch confirmation where required.'),
            ('بعد الاعتماد: الفرع والسائق يراجعان النسخة المعدلة؛ فرق الاسترداد يبقى إجراءً مستقلًا.', 'After applying: branch and driver acknowledge the revised copy; any refund remains a separate action.'),
            ('إذا تعذر التعديل استخدم الرفض مع السبب أو سحب طلب التعديل؛ لا تطبق إجماليًا دون موافقة.', 'If the change cannot proceed, reject with a reason or withdraw the amendment; do not apply a total without consent.'),
            source_id=active.get('id'), return_supported=False))
    pending=financial_case and financial_case['status']=='pending'
    if pending:
        shortfall=financial_case.get('kind')=='collection_shortfall'
        outstanding=financial_case.get('pending_amount_halalas',0)>0
        stages.append(step('collection_recovery' if shortfall and outstanding else 'refund_execution' if outstanding else 'financial_case_review', 'accounting',
            ('راجع فرق التحصيل وسجل أي مبلغ استُلم فعليًا من معالجة فرق التحصيل.' if shortfall and outstanding else 'نفّذ الاسترداد الفعلي من مسار معالجة الاسترداد.' if outstanding else 'تحقق من اكتمال الأدلة ثم سجّل إقفال المعالجة المالية.', 'Investigate the collection difference and record any actual receipt through collection recovery.' if shortfall and outstanding else 'Execute the actual refund through the refund resolution path.' if outstanding else 'Verify completed evidence, then record financial case resolution.'),
            ('التحصيل الفعلي، وسيلة الدفع، مرجع العملية والعهدة المستلمة عند النقد.' if shortfall else 'مرجع الاسترداد الفعلي ووسيلة الدفع والمبلغ.', 'Actual collection, tender method, transaction reference and receiving cash custody.' if shortfall else 'Actual refund reference, method and amount.'),
            ('تبقى المراجعة المالية معلقة حتى اكتمال التحصيل أو معالجة مالية معتمدة متاحة بالنظام؛ الإقفال التشغيلي مستقل.', 'Financial review remains pending until collection or a supported authorized financial treatment is complete; operational completion is independent.'),
            ('إذا لم يُحصّل الفرق فلا تقفل الحالة بملاحظة أو تخصمه آليًا من السائق؛ مسار عدم الاسترداد المالي العام لم يُعتمد هنا.' if shortfall else 'إذا تعذر الاسترداد تبقى الحالة معلقة؛ لا تسجل ردًا وهميًا أو تعيد كتابة الفاتورة.', 'If the difference cannot be collected, do not close it with a note or automatically charge the driver; no general non-recovery treatment is certified here.' if shortfall else 'If refund cannot proceed, keep the case pending; do not record a fictitious refund or rewrite the invoice.'),
            source_id=financial_case['id'], return_supported=False,
            limitation=text('الاستكمال مسار مستقل بين المحاسب والمدير المسند؛ قبول الرد لا يعيد فتح التقفيلة ولا يغيّر المستند المالي.', 'Clarification is a separate path between the requesting accountant and assigned manager; accepting a response does not reopen a closure or change its financial source.')))
    if not stages and not cancelled and not delivered:
        role='driver' if order['delivery_status'] in ('assigned','accepted','picked_up','problem') else reviewer
        stages.append(step('order_execution',role,
            ('تابع المهمة الحالية واستكمل الاستلام والتسليم بإثبات فعلي.' if role=='driver' else 'تابع التجهيز وإسناد الطلب إلى سائق متاح.', 'Continue the current task and complete pickup/delivery with actual evidence.' if role=='driver' else 'Follow preparation and assign the order to an available driver.'),
            ('الحالة الفعلية وصور الاستلام والتسليم والتحصيل الفعلي لكل طلب.', 'Actual status, pickup/delivery photos and actual collection per order.'),
            ('بعد التسليم: إرسال تقفيلة السائق واستلام النقد ثم مراجعة المحاسبة وفق حالاتها المستقلة.', 'After delivery: driver declaration, actual cash receipt, then independent accounting review.'),
            ('عند التعثر سجّل مشكلة المهمة أو تذكرة متابعة من الطلب؛ لا تؤكد تسليمًا لم يحدث.', 'For a blocker, report a task problem or an order support ticket; never confirm a delivery that did not occur.'), return_supported=False))
    last=events[-1] if events else None
    return dict(operational_status='cancelled' if cancelled else 'completed' if delivered else 'active',
        financial_status='pending' if pending else 'case_resolved' if financial_case else 'no_case_recorded',
        stages=stages, assignment_recorded=False,
        state_note=text('اكتمال التشغيل لا يعني اكتمال المحاسبة أو مطابقة البنك.', 'Operational completion does not establish accounting completion or bank matching.'),
        last_recorded_event={k:last.get(k) for k in ('id','event_type','actor_id','actor_name','created_at')} if last else None,
        history_source='order_events', history_is_recorded_only=True)


def employee_ticket(ticket):
    decided=ticket['status'] in ('approved','resolved')
    rejected=ticket['status']=='rejected'
    return dict(review_only=True, execution_status='not_established_by_ticket',
        responsible=ROLES['owner'],
        action=text('راجع الطلب وسجل القرار والسبب من نموذج المتابعة.' if not decided and not rejected else 'القرار مسجل؛ تحقق من تنفيذ الإجراء من شاشته المخصصة.' if decided else 'الرفض مسجل مع السبب في سجل المتابعة.', 'Review the request and record the decision/reason in the follow-up form.' if not decided and not rejected else 'Decision recorded; verify execution in the dedicated screen.' if decided else 'Rejection and reason are recorded in follow-up history.'),
        next_step=text('تنفيذ الصلاحية أو تصحيح النقد يحتاج إجراءً مستقلًا من المخول؛ اعتماد التذكرة لا ينفذه.', 'Permissions or cash corrections require a separate authorized action; ticket approval does not execute it.'),
        fallback=text('طلب معلومات إضافية يكون عبر متابعة المالك الحالية؛ لا يوجد إرجاع مسند لقسم آخر من هذه التذكرة.', 'Additional information uses the current owner follow-up; this ticket does not implement an assigned cross-department return.'),
        return_supported=False)

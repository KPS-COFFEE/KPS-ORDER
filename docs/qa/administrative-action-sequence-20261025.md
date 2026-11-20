# Administrative action sequence and responsibility — owner scope addition

Date: 2026-10-25 (Asia/Riyadh).

Status: mandatory addition to the delivery/administration hardening acceptance scope. This document records the requirement; the common presentation and missing routing are not implemented or production-certified by this documentation change.

## Owner requirement

Every administrative matter must show its actual progression. A generic "اتخاذ إجراء" is insufficient. The employee and management must know which department is responsible now, what that department must do, who receives the next step, and where a return goes. Preserve the current screen layout, styling and navigation.

This applies across both brands to order exceptions, cancellation/return and collection cases, driver and manager closures, accounting and receipt corrections, employee requests, vehicle/fuel/maintenance matters, printer escalations, and customer-message follow-up. Financial records and permissions remain scoped to their real brand, branch and operational day.

## Required information inside existing cards and details

| Arabic label | Required meaning |
| --- | --- |
| الحالة الحالية | Actual current state, including a clear distinction between operational completion and financial completion. |
| لدى الآن | Responsible department and the assigned employee when assignment is actually recorded. If unclaimed, show the eligible department as awaiting receipt; never invent an employee. |
| الإجراء المطلوب من القسم | A specific action and the information needed to perform it. |
| الخطوة التالية | The receiving department/person and the condition for transfer after the selected action. Multiple independent pending steps must remain explicit. |
| الإرجاع إلى | A permitted target and whether this is correction before submission/receipt or a linked information/correction follow-up after closure. |
| سبب الإرجاع والمطلوب تصحيحه | Mandatory actionable reason, required correction, and the party that receives the response. |
| آخر إجراء | Actual actor, department, action, time and result; the last actor is not automatically the current responsible person. |
| سجل التسلسل | Ordered history of submission, receipt, decision, return, correction, resubmission and final completion. Each entry retains from/to, actor, time, reason and source reference. |

Use existing permission-filtered history and source records. Where assignment, return or execution tracking does not exist, implement it as an explicit additive domain change; do not simulate a workflow with UI text alone.

## Action labels

Replace vague calls to action with domain-specific wording, for example:

- "إجراء مدير التوصيل: مراجعة البلاغ".
- "تأكيد استلام النقد".
- "إرسال إلى المحاسبة".
- "إجراء المحاسبة: مراجعة الموازنة".
- "إرجاع إلى السائق للتصحيح" only while that canonical return is permitted.
- "طلب استكمال من مدير التوصيل" for a linked follow-up that preserves an accepted receipt or closed report.
- "إعادة الإرسال إلى المحاسبة" after the required information is supplied.
- "بانتظار قرار المالك" only when the current decision actually requires the owner.

An authorized action must be rechecked by the existing server mutation endpoint. Observers see the responsible department and next step without being offered another department's controls. An approval is not displayed as execution until the corresponding execution record exists.

## Closure and accounting example

| Actual stage | Current responsibility and action | Next step / return |
| --- | --- | --- |
| Driver preparing the declaration | Driver records the actual counted cash/card evidence and submits. Existing blind declaration rules remain. | Actual cash recipient and the driver's manager; do not expose expected amounts to the driver. |
| Submitted, cash receipt not confirmed | Named custody recipient confirms actual receipt; manager performs operational review. | Manager can return the submission to the driver only through the current permitted return route and with a reason. |
| Final custody receipt confirmed | Manager completes operational review. | Operational completion and subsequent accounting review. A request for clarification must not undo receipt or rewrite the declaration. |
| Manager shift closed | Authorized accounting department reviews the evidence. | Balance review, cash receipt recording and bank matching retain their independent actual states. |
| Accounting needs a clarification | Assigned operational manager supplies the requested explanation or obtains the driver's relevant statement. | Response returns to accounting with original closure and custody preserved. |
| Financial evidence and differences resolved | Independent authorized accountant completes the applicable financial approval. | Clearly show financial completion and retained history; owner has oversight within existing scope. |
| Exceptional approval required | The actual authorized approver handles the specific exception. | Execute the allowed adjustment or return with a reason. Routine work must not be routed to the owner by default. |

Cash receipt and balance approval may occur in either order. "الكاش مستلم" must not imply "الموازنة معتمدة", "مطابق للبنك", or "مقفل ماليًا". Waiting on bank evidence is displayed as that external dependency with the responsible accounting follow-up.

After final receipt or immutable closure, a return is a linked clarification/correction task where supported, not a backward financial state transition. Do not reopen a day/month, delete receipt evidence, reassign accepted cash to the driver, or overwrite original declarations to create a convenient visual sequence. If a post-finalization correction route is not implemented, show the true restriction and keep that scope item open.

## Existing domain boundaries to preserve

- Managers operate, receive cash and supply factual explanations. Accounting owns financial differences and settlement; existing exceptional owner approvals remain explicit.
- Current driver closure return is permitted only for a submitted closure before confirmed final custody. Its canonical state becomes closing; the display must explain that it was returned, by whom and why.
- A rejected cancellation request does not settle a collection shortfall. Operationally delivered and financially pending may be simultaneous.
- Returning a free item has zero financial effect and does not remove a separate collection difference.
- Employee ticket approval is currently review-only. It must not imply that a permission was granted or money moved. Department routing requires permitted assignment, not expanded reviewer rights.
- Printer escalation currently records an action/reason; a named recipient must come from a real assignment or approved routing policy.
- Customer-care/privacy transfer restrictions and branch/brand access remain authoritative. No unrestricted "return to any department" control.
- Maintenance/fuel/vehicle workflows use their real assigned shift, department and delegated capability.
- Show a person only when recorded assignment or a deterministic scoped source establishes responsibility. Do not hard-code employee names.
- New and historic records must distinguish actual recorded history from events that were never captured. Do not fabricate past handoffs.

## Implementation direction

Add a common read-only presentation adapter to existing serializers and reuse the current card/modal components. Derive current action, authorized next targets and blocking reasons from each domain's real state, permissions and events. This adapter must not become a duplicate workflow engine.

Any genuinely missing handoff/return/response route requires its own explicit server transition, concurrency/version validation, idempotency, scoped authorization and append-only history linked to the original transaction. A UI descriptor never grants authority.

The owner can inspect the whole authorized sequence in the existing oversight views. Other employees see only their permitted scope. Keep customer-facing and driver displays simple and preserve existing privacy restrictions.

## Acceptance required before calling this scope complete

1. Every administrative category in the scope inventory has a verified mapping for current responsibility, action, next step, return and completed/no-action state.
2. A submitted item shows the actual receiving department, not a generic "اتخاذ إجراء".
3. Return requires a permitted destination and meaningful reason, retains the original history, and identifies the party that receives the correction.
4. Correction/resubmission changes the current responsibility only after a successful server transition; repeated submission produces no duplicate effects.
5. Closed/received financial records remain immutable through information requests and later corrections.
6. Cash-before-balance and balance-before-cash both display the true remaining step; unresolved financial cases never appear financially complete.
7. Approval-only employee tickets are distinguishable from executed permission/cash changes.
8. Role, branch and brand isolation; revoked authority; stale state; concurrent transitions; and unauthorized return targets are verified.
9. Existing history and newly recorded handoffs remain attributable to actual actors. Historic missing data is explicitly limited.
10. AR/EN and mobile checks confirm the guidance fits existing cards/dialogs without redesign, raw keys, misleading ownership, or exposure of driver-hidden financial data.
11. End-to-end examples cover order issue through accounting, driver return before cash receipt, accounting clarification after receipt, independent cash/balance completion, employee review versus execution, and printer/maintenance/customer-care routing.
12. Publish and verify the exact tested release before marking this requirement implemented on the live sites.

## Relationship to existing work

- PR #102 adds actual collection/shortfall and free-item-return controls; this document adds mandatory administrative progression visibility and missing handoff acceptance. The full progression scope is not supplied merely by those collection changes.
- PR #90 provides independent financial-evidence checks; it is not a universal routing/return workflow.
- PR #92 provides employee activity history and PDF; it does not by itself determine who owes the next action.
- Preserve the current owner-approved UI structure throughout all three workstreams.

Reference source reviewed: production commit b26fdd197a08c8561ac4c59668e63b7d5d583ff7 and PR #102 functional head d0fe44f5b248a30e4f213e3750fe2e63e0263c98.


## Owner addition — built-in solutions and alternatives

Every required step above must also offer its supported solution, the authorized executor, necessary evidence, the next party, and a safe alternative or an assigned tracked follow-up when the first action cannot succeed. Routine incidents must be manageable inside the system without repeatedly asking the AI assistant or the owner.

See [Built-in resolution paths](administrative-resolution-paths-20261025.md) for the concrete order, payment, closure, access, device and unknown-case matrix. A handoff is not a resolution; preserve responsibility until acceptance and distinguish operational completion from verified financial treatment. This additional scope remains pending implementation and release verification.

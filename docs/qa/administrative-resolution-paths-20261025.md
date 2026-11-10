# Built-in resolution paths — owner scope addition

Date: 2026-10-25 (Asia/Riyadh).

Status: mandatory implementation and acceptance requirements. This is the resolution specification accompanying the administrative action sequence. It does not certify that the actions, fallbacks or escalation routes below are implemented on the live sites.

Owner request: "مع وضع الحلول، بدون كل شوي ارجع لك، لازم كل شي له حل".

## Intended operating outcome

The employee must find the supported solution inside the current transaction screen: the problem, the next safe action, who may perform it, the evidence required, and the next recipient. If that action cannot succeed, provide a real alternative or an assigned, tracked follow-up.

Known problems require verified resolution paths. An unfamiliar problem requires a durable case with a responsible department/person, a permitted next action and an escalation path. Neither a generic error message nor transfer to another department counts as resolution. Routine work must not depend on asking the AI assistant or the owner what to do.

Keep the existing layout, styles, navigation, driver simplicity and brand/branch permissions.

## Required resolution card

Use existing cards/dialogs. Show:

- "سبب التعثر": the specific business or technical blocker in plain language.
- "الحل المتاح الآن": the safe action supported by the current state.
- "من ينفذه": the responsible authorized department and actual assignee where known.
- "المطلوب لإكمال الحل": evidence or missing fields, with their source.
- "بعد التنفيذ": the verified resulting state and the next responsible party.
- "إذا تعذر الحل": a permitted alternative, including why it applies.
- "الإرجاع / الاستكمال": the target, exact information needed, reason and recipient of the response.
- "حالة المعالجة": pending, awaiting evidence/receipt/bank, resolved operationally, or financially completed as actually supported.
- "سجل المعالجة": attempts, results, actor, time, reason, evidence and handoffs.

Never offer a generic "اتخاذ إجراء" as the only guidance. Never present unsupported actions as enabled buttons. An unavailable action must show its blocker and the supported follow-up. Guidance is derived from server state and authority; it does not grant authority.

## Implementation status vocabulary

"Existing" means an underlying action is present in the reviewed production code, not that the common guidance and all fallback paths are certified.
"Draft #102" means functionality in the unpublished candidate.
"Required" means missing or incomplete routing, behavior or verification.

Production source reviewed: b26fdd197a08c8561ac4c59668e63b7d5d583ff7.
Draft source reviewed: 7613e5ce8fb3ac4b495a9c8227b3522b87257307 (functional changes based on d0fe44f5).

## Resolution matrix — orders and delivery

| Trigger | First action and authority | Alternative / next responsible party | Evidence and completion condition | Reviewed availability |
| --- | --- | --- | --- | --- |
| Duplicate-looking orders | Delivery manager compares the actual order identities, source and payment state; identify the intended duplicate before cancellation. | If invoiced/collected, use operational cancellation and the financial case; accounting handles actual refund. | Preserve both records, reason and monetary lineage. Repeated request with the same idempotency key must not create another order. | Existing actions; unified guided route required. |
| Item unavailable or wrong items | Authorized order amendment with branch confirmation and customer agreement to the revised total. | If the alternative is rejected, use cancellation appropriate to payment/pickup state. | Approved revision, actual customer acceptance and branch/driver acknowledgement; no silent snapshot/price change. | Existing actions; guided fallback required. |
| No eligible driver | Manager sees actual eligibility blockers and can resolve the applicable shift, branch, period or inspection issue. | Request an authorized driver-support transfer; keep a responsible manager until a real assignment is accepted. If service is impossible, communicate and use the allowed delay/cancellation outcome. | Eligible driver/shift and accepted handoff; no fake shift under another driver. | Eligibility/support exist; actionable exclusion reasons and complete fallback required. |
| Wrong assignment before pickup | Manager reassigns with a reason, preserving previous assignment history. | If no eligible replacement, keep it in the responsible manager's pending queue and use driver support. | Correct eligible assignment and source history. | Pre-pickup immediate reassignment is in draft #102; live transfer behavior differs. |
| Driver breakdown after pickup | Manager requests a real handover; the receiving driver confirms physical receipt. | Collected funds cannot be moved by ordinary task handover. Route custody/attribution to the supported financial process. | Physical handoff and actor/time evidence; original money remains traceable. | Underlying handover exists; guided financial fallback incomplete. |
| Customer unreachable | Record actual contact attempts and ask the responsible manager for the operational decision. | Next contact attempt, address clarification, or permitted cancellation with documented disposition of the goods. | Attempts, decision, actual goods disposition and separate financial state. | Issue/cancellation actions exist; tracked next attempt and escalation required. |
| Wrong customer location | Use the supported location change or approved order amendment according to current assignment/dispatch state. | If coverage/price/branch changes, obtain the required approval/revision or use the valid cancellation/new-order route. | Old/new location, coverage decision, customer agreement and driver revision acknowledgement. | Existing actions; state-specific guidance required. |
| Delivery occurred but screen is stuck | Authorized manager uses evidenced delivery recovery; show each missing prerequisite as a direct action for its owner. | Missing evidence stays under verification; closed shifts need a supported linked correction instead of reopening historical records. | Actual pickup/delivery evidence, valid time, authority and truthful financial state. | Recovery exists; documented-shortfall parity in manager recovery remains missing in draft #102. |
| Completed order belongs to the wrong driver | Use the existing owner-authorized attribution correction only when its narrow safeguards hold. | After custody/closure/refund/settlement, request a separately authorized accounting correction linked to the original. | Original invoice/collection preserved; balanced custody effects; correct effective attribution. | Restricted correction exists; complete post-closure resolution route required. |

## Resolution matrix — money, returns and financial treatment

| Trigger | First action and authority | Alternative / next responsible party | Evidence and completion condition | Reviewed availability |
| --- | --- | --- | --- | --- |
| Positive partial collection | Driver records the actual collected cash/card pieces and reason. Accounting receives the documented remaining balance. Operational delivery can be evidenced separately. | Recover the outstanding money through the authorized route; if not recoverable, obtain a distinct authorized financial decision and its actual execution. | Original invoice preserved, actual receipt, remaining balance and recovery/disposition evidence. A note does not clear the amount. | Positive partial collection/recovery in draft #102; non-recovery financial outcome is not implemented there. |
| Delivered with zero money received | Record the actual delivery incident and open a zero-collection financial claim, without inventing a receipt or marking the order paid. | Accounting pursues collection or a separately authorized financial outcome; the operational manager supplies factual evidence. | Delivery evidence, actual zero collection, outstanding amount, accountable follow-up and eventual financial execution. | Required. Draft #102 positive-amount collection and shortfall checks exclude zero. |
| Multiple card receipts or cash/card split | Record individual actual pieces and genuine references; calculate the total using the existing monetary rules. | Uncertain or disputed card payment goes to accounting reconciliation; never substitute a guessed receipt. | Each payment's source, amount and reference; no double counting. | Multiple card pieces in draft #102; complete disputed-payment follow-up required. |
| Free gift returned | Record the exact free line, quantity, recipient and reason with zero financial effect. | Any actual collection shortage opens a separate financial case. | Free-line identity, valid remaining quantity and receiving evidence; invoice total unchanged and no cash refund invented. | Draft #102. |
| Paid item partially returned | Authorized reviewer determines the actual returned lines and their net charged value and uses the permitted refund/fiscal path. | A separate authorized compensation decision is distinct from a line refund; route missing evidence back to the responsible manager. | Remaining quantity/value and collected balance limits, actual refund source, fiscal evidence where applicable. | Refund action exists; net selected-line valuation and complete guidance require verification/extension. |
| Cancellation with invoice/collection | Manager ends the operational journey using the approved route and records what happened to the goods; accounting owns monetary treatment. | Pending provider payment or unavailable cash custody stays assigned for verification/refund; cancellation alone does not assert money was returned. | Reason, actual disposition, collections, real refund and financial case completion. | Backend route exists; full customer-request UI integration in draft #102. |
| Missing item / quality complaint | Create linked support follow-up and use an authorized replacement/compensation outcome when applicable. | Refer the decision to an already authorized approver or deputy; owner only where the policy reserves the decision. | Original order, specific issue, approval and actual replacement/refund execution. | Underlying support/complimentary approval exists; common fallback/execution closure required. |
| Shortage cannot be recovered | Accounting prepares a specific proposed financial treatment and effect, then obtains the authority required for that treatment. | Keep a named pending decision with follow-up while evidence or approval is missing; do not label the case settled. | Distinct decision, authority, reason, amount, execution record and impact on reporting. | Required; draft #102 only supports real recovery. This specification grants no new write-off, discount, compensation or payroll-deduction authority. |

Manager operations, accountant financial treatment and reserved owner approvals remain separate. Never create automatic salary deductions, write-offs, discounts, refunds, transfers or exemptions to make a case disappear.

## Resolution matrix — closures, receipts and accounting

| Trigger | First action and authority | Alternative / next responsible party | Evidence and completion condition | Reviewed availability |
| --- | --- | --- | --- | --- |
| Driver cannot submit closure | List each blocking task/transfer/receipt with its identifier, reason, responsible actor and valid action. | Evidenced recovery for an actually delivered task; valid cancellation for a task not delivered; no bulk fictional delivery. | Each blocker completed or resolved through its supported route. | Guards/actions exist; unified actionable blocker list required. |
| Driver unavailable or device failed | Manager can use the currently authorized on-behalf closure with actual evidence and mandatory reason. | Request missing declaration/evidence from the driver; keep unsupported amounts unconfirmed. | Actual counted money/card evidence, custody receipt, actor and reason; preserve owner audit notification. | Existing constrained action. |
| Manager cannot close | Show the exact open driver shifts, tasks, custody or handover preventing closure. | Supported manager handover with actual receiving acceptance; monetary custody remains independently verified. | All applicable operational closure guards satisfied. | Guards/handover exist; guided alternative list required. |
| Declaration error before final cash receipt | Manager returns the submission to the driver with the exact correction required; driver resubmits. | If the return route is blocked, show the real custody/state blocker and route to the authorized correction path. | Original declaration and return reason retained; driver supplies corrected actual data. | Return exists. Preserve the approved removal of manager overwrites in draft #102. |
| Error after final cash receipt | Accounting opens the supported linked correction; where the recorded received-cash amount changes, use the required independent owner approval. | Request evidence from the actual recipient/manager; do not reopen the driver's declaration or silently move custody back. | Original receipt/declaration preserved and a separately authorized correction record. | Constrained cash-correction ticket exists; general follow-up/completion required. |
| No cash recipient is available | Identify the permitted recipient under the current location/shift policy and request receipt. | Arrange an already authorized deputy or approved delegation; remain awaiting actual receipt with real custody unchanged. | Confirmation by the actual permitted recipient; no artificial acceptance even for zero. | Receipt rules exist; tracked unavailability/deputy routing required. |
| Accounting needs more information | Return a linked clarification task to the responsible manager, specifying each missing fact; manager obtains the driver's relevant statement if needed. | Reassign to an authorized deputy if unavailable, retaining the original reviewer as response destination. | Response/evidence returns to accounting and is accepted; closed financial source remains unchanged. | General cross-department clarification/return route required. |
| Cash and card/bank totals differ | Preserve actual declarations and receipts; accountant investigates order collections, refunds, custody, device/period and bank evidence. | Request a specific missing statement or process a separately authorized correction; never overwrite an amount just to match totals. | Confirmed explanation and authorized financial effect; an unexplained amount stays pending. | Underlying reviews/revisions exist; guided diagnosis and all resolution alternatives incomplete. |
| Bank posting is delayed | Show reviewed device balance and pending bank verification as separate facts. Cash receipt remains independent. | Track the bank/provider query with the responsible accountant and permitted follow-up deadline. | Actual supporting evidence; manual entry of a bank amount alone is not proof of external verification. | Independent accounting stages exist; complete external-evidence follow-up required. |
| Closure evidence is missing/inconsistent | Block financial approval with an exact missing/mismatched-evidence list. | Preserve operational closure and real cash receipt where permitted; assign evidence retrieval/investigation to its actual source. | Independent evidence check passes, or a separately supported and authorized later correction is executed. | PR #90 is pending; full guided flow required. |
| Error found after day/month finalization | Open a later correction proposal linked to the immutable original and preview its permitted reporting effect. | Keep a tracked investigation/decision while evidence or authority is missing. | Separately authorized executed correction; no rewriting the finalized period. | Immutability guards exist; general later-adjustment flow required. |

Use actual policy deadlines, not newly invented blanket time limits. Cash-before-balance and balance-before-cash must both remain supported. Driver-facing guidance must retain blind declarations and must not reveal hidden expected totals/variances.

## Resolution matrix — access, devices and technical failures

| Trigger | First action and authority | Alternative / next responsible party | Evidence and completion condition | Reviewed availability |
| --- | --- | --- | --- | --- |
| OTP delayed/invalid/expired | Safe cause-specific guidance and a resend countdown based on the server's actual limits. | Restricted support incident tied to the attempt; authorized identity support investigates provider/configuration/account problems. | Successful valid verification or a resolved support issue; no OTP bypass/account enumeration. | Diagnostics and limits exist; integrated self-service support/countdown required. |
| Session expired or permission changed | Reauthenticate or refresh state, then recheck authority before offering the action. | Authorized permission review or reassignment of pending work to an already eligible employee. | Current identity and scope; do not revive suspended access or silently grant permission. | Core controls exist; guided pending-work routing required. |
| Shift cannot open | Show the actual blocker: open existing shift, manager shift, period, branch, vehicle/fuel inspection, or stale data. | Manager handles a permitted operational prerequisite; technical support receives an identified data/compatibility incident. | Correct person's valid shift; no opening another employee's account as a workaround. | Underlying guards/fixes exist; complete diagnostic guidance required. |
| Response lost / timeout / repeated click | Show "نتيجة العملية لم تُؤكد"; inspect server result and reuse the same persisted operation key after fresh authority checks. | One deduplicated support incident remains linked to that operation if outcome is still unknown. | Confirmed original result or safely repeated idempotent operation; no duplicate sale, collection or refund. | Existing idempotency protection; result lookup/guided fallback requires completion. |
| Printer offline or print outcome unknown | Check the linked device and actual paper output, then use permitted confirmation/reprint with a reason. | Assign the fault to the actual authorized printer responder; use another approved in-scope printer only where supported. | Physical output or a tracked unresolved fault; reprints retain provenance. | Print alert actions exist; recipient assignment/complete fallback required. |
| Camera/upload fails | Show supported permission, file and network remedies; allow a real image through a supported input path. | Authorized operations/support follow-up; use only an already approved alternative evidence path, otherwise keep evidence missing. | Real stage-appropriate proof; no dummy image or silent evidence waiver. | Existing upload paths; guided alternatives and escalation need verification/completion. |
| GPS unavailable/inaccurate | Show permission/retry guidance and age/accuracy of the last real reading. | Manager sees tracking unavailable and follows the actual operational status; customer address correction uses the supported map/address route. | Genuine tracking evidence or explicitly unavailable tracking; manual location is not relabeled GPS evidence. | Partial; common guidance/follow-up required. |
| Technical service outage | Clearly distinguish unsent, submitted-unconfirmed and confirmed operations. Group related failures under one incident. | Authorized technical responder and an established independent support channel where configured; recheck pending results after recovery. | Service recovery and reconciled outcomes; no false confirmation while the service is down. | Incident management and independent fallback need implementation/verification. |
| Employee request approved but not executed | Show the approval and the actual required executor/action separately. | Authorized follow-up to the executing department; preserve owner-only review where current policy requires it. | Actual permission/data/cash change reference if allowed, not merely an approved ticket. | Review-only tickets exist; execution linkage/routing required. |
| Message/complaint sent to wrong team or unanswered | Assign an eligible responder, reply and use only permitted route transfers with the reason and full context. | Eligible deputy/escalation under approved coverage policy; retained responsibility until new ownership is accepted. | Confirmed next ownership and substantive response; a financial matter remains separately pending. | Team/assignee/transfer exist; accepted handoff and timed deputy escalation required. |
| New/unclassified problem | Create one durable linked case from the transaction with safe context, a responsible department and an actual next action. | Categorize, request evidence, use an existing solution, or escalate to the authorized decision maker/technical responder while responsibility remains visible. | Demonstrable outcome or an explicitly authorized alternative disposition; no generic "تمت المتابعة" closure. | Some "other" tickets exist; common owned fallback and resolution verification required. |

All recovery paths preserve current geographic access restrictions and owner-reserved exceptions where applicable. This requirement does not enable or certify the separate administrative geographic-access change.

## Ownership and escalation contract

1. A problem remains with a visible responsible department/person. Transfer is not completion.
2. The current party retains follow-up responsibility until the authorized receiving party accepts, unless access revocation requires safe reassignment under existing policy. Never orphan the case.
3. Staff who cannot resolve it must have a supported request-for-information, permitted alternative or escalation—not a dead-end message.
4. Return specifies the missing information, who supplies it and who receives the response. Supply of the response does not itself imply approval.
5. An unavailable responder triggers the configured eligible deputy/coverage route. Escalate to the owner only for a decision reserved to the owner or exhausted authorized coverage.
6. Customer privacy/care routing and financial/branch/brand boundaries remain intact. Technical support does not acquire accounting approval.
7. Apply current configured deadlines and record overdue handling. Do not create notification loops or repeated incidents for retries of the same underlying operation.
8. Every attempt and handoff retains actor, time, reason, previous/current state, evidence and the source transaction's version.

## Concrete candidate gaps that must not be hidden by guidance

- Zero-collection delivery is excluded by the draft's positive collection amount and positive-shortfall predicates. Add an explicit non-receipt incident/claim route; do not fake a positive receipt to satisfy validation.
- Normal driver delivery accepts a documented partial shortfall in draft #102, but validate_delivery_resolution for authorized manager recovery still requires payment in full. Align both routes with the same verified evidence/financial-case policy and preserve all pickup/time/photo/role guards.
- Draft #102 recovery is real receipt recovery only; it is not a completed non-recovery/write-off/discount/liability workflow.
- Returned-item monetary valuation needs its own verified policy; blocking an all-free refund does not certify mixed/discounted paid-line refund valuation.
- Existing chat transfer clears the assignee immediately. Accepted ownership and deputy escalation require explicit implementation.
- Post-receipt clarification and post-finalization financial correction must preserve source records; the old pre-receipt return endpoint is not a universal solution.
- Existing employee ticket approvals and printer "escalated" labels do not establish actual execution or named receiving ownership.

## Verification required for completion

For each supported case, test the primary action, an unavailable/failed primary action, the permitted alternative, the return/resubmission path where applicable, and the final closure proof.

Required combined witnesses include:

- Partial collection and zero collection, normal driver completion and authorized manager recovery, with operational/financial states remaining truthful.
- Free return, mixed/discounted paid-item return, repeated return and no double refund.
- Driver declaration return before receipt, clarification after receipt, and later correction after finalized accounting.
- Cash/balance in either order, delayed bank evidence, and an unresolved difference that blocks financial completion.
- Existing actor unavailable, revoked authority, receiver refusal/no acceptance, and fallback escalation without granting new rights.
- Timeout after server commit, reload/re-authentication, same-key retry and concurrent/stale-version requests with no duplicate effects.
- Unknown issue and technical outage, ensuring visible ownership, safe context, no unsupported completion and verified recovery.
- Driver privacy, current brand/branch/role boundaries, AR/EN mobile display and unchanged layout/navigation.

No case may be counted as solved solely because a button was clicked, a note saved, a transfer requested or a ticket marked reviewed. Verify the actual resulting operation and financial evidence before closing.

The release remains incomplete for this scope until the implemented paths pass these witnesses and the exact tested revision is verified on production. Documented requirements and focused tests of unrelated paths are not a completion certificate.

## Companion documents

- [Administrative sequence and responsibility](administrative-action-sequence-20261025.md)
- [Delivery workflow controls checkpoint](delivery-workflow-controls-20261025.md)

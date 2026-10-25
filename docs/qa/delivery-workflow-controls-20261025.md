# Delivery workflow controls — implementation checkpoint

Status: local review candidate, not pushed, merged, or deployed. Base: `093f354` on `agent/kps-order-emergency-v4`.

## Owner constraints

Keep existing screen structure, styling and navigation. Drivers record actual per-order cash/card payments, including multiple card payments. Their shift declaration is blind to expected totals. Delivery managers operate, receive cash, and report exceptions; accounting reviews financial differences. Every mutation retains actor, reason and time. A free item return must never reduce the invoice or invent a refund.

## Implemented in this candidate

- Multiple card payment pieces are stored individually; sum validation and the order lock prevent overcollection. Migration 105 preserves historical collection rows and legacy uniqueness while allowing separate new payment pieces.
- A driver can record a positive partial actual collection with an explicit shortfall reason. The original invoice and gift values remain unchanged, payment remains unpaid, and a pending accounting case opens. Actual delivery evidence can complete the operational task without declaring the order fully paid.
- Example acceptance fixture: 25.00 invoiced, 19.50 actually received, 5.50 pending. Rejecting a cancellation request does not settle that difference.
- Pending collection cases prevent financial approval of the related driver/manager/day/month while permitting operational closure and actual cash receipt. A note cannot close an unpaid shortfall.
- Accountant-only recovery records require a real receipt reference and current order version. Cash requires an authorized open manager custody; card recovery never enters cash custody. Historical driver submissions are preserved. This candidate supports actual recovery, not write-offs, discounts, payroll deductions or liability allocation.
- Free item return records identify the snapshot line, quantity, actual receiving party and reason, with zero financial effect. Duplicate/excess quantities are rejected. Positive monetary refunds naming only free lines are rejected.
- Existing payment-support tickets refer documented shortages to accounting and cannot be marked financially solved while their case remains pending.
- Before pickup, the current UI reassigns immediately with a reason and retains the old task. After physical pickup, existing recipient acceptance remains mandatory. Collected tasks cannot transfer their financial custody.
- Manager overwrite of submitted driver cash/card totals is disabled. Returning an unreceived submission for driver correction remains available.
- Existing cancellation controls use the operational-cancellation/accounting route and record the disposition of a picked-up order. Rejection guidance explicitly says it does not change the invoice or collection.
- Existing styling and layout files are unchanged. Fields/actions and short instructions are added inside the current dialogs; unreachable manager refund/correction controls are removed.

## Validation performed

- 26 local Python checks passed (plus 7 subtests), covering blind driver views and existing reconciliation behavior.
- Node AR/EN collection contract checks passed for legacy cash/card/split and new multiple-card/shortfall paths; closure identity checks passed.
- Python compilation, both edited JavaScript syntax checks, and `git diff --check` passed.
- 12 new PostgreSQL acceptance cases collect successfully. These are NOT execution passes.
- PostgreSQL 18 CI workflow prepared, including existing collection, accounting, support, cancellation, permission and browser regressions.

## Blockers and remaining approved work

The automatic approval reviewer rejected publishing this candidate to the public GitHub repository without explicit approval for that destination. The remote candidate branch does not exist. No workaround or alternate publishing route was attempted. Local PostgreSQL initialization was unavailable because the runtime disallowed switching to an unprivileged database user; no production database was used.

Database tests, browser acceptance, concurrency, migration preservation/rollback, broad security and release checks remain mandatory before production. No accounting integrity or production readiness certification is claimed.

The broader approved scope still includes one aggregate cross-brand shift declaration/receipt without inventing per-brand cash allocations, configurable AM/PM staffing and duration, and complete state-specific operational guidance. Those changes are not implemented by this candidate. Existing per-brand closure forms remain until the pooled physical custody and financial reporting contract is implemented and tested; simply splitting an actual aggregate shortage across brands would be incorrect.


## Owner addition — visible administrative sequence (2026-10-25)

Mandatory additional acceptance: every administrative matter must identify its current responsible department/person, the exact action needed, the next receiving party, permitted return destination, required correction and complete actor/time/reason history inside the current screens.

See [Administrative action sequence and responsibility](administrative-action-sequence-20261025.md). This is an added scope/acceptance requirement, not an implemented or deployed feature. Preserve the distinction between operational closure and financial completion, existing role/branch/brand authority, and immutable declarations/receipts after custody acceptance. Accounting requests for clarification return to accounting after the responsible party responds; they must not reopen a finalized financial record.

Checkpoint clarification: the earlier local-only statements above record the preparation phase. PR #102 was subsequently published as a draft, and the dedicated PostgreSQL/browser acceptance run [40169620231](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/40169620231) passed 141 tests on functional head d0fe44f5b248a30e4f213e3750fe2e63e0263c98. That focused result does not certify this newly added administrative-sequence scope or the complete release. The PR remains unmerged and unpublished to production at this checkpoint.

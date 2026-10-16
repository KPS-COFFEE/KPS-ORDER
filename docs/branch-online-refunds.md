# Disabled sandbox refund foundation

No live or merchant activation is authorized by this code. The new
`KPS_BRANCH_PAYMENT_REFUNDS_ENABLED` flag defaults OFF, and both existing sandbox
flags plus seller-scoped test credentials are also required. No credentials are
configured by a migration. Existing Delivery refunds are unchanged.

Owner-only routes (existing session, CSRF, brand scope and idempotency rules):

- `POST /api/branch-payments/orders/{order}/refunds`: authorize an exact amount
  and nonblank reason against the current order version and verified balance.
- `POST /api/branch-payments/refunds/{refund}/execute`: GET the current provider
  balance, then commit a single-submit claim, send at most one refund POST, and
  independently GET the outcome before recording a verified refund.
- `POST /api/branch-payments/refunds/{refund}/reconcile`: GET only after a timeout
  or process restart. Never repeat an uncertain refund POST.

Partial and full refund amounts cannot exceed the confirmed online receipt less
verified refunds. One unresolved authorization per order prevents overlapping
financial effects. Authorization freezes further fulfillment of an unfinished
order. Ledger events include authorizer, immutable amount/reason, original
attempt and provider payment reference through its immutable relation. Verified
refunds cannot be overwritten or deleted. Completed order records are not
rewritten; their current refund totals are projected from the separate ledger.

All provider calls use the same confirmed seller context and validate UUID,
amount, SAR, metadata, test mode, status and cumulative refunded amount. Raw card
data, response bodies and secrets are never persisted. No undocumented provider
idempotency header is assumed. A matching cumulative refund already present at
the provider is reconciled instead of submitted again.

The new financial table is included in scoped business exports, full backup and
preservation manifests. It is not inserted into Delivery cash/card custody.
Fiscal-document orders are blocked pending the matching fiscal credit-note flow.

The bilingual owner order workspace shows authorized requests and their status.
Authorization and provider submission are separate actions. Uncertain requests
offer verification only; disabled configuration provides no submission control.

Still open: branch accounting screens, refund
webhook processing, automatic recovery of unresolved refunds, fiscal credit-note
acceptance and real Moyasar sandbox acceptance. A failed read-only preflight
leaves the unsent authorization retryable. Once the submission claim commits,
uncertain outcomes permit GET reconciliation only, never a repeated refund POST.

Provider contract inspected 2026-10-16:
https://docs.moyasar.com/api/payments/05-refund-payment

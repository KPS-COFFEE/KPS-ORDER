# Sandbox payment notification foundation

This is prepared code, not merchant acceptance or permission to activate. All
new flags default OFF. Existing Delivery collection and historical records are
unchanged. No production provider account or endpoint has been configured.

## Contract

`POST /paymenthooks/moyasar` is a provider endpoint outside the browser `/api`
namespace. Browser session/CSRF rules are unchanged. It requires both
`KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED=true` and
`KPS_BRANCH_PAYMENT_SANDBOX_ENABLED=true`, plus the exact brand/seller credential
prefix from `payment_gateways.credential_prefix(...)`, suffixed with
`_TEST_WEBHOOK_SECRET`. The secret must be 32–512 characters and is compared in
constant time. It is configured by the operator in Moyasar, not inferred from
an account name or a browser parameter. Never use a live secret here.

The documented Moyasar `secret_token` authenticates notification delivery; it is
not an HMAC signature and it does not prove payment. Top-level `live` must be
the JSON boolean false. The payment UUID must resolve to a persisted sandbox
attempt with the same immutable brand, branch, and seller scope as its order.

Paid/captured notifications are durably queued before returning 202. Duplicate
event IDs for the same attempt do not create additional work. Other authenticated
event types return `queued:false`: in particular, `payment_verified` does not
mean money was collected. Refund events are not implemented by this worker.

Only event ID, type, attempt, scope, receipt time and worker progress are stored.
Raw notification bodies, token, card fields and account names are not stored or
logged. The inbox is included in full private backup and preservation manifests,
but excluded from business exports.

## Verification and recovery

The existing service starts the worker only with the flags enabled (not during
tests). Workers claim one row with `FOR UPDATE SKIP LOCKED`, commit a 60-second
lease, then independently GET the payment through the existing seller-scoped
sandbox adapter, outside a database transaction. The adapter verifies payment
ID, metadata, amount, currency, mode, paid status and zero refunded amount.

The shared ledger application locks order before attempt. A receipt and its
paid transition/audit commit atomically, exactly once, even when customer
reconciliation and multiple notifications race. System processing records a
null system actor; it never impersonates a customer. Expired or cancelled-order
success remains `review_required` and does not resume fulfillment.

Verification failure leaves the event pending with backoff from 60 seconds to one hour.
Crashes recover after lease expiry. A crash after financial commit but before
inbox acknowledgement safely replays the terminal attempt. Database failures
do not acknowledge undurable notifications. Only a fixed error code is logged.

## Acceptance still open

The tests use synthetic notifications and mocked provider GETs on isolated
PostgreSQL; they are not actual Moyasar sandbox acceptance. Merchant onboarding,
confirmed seller ownership, event subscription, real provider delivery, refund
and decline/expiry retry workflows, uncertain checkout-session recovery, normal
new-order payment gating, and complete end-to-end acceptance remain open.

With the separate OFF-default `KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED=true`,
the worker also queues pending attempts whose checkout claims are at least 30
seconds old, including interrupted or uncertain claims. Each attempt gets one
durable `reconciliation_requested` work item. This sweep only performs provider
GET verification: it never recreates checkout credentials or repeats a charge.
It can run with webhook reception disabled. No live payment or public-service
activation is implied. Recovery of an uncharged, unusable checkout intent still
requires a separate safe renewal workflow.

Provider reference inspected 2026-10-16:
https://docs.moyasar.com/api/other/webhooks/webhook-reference

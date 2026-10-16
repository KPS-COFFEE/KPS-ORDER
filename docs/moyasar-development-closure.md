# Moyasar development closure checkpoint

Scope: existing branch/PR only. No deployment, merchant selection, live activation,
Render change, DNS change, SMS change or operational data mutation is authorized.

## Implemented, subject to exact-candidate CI results

- Cashier acceptance precedes checkout; amount/currency and seller are server-owned.
- Secure provider checkout, independent verification, durable receipts and payment
  reconciliation; browser redirects and notifications alone never prove payment.
- Safe renewal only after proof that an exposed intent is unusable, or the old
  secret was never exposed. The checkout/order-lock race is covered by a regression.
- Authorized refunds use one durable submission claim and independent GET proof.
  GET-only leased recovery handles lost responses without a second refund POST.
- Test/live modes are isolated in attempts, receipts, provider keys, callbacks and
  reports. Existing test records are unchanged by migration 070.
- Live requests fail closed without exact-release acceptance and owner approval,
  seller-specific credentials and documented merchant ownership attestation.
  Defaults remain OFF. No actual provider request is made by contract simulations.

## Prepared but disabled: live-mode contract

`KPS_BRANCH_PAYMENT_MODE` defaults to `test`. Live mode also requires
`KPS_BRANCH_PAYMENT_LIVE_ENABLED=true`, exact current revision in
`KPS_BRANCH_PAYMENT_LIVE_ACCEPTANCE_SHA` and
`KPS_BRANCH_PAYMENT_LIVE_OWNER_APPROVAL_SHA`, and the existing checkout/refund,
readiness and owner branch/brand service gates. Selecting a mode is not activation.

Seller-scoped names use `payment_gateways.credential_prefix(...)`. Live suffixes:
`_LIVE_SECRET_KEY`, `_LIVE_PUBLIC_KEY`, `_LIVE_WEBHOOK_SECRET`,
`_LIVE_ACCOUNT_VERIFIED`, `_LIVE_ACCOUNT_ID`, `_LIVE_OWNERSHIP_EVIDENCE`.
The last three record an operator's reviewed evidence; they do NOT independently
prove account ownership to Moyasar. Do not set them before external verification.
No merchant or legal invoice issuer is inferred from branch names.

Live-mode browser/HTTP tests use synthetic keys and intercepted provider traffic.
They do not certify actual sandbox or live transactions, bank settlement, or
merchant ownership. Real acceptance requires authorized credentials and evidence.

## Explicit unresolved acceptance cases — closure is BLOCKED

1. A consumed/declined intent cannot yet be safely renewed automatically. Provider
   evidence linking that intent to its terminal failed payment is needed; a 404,
   timeout, browser claim or unrelated failed payment cannot authorize a new charge.
2. If a refund claim commits but its POST never takes effect, GET-only recovery
   preserves it as unresolved. Safe resubmission requires a provider-approved
   idempotency/reconciliation contract; blind refund retries remain forbidden.
3. Contradictory late success on a superseded intent has no dedicated exception
   ledger workflow. Normal renewal requires provider evidence of non-usability;
   this exceptional scenario is not certified as handled end-to-end.

These are specific limitations, not a claim that all remaining work is merely
merchant paperwork. Do not mark DEVELOPMENT CLOSED while they remain unresolved.

## External dependencies

- Moyasar merchant approval, seller/account decision, verified ownership and keys.
- Authorized real sandbox/production acceptance of checkout, callback, refund and
  cancellation contracts (not performed by mocked tests).
- Owner/legal invoice-issuer and associated fiscal decisions for the new channels.

## Separate hosting capacity blocker

The current small Render profile previously failed 500-user latency. It remains a
hosting blocker, not a payment-code test. Payment-closure-only commits deliberately
skip expensive load jobs and explicitly do not recertify capacity. Full load
commands, scenarios and thresholds remain unchanged for normal hardening runs.
Existing failures are not waived. No new public-launch readiness claim is made.

# Disabled sandbox session renewal

`POST /api/branch-payments/attempts/{id}/renew` is customer-scoped and
CSRF-protected. It preserves the original attempt and encrypted checkout row.
The unique active-order index and immutable successor relation make concurrent
retries return one successor. The order lock is reacquired after provider I/O;
payment, cancellation or a changed checkout session prevents retirement.

Renewal is allowed only for an unexposed session (no checkout claim, or a
creating/uncertain claim older than 60 seconds), or after seller-authenticated
provider GET confirms an unused expired intent (provider Date header plus a
60-second margin; no reliance on an application clock running ahead) or a
canceled intent. Provider amount, currency, description, callback and intent ID
must match. An expired application attempt alone is not proof. Neither 404,
timeout nor a fulfilled intent authorizes a second payment. A delayed original
worker cannot expose credentials after the attempt is superseded.

The Arabic/English customer control requests a server decision; it cannot
retire attempts or report payment success itself. No live-payment path or
activation flag is enabled. Renewal records are preserved in exports/backups.

Still separate: consumed/declined-intent retries require conclusive linked
payment evidence; pending/unknown charges stay blocked. Real merchant sandbox
acceptance, legal seller decisions and live integration remain unverified.

Provider contract inspected 2026-10-17 (Riyadh):
- https://docs.moyasar.com/api/payment_intents/03-fetch-payment-intent
- https://docs.moyasar.com/api/payment_intents/04-cancel-payment-intent

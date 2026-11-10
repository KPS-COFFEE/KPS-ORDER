# Disabled refund recovery worker

`KPS_BRANCH_REFUND_RECONCILIATION_ENABLED=true` is a separate, default-OFF
gate. Sandbox, provider sandbox requests and refunds must also be explicitly
enabled. No configuration or live service is activated by this implementation.

The worker queues only already `submitting` or `uncertain` refund records. It
never authorizes an amount, creates an order or submits a provider refund POST.
Unsent `requested` authorizations are not selected and are rejected even if an
invalid operational queue record is inserted for one.

Durable unique jobs, `SKIP LOCKED` claims and expiring leases recover process
crashes. Provider I/O holds no database transaction. Failed or inconclusive GETs
back off from 60 seconds to one hour. Every confirmation rechecks persisted
order/brand/branch/seller scope and the exact cumulative authorized refund.
Concurrent worker/manual verification produces one immutable financial result.
Worker audit events use a null system actor and retain the original authorizer;
the worker does not impersonate that owner or acquire permission to submit money.

Operational queue metadata is preserved in full backups, restored in dependency
order, and excluded from customer/business exports. Existing completed order
snapshots and Delivery collection/refund flows remain unchanged.

Limits: a durable submission claim with no confirmed provider effect stays
unresolved. This worker deliberately does not infer that a refund POST can be
repeated from an unchanged balance or timeout. Provider-confirmed safe retry,
real merchant acceptance, live mode and fiscal credit-note integration are
separate gates; synthetic tests are not merchant certification.

# Customer requests: awaiting processing

Built on review HEAD `78e86bb35f4586304acde770a9e2e3cd89e16828`, retaining
the evidence-memory correction and all prior operational changes.

Customers can submit a cancellation request or report this order as a duplicate
from the existing amendment dialog. Neither operation cancels an order by itself.
The existing amendment proposal, branch feasibility and customer price-consent
workflow remains responsible for actual item/address changes.

The approved Arabic pending label is **بانتظار المعالجة** for cancellation,
duplicate and unreviewed amendment requests. Delivery requests are routed to the
authorized delivery manager; pickup/car requests to the authorized branch
manager. Owners retain their existing scope. Both inbox queries filter brand,
branches and operational routing on the server before their bounded result limit.

Request submission uses the existing order lock, rate limiting and idempotency.
Concurrent submissions cannot create two pending cancellation/duplicate requests
for the same order. An optional related duplicate order must belong to the
authenticated customer's authorized identity; it is never cancelled implicitly.

Opening the request or conversation changes no request state. Pending requests
remain in the operational alert channel until an explicit recorded decision.
Expired amendment offers remain visible for rejection/withdrawal; expiry still
prevents applying an expired quote. No stale offer is automatically accepted.

Cancellation in the manager dialog first calls the existing cancellation route.
Its current refund, fiscal, active-task, permission and version guards remain in
force. A request cannot be marked resolved unless the order is actually cancelled.
Rejection requires a reason and leaves the actual order unchanged. The customer
can read the outcome and its timestamp in the same dialog.

Requests and decisions are appended to existing `order_events`, with actor ID,
server timestamp and audit entries. No new table, migration, provider or permission
grant is introduced. Existing portable backup/restore already includes this table.
A separate synthetic-schema restore verifies the new request/decision events.

## Focused validation

- PostgreSQL 18.6: initial request/amendment/editor/HTTP-browser execution:
  **27 PASS**. The request suite subsequently adds concurrency and separate-schema
  restore witnesses; all **7 request cases PASS**.
- Real Chromium: **6** customer-to-manager-to-customer HTTP/database journeys,
  Arabic and English at **390 / 768 / 1280 px**, with persisted decisions and
  screenshots; **2** existing amendment-editor cases PASS. Staff login uses the
  actual four-digit SMS flow with synthetic external provider I/O; customer
  sessions are seeded in the isolated test database.
- Wider cancellation, split-payment and operational regression execution:
  **38 PASS**; four additional browser cases initially errored because that
  invocation omitted the local Chromium executable path. An explicit-path rerun
  exposed a timing-dependent synthetic-audio test defect: Playwright sets CDP
  `userGesture: true` during assertion evaluations, which could unlock the fake
  audio context before an actual click. The same two cases passed on the prior
  source under this browser, confirming timing sensitivity rather than proving
  a production audio defect. The fake now also requires a trusted pointer/key
  event, retains all original pulse/continuous/stop assertions, and explicitly
  checks no trusted event before the click and one afterward. All **4 browser
  bell cases PASS** with that correction. Application audio logic was unchanged.
- JavaScript syntax, mandatory-SMS UI, owner-configured bell scheduling and
  order-status/request-badge VM checks pass. The status test loads the new helper
  dependencies rather than bypassing them.

Artifacts are generated under `test-artifacts/customer-request-*.png` and
`test-artifacts/manager-request-*.png`, and the focused CI workflow uploads them.
Screenshots are synthetic UI evidence, not physical device or production evidence.

No merge, deployment or production data mutation is part of this change.
Fresh exact-image security certification and the existing production backup,
rollback and owner deployment gates still apply to the new application SHA.

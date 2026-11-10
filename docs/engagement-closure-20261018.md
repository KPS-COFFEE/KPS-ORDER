# Customer engagement implementation — activation remains OFF

Continuation of `agent/enbishan-isolated-development-20261017` from `4bbf185`.
No production merge, deployment, DNS, Render configuration, real-customer data,
online-payment activation or messaging action is part of this change.

## Implemented

- CRM service follow-ups are append-only, audited and owner/brand scoped.
- Marketing notices are explicit owner-approved Arabic/English versions. Grants
  require the currently selected notice. Revocations remain possible without an
  active notice. Registration never grants marketing consent or changes order SMS.
- Immutable promotion definitions support branch, service, product and customer
  segments, global/automatic offers, bounded use and deterministic single-offer
  selection. Modifier additions and delivery fees are not discounted.
- A brand/customer wallet and append-only ledger reserve promotional credit in
  the same transaction as order creation and its durable idempotency response.
  A wallet lock plus rule locks serialize use limits and prevent overspending.
- Completed, actually paid orders earn credit. Confirmed cash/live-provider
  refunds adjust earnings and return redeemed credit proportionally. Uncertain
  refunds do not invent a provider result. Cancellation restores reserved credit
  once. A previously spent, later refunded reward can leave a negative credit
  balance; no further credit can then be spent. No driver cash is moved.
- Test-mode online orders cannot consume live credit or offer eligibility.
- Optional gift details retain the verified purchaser's order ownership and
  invoice buyer, use recipient contact for fulfilment only, print the gift note,
  and do not save the recipient address to the purchaser's personal address book.
- Customer and owner screens are connected to these APIs. Customer fields reset
  after a confirmed order and on identity changes. Owner writes require fresh
  server authorization, CSRF, idempotency and sensitive SMS where appropriate.
- Additive order fields do not change the fingerprints of previously committed
  ordinary requests. Existing retry keys can still replay their original result.
- Full archives include the new tables; wallet projection triggers are disabled
  only inside the separate restore schema while stored ledger/projection rows
  are imported. Consent sequences are restored before later new decisions.

## Explicit supported policy boundaries

No rates, approved products, terms or limits have been seeded. Owners must supply
the approved version. The implemented cashback contract is item promotional
discount credit, no expiration, proportional confirmed-refund reversal. That
contract must be explicitly approved; it is not assumed to be a prior decision.
Cash-equivalent payout and other expiry treatments are not silently fabricated.
Discount rates that create free orders are rejected (maximum 99.99%). Benefited
orders require cancellation/refund and a newly priced replacement instead of
an in-place amendment that could corrupt the reserved benefit or issued invoice.
Ordinary amendments retain their previous behavior; gift purchaser snapshots
survive permitted address/item revisions.

## Default-OFF release configuration

Each feature requires `ENABLED=true`, `ACCEPTANCE_SHA=<exact runtime SHA>` and
`OWNER_APPROVAL_SHA=<same SHA>` under its respective prefix:

| Scope | Prefix |
| --- | --- |
| KPS benefits | `KPS_CUSTOMER_BENEFITS_KPS` |
| Enbishan benefits | `KPS_CUSTOMER_BENEFITS_ENBISHAN` |
| KPS gifts | `KPS_GIFT_ORDERS_KPS` |
| Enbishan gifts | `KPS_GIFT_ORDERS_ENBISHAN` |
| Approved multibrand runtime | `KPS_MULTIBRAND` |

KPS approval cannot activate Enbishan. No public endpoint writes these flags.
Runtime revision comes from `RUNTIME_REVISION` / `RENDER_GIT_COMMIT`.
Multibrand runtime additionally requires explicit `KPS_MULTIBRAND_DATABASE_HOST`
and `KPS_MULTIBRAND_DATABASE_NAME` matching its configured DSN, and an approved
`KPS_STOREFRONT_HOST_BRANDS` JSON map whose hosts are all in `APP_ALLOWED_HOSTS`.
`PUBLIC_BASE_URL` must be one of those hosts. Forwarded host headers never select
a brand. `APP_ALLOWED_ORIGINS` and TLS/domain configuration still need their
normal approved deployment values. No values were changed in an external service.

The existing `ENBISHAN_ISOLATED_ENABLED` path retains its original local test DB
and no-provider-credentials rules. Both paths cannot be enabled together.
Runtime tests must use named test databases. Shared work context still requires
real staff credentials and explicit current grants; customer endpoints force
customer context and never trust a work header to reclassify the identity.

Online payment live flags, merchant credentials and the two unresolved Moyasar
retry contracts are unchanged and remain fail closed.

## Required execution evidence before READY

Focused source regression: **158 passed**, 61 PostgreSQL-marked cases excluded.
Expanded available-source regression: **1011 passed, 9 skipped, 950 deselected**
and 197 successful subtests. JUnit evidence: `docs/engagement-source-20261018.xml`.
The expanded run excludes PostgreSQL-marked cases and eight modules blocked by
the unavailable test DB/browser binaries: `test_backup_owner_phone_archive`,
`test_branch_checkout_browser`, `test_car_color_browser`,
`test_map_resolution_security`, `test_optional_customer_plate_browser`,
`test_owner_sms_channel_probe`, `test_security_audit_local`,
`test_test_carryover_archive`. Their execution remains mandatory, not waived.
The initial unrestricted source attempt exposed those environment blockers plus
one order-insert test fixture requiring the additive discount field and two
invoice-cache fixtures using a brand excluded by the existing KPS-only source
address policy. Fixtures now match the actual schema/policy; a new test explicitly
asserts that Enbishan cannot inherit a source-file fiscal address. No fiscal
address authorization was broadened. All four changed/new JavaScript modules
passed syntax checks. These are not end-to-end results.

The focused local source suite, JS parsing and PostgreSQL18.6 SQL/PLpgSQL parsing
are distinct from database execution. No source/parser success certifies a real
transaction, a browser journey, backup restoration, container image or latency.
The new PostgreSQL cases include consent/revocation replay, wallet redemption,
confirmed-refund clawback, cancellation replay, concurrent customer offer limits
and same-phone cross-brand isolation. Four real mobile Chromium cases cover both
brands and both languages. They must execute, not be counted as passing by skip.

The existing dual-brand/KPS regression workflow includes these suites. The
unchanged security image/rollback jobs also cover this branch and the new source/
PG suite; HIGH/CRITICAL, full scans, SBOM and mandatory tests remain required.
No expensive load test was repeated during this implementation.
Final image/rollback/performance evidence
must match the final candidate; prior security-stream success is not inherited.

Last previously observed CI blocker: GitHub's account billing lock, no runner,
zero executed steps. Do not call this an application failure or a PASS. Do not
bypass the lock, change billing or deploy to obtain a result.

# Pending normal startup migrations — source review, 2026-10-23

**Decision: not cleared for production migration.** No migration, production
query, deployment, backup or restore was performed for this review.

## Current continuation: eleven normal migrations

The current review adds `096_campaign_report_closures.sql` and
`097_authentica_customer_challenges.sql` to the nine-file inventory below.
There are now **eleven added normal migrations**, 84 source SQL files versus
73 in production source `4e4d781`; no existing normal SQL file was edited.
These remain source counts, not the live registry count.

- `096` creates the empty append-only period-report snapshot table, brand/date
  index and immutability trigger. No report is created and no offer, campaign,
  order, permission or accounting state is changed by migration. The new table
  is included in preservation, current export/restore and the distinct prior
  application's compatibility inventory. Actual populated archive/restore and
  concurrent finalization witnesses are required on the revised exact head.
- `097` adds nullable Authentica contract hash/length columns to the existing
  OTP challenge table, extends two provider allowlists, limits Authentica to
  ENBISHAN SMS customer/probe challenges and adds a partial generation index.
  It neither selects nor enables a provider. Existing rows keep null new
  fields. Constraint validation and index construction can lock existing
  identity tables. An older application has not been certified for active
  Authentica data; provider disablement is not a schema downgrade.

Both migrations execute at ordinary startup even while features/providers are
off. The actual registry, private backup/recovery, realistic locking/duration,
exact-head CI and final owner production approval gates remain mandatory.
The following section retains the original nine-migration source review.

## Historical nine-migration review

Reviewed source: `626a224f87828e4929f8e8dd58dbca3c3ed31916`.
Production-source comparator supplied by the release owner:
`4e4d781b4fa82b7459410694ff5c4ac06842e6e7`.
`git diff --name-status` shows exactly nine added normal migration files and no
modified existing normal migrations. The source directories contain 82 versus
73 SQL files. These counts are **source inventory**, not observed database state.
`079_customer_engagement.sql` and the pre-existing
`079_owner_extra_permissions.sql` are distinct filename-based migration IDs.

## What changes

| Migration | Compatibility and data effects | Main release risk |
| --- | --- | --- |
| 064 checkout sessions | New empty session table, encrypted secret bytes, FK to existing attempt, terminal-state immutability. No backfill or activation. | New FK/DDL locking; new table must be included in private recovery. |
| 065 payment webhooks | New deduplicated inbox, processing/lease metadata and pending index. No event enqueued by migration. | Durable asynchronous work needs the new application; inserts rely on correct canonical attempt/brand pairing. |
| 066 online refunds | New authorization table, one in-flight refund per order, immutable transition trigger. No refund issued by migration. | Adds financial state and refund history that an older application may not understand. |
| 067 payment reconciliation | Replaces the 065 event CHECK to permit `reconciliation_requested`. Existing allowed values remain valid. | The affected inbox is newly created on this exact upgrade; later application rollback must understand added event type. |
| 068 payment renewal | Replaces global per-order attempt uniqueness with one non-superseded attempt per order; adds `superseded`, replaces transition function and adds immutable renewal links. Existing unique data satisfies the narrower partial index. | Ordinary non-concurrent index/constraint work touches existing attempts. Older code's one-attempt assumption ceases to hold once renewal is used. |
| 069 refund reconciliation | New empty retry/lease table and due index; financial authorization stays separate. | Worker recovery semantics and private recovery coverage; independent brand FK is not a composite ownership check. |
| 070 payment modes | Replaces two `mode='test'` checks with `mode IN ('test','live')`. Existing test rows remain valid. No seller, flag, key or receipt change. | Schema now admits live records even while runtime gates are off. Previous source was sandbox-only. |
| 071 late receipts | Replaces per-order receipt uniqueness with uniqueness only for `disposition='applied'`; keeps per-attempt/provider uniqueness from 063. | Allows multiple historical receipts for an order, intentionally retaining exceptional money. Older reports/reconciliation may assume one receipt. |
| 079 customer engagement | Adds `orders.discount_halalas DEFAULT 0`, replaces the total reconciliation CHECK, creates consent/follow-up/benefit/wallet tables and immutable/deferred financial triggers. | Existing orders/users are touched by DDL, CHECK validation and a non-concurrent users index. Deferred triggers run on ordinary order/collection/refund events even with features off; absent benefit state returns early. |

There is no `DROP TABLE`, `TRUNCATE`, historical `DELETE`, bulk business-row
`UPDATE`, or provider call in these nine files. Column default zero preserves
the old total equation for historical orders. The engagement migration requires
exactly one prior total equation via catalog inspection; an unexpected schema
fails closed instead of dropping an arbitrary financial constraint. New SQL
functions contain future ledger updates/inserts; defining them is not a
historical reward backfill.

## Startup, blocking and failure behavior

`app.db.migrate()` runs in one transaction, takes advisory lock `764832610`,
then hashes existing columns/rows of the available `PRESERVED_TABLES` under
`LOCK TABLE ... IN SHARE MODE`, applies all missing normal migrations, runs
optional multibrand migrations and retained one-time owner decisions, compares
the old columns again and writes a private preservation audit. Applied filename
checksums prevent silently changing a previous migration.

**Feature flags do not skip these nine normal migrations.** A deployment can
apply them before any ENBISHAN, benefits or live-payment flag is enabled.
Failure of any pending statement aborts this migration transaction, including
its migration registry entries and preservation audit.

`db.tx()` sets a 15-second **per-statement** timeout. It sets no explicit
`lock_timeout` or overall transaction deadline. SHARE locks block ordinary
writes and remain until commit; ALTER TABLE and non-concurrent index work can
add stronger locks. Preservation hashes scan/sort the live historical rows.
Consequently the default-off features do not establish zero downtime or a
production migration duration. Two Uvicorn workers each run startup; the second
can wait on the advisory lock while the first migrates, potentially hitting its
statement timeout. Synthetic fresh-schema timings are not a concurrency/size
certificate for production.

PostgreSQL 18 references checked for this review:
[ALTER TABLE](https://www.postgresql.org/docs/18/sql-altertable.html) and
[CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html).
Constant-default column addition does not itself require a table rewrite, but
CHECK validation/index building and lock acquisition remain relevant. Ordinary
index creation blocks writes; these files do not request concurrent builds.

## Isolation and rollback limits

Engagement customer/wallet/rule relationships use composite brand ownership
FKs. Runtime benefit synchronization checks the order's brand/customer, and
wallet insertion validates its benefit-state owner. This is stronger than an
unscoped customer-ID lookup.

**Reviewed source gap and newly prepared guarded hardening:** the nine normal
migrations reference payment brand/order/attempt/refund independently, and
`benefit_order_states.order_id` initially has only an independent order FK.
New optional `app/multibrand_migrations/003_financial_brand_ownership.sql`
closes those relationships with validated composite foreign keys, while
preserving deferred benefit-state-before-order insertion. Renewal links retain
the API's existing same-order invariant through validation and a narrow trigger.
It does not bind immutable attempt history to an order's current branch or
restrict the deliberate shared employee identity. It rewrites no data: retained
conflicts abort the transaction. The migration only runs through the existing
isolated/exact-release multibrand guard. This is **new hardening awaiting its
own PostgreSQL evidence**, not certification of the nine normal migrations or
a statement that a production schema was changed. Negative INSERT, deferred
commit, valid control, failed migration and repeat-application witnesses are in
`tests/test_enbishan_financial_ownership_constraints.py`.

Rolling the application image back does not undo this committed schema. Raw
`4e4d781` rollback has not been certified here against populated discounts,
wallets, superseded attempts, live receipts or duplicate late receipts.
Existing rollback certification is rooted in prior source
`80a1d727f6695c4ce3685e726ab5b34674e1cfa0` plus a compatibility overlay; its
reviewed drill exercises trips, printing, settlement, export and re-upgrade,
not all newly populated payment/engagement states. Do not extrapolate it into
an unrestricted rollback guarantee for these nine migrations.

## Isolated test coverage present in the reviewed source

These are coverage descriptions, **not new passing-result claims**. This
read-only review did not run PostgreSQL tests or verify a production backup.

- `test_branch_checkout.py`: concurrent single intent, uncertain-create no
  automatic repeat, authorization, encrypted checkout restore.
- `test_branch_payment_webhooks.py`: duplicate events, independent provider
  proof, retry/expired leases, concurrent workers with one financial effect,
  inbox restore and missed-callback reconciliation.
- `test_branch_refunds.py`, `test_branch_payment_renewal.py`,
  `test_branch_late_payment.py`, `test_branch_payment_reports.py`: single-submit
  refund recovery, contention, scoped permissions, immutable historical
  attempts, late duplicate receipts, independent refunds, totals and restores.
- `test_customer_engagement_closure.py`: order total/discount constraint,
  immutable discounts, concurrent coupon limits, wallet redemption/reversal,
  brand separation and default-off exact-release gates.
- `test_customer_engagement_restore.py`: populated encrypted ledger restore,
  preserved balances/entries, sequence advancement and functioning post-restore
  triggers without replaying previous ledger credits twice.
- `test_enbishan_customer_scope_constraints.py` and the public/dual-brand
  suites: customer/order ownership and API isolation. The new guarded financial ownership suite separately exercises direct INSERT
  and deferred-commit relationship constraints; its exact-head results remain
  required before claiming those checks passed.

`multibrand-final.yml` includes the engagement/restore and dual-brand tests.
`enbishan-isolated.yml` includes the newer checkout/webhook/refund/renewal
coverage. Source selection is not an exact-head successful workflow result;
retain the matching JUnit/workflow IDs before claiming certification.

Before a production go: record actual schema registry compatibility without
changing it, obtain authorization for the nine migrations, certify an isolated
populated baseline-to-candidate upgrade including contention/duration and repeat
startup, identify a verified private recovery artifact and tested compatible
application rollback, then retain the complete exact-head release gates. No
backup, export, production clone or restore artifact is asserted by this review.

## Exact file SHA-256 inventory

| Migration ID | SHA-256 |
| --- | --- |
| 064_branch_checkout_sessions.sql | e6247c9489b16395a08a445a611e3ccadcdfe874393e86ae931e67111ec0cfec |
| 065_branch_payment_webhooks.sql | db7486d75f8ad1cc184f7aff0fd03812cb143f57606a679953573c8059df1ec0 |
| 066_branch_online_refunds.sql | 9437e0edf947c9591fd28f5b28d7cceafb7599c1cc8b2fc76510f2950c5d51eb |
| 067_branch_payment_reconciliation.sql | 5c48a5a9729ac819c57047f65d0e599f80eff69b38b8170b945646c3a3821359 |
| 068_branch_payment_renewal.sql | 4ee6b11bf8a3841fa4637556d3be26b669ddb58673aa8c60c819cb1ba8c85ffe |
| 069_branch_refund_reconciliation.sql | 4119d1a9ca6c3a6552a8341f4b2096b87d02d5d5e329a3391e22812b5029778f |
| 070_branch_payment_modes.sql | dd2457a59b28725c9e576ce0c49f7e691399498a9bba75d9832bee2160301007 |
| 071_late_branch_payment_receipts.sql | d34b62f6a0049584c039a71dd21452451b706979908a4d483d04a11ad3ada68b |
| 079_customer_engagement.sql | e1d13f47ec11a15818b4d1261587f008eaa4866c7835372ec8f93e010c2fe2e9 |
| 096_campaign_report_closures.sql | 0e05eb29329fa154d2e923d7692ba997b2b484ba86b48c0282b723a7731e0c58 |
| 097_authentica_customer_challenges.sql | 41d58a34b1f814b821a3072c4b64afcd3239334e405b008226ccdf09b79af8e3 |

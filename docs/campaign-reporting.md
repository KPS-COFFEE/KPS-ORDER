# Online orders and gift reporting

The existing banner engine remains the campaign engine: every banner, image,
branch/city target, schedule and manual active flag belongs to one brand.
Advertising visibility requires the separate audited owner setting. Gift
eligibility is a separate audited policy; hiding a banner does not reset claims.
The approved KPS creative and its orange shimmer assets are unchanged. No KPS
offer or creative is installed for Enbishan by these reporting changes.

Two read-only endpoints extend the existing canonical orders and permanent gift
claim ledger. They require an owner or delivery manager with `reports_view`.
Brand is always taken from the authenticated account. Managers are limited to
their assigned branches; revoked report access and morning shift-specific history
restrictions fail closed. Reads are audited without names or full phone numbers.

- `/api/reports/promotions/daily?start=2026-10-22&end=2026-10-22`
- `/api/reports/promotions/beneficiaries?start=2026-10-22&end=2026-10-22`

Both accept an optional `branch_id` and `policy_version`. Beneficiaries supports
`limit` (1–100) and `offset`. The date range is inclusive, limited to 93 Riyadh
calendar dates. The end timestamp is exclusive so midnight is not counted twice.
These calendar reports supplement, and do not replace, the immutable accounting
or driver/manager closure reports. There is no new financial write. The additive `096_campaign_report_closures.sql` migration only creates the append-only final-period report store and its index/trigger; it does not change orders, eligibility or accounting.

Online order totals exclude staff delivery-center orders and owner-marked test
orders. Cancelled/rejected orders are counted separately. First-time and repeat
counts use noncancelled online history from the same visible brand/branches,
normalizing Saudi phone variants including Arabic/Persian digits and copied
formatting controls. Missing/invalid phone numbers use the private customer ID
rather than merging unrelated customers. A person who places their first and
second order in the same reporting period can appear in both first-time and
repeat counts; they appear once in `unique_customers`. These are observed online
history metrics, not an assertion that an offline customer has never visited.

Gift claims count redemption at order creation. A cancelled gift order remains
shown separately and does not regain eligibility. A version filter changes gift
metrics only; it never turns total online orders into a count of beneficiaries.
No beneficiary name, complete phone or private customer ID is returned. The
beneficiary list contains order references and a masked last-four phone suffix.

The existing gift version is a policy revision, not a separate campaign ID.
Reports cannot retroactively attribute earlier generic banner exposure, and no
campaign attribution is fabricated. The staff screen at `/campaign-report` presents bilingual date/branch/revision
filters, daily totals and paginated masked beneficiaries. It uses the existing
work session. Additional-brand selection is sent only after `/api/session`
returns that explicit grant, and every report read is still authorized server
side. Failed or revoked access clears the previous report. It has no inline
script, no stored customer data and no additional OTP requirement.

Owners can explicitly **save a final period report** from the displayed report
filters. This automatically captures one complete observation of the summary,
every day, and **all beneficiary pages**, in one PostgreSQL statement/MVCC
snapshot. The existing idempotency transaction remains READ COMMITTED, so
concurrent retries see the original committed response. A repeated request with
the same key creates no second snapshot. Current role/brand/report/branch access
is checked before replay. The UI confirms the action without an OTP.

- `POST /api/reports/promotions/closures`: owner only; exact calendar filters,
  reason and idempotency key required.
- `GET /api/reports/promotions/closures`: latest scoped saved periods (50/page).
- `GET /api/reports/promotions/closures/{id}`: frozen daily/summary and paginated
  masked beneficiaries; current managers may read only if **every** saved branch
  remains within their current grant. Morning restrictions still apply.
- `GET /api/reports/promotions/closures/{id}/download?lang=ar|en`: complete UTF-8
  CSV, all days/beneficiaries, selected scope, definitions, reason, save time and
  content digest. Spreadsheet formulas are neutralized. Downloads are audited.

The snapshot records the selected date/branch/revision filters and a SHA-256
of its complete canonical JSON. Database UPDATE/DELETE triggers reject edits.
Every reopening/download verifies the digest. Later order cancellations,
new claims or branch renaming do not rewrite the saved report. The durable
saved-report list is the in-app evidence; no outbound recipient is assumed.
Both current and prior compatible archives explicitly preserve the new table;
restoration uses matching current source. The real isolated PostgreSQL witness
checks populated export/restore and the restored immutability trigger.

Technical limits are 20,000 beneficiaries and 16 MiB per complete snapshot.
An oversized request fails atomically with a shorter-period instruction; it
never saves or exports a truncated 'final' report.

**This action finalizes a report, not an offer.** It does not deactivate a
banner, gift, schedule or accounting shift. It does not invent banner exposure
attribution or equate gift policy revisions with campaign identity. Scheduled
automatic campaign-end snapshots/notifications remain unavailable until that
separate lifecycle is explicitly defined. Existing KPS advertising and gift
eligibility remain unchanged.

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
or driver/manager closure reports. There is no new financial write or migration.

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

Immutable campaign closing snapshots and automatic post-close delivery of
marketing reports are not introduced by this patch.

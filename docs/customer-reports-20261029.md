# Customer cohorts and retention reports

Owner-approved feature for KPS ORDER; primary source branch:
`agent/kps-order-emergency-v4`. Candidate branch:
`agent/driver-fair-hours-20261029` (combined PR #147).

## User entry and scope

Operations → Reports → **تقرير العملاء والشرائح**. The owner-only page is
`/customer-report?operations_brand=kps` (or an authorized Enbishan scope).
It reads `/api/reports/customers` and preserves the existing CRM page.
No migrations, business-record writes, messages, coupon issuance, gift grants,
identity merges or changes to marketing consent are introduced.

## Definitions

- A purchase uses the canonical CRM rules: verified identity/proven account
  links, certified operating cutoff, completed non-test orders and positive
  actual net collections. Fully refunded/cancelled orders are excluded.
- Periods are inclusive **Riyadh calendar dates**, separate from the 16:00
  operational day. Supported range: up to 366 dates through today.
- A period-new customer first purchased inside the period. A returning
  customer purchased before the period and again inside it. A customer with
  no purchases in the period remains available for retention follow-up.
- Daily new customers are distinct first-day buyers. Daily returning customers
  first bought on an earlier date. Extra orders on the first day count as
  repeat orders without counting the customer twice. Daily unique counts must
  not be summed into a monthly unique count.
- Ranking uses period order count, period actual net value, or absence days.
  First/last purchase, lifetime statistics and cadence use known qualifying
  history through period end. Value includes collected tax and fees within the
  order total. The displayed ranking is paginated, with deterministic ties.
- Absence ends at the requested period end. Cadence is the median of up to 12
  recent gaps between distinct purchase days, requiring 5 purchase days.
  Overdue: >= 2× cadence and >= 7 days. Possibly lapsed: >= 3× cadence and
  >= 14 days. Widely variable quartiles produce an irregular-pattern label;
  sparse history and one-purchase buyers are labeled separately.
- These are initial indicative retention rules, not calibrated proof of churn.
  The selected brand is isolated; absence here does not prove absence from
  another brand. Historical results reflect current completion/refund evidence
  and are not immutable accounting snapshots.

## Verification and release checkpoint

At the local candidate checkpoint:

- 18 non-database authorization/filter/CRM compatibility tests passed.
- PostgreSQL syntax parsed successfully.
- Actual report SQL passed isolated synthetic CTE checks on PostgreSQL 18.6
  (PGlite/WASM): cohorts, refund arithmetic, Riyadh midnight boundaries,
  rankings, reconciliation and retention states.
- Actual HTML/JS passed AR/EN DOM interaction checks: initial rendering,
  escaped names, filters, language change and clearing after lost authorization.
- Node syntax and Git whitespace checks passed.
- Real Chromium layout tests and the full network PostgreSQL HTTP route suite
  remain pending. DOM checks do not certify browser layout.

Run the dedicated workflow `.github/workflows/customer-reports.yml` against the
exact candidate after pushing its branch. It uses PostgreSQL 18.6, the real
route fixtures and Chromium at Arabic/English mobile/desktop widths. Do not
describe pending checks as passed. Local examples:

```bash
python -m pytest -q tests/test_customer_reports.py tests/test_customer_crm.py -m 'not postgres'
# TEST_DATABASE_URL must refer only to an explicitly named local test database.
TEST_BROWSER=1 python -m pytest -q tests/test_customer_reports.py tests/test_customer_crm.py tests/test_customer_reports_browser.py
```

The owner has authorized publication with employee access controls and fair driver
hours in combined PR #147. This integration also requires complete projected
brand scope, the work session cookie for native navigation, staff geography
protection and live session revocation on the report page.

Latest local verification: 89 non-PostgreSQL customer/CRM, geography and session
context tests passed; 16 existing browser-handler revocation tests passed.
The added PostgreSQL and real Chromium suites must pass on the final uploaded
commit before merge. Follow release preflight and exact deployed merge revision
pins; retain all currently disabled feature flags.

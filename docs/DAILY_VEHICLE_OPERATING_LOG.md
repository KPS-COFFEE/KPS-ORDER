# Daily vehicle operating log

Implementation scope: KPS ORDER, additive to the existing driver inspection,
refueling, shift approval, vehicle fault and delegated admin permission flows.

## Persistent model

- `fleet_vehicles`: one permanent vehicle identity per brand and normalized plate.
  Existing historical shift snapshots and registered driver vehicle details are
  registered without rewriting any existing operational data.
- `vehicle_operating_logs`: one persisted reference/metric record per canonical
  driver shift. Multiple shifts in one operating day remain distinct rows.
  Vehicle, shift and operating-day identities cannot be changed or deleted.
- `vehicle_log_corrections`: append-only audited before/after metric overlays.
  Closed shift inspections, refuels and financial closures remain unchanged.
- SQL triggers synchronize canonical inspections/fuel/status into the ledger in
  the same transaction. Failed operations and idempotent retries cannot create
  duplicate daily rows. A unique shift primary key is the replay safeguard.
- Driver changes retain the original shift's vehicle identity and driver ID.
  Opening a shift locks the vehicle identity before checking active use and the
  latest ending odometer across drivers, including audited corrections.
- Backup/export includes all three tables with brand scope. Separate-schema
  restore imports stored ledgers rather than replaying their materialization;
  only those two derived-data triggers are temporarily disabled in that isolated
  transaction and reenabled before verification.

## Access and operation

Owner and delegated admins with `vehicle_maintenance_manage` can use vehicle
cards, history, reports, exports and audited corrections. This reuses Fawzi's
existing vehicle fault/maintenance permission without granting order, finance,
user administration or shift approval access. All branches in a multi-branch
shift must be within the delegated user's scope. Historical driver scope is
read from the shift, not the driver's current branch assignment.

Open records are completed through the canonical shift flow. After financial
closure approval, corrections require a nonempty reason and current revision.
Only one competing revision wins; replay returns its saved result after current
access checks. The fleet report has no direct edit controls.

## Reports and calculations

- Vehicle history: newest first, all years, server-side pagination.
- Fleet report: inclusive operating-date interval up to one year, vehicle,
  driver, branch, status color, faults-only and exceptions-only filters.
- Operating day: persisted delivery day, fallback 16:00 Asia/Riyadh for historical
  shifts lacking the existing date field. Calendar date and timestamps are also
  shown in Riyadh time.
- Summary: distinct used vehicles, distance, filled liters, weighted comparable
  KM/L, four color counts, fault count and exception-record count.
- Clicking a summary keeps the selected date and form filters. Clicking a fault
  count shows records with faults; a record may contain multiple fault reports.
- UI/report totals share a repeatable-read snapshot. Pagination is applied in
  PostgreSQL; summary covers all matching records, not only the displayed page.
- PDF and genuine XLSX export all filtered rows, not only a displayed page.
  PDF separates distances and fuel/faults into two readable tables with matching
  row numbers. Arabic/English headers, timestamps and status labels are supported.
  XLSX uses inline strings, so user-entered formula text remains inert.
- Actual distance = ending minus starting odometer. Filled liters = canonical
  refuel entries. Efficiency requires submitted/closed, full at both endpoints,
  positive distance and positive liters; all other rows are grey.
- Provisional baseline: 10 KM/L, green >=9, orange >=8 and <9, red <8.
  Threshold comparison uses the unrounded ratio; displayed ratios are rounded.
  Color is a review indicator and never creates a driver liability or deduction.
- Average KM/L = summed distance / summed liters from eligible full-to-full
  records only. Missing observations do not enter the comparable ratio.
- KPS ORDER currently has no canonical measured GPS track or assigned route
  distance source. Their columns and variance remain null/“unrecorded”; no
  straight-line approximation, duplicated GPS store or invented number is added.

## Validation status

Local validation on 12 October 2026:

- 18 passed: metric weighting, incomplete data, selected-filter intersections,
  date validation, correction input validation, XLSX formula safety, PDF sections
  and Riyadh timestamps, existing accounting/export/print helper checks.
- Python compilation, JavaScript syntax and whitespace checks passed.
- Arabic and English synthetic PDFs rendered and visually inspected.

Prepared but **not executed** in this environment:

- Real PostgreSQL acceptance: ten vehicles/twelve days, two drivers on one car,
  multiple shifts/day, all colors, faults, multi-branch/brand isolation,
  correction version/idempotency/access revocation and unchanged source history.
- Real Arabic/English Chromium journeys: cards, history, filters, red summary,
  filtered downloads and audited corrections on mobile/desktop widths.
- Related shift, maintenance, cash closure and portable backup regression suites.

The local PostgreSQL runtime could not start under the environment's permissions.
GitHub publication was rejected by automatic approval review because publication
to the new branch was not authorized. No CI success, deployment or live activation
is claimed. Workflow: `.github/workflows/fleet-operating-log.yml`.

## Approved publication follow-up

Owner explicitly approved publication to the existing PR #1 branch on 12 October.
Initial published revision: dbea1c1f647da80be7bd9d9249f5cc63387a7aec.
Real PostgreSQL 18.6 and Chromium first run: 116 passed, one fixture key failure.
The correction test now uses a valid canonical idempotency key; the guard was not
weakened. The test-only PDF reader is pinned to upstream security release 6.20.1.
Inspection findings count as a fault-bearing record even without a separate fault
report. Existing report count is retained when available to avoid double counting
a single inspection and its corresponding report. Deployment remains manual.

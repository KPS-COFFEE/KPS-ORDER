# Manager operational release independent from treasury receipt

A submitted shared manager cash bag no longer holds operational staffing open until an owner receives it. Submission checks live tasks, driver physical handovers and pending operational handovers before appending an immutable operational release. Cash declarations, witnessed receipts, accounting allocations and report approvals remain separate records.

Opening a subsequent own manager workday also releases an eligible pre-upgrade submitted shared workday after repeating those readiness checks. The UI exposes a fresh workday form for a submitted shared bag. An actually open/closing duty continues to block duplicate staffing. No synthetic receipt, transfer, cash allocation or accounting approval is created.

Treasury receipt can be delegated to an existing admin with explicit custody_view and custody_transfer permissions and all affected brand/branch scopes. No permission is granted by this change. Only the named independent recipient can confirm the amount actually received; the recipient cannot approve their own accounting allocation. If a recipient is unavailable, cash stays with the declaring manager and operational completion proceeds. Recipient assignment can be corrected by appending a new pre-receipt declaration revision.

An operationally ended declaration can be withdrawn/corrected without reopening the previous duty or modifying an active subsequent duty. Actual receipt preserves the original operational end timestamp. Full archives retain and validate the release evidence; contradictory returned/received cash evidence still fails restoration.

Validation:
- 70 PostgreSQL/unit checks passed across manager closure, central workdays, backup/restore, staffing selectors and employee permissions before the final legacy/no-recipient cases.
- The final manager API cases passed (10) including legacy opening and no recipient.
- AR/EN Node UI selectors pass for old receipted and unreceived submitted bags, while open/closing duty remains active.
- Python compilation, JavaScript syntax and git diff checks pass.
- Four real-browser acceptance cases (AR/EN, legacy/new) are included in delivery-workflow-controls CI. Local execution was blocked because Chromium is absent; its official download returned a truncated archive. No local browser acceptance or production completion is claimed.

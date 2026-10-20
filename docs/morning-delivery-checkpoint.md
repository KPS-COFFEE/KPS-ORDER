# Morning delivery implementation checkpoint

Review-only; not approved for merge or production activation.

Implemented: disabled-by-default, owner-scoped policy API with step-up,
version/idempotency/audit; Hafar 04:00–16:00 Riyadh routing to approved execution
branches; whole khaliya basket requires approved branch 2; public menu and branch
schedule qualification; hidden execution routing; explicit morning manager shifts
and inherited driver period; approved preparer/manager write gates; new morning
orders require an open morning manager duty and preparer acceptance before assignment.

Local verification: 61 policy/progress/gift/morning unit tests passed; separate
delivery-hours/morning run passed 28 (14 database tests deselected). These runs
overlap. Python compile and diff whitespace checks passed.

Still required before activation:
- Owner settings form and manager shift period UI.
- Morning subset selection for driver shifts without broadening branch permissions.
- Database integration tests for routing, duty, policy writes and assignment replay.
- Closing/carryover, chat permissions and morning pending-order reconciliation tests.
- Product availability endpoint alignment and single-clock quote boundary review.
- Full customer browser journey verification and owner preview approval.

No production settings have been changed. Interactive Mapbox remains separate,
pending configuration and implementation.

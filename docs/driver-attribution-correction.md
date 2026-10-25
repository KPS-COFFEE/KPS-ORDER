# Completed delivery attribution correction

An owner can correct the actual driver and collector on a delivered, fully paid
order before closure. Preview the original task, collections, and eligible
receiving shifts, confirm the actual actor, and submit a reason with the expected
order version and an idempotency key. Source and target must remain open under
the same open manager, brand, branch, period, and operating day. The target may
have opened after delivery: this is an owner attestation, not a new dispatch.

The correction is one append-only record per order. Original task, tender,
invoice, order snapshot, physical trips, and collection entries stay unchanged.
Effective task and collection views retain their IDs, amounts, and timestamps
while exposing the corrected driver/shift. Cash receives equal opposing custody
adjustments; card changes attribution without creating cash. No sale, receipt,
refund, kitchen ticket, or customer notification is created. Future refunds use
the effective collection shift. An order with a correction cannot use test-void.

Reports, card reconciliation, completed-order counts, accounting and order
history use the effective views. Raw records and the owner's reason remain in
the exported ledger. Physical trip/GPS records retain their original identity.
The two original sales and their total are therefore counted once.

## Rollback boundary

After the first correction, deploy only a build that understands
`order_driver_attribution_corrections` and both effective views. Older application
images can display the original attribution and select the original refund
custody; they are not a supported operational rollback after a correction.
The database additionally refuses a driver closure snapshot whose task or
collection identities disagree with effective attribution. This fails closed
even for card-only corrections. It does not make an old application compatible.
Restore a compatible application build; do not remove correction records,
disable immutable-record triggers, or rewrite historical evidence.

The prior-application compatibility overlay preserves and exports the correction
ledger, including its original collection evidence. This certifies data retention
only; it does not add effective attribution to that older application's screens
or mutation routes. Its pre-correction rollback drill must still pass unchanged.

Validation: `tests/test_driver_attribution_correction.py` is included in the
required PostgreSQL host and exact-image suites. The required host workflow also
runs `node --test tests/test_driver_attribution_ui.cjs`.

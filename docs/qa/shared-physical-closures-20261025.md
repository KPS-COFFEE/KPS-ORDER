# Shared physical closures

Implementation contract; successful PostgreSQL/browser results must be recorded
against the exact release commit before activation.

## Driver

The current shared dialog accepts one actual cash count and one actual card
terminal total for the entire physical workday. No brand actuals are inferred.
The immutable declaration captures brand ledger expectations separately, keeps
the cash/card declaration unchanged, and records the sender and timestamp.

The assigned delivery manager confirms one actual cash receipt. A mismatch is
returned to the driver before receipt. Returns preserve the original declaration
and the replacement becomes a new revision. Receipt never overwrites either.
The physically received pool remains awaiting accountant attribution; it is not
represented as an approved zero variance or a guessed brand cash receipt.

A standing float backed by the actual count creates its already-approved brand
carryovers once. The verified physical end permits a new shift while financial
attribution is pending. If the retained total is below the approved standing
float, accounting must first attribute the actual retained custody; the system
never guesses which brand owns a shortage. The UI states that exception and the
supported accounting next action.

## Delivery manager

A manager may declare the complete actual cash bag after all drivers have
physically finished. Unallocated child cash does not force a manager to split
actual cash between brands. The selected independent owner confirms actual bag
receipt from their own authorized account. This ends physical staffing and allows
the manager to open the next workday while old financial legs remain submitted.

Before owner receipt, the sender or designated owner may return the manager
closure for correction. The declaration and return remain immutable; the manager
submits a new revision. After physical receipt, corrections require linked
accounting treatment and never alter the original declaration or receipt.

## Accounting

The accountant attributes the driver declaration to the original brand legs using
explicit evidence. The cash, card, retained float, and received cash sums must
exactly equal the immutable originals. No manager has this allocation permission.
The allocation records its real actor, evidence, and original receipt reference.
Canonical brand transfers are attribution of that existing receipt, not a second
physical receipt. Standing carryovers are not duplicated.

A submitted manager ledger can receive only the delayed child attributions
captured in its declaration. Once all children are financially attributed, the
accountant attributes the owner-received manager bag. The manager bag total must
match the owner receipt exactly. Canonical reports are created only then, with
accounting allocation provenance rather than a fabricated original driver brand
count. A financial day/month approval remains blocked while either family has
unallocated declarations. Existing order exceptions and bank matching remain
separate required steps.

## Controls

- Declaration, return, receipt and allocation records reject UPDATE and DELETE.
- Physical-end markers require matching immutable receipts, cannot be changed or
  cleared, and cannot be supplied on newly inserted shifts.
- Driver and manager staffing retain one active physical workday per identity.
- All original branch/brand grants are revalidated before actions and replays.
- Pending parent handovers block declarations; pending shared declarations block
  parent handover until attribution, preventing a frozen recipient from changing.
- Legacy grouped driver per-brand submit/close endpoints return an explicit 409;
  grouping cannot be bypassed by closing a canonical component separately.
- Aggregate records are private full-archive evidence and are excluded from
  single-brand business exports to avoid disclosing another brand's amounts.

## Verification

`tests/test_aggregate_driver_closure.py` covers actual sums, immutable originals,
wrong roles, replay protection, pre-receipt correction, allocation totals, next
physical shifts, and parent handover safety. `tests/test_aggregate_manager_closure.py`
covers owner receipt, next manager workday before accounting, delayed driver and
manager attribution, independent old/new custody, and correction revisions.
The AR/EN shared browser journey verifies a single cash field, a single card
field, one vehicle inspection and the manager/accountant sequence.

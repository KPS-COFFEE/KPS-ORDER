# Isolated Branch 2 rehearsal

The owner opens the rehearsal from the existing operations administration screen.
The same application, database, employee accounts and approved Branch 2 printer
are used. This is an explicitly simulated rehearsal, not a sale or an operational
shift. It never changes the morning admission window or evening operations.

After fresh owner verification, choose an existing Branch 2 manager,
preparer and an available driver. Names and registered mobiles identify the
existing accounts; the rehearsal creates no employees or general permissions.
This grant is limited to the rehearsal: normal morning service may remain off,
and its staffing allowlists and ordinary employee permissions are not changed.
Every participant signs in using their own normal work authentication and opens
the isolated rehearsal view. The authorization lasts 20 minutes.

The preparer or selected manager accepts the fictional order. Acceptance atomically
queues one watermarked printer test on the unique already approved Branch 2 device
and automatically assigns the simulated order to the already selected trial driver.
No owner assignment is needed. That driver accepts personally before the preparer
can choose “تحضير الطلب”, then “تم التجهيز”. The selected driver records pickup,
then simulated delivery. The selected manager closes the rehearsal and reviews its
event report. The owner can also close it or stop it administratively.

If the preparer is delayed, the selected manager can perform preparing and ready
with a mandatory reason recorded against the preparer in the simulated event log.
The manager cannot accept, pick up or deliver on behalf of the driver. Acceptance
records a separate automatic-assignment event; every later event identifies the
actual actor. No ordinary staffing permission is implied by this bounded grant.
Queue submission alone is not proof of physical paper output. Existing historical
trial reports remain readable; unfinished trials using the former owner-led flow
must be stopped and restarted under this flow rather than migrated.

Only bounded rehearsal state in existing application settings, audit events,
idempotency records and the existing printer test queue are written. No order,
operational shift, task, invoice, collection, custody, settlement or sales counter
is created. Reports clearly identify the rehearsal and carry no financial effect.
Normal live morning orders retain their existing execution routing, preparation,
assignment and delivery checks and must be tested during their approved hours.

Expiry, account revocation, branch changes or an owner stop prevent further
rehearsal actions. Historical rehearsal access remains authenticated and scoped.
The owner can stop a rehearsal; only that rehearsal's still queued printer test
can be cancelled. Claimed/submitted jobs and ordinary operational print jobs are
never replayed or cancelled by rehearsal cleanup.

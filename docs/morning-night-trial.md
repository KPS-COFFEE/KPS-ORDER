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

The preparer or selected manager accepts the simulated order. Acceptance queues
one watermarked printer test on the unique already approved Branch 2 device;
it does not revoke or change printer approval. The preparer marks the simulated
order ready, the owner assigns it to the selected driver, and that driver accepts
and records simulated delivery. The owner closes the rehearsal and reviews its
event record. Queue submission alone is not proof of physical paper output.

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

# Prelaunch cleanup safety status

`app.prelaunch_inventory.py` remains inventory only. It has not cleaned the source.
`app.prelaunch_cleanup.py` adds an executable **rollback-only** rehearsal, not a production reset command.

The rehearsal requires PostgreSQL 18, a separate database with a `test_` name, and an individually reviewed manifest containing table, single-column primary key, exact ID and SHA-256 of the complete row. It locks all tables in the current schema, refuses fiscal-connected and preserved tables, refuses enabled user triggers and cross-schema foreign-key relationships, checks complete table snapshots for unexpected cascades or mutations, and always rolls the attempted deletion back. Configuration, identities, counters, fiscal JSON, amounts and unrelated rows must remain byte-equivalent through the snapshot comparison. No financial settlements, mileage or invoice numbers are synthesized.

A synthetic PostgreSQL 18.6 test exercised: unexpected cascade rejection; exact reviewed child/parent ordering; rollback after success and failure; stale row digest rejection; refusal to use the source database; and fiscal relationship retention. This proves the rehearsal guard, **not** the production backup, its restoration, actual source cleanup or operational readiness.

## Actual-source work remains blocked

No authorized private source database, encrypted durable full backup, backup decryption/restore material, or verified source-restoration evidence is available to this task. No source data was deleted, archived, settled, renumbered or reset.

Before source mutation, the operator must:

1. Obtain authorized private database access without opening public ingress; identify exact running revision and freeze concurrent writes and queues for a bounded window.
2. Make a complete encrypted backup in protected durable storage, including attachments and required restore keys. Record backup digest and storage access controls.
3. Restore that exact backup into independent PostgreSQL 18 and reconcile rows, relationships, amounts, fiscal document payloads/QR, attachments, counters and sequences against the frozen source.
4. Review the complete row manifest, including every row retained or archived for fiscal/external-payment history. Unknown rows and relationships remain preserved.
5. Run the rollback rehearsal on that restored source copy. Current conservative table retention intentionally blocks deletion of fiscal-connected operational rows. A reviewed archive/report-exclusion procedure for those rows is still required; do not bypass it or invent settlements to release shifts.
6. Only after these proofs exist, prepare a separately reviewed production executor with explicit commit authorization and recovery evidence. The rehearsal function deliberately exposes no production commit path.

The next real driver shift must use the actual vehicle odometer reading. Retained experimental records must be excluded from new operations through a verified archive/reporting approach, not silent deletion or fabricated closures.

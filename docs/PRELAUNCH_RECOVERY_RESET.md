# Prelaunch recovery and reset — BLOCKED, no source deletion

Observed 2026-10-17 Riyadh: existing service srv-db4s1v942hec73epmar0 is live
on ca67811e94eb5d0605355bc3253640209a8a6248, auto-deploy OFF. Its matching
PostgreSQL18 database is dpg-db4rjgo473hc738soueg-a in Frankfurt, plan 0.1c-256mb,
1GB. Its external IP allowlist is empty. Preserve that restriction.

The connector cannot query the private database and does not expose authenticated
shell, full backup download, restore or provider backup-state operations. Current
provider retention/PITR and a current successful backup timestamp remain
UNVERIFIED; this is not evidence that provider backups are absent. The existing
isolated recovery database dpg-db50dcdckfvc738gsrhg-a is suspended. Do not resume,
purchase or upgrade a resource without separate owner authorization. Historical
restore tests are not proof of today's source recovery.

## Required private execution (no public PostgreSQL exposure)

An authenticated private service shell or controlled internal recovery runner must
use the existing canonical DATABASE_URL. Credentials and archive keys must stay in
private secrets, never command text, logs, GitHub, customer downloads or this report.

1. Enter a controlled maintenance window, stop NEW customer orders and financial
   mutations without interrupting committed recovery work. Record source revision,
   database identity, attachment sources and the exact freeze time. No destructive
   reset may run while real orders or live financial writes are possible.
2. Using the existing tools/portable_backup.py, make a FULL database-mode encrypted
   archive: `python -m tools.portable_backup backup --encrypt --output <new-private-path>`.
   Supply the existing/approved 32-byte KPS_BACKUP_KEY privately. A business export
   excludes authentication and is NOT a full backup. Verify ciphertext SHA256,
   authenticated decryption, manifest file digests, exact table counts, primary ID
   digests, invoice references, financial totals and attachments/static bytes.
3. Run `python -m tools.prelaunch_inventory --output <new-private-manifest>` inside
   the same private source environment. The tool is strictly READ ONLY and has no
   reset option. Unknown tables remain preserved. All approved deletion counts
   start at ZERO. Inventory the DB's own sequences and stored filesystem attachments
   as well as portable table data; verify complete archive coverage.
4. Restore into a DISTINCT isolated database (never another schema in the source).
   The existing portable restore accepts only explicitly named local test databases
   or the specifically bound INTERNAL recovery resource. Keep recovered outbox,
   callbacks, payments, mail/SMS and dispatch workers isolated; do not transmit
   archived messages. Restore attachments to a new private directory.
   `python -m tools.portable_backup restore-drill --input <encrypted-path> --target-database-id <bound-id> --assets-output <new-private-directory> --evidence-output <new-private-evidence>`.
   Configure KPS_RESTORE_DATABASE_URL privately for the bound isolated target.
5. Independently reconcile source/archive/restored counts, identity digests,
   immutable orders/invoices, cash/card/refund/custody totals, shifts and vehicle
   history; prove newer source writes were not lost or overwritten. Record measured
   RPO/RTO and ciphertext/restore evidence. Synthetic local tests do not satisfy
   this actual-source gate. Keep unresolved recovery alerts visible. For each old
   alert document its specific event/cause, provider response, repair and independent
   verification before owner acknowledgment; don't mass-acknowledge warnings.
6. Store the encrypted archive in restricted durable storage with separate key
   custody, access logging, retention schedule and legal hold. Retention is record
   dependent: preserve required tax/financial evidence and outstanding disputes for
   the applicable period confirmed by the authorized accountant/legal review. No
   arbitrary blanket deletion date is approved by this runbook. Apply documented
   privacy retention to non-regulated test personal data after holds expire.
7. Review potentially issued fiscal_documents, fiscal policy, external payment
   attempts/receipts/refunds/webhooks and financial records individually. The owner
   test-data declaration alone does not authorize deleting issued fiscal evidence.
   Preserve security/access audits, invoice sequences/counters, actual employee
   accounts, branches, catalog, vehicles/plates and all service configurations.
8. Only after ALL prior gates pass, prepare a row-scoped, FK-ordered reset manifest
   with reviewed record IDs, source fingerprints and expected counts. Implement and
   rehearse its atomic transaction in the isolated restored database. Archive/retain
   required evidence; preserve immutable tables and triggers. Fail on an unexpected
   source change or remaining dependency. No executable deletion/reset was added in
   this checkpoint because the actual inventory and restoration are unavailable.
9. Reconcile no orphan FKs, experimental balances or active test shifts remain in
   the new operational baseline. Preserve invoice references and never reset/reuse
   regulated numbers. The first production driver shift must record REAL current
   odometer; no fabricated mileage or rewriting closed history.
10. Run the synthetic delivery journey in a separate isolated test database, then
    approve a narrow release and rollback plan. Never seed a synthetic order into
    the clean production source. Reopen real orders only after final owner approval.

## Immediate economical option

Use the existing portable export/encryption and an already available authorized
private compute path plus durable encrypted storage. No new paid server/database
is required by the code. Whether existing provider retention/storage is adequate
cannot be priced or certified until its actual state and required retention are
verified. Preserve the current 500-user capacity failure as a separate hosting
limitation; no upgrade or large load run is authorized by this checkpoint.

## Applicability evidence remains open

The application reports Phase2 unsupported. No current seller notification or
applicability decision has been read from the private operating source in this
checkpoint. A Phase1 TLV QR is not Phase2 integration evidence. Confirm the
authorized Reem seller profile and ZATCA notification/applicable date before
activation; do not delete potentially issued tax documents.

Official references reviewed: ZATCA Wave25 announcement
https://zatca.gov.sa/en/MediaCenter/News/Pages/Wave25-E-invoicing.aspx
and SDAIA transfer safeguards
https://sdaia.gov.sa/Documents/StandardContractualClausesForPersonalDataTransferEN.pdf .
These general sources do not establish this issuer's applicable phase or this
installation's cross-border compliance. The actual transfer assessment, lawful
basis, controller details, retention and processor safeguards remain to be evidenced.

## Focused CI checkpoint

The existing capacity selector recognizes `[prelaunch-focused]` as an explicit
request NOT to repeat expensive load certifications. Dependency/security and
ordinary regression jobs remain enabled. Capacity output remains NOT CERTIFIED,
all historical failures remain open, and no threshold or workload was changed.
An unmarked final-release commit still requires the complete capacity selector.
This checkpoint is not a release certification and must not be merged/deployed.

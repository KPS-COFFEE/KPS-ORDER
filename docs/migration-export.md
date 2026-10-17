# Versioned export and protected restoration

The independent application keeps operational rows, attachments and reports in PostgreSQL. Updating code uses checksum-verified forward migrations; it never replaces running data with an earlier backup.

An authenticated brand owner can download /api/admin/export. The v1 business ZIP includes its brand, original branch numbers/source identifiers, catalog/options, customers and staff identities needed for actor references, orders and immutable snapshots, conversation text/images, driver tasks, collections/refunds, shifts, custody events, closures and saved reports. The export uses one repeatable-read snapshot and excludes usernames/password hashes, sessions, OTP challenges, authentication counters and replay records. Other brands cannot enter the business archive.

The manifest describes source revision, migration checksums, scope, creation time, row counts, monetary totals and every file's size/SHA-256. BYTEA becomes individually hashed blobs; JSON rows refer to their exact files. Original static KPS images are included. Preserve primary IDs/source identifiers during eventual TILQA import, reject repeated source records, and never replay historical sales, collections or custody transfers. This export does not activate a TILQA integration.

Brand-scoped recovery observations, backup state and durable owner alerts are included in both formats, with their table names declared separately in the manifest. They contain no signing/API secrets, raw provider payloads or download URLs. Retain these as historical source observations; importing them does not activate old hosting configuration or replay alerts. Conversations without an order retain their explicit service/branch context and messages.

The browser download has a 64 MiB uncompressed limit to protect the smallest service. Larger datasets need the private backup tool or a separately scoped export process. Failure does not delete or change data.

## Private full backup

Set DATABASE_URL privately in the operating environment, then run:

    python tools/portable_backup.py backup --output /protected/kps-backup.zip
    python tools/portable_backup.py verify --input /protected/kps-backup.zip

The output is exclusively created with permissions 0600 and cannot overwrite an existing file. Full backup includes authentication records and must be treated as secret. Credentials are never printed or passed in command arguments. Copy the verified file to separate encrypted storage with an explicit access/retention policy. The example directory is a placeholder; it is not evidence of configured off-site storage or scheduled backup.

File/checksum verification proves artifact integrity. It does not prove database restoration.

## Separate restore drill

Provision an isolated PostgreSQL database on a local test host named test_* or *_test, set KPS_RESTORE_DATABASE_URL privately, and run:

    python tools/portable_backup.py restore-drill --input /protected/kps-backup.zip

Restoration is permitted only to a newly created kps_restore_<UUID> schema. It refuses existing schemas, runtime/production database names, remote production hosts, URL options or mismatched migration checksums. It applies the same migrations without catalog seeding, restores rows/bytes in dependency order, advances the chat sequence, and checks counts/financial totals before committing. Every failure rolls back the new schema. It never overwrites the source or newer orders. Use matching code first; apply newer forward migrations after verifying the recovered snapshot.

This tool is a portable application-level PostgreSQL snapshot. Managed Render automatic backups/PITR, retention, last successful remote backup, disaster-loss window and restoration time need actual configuration and measurement. No scheduled cloud backup or successful restore is claimed merely because the code exists.

Before TILQA cutover, verify customer/order counts, historical options/prices, attachment/report bytes, monetary totals, open shifts and unresolved tasks. Take a protected final snapshot, choose one receiver for new orders, and preserve post-cutover orders during rollback. The KPS domain and branding remain; switching customer traffic requires the owner's authorization after launch gates pass.

## Complete-row and sequence preservation (2026-10-17)

New private full snapshots include checksummed `metadata/sequences.json` with
all source sequence configurations, allocated `last_value` and `is_called`.
The isolated restore preserves these values exactly; it never reconstructs
numbers from maximum retained rows. This includes unused allocated numbers and
sequences that have not yet been called. Older snapshots without this evidence
remain readable for inspection, but this strict restoration procedure requires
a new snapshot before claiming complete recovery.

Rows are imported with `OVERRIDING SYSTEM VALUE` to preserve existing generated
identity values. Every complete restored row, including fiscal JSON and QR,
is compared with the archive before recovered print credentials/queues and
ephemeral worker observations are deliberately retired. Results include
`complete_rows_verified`, per-table `row_sha256`, `sequence_state_verified` and
`sequence_state_sha256`. These flags prove the supplied archive's restoration;
they do not prove that the archive came from current production or was stored
in durable storage. Recording a restore drill requires all these flags as well
as restored static assets. `owner_phone_challenges` is included only in full
private archives and excluded from business exports as authentication material.

For actual source execution, use the exact source revision's migration files.
Copying a newer mixed development checkout into production or running its
migrations is not part of a backup. Before restoring a production snapshot,
compare its migration checksums to the isolated matching-code checkout. A
controlled source write window is necessary for final source reconciliation:
PostgreSQL sequence allocation is not MVCC, and a snapshot by itself does not
prove that no new orders arrived afterwards.

The private command is:

```sh
python -m tools.portable_backup backup --encrypt --output /private/new-name.kpsenc
```

Encryption uses an independently
protected strict base64 or URL-safe base64 encoding of exactly 32 bytes in
`KPS_BACKUP_KEY`; never print that value. The envelope is `KPSENC1` plus NUL,
a 12-byte random nonce and AES-GCM authenticated ciphertext. Preserve the key
in approved secret storage separately from the archive. The CLI uses exclusive
0600 creation and fsync, decrypts and checks the resulting archive, but its
local file is not durable storage. An authorized private-shell operator may
transfer only that ciphertext through an approved protected upload session,
finalize durable retention and verify the stored object's digest. Download and
restore that exact stored object, not a second independently generated export.
Do not place a plaintext archive, encryption key, credentials or signed transfer
URL in public logs, repositories or launch reports.

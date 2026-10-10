# Independent KPS ORDER hosting and recovery

Status at the V5 preflight on 2026-10-10: the existing independent Docker
service is live at `bf654354ab907597d81d2ff4d269fc33f8088794`, on
`agent/kps-order-emergency-v4`, with auto deploy and previews disabled.
`/ready` returned 200, database connected, five migrations, zero verified
products and branches, and ordering unconfigured. The independent PostgreSQL
18 database is available with its external IP allowlist empty. The owner
selected My Workspace previously; this was reconfirmed through resource
metadata. Reuse these resources for V5. No customer routing has moved.

Private DATABASE_URL binding and `/ready` health check were completed in V4.
The historical setup details below describe the original enrollment; they
are not remaining V5 binding tasks.

## Verified source and resource audit — 10 October 2026

| Item | Verified value |
| --- | --- |
| Current source service | `srv-daehgeqd0e5s738h8b8g`, static site `kps-order-test` |
| Current public URL | `https://kps-order-test.onrender.com` |
| Repository / branch | `KPS-COFFEE/KPS-ORDER` / `agent/apex-v2-integration` |
| Current live commit | `b9d4f523c18981032151f5ab5dad57bd23f946c5` |
| Current live deploy | `dep-daloetqd0e5s7383vc8g`, live since 17 September 2026 |
| New implementation branch | `agent/kps-order-emergency-v4` |
| New independent database | `dpg-db4rjgo473hc738soueg-a`, `kps-order-emergency-v4-db`, available |
| New independent web service | `srv-db4s1v942hec73epmar0`, `kps-order-emergency-v4` |
| New independent test URL | `https://kps-order-emergency-v4.onrender.com` |
| Database / database user | `kps_order_emergency_v4_db` / `kps_order_emergency_v4_db_user` |
| Confirmed workspace | My Workspace, `tea-d9ttnf142hec7393049g` |

The initial audit found only APEX PostgreSQL instances in the available
workspaces, so the newly authorized independent instance was necessary. Other
databases must not be reused or modified by this temporary system. The current
KPS ORDER static site in My Workspace remains available throughout development.

## Concrete deployment configuration

`render.yaml` defines **only** the paid Docker web service. It deliberately
omits a `databases` section because the managed PostgreSQL 18 instance already
exists in Frankfurt. The web service must use that instance's private internal
URL. Frankfurt is a regional choice for Saudi users, not a measured latency
result. Keeping app and database in one region enables Render's private
connection. The database has no public IP allowlist; automatic app deploys and
automatic preview stacks are disabled. No custom domain is attached.

| Resource | Configuration | Estimated full-month charge |
| --- | --- | ---: |
| Web service (created) | `0.5c-512mb`, 0.5 CPU / 512 MB, one instance | USD 7.00 |
| PostgreSQL compute (created) | `0.1c-256mb`, 0.1 CPU / 256 MB | USD 6.00 |
| PostgreSQL storage (created) | 1 GB at USD 0.30/GB/month | USD 0.30 |
| **Base estimate** | **One app + one independent database** | **USD 13.30** |

Prices were checked against official Render sources on 10 October 2026. This is
a starting test configuration, not a claim that load certification passed.
The estimate excludes taxes, OTP/messages, workspace subscription charges if
applicable, excess bandwidth/build usage, optional external backup storage, and
temporary recovery instances. Increase compute only after measured pressure;
observe database storage and keep headroom. Render permits 1 GB or multiples
of 5 GB for initial database storage. Storage increases cannot be reversed.

Small files, attachments, banner images and report bytes are stored as protected
PostgreSQL `bytea` records with their metadata and access rules. They survive
app restarts/deploys and are included in database backups and restores. No
attachment disk or separate object storage purchase is required for this
initial implementation. Upload size limits and storage monitoring remain
necessary: putting large or unbounded media in a 1 GB database increases
storage, memory, transfer and backup cost. Move to an authorized private object
store if measured file volume justifies it; retain stable attachment IDs and
checksums during that migration.

Docker copies runtime `app`, `data`, `migrations`, `public`, staff metric
documentation, the exact private `tools/portable_backup.py` operator CLI and
pinned requirements. It excludes other legacy tools, the historical root site, uploaded handoff, tests,
local environments, credentials and backup files. The process runs as an
unprivileged user. Uvicorn listens on `0.0.0.0:$PORT`; core lifespan runs
versioned, additive migrations under the PostgreSQL advisory lock. No startup
reset/reseed of operational data is permitted.

The private backup CLI can be run in Render Shell after environment binding.
Its presence in the image does not persist generated backup files: copy verified
archives to an authorized protected destination outside the ephemeral web
filesystem, and use a separate permitted target for restoration drills.

## Configuration and test deployment

1. Use **My Workspace** (`tea-d9ttnf142hec7393049g`), already selected by the
   owner. Verify the branch/files are available remotely. Local Git push was
   blocked by unavailable local GitHub credentials; the authenticated GitHub
   connector can publish the finished branch without requesting tokens in chat.
2. Validate `render.yaml` with the official schema or `render blueprints validate`
   using Render CLI v2.7.0 or higher. The current environment has no Render CLI
   or Docker executable, so container build/remote deploy verification must not
   be reported as passed before it is actually run.
3. Configure the **existing** independent web service
   [in Render Dashboard](https://dashboard.render.com/web/srv-db4s1v942hec73epmar0).
   The service-only Blueprint is a reviewable configuration reference; do not
   apply it as a second stack that creates another service. Open the existing database's
   [Render Dashboard page](https://dashboard.render.com/d/dpg-db4rjgo473hc738soueg-a),
   choose **Connect → Internal Database URL**, and copy that value directly into
   the new service's private `DATABASE_URL` field in Render. Credentials must
   stay in Render/private execution; do not send them in chat or commit them.
4. Set `PUBLIC_BASE_URL=https://kps-order-emergency-v4.onrender.com` and
   `APP_ALLOWED_HOSTS=kps-order-emergency-v4.onrender.com`. Do not use a wildcard.
5. Retain `SESSION_COOKIE_SECURE=true`. Bootstrap the owner through the private
   CLI documented by core, with an independently verified identity and a
   password entered privately. There are no public owner login defaults.
6. `OTP_PROVIDER=disabled` explicitly rejects customer verification with a
   provider-unavailable error. It provides no development code or bypass. For
   actual Twilio Verify activation, configure `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID` privately, select the approved
   SMS/WhatsApp channel and change `OTP_PROVIDER=twilio_verify` only after the
   real provider flow passes. Do not claim provider enrollment or sending is
   complete because code exists.
7. Confirm the actual deploy commit, `live` status, `/health` liveness and
   `/ready` database/schema readiness. Check sanitized errors and run the full
   authorization, workflow, persistence/redeploy and recovery gates against
   isolated test data before any customer routing change.

`order.kps.sa` remains the intended customer address. Domain/DNS changes and
customer cutover require passing launch gates and the owner's final conversion
approval. Do not modify the existing static service or its source branch as a
side effect of this test deployment.

### Current deployment capability boundary

The authenticated Render connector can create resources, update service
environment variables from supplied string values and run read-only PostgreSQL
queries. It does not expose the database connection-info endpoint or a
`fromDatabase` binding for direct web-service creation. The environment has no
Render CLI, no `RENDER_API_KEY` environment value and no documented saved CLI
authentication config. Installing the CLI alone would not supply authorization.
Database credentials have not been fetched or printed. Complete the private
connection binding in Render Dashboard; do not request passwords/API keys in
chat as a workaround.

The read-only Render connector also lacks export/recovery-info API operations.
Repository monitoring can call the documented REST endpoints after the owner
configures a private Render API key; having the monitoring code does not prove
that cloud backup observations or delivery have been enrolled.

## Backup guarantees and remaining recovery gates

Paid Render PostgreSQL provides continuous point-in-time recovery (PITR).
Its recovery window is 3 days for Hobby workspaces and 7 days for Pro or higher;
the actual selected workspace plan must be verified. PITR restores into a
**new** instance and cannot target the most recent 10 minutes. On-demand logical
exports are retained by Render for 7 days. Deleting a database removes its
Render backups, so deletion is not an archiving strategy.

Because files are in the database, a successful full database recovery restores
both records and file bytes. This avoids a mismatch between a database restore
point and a daily attachment-disk snapshot. A Render persistent disk would be
preserved across deploys, but it would be accessible to only one instance and
would prevent zero-downtime app deploys; the no-disk configuration avoids those
constraints. Neither architecture by itself proves a tested disaster recovery
procedure or zero data loss.

The handoff requires a separate protected backup copy, not a second hosting
provider. Managed Render PITR stores recoverable copies separately from the
operating database and restores them into a different instance; using that
protection together with a protected logical export is the intended same-provider
backup design. An off-provider copy can add disaster resilience later, but it is
not an extra mandatory purchase or launch gate introduced by this configuration.
Actual backup availability, retention, monitoring and recovery validation still
need evidence from the deployed database.

Before enabling customer orders:

- Verify the new paid instance's recovery window and record its available
  recovery points. Create a logical export before data-impacting changes.
- Verify the managed automatic PITR copy and create a protected logical export
  covering full database records and attachment/report bytes. Render's recovery
  storage can provide the separate-copy location; another vendor is not required
  by the handoff. A backup saved solely to this service's ephemeral filesystem,
  GitHub source code, or public logs does not count.
- Record the actual retention, schedule, last successful backup, failure
  alert delivery and administration backup-health indicator. Do not claim a
  scheduler or alert exists until deployed and exercised.
- Restore to a distinct empty PostgreSQL 18 database; never overwrite the
  operating database. Check row counts and source IDs, order snapshots and
  amounts, conversations, shifts/collections/closing snapshots, reports and
  every attachment's SHA-256. Measure elapsed recovery time and document the
  recovery point; neither RTO nor RPO has been measured in this hosting audit.

For a manual full custom-format export with PostgreSQL 18 client tools, pass
credentials through a private environment and write to a protected backup
destination outside this repository:

```bash
pg_dump --dbname="$DATABASE_URL" --format=custom --file="$KPS_BACKUP_PATH"
pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname="$KPS_RESTORE_DATABASE_URL" "$KPS_BACKUP_PATH"
```

The restore URL must refer to the distinct empty recovery database. The dump
includes table `bytea` data; do not exclude attachment/report tables. Protect
and, where required, encrypt both exports and their metadata. Changing the
application version must never replace the operating database with an older
copy or discard orders created after the backup.

## Repository backup monitoring and actual configuration boundary

`app/recovery.py` and migration `004_recovery.sql` retain protected provider
observations, canonical health state and an owner-only durable alert queue in
PostgreSQL. `/api/admin/recovery` reports unknown/unconfigured values until real
provider evidence arrives. A successful backup observation does not establish a
measured restore time. No backup download URL, raw webhook body, API key or
signing secret is stored in recovery tables or returned to the owner panel.

To enable read-only monitoring, configure the following directly in the existing
service's private Render environment, then redeploy:

| Private setting | Value / purpose |
| --- | --- |
| `RENDER_POSTGRES_ID` | Exactly `dpg-db4rjgo473hc738soueg-a`; any other database is rejected |
| `RENDER_BACKUP_BRAND_ID` | Existing owner brand ID; default `kps` |
| `RENDER_API_KEY` | Authorized private Render API credential; never paste into chat or commit |
| `RECOVERY_POLL_ENABLED` | `true` by default when the bound API credential exists; `false` disables background checks |
| `RECOVERY_POLL_INTERVAL_SECONDS` | Default 900; enforced range 900–86400 |

After database migrations, an optional task in the existing app process performs
fixed-resource GET requests to `/v1/postgres/{id}/export` and `/recovery` every
15 minutes by default. It creates no export, restore instance, cron service or
other paid resource. It stops when private monitoring configuration is absent.
The owner can request the same check with a CSRF-protected refresh action.

The official recovery response supplies `recoveryStatus` (`AVAILABLE`,
`BACKUP_NOT_READY` or `NOT_AVAILABLE`) and optional `startsAt`. The app records
the actual earliest recoverable timestamp only when available. The API does not
supply an upper recovery timestamp, configured retention duration, exact
potential-loss window or measured RTO, so those fields remain unverified. Export
entries supply IDs and creation timestamps; their signed download URLs are
discarded. `BACKUP_NOT_READY` is a visible blocker, not an invented failure.
An observed `NOT_AVAILABLE`, signed backup/PITR/WAL failure, or failed provider
verification creates an appropriately labeled durable owner alert. Repeated
unacknowledged failures of the same type are grouped within 24 hours while all
observations remain available. Owner acknowledgment retains the record; it
does not erase or retroactively fix a failure. The durable failure queue now
prepares one encrypted mail per alert and active registered owner with verified
email, using the existing shared outbox after the brand email channel is active.
Dispatch rechecks that owner's current role, activity and verified recipient.
`/api/admin/recovery` reports waiting/pending/provider-accepted/delivered states;
provider acceptance does not prove inbox receipt. The existing live channel must
be configured and a failure alert received before calling cloud delivery proven.

The existing web process also runs bounded runtime checks when the independent
database is bound and `MONITOR_ENABLED` is not `false`. The defaults are 120-second
checks, a 300-second queued-mail age threshold and a 2000ms DB-latency threshold.
Optional private settings are `MONITOR_INTERVAL_SECONDS` (60–3600),
`MONITOR_MAIL_QUEUE_AGE_LIMIT_SECONDS` (60–86400),
`MONITOR_DATABASE_LATENCY_LIMIT_MS` (100–30000), and
`MONITOR_DATABASE_STORAGE_LIMIT_BYTES` (leave unset until capacity is observed).
The owner-only `/api/admin/monitoring` reports DB size/latency, actual cgroup
memory, process CPU totals, ephemeral disk pressure, aggregate server errors,
mail failures/backlog and stale backup observations. Successful runtime checks
never update backup success timestamps. Monitoring creates no paid service.
An in-process worker cannot send reliable downtime alerts while its service or
DB is stopped; native Render or another existing independent notification route
must be configured and exercised. That separate gate remains explicitly
unverified in the API until external evidence exists.

`tools/portable_backup.py` reuses the full archive engine. `backup --encrypt`
uses a dedicated 32-byte base64 `KPS_BACKUP_KEY` from private secrets and writes
an authenticated AES-GCM envelope to an exclusive mode-0600 file. This local
file is not durable external storage; protected Render logical exports remain
the existing separate-copy route, with actual retention verified separately.
The restore tool still accepts local PostgreSQL test databases. For the existing
Render drill resource only, it also accepts
`--target-database-id dpg-db50dcdckfvc738gsrhg-a` and its exact INTERNAL hostname
in `KPS_RESTORE_DATABASE_URL`. Public/external hostnames and the operating DB
are rejected. It creates a fresh UUID schema and never replaces an existing
schema. `--assets-output` must name a new directory; copied static images and
every restored database blob are checked against SHA-256. Primary identifiers,
record counts and monetary totals are compared to the snapshot after insertion.
`--evidence-output` writes measured elapsed time, snapshot timestamp/revision and
opaque identifier hashes to a new private file. `--record` records only a
verified full drill including restored static assets, with matching runtime
revision, in the bound source's recovery observations. Export or configuration
alone never establishes measured recovery success.

Signed provider events can additionally be ingested at
`https://kps-order-emergency-v4.onrender.com/webhooks/render/recovery` after
configuring `RENDER_WEBHOOK_SIGNING_SECRET` privately and actually enrolling
the endpoint in Render. The handler verifies Standard Webhooks HMAC-SHA256
against the raw body, a five-minute timestamp bound and the exact new database
ID. Event retries are idempotent; a reused ID with changed content is rejected.
Render webhooks require a Pro workspace or higher. No workspace upgrade or
webhook enrollment has been performed; optional API polling avoids adding that
subscription merely for this test configuration. Signed event handling is
implemented and tested with synthetic signatures, which is not cloud delivery
proof. The existing Render connector cannot perform enrollment or obtain a
signing secret through its exposed tools.

CI uses only an ephemeral PostgreSQL 18 test database and synthetic provider
responses/signatures. It restores actual application archives into a separate
isolated test schema and runs Chromium against the real test app/database;
the retained screenshots/PDF/CSV examples contain synthetic test data. This
validates repository behavior, not Render PITR operation. Capture a real
bound-database observation, provider failure delivery drill and distinct
recovery instance validation before claiming the cloud recovery gates passed.

## Official references

- [Render pricing](https://render.com/pricing)
- [Render compute plans](https://render.com/docs/compute-plans)
- [PostgreSQL creation, private connections and storage](https://render.com/docs/postgresql-creating-connecting)
- [PostgreSQL recovery and backups](https://render.com/docs/postgresql-backups)
- [Render persistent disks](https://render.com/docs/disks)
- [Render regions](https://render.com/docs/regions)
- [Blueprint YAML reference](https://render.com/docs/blueprint-spec)
- [Render webhooks and signed event protocol](https://render.com/docs/webhooks)
- [Official Render API OpenAPI schema](https://api-docs.render.com/openapi/render-public-api-1.json)
- [List PostgreSQL exports](https://api-docs.render.com/reference/list-postgres-export)
- [Retrieve PostgreSQL recovery information](https://api-docs.render.com/reference/retrieve-postgres-recovery-info)
- [Small-business hosting cost model](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses)

## V5 mail and safe release

The existing paid resources are reused with no mandatory new worker service,
map provider, disk or database. The app process runs a durable report retry
worker and, only with an authorized configured provider, the mail worker.
No real email provider, SMTP credential or verified sender has been supplied.
Configure secrets only in the existing service environment: EMAIL_PROVIDER
(resend or smtp_ssl), EMAIL_FROM, MAIL_OUTBOX_KEY (base64-encoded 32 random
bytes) and RESEND_API_KEY or dedicated SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD
(SSL port 465). Do not use a Gmail account password in chat.

Start the email channel in testing, prove arrival by consuming a one-use
link in an authorized inbox, then enable active. Current owner password
access is preserved until that succeeds. The provider response alone never
marks a channel delivery-proven. SMS and WhatsApp stay disabled and honest.

V5 schema changes are additive. Original five migration checksums remain
unchanged. Portable format v2 includes identity, thresholds, drafts, report
renderings and notifications in private full recovery; business exports
exclude sessions, identity tokens and encrypted authentication-mail payloads.
Use an isolated empty test target for restore. Never substitute a historical
backup for the operating database or erase orders received after a release.

A rollback must use a schema-compatible, security-reviewed V5 revision.
An arbitrary pre-V5 image lacks guest/channel/invitation authorization and
is not an approved operational rollback. Keep the same live database and
its newer records. Customer DNS cutover remains a separate owner-approved
step after actual operating setup and acceptance.

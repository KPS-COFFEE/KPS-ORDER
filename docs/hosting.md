# Independent KPS ORDER hosting and recovery

Status: the owner selected **My Workspace** and the independent paid PostgreSQL
instance below has been created and is available. The new web service has not
been created or deployed; no customer traffic or domain has been moved. Paid
independent resources and test deployment are already authorized. Do not create
a second database while completing this service deployment.

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
| Database / database user | `kps_order_emergency_v4_db` / `kps_order_emergency_v4_db_user` |
| Confirmed workspace | My Workspace, `tea-d9ttnf142hec7393049g` |

The initial audit found only APEX PostgreSQL instances in the available
workspaces, so the newly authorized independent instance was necessary. Other
databases must not be reused or modified by this temporary system. The current
KPS ORDER static site in My Workspace remains available throughout development.

## Concrete deployment configuration

`render.yaml` now defines **only** the new paid Docker web service. It deliberately
omits a `databases` section because the managed PostgreSQL 18 instance already
exists in Frankfurt. The web service must use that instance's private internal
URL. Frankfurt is a regional choice for Saudi users, not a measured latency
result. Keeping app and database in one region enables Render's private
connection. The database has no public IP allowlist; automatic app deploys and
automatic preview stacks are disabled. No custom domain is attached.

| Resource | Configuration | Estimated full-month charge |
| --- | --- | ---: |
| Web service (not yet created) | `0.5c-512mb`, 0.5 CPU / 512 MB, one instance | USD 7.00 |
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

Docker copies only runtime `app`, `data`, `migrations`, `public` and pinned
requirements. It excludes the historical root site, uploaded handoff, tests,
local environments, credentials and backup files. The process runs as an
unprivileged user. Uvicorn listens on `0.0.0.0:$PORT`; core lifespan runs
versioned, additive migrations under the PostgreSQL advisory lock. No startup
reset/reseed of operational data is permitted.

## Configuration and test deployment

1. Use **My Workspace** (`tea-d9ttnf142hec7393049g`), already selected by the
   owner. Verify the branch/files are available remotely. Local Git push was
   blocked by unavailable local GitHub credentials; the authenticated GitHub
   connector can publish the finished branch without requesting tokens in chat.
2. Validate `render.yaml` with the official schema or `render blueprints validate`
   using Render CLI v2.7.0 or higher. The current environment has no Render CLI
   or Docker executable, so container build/remote deploy verification must not
   be reported as passed before it is actually run.
3. Apply this **service-only** Blueprint to create exactly one independent web
   service. Do not create another database. Open the existing database's
   [Render Dashboard page](https://dashboard.render.com/d/dpg-db4rjgo473hc738soueg-a),
   choose **Connect → Internal Database URL**, and copy that value directly into
   the new service's private `DATABASE_URL` field in Render. Credentials must
   stay in Render/private execution; do not send them in chat or commit them.
4. Set `PUBLIC_BASE_URL` to the allocated HTTPS service URL and
   `APP_ALLOWED_HOSTS` to its exact hostname. Do not use a wildcard. Confirm the
   actual allocated URL rather than claiming the draft service name is live.
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

## Official references

- [Render pricing](https://render.com/pricing)
- [Render compute plans](https://render.com/docs/compute-plans)
- [PostgreSQL creation, private connections and storage](https://render.com/docs/postgresql-creating-connecting)
- [PostgreSQL recovery and backups](https://render.com/docs/postgresql-backups)
- [Render persistent disks](https://render.com/docs/disks)
- [Render regions](https://render.com/docs/regions)
- [Blueprint YAML reference](https://render.com/docs/blueprint-spec)
- [Small-business hosting cost model](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses)

# Private production evidence before PR65 activation

The 23 October read-only Render control-plane check still identifies the live
service as `kps-order-emergency-v4` (`srv-db4s1v942hec73epmar0`), source
`4e4d781b4fa82b7459410694ff5c4ac06842e6e7`, deploy
`dep-dbdckefb9u1s7397qt50`. Automatic deployment remains off. The existing
PostgreSQL18 instance `dpg-db4rjgo473hc738soueg-a` is available in Frankfurt,
with an empty external IP allowlist. No deployment or configuration was changed.

A minimal metadata SELECT via the hosted Render connector was rejected:
`INVALID_ARGUMENT`: the database blocks all external connections, and the
hosted connector has no IP address to allowlist. The current environment has no
Render API key or database credentials. This is an access limitation, not proof
of database failure. Network isolation was retained.

## Concrete read-only collection

`tools/production_release_preflight.py` can run in the already authorized
private service environment against its existing `DATABASE_URL`. It does not
import application startup, initialize a pool, run migrations, scan orders,
change grants, write audits, export customer data, or execute arbitrary SQL.
Its target is fixed to the existing KPS ORDER database and public schema.
Unrelated projects, alternative services, connection options and host-address
overrides are rejected. External connections require TLS.

Run the reviewed candidate tool and its complete migration/source directories
inside an authorized private shell; do not start the candidate application.
The checkout's code and migration hashes must match the exact candidate under
review. Existing production environment credentials stay in the shell and are
never placed in command arguments, screenshots or chat.

```bash
python tools/production_release_preflight.py --output /tmp/kps-release-evidence.json
```

For the already authorized Fawzi account, first set one private shell variable
`KPS_REVIEW_STAFF_PHONE` through the secure shell environment, then:

```bash
python tools/production_release_preflight.py --output /tmp/kps-release-staff-evidence.json --staff-phone-env KPS_REVIEW_STAFF_PHONE
```

The tool uses a repeatable-read, read-only transaction, 2-second statement
timeout, 250ms lock timeout, 5-second idle transaction timeout and 15-second
whole-transaction timeout. Evidence is written exclusively to a new mode0600
file; existing files and symlinks cannot be overwritten. Failures print no
database URL, credentials, SQL response, phone or provider data. Private output
must stay in restricted operational storage, not Git or public artifacts.

The report compares actual applied migration names and hashes against both
normal and guarded candidate migration directories. Unknown or changed applied
versions fail checksum compatibility. Pending normal and guarded migrations
are reported separately. The reported table row estimates come from PostgreSQL
catalog statistics; they are not exact row counts or a preservation digest.
Lock mode totals are metadata observations, not a contention-duration drill.

Staff readback is targeted to one authorized phone, excludes customer accounts,
names, raw phone values, password/session/OTP/token fields and unrelated
capabilities. It returns account state, home-brand branches and the current
source-policy vehicle/driver/printer capability projection, including denials.
Active vehicle swaps still require the existing owner/admin role and explicit
capability; printer access retains branch/resource scope. Cross-brand
membership and live route operation are not inferred from a home-brand result.

## Independent remaining gates

Even a successful collector run always leaves `release_approved`,
`backup_verified`, `restore_verified`, `migration_duration_verified` and
`production_migration_permitted` false. The owner must review the actual evidence
and all remaining gates:

1. Actual applied registry compatibility, including the previously unexplained
   live registry count; no unknown checksum may be ignored.
2. A current full encrypted private backup artifact, independently verified and
   stored durably. A provider plan, successful backup observation, old suspended
   restore-drill service or a local ephemeral filename is not that artifact.
3. Successful isolated restore and financial/static-asset reconciliation of
   that artifact, with source revision and artifact digest bound to evidence.
   Recovery-state/provider event timestamps alone do not establish this.
4. Realistic size/contention/startup migration duration and an approved recovery
   procedure for the pending schema changes. Source review and synthetic fixture
   startup remain evidence with narrower scope.
5. Actual Fawzi home-brand permissions and branches, then any separately granted
   ENBISHAN membership. No new permissions are granted by this tool.
6. Exact final candidate checks, operating menu/domain/provider inputs, and the
   final owner activation approval required by the original execution order.

No live collector execution, current backup artifact, production restore,
registry compatibility, actual Fawzi grants or production migration duration is
claimed by this preparation.

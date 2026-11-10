# Official domain cutover preparation — 18 October 2026

Status: implementation and isolated contract tests only. Production transition is NOT complete. No Render, DNS, account, printer, or production-data settings changed.

## Runtime preservation evidence

The authenticated Render inspection owned by the release operator and read-only `https://order.kps.sa/health` identify live revision `466bb9868768002d35f277a8aa4628fefc431126`. Security candidate is `6b0739678fefb62c45baa792a7de6d9e107cb0a5`; existing security stream was `4d1f8ae08a119b50d83f4cfafe57daaab8bbd637` at inspection (use `git rev-parse HEAD` for the completed candidate).

Live SHA is an ancestor of the security candidate (`git merge-base --is-ancestor` exit 0). Security runtime changes are limited to auth, catalog/map parsing, employee SMS, exports, SMS policy, runtime dependencies/container tooling, and associated UI/tests/docs. A direct tree comparison live → security has no changes in `app/operations.py`, `app/driver_checks.py`, `app/branch_printing.py`, `app/orders.py`, `app/branch_payments.py`, `app/fiscal.py`, `app/fiscal_issuance.py`, or `app/invoice_profiles.py`. This is source preservation evidence, not proof of every live setting or physical device.

`Collection.reference` remains optional. Only delivery collections by the assigned driver can omit the card reference; branch collections still require it. Cash/card/split checks, locked order/task/shift, payment state rejection, transaction idempotency, and cash-custody entries remain present. AR/EN driver collection UI fixture executed successfully, including exact payloads and split boundaries.

Observed remote references (not merged):

| Stream | Observed HEAD | Treatment |
| --- | --- | --- |
| driver payment hotfix | `466bb9868768002d35f277a8aa4628fefc431126` | Same as live |
| emergency branch | `6d9d556d0380732e3de6fc04a0cc620eb4cf3112` | Historical production stream; do not replace newer runtime with this head |
| Enbishan isolated development | `ea16b118fab7d0ec7da0611d7734f9d39d292d52` | Unpublished integrated readiness development; expressly excluded |
| security closure | `4d1f8ae` before this work | Existing review stream retained |

Other remote refs exist for menu preview, routing, and CI optimization; presence alone does not establish owner approval or production status. No unpublished branch was incorporated.

## Host enforcement

New ASGI `OfficialHostMiddleware` is default OFF. It does not redirect or change cookies. When explicitly enabled, application requests require one exact Host from `KPS_OFFICIAL_HOSTS`. It rejects wildcard/scheme/port configuration and disallows onrender.com in the application allowlist. Arbitrary `Forwarded` / `X-Forwarded-Host` cannot bypass it. Unapproved HTTP hosts receive 421 with no redirect; unapproved WebSocket hosts close with 1008.

The only legacy-host exception is explicitly configured `KPS_INTERNAL_HEALTH_HOSTS` with GET/HEAD on exact `/health` or `/ready`. No `/api`, `/ops`, printer, trailing-slash probe, or mutating request is exempt. Existing `APP_ALLOWED_HOSTS` remains a second independent restriction and must include the configured probe host if internal probes use it. Existing portal host/role routing and Origin/CSRF controls remain independent and must not be relaxed.

Proposed configuration for a later approved deployment (DO NOT apply now):

```
KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED=true
KPS_OFFICIAL_HOSTS=order.kps.sa
KPS_INTERNAL_HEALTH_HOSTS=kps-order-emergency-v4.onrender.com
```

Only include admin/staff domains after their DNS/TLS, employee SMS login, and role boundaries are actually verified. `PUBLIC_BASE_URL` / `APP_ALLOWED_ORIGINS` must match approved official origins. Existing sessions use Secure, HttpOnly, SameSite=strict, host-only cookies. A host-only Render session cannot be transferred to the official domain; a fresh real SMS login is required, with no cookie copying or weakened authentication.

## Required sequence before owner approval to close legacy access

1. Verify authoritative DNS, certificate chain/SAN/expiry, and HTTPS on every approved official domain. Read-only proxied requests: order `/health` 200 with the live SHA; Render `/health` 200 with the same SHA; order `/` HEAD 405 (route only allows GET), HSTS/CSP/security headers present. Admin/staff `/health` returned proxy 502 connection-refused, so their reachability is unverified and cannot be treated as configured. Direct DNS/socket certificate inspection was blocked by sandbox name-resolution restrictions; a successful proxied health request is not a substitute for complete DNS/TLS evidence.
2. Reinstall/update branch bridges gradually to the official HTTPS origin; pair each new computer with a fresh code, never copy device secrets. Keep each old pairing until its replacement is confirmed.
3. Record per-branch device heartbeat, Windows spool result, actual paper confirmation, approval, width, and AR/EN/logo result. The host middleware tests cannot certify a Windows computer or physical printer.
4. Verify real staff SMS login, session, APIs, owner-sensitive step-up, and printer polling/acknowledgement on official domains. Preserve existing Origin and CSRF boundaries.
5. Reconcile all required branch devices and administrative integrations; no old-origin client may remain dependent on Render.
6. Obtain owner approval for exact candidate deployment and configuration changes. Enable the guard only then. Retain Render service/database and verify health/readiness through the configured internal probe host. Test that ordinary legacy-domain access receives 421.
7. Removing Render's public subdomain at the platform level is optional additional hardening, needs separate approved platform verification, and must preserve health probes. The actual tested application Host guard is the prepared protection here.

Rollback of a future approved cutover: disable the new flag to restore the previous host policy; retain a separately verified security-safe application image. Rollback must not reset permissions, accounts, operational data, or printer pairings.

## Verification boundaries

Focused middleware contract suite: 21 PASS. Driver collection UI AR/EN Node fixture: PASS. A broader portal test attempt had 48 PASS / 13 setup ERROR because no isolated TEST_DATABASE_URL was provided; those 13 database cases were not executed or certified. Targeted follow-up belongs in the main CI evidence. Existing `tests/test_split_payment.py::test_delivery_split_only_cash_enters_custody` covers 12 cash/card/split and absent/blank/optional-reference combinations, same-key replay, different-key repeat rejection, role rejection, and cash-only custody accounting; run against isolated PG18. Database collection tests and real browser SMS/print checks are recorded by the main release report. DNS/TLS completeness and branch migration remain BLOCKED until verified. Historical security/image/browser certificates belong to their exact SHA, not automatically to this modified runtime.


## Parallel closure checkpoint — fresh evidence

Source inspected: `80a1d727f6695c4ce3685e726ab5b34674e1cfa0`.

Read-only HTTPS requests repeated on 18 October 2026 returned:

| Endpoint | Result | Observed revision |
| --- | --- | --- |
| `https://order.kps.sa/health` | 200 alive | `466bb9868768002d35f277a8aa4628fefc431126` |
| `https://order.kps.sa/ready` | 200 ready; database connected | same |
| `https://kps-order-emergency-v4.onrender.com/health` | 200 alive | same |
| `https://kps-order-emergency-v4.onrender.com/ready` | 200 ready; database connected | same |
| `https://admin.kps.sa/health` and `/ready` | 502 from access path | unverified |
| `https://staff.kps.sa/health` and `/ready` | 502 from access path | unverified |

One additional short-timeout `/ready` request timed out; the completed 15-second-timeout request returned 200. These are bounded point-in-time observations, not a latency certificate. Public health and ready attest the live revision only, not authenticated behavior or staff/print acceptance.

Focused command at this source: `python -m pytest tests/test_print_bridge_domain_migration.py tests/test_official_host_policy.py -m 'not postgres' -q`: **31 PASS**, **3 deliberately deselected PostgreSQL tests**, no failures/errors/skips. The deselected cases must be run against isolated PostgreSQL before acceptance. Existing Windows CI job in `.github/workflows/emergency-v4.yml` compiles the actual bridge with Windows .NET Framework and invokes `--self-test`; no new Windows execution is asserted at this checkpoint. The current container has no Windows runtime, Mono, or C# compiler. Source/package assertions are not Windows execution.

Mohammed remains **not verified / not changed**. A last-four phone suffix is not sufficient identity proof. A future authorized operator must match the existing account, role, brand and actual assigned branch IDs, obtain owner-sensitive SMS authorization, then grant only `branch_printers_manage` through the existing permission endpoint. No production account read with assumed identity, creation, branch assignment or permission mutation occurred here. Physical paper remains pending a real branch Windows printer test and explicit operator acceptance; simulated `submitted` acknowledgements cannot establish paper output.

The proposed host allowlist above now includes only `order.kps.sa`, because admin/staff reachability has not been established. The guard remains OFF, Render remains running and reachable, and legacy printer registrations remain unchanged. No production merge, deploy, configuration or DNS change was performed.

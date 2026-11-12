# Exact-release check before a Render deploy

Production: `kps-order-emergency-v4` / `srv-db4s1v942hec73epmar0`, My Workspace.

The 2026-10-24 05:31 Riyadh attempt failed with
`FEATURE_RELEASE_NOT_APPROVED`. The next deployment at 05:34:57 succeeded.
An enabled feature requires BOTH acceptance and owner approval SHAs to equal
the exact candidate commit. A previous release's approval is insufficient.

Before each deployment:

1. Pin the intended candidate commit and verify its required CI evidence and
   authorization. This tool validates configuration equality only; it does not
   provide acceptance evidence or grant owner approval.
2. Read the service's actual release configuration. Prepare a JSON object with
   `KPS_MULTIBRAND_ENABLED`, `KPS_CENTRAL_OPERATIONS_ENABLED`, `RUNTIME_REVISION`
   if set, and ALL enabled release-gated feature flags plus their acceptance
   and owner approval SHAs, including brand-specific flags. Use strings.
   Include disabled release-gated flags as `"false"`. Do not export the whole
   environment or include credentials. An omitted optional feature cannot be
   checked, so compare the manifest keys against the service's actual keys.
3. Run `python tools/feature_release_preflight.py --revision FULL_CANDIDATE_SHA`
   with that JSON on stdin. Exit 1 means STOP before triggering Render.
   Never manufacture approvals or disable a live feature to make this pass.
4. If authorized configuration changes are needed, save only in Render's
   environment editor, then re-read the saved release keys and rerun the check.
   The connector's environment-update operation can trigger a deployment;
   do not use it as a save-only substitute.
5. Deploy the specific certified commit. Do not substitute a branch HEAD that
   changed after the check. Preserve the other service settings and keep the
   existing runtime approval check enabled.
6. Verify the deployment is live, both official domains' `/ready` report the
   candidate revision, database connected and data preservation verified.
   Review startup, failure events and HTTP errors after the swap.

This is an operator preflight, not an automatic Render hook. It makes no API
calls, database connections, configuration changes or deployment requests.

## Separate 05:20 event

Render recorded `server_failed` / `earlyExit: true` at 02:20:01.598308 UTC for
instance `srv-db4s1v942hec73epmar0-nhxbc`. The same instance logged `/ready` 200
at 02:19:59.905635996 and 02:20:04.919156529 UTC. The complete 13-second app-log
window contained 58 HTTP lines and no process exit/traceback message. The only
deadlock found from 01:00 UTC was at 01:08:41 UTC, a separate incident. Memory
around 02:20 was about 317 MB of a 2 GB limit. No HTTP 5xx was found in the
02:18–02:24 UTC query. These observations do not establish the cause of the
platform event or prove uninterrupted traffic. Do not label it an OOM,
deadlock, deployment swap or delayed alert without further evidence.

If it recurs, preserve the precise event timestamp, instance ID, surrounding
app logs and platform metrics before changing the service. Provider-side
exit diagnostics are needed to resolve an event with no corresponding app log.

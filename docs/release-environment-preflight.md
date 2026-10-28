# Exact-release configuration gate

On 2026-10-28 (Asia/Riyadh), the same source revision failed startup at
16:45:02 because `RUNTIME_REVISION` pointed to different code, then at 16:48:07
because an enabled exact-release feature still lacked approval for the new
revision. Deployment became live at 16:52:15. These startup failures alone
do not establish customer downtime.

`python -m app.release_preflight` checks the candidate's effective environment
without importing the application, opening a database, running migrations,
starting workers, sending messages, or changing any flag or approval.
It reports all revision and approval mismatches together and exits nonzero
until both acceptance and owner approval match each enabled exact-release gate.
Disabled gates and unrelated operational flags retain their existing behavior.
The output contains variable names and the candidate SHA, never secret values.

## Before triggering a deploy

In the authorized deployment environment, with the candidate's complete effective
environment loaded privately (including environment-group overrides), run:

```bash
python -m app.release_preflight --candidate-sha FULL_40_CHARACTER_COMMIT_SHA
```

Proceed to the deploy trigger only on exit status 0 and deploy that exact SHA.
Re-run the check if source, flags, or approvals change. Passing this check is
configuration evidence; it does not issue owner approval or replace CI, database
compatibility, backup, or launch checks. Do not rewrite approval values merely
to make the check pass. If a pin is configured, it must match the intended SHA;
an absent pin uses Render's authoritative `RENDER_GIT_COMMIT`.

## Render rollout gate

The Blueprint includes `preDeployCommand: python -m app.release_preflight`.
For this existing paid Docker service, after the candidate image contains this
module, set **Pre-Deploy Command** to that command in Render or apply the
reviewed Blueprint change. A repository edit alone does not update an existing
service's settings. Verify the pre-deploy step actually runs before marking this
control enabled. This blocks rollout before new web workers or migrations run,
but a rejected pre-deploy step is still a failed deployment in Render's history.

The same read-only check should run before an API/manual deploy trigger to avoid
even starting a deployment with known bad configuration. A later configuration
change can invalidate an earlier check; the pre-deploy check covers the
candidate environment again. Keep `/ready` as the traffic readiness check.

After a successful rollout, verify the deploy is `live`, `/health` and `/ready`
return HTTP 200, both report the intended revision, and `/ready` reports the
database connected and data preservation verified for that revision.

Run the focused regression suite with:

```bash
python -m pytest -q tests/test_release_environment_preflight.py tests/test_runtime_revision.py
```

# KPS-ORDER: isolated GitHub Actions efficiency review

Date: 2026-10-17 Asia/Riyadh. Status: IMPLEMENTED IN DRAFT PROPOSAL, NOT MERGED, NOT RELEASE-CERTIFIED.

Audited base: `4f4485d4c74b38ab651c30764775e2634759bbf7`. Snapshot: October runs available at audit start (2026-10-16 23:10 UTC), 1,687 runs, all job-attempt pages fetched. Both repositories total 3,142 runs and 11,501 job records; four TILQA BuildFailed entries have zero jobs. New development after the snapshot is excluded.

## Observed major contributors

| Workflow | Runs | Linux min | macOS min | Windows min |
|---|---:|---:|---:|---:|
| `.github/workflows/emergency-v4.yml` | 607 | 5,334.90 | 0.00 | 103.73 |
| `.github/workflows/software-hardening.yml` | 368 | 2,574.55 | 0.00 | 0.00 |
| `.github/workflows/fleet-operating-log.yml` | 228 | 685.52 | 0.00 | 0.00 |
| `.github/workflows/khaliya-routing.yml` | 186 | 360.00 | 0.00 | 0.00 |
| `.github/workflows/driver-float.yml` | 68 | 320.80 | 0.00 | 0.00 |
| `.github/workflows/employee-permissions.yml` | 71 | 175.02 | 0.00 | 0.00 |
| `.github/workflows/order-amendments.yml` | 89 | 149.70 | 0.00 | 0.00 |
| `.github/workflows/branch-services.yml` | 32 | 133.65 | 0.00 | 0.00 |
| `.github/workflows/menu-preview.yml` | 12 | 75.55 | 0.00 | 0.00 |
| `.github/workflows/customer-legal.yml` | 13 | 58.42 | 0.00 | 0.00 |
| `.github/workflows/tawked-otp.yml` | 11 | 17.82 | 0.00 | 0.00 |
| `.github/workflows/hosting-capacity.yml` | 2 | 15.53 | 0.00 | 0.00 |

## Changes and expected benefit


Three acceptance workflows (fleet, driver float/privacy, order amendments) now cancel obsolete commits only within the same development PR. Fleet push evidence remains uncancelled and separate. Software hardening's dependency-security setup now caches pip **downloads**, keyed by both runtime and dev requirements; pip-audit still runs with its original strict options and produces the identical evidence. No installed virtualenv, executable build output, PASS result or certificate is restored. GitHub's existing PR cache scope applies; no pull_request_target, extra credential or privileged cache writer is added. Existing retention and cache-size settings are untouched.

The high-volume `emergency-v4.yml` and `software-hardening.yml` SHA-based concurrency is intentionally retained: it currently coalesces same-SHA push/PR duplicates while preserving independent candidate evidence. Replacing it naively with PR-only groups would lose that deduplication; replacing it with branch-wide groups could cancel an authoritative full-regression/load receipt. No trigger was removed until an equivalently fail-closed replacement can be proven. There were **297 emergency, 182 hardening and 56 fleet same-SHA push/PR pairs** in October; pairs do not mean both were fully billed or redundant, and most emergency/hardening push copies were already cancelled. Full PG18, payment, financial/refund, browser, recovery and resource-capacity repetitions remain unchanged.

KPS ORDER is **public** at audit time and observed runners are standard Ubuntu/Windows labels. Under GitHub's published policy, standard hosted runner minutes in public repositories are free. Therefore expected direct runner-dollar savings for these KPS ORDER changes are **$0 under unchanged visibility/runner policy** (high confidence in the policy; historical billed allocation still awaits the export). Efficiency improves but must not be added to TILQA dollar savings. Storage remains separately accounted and retention was not reduced.

The linked-PR overlap upper bound for the three selected acceptance workflows is **119.82 Linux elapsed minutes** in October, before cancellation latency and any protected certification. Expected monthly runner-minute reduction is cadence dependent; dollar savings remain zero under the current public standard-runner policy. Audit caching has no defensible measured time saving yet.

The source branch restricted several jobs to `agent/kps-order-emergency-v4`. Initial isolated-PR runs were SKIPPED by that existing restriction. A follow-up now also admits same-repository PRs from `agent/ci-actions-cost-optimization-*` to that exact emergency base, so the unchanged full regression, security/load, driver/privacy and amendment acceptance can actually validate this CI proposal. Arbitrary/fork source branches and other bases are not newly admitted. Full final-HEAD evidence is still required; initial skipped runs never count as release acceptance. This PR targets the emergency source branch without writing to it.

## Files changed

- `.github/workflows/driver-float.yml`
- `.github/workflows/emergency-v4.yml`
- `.github/workflows/fleet-operating-log.yml`
- `.github/workflows/order-amendments.yml`
- `.github/workflows/software-hardening.yml`

- `docs/ci-cost-20261017/README.md` (this report)
- `docs/ci-cost-20261017/evidence.json` (API evidence index and workflow hashes)


## Acceptance and isolation

Only superseded **pull_request** executions share a new stable workflow/PR-number group. Different PRs never share a group. Dependency changes do not skip tests; a new HEAD still executes the identical acceptance inventory. Manual, push and scheduled executions in the edited concurrency blocks use a run-ID group with cancellation disabled. No execution was cancelled manually by this audit.

For authoritative release/native certifications, apply `ci-preserve-certification` **before starting** the run. Such runs use a unique run-ID group and cancellation is disabled. Adding the label after a run has started does not retroactively change its concurrency group; start a fresh certification. Existing dedicated composed-closure, final-release, performance, large-volume and security-policy workflow concurrency remains unchanged. Completed evidence and artifact retention are unchanged. The final release HEAD must still satisfy all original gates; a cancelled obsolete HEAD is never release evidence.

Parsed YAML comparison proves unchanged workflow names, job IDs, matrix names, triggers/path filters, dependencies, commands, runner labels, permissions, services, environment, acceptance assertions, artifact upload and retention. The per-job exceptions are the explicitly recorded pip-download cache addition and a same-repository CI-branch eligibility expansion in KPS ORDER. The latter admits only PRs from agent/ci-actions-cost-optimization-* to agent/kps-order-emergency-v4; every legacy eligible source remains eligible. A 16-case eligibility truth table passed. This enables real tests on this isolated PR without admitting arbitrary or fork PRs. No mocks, PASS substitutions, continue-on-error, timeout reductions, entitlement/auth changes or production actions were introduced.

Core reuse implementation and tests are byte-identical to the audited source. Existing `validate_producers` accepts all six TILQA workflow changes. The existing 58 Core-certificate tests passed locally after loading their unmodified fixture dependencies. These validate missing/failed/skipped evidence rejection, changed inputs, strict performance thresholds and transient API exhaustion. This is NOT a PostgreSQL/browser/native release certification.

## Before/after verification status

The evidence JSON records source commit, run IDs, job IDs, measured runtime totals and hashes of every changed workflow. Local YAML acceptance contract comparison: 11/11 across both repositories, with only the documented cache/eligibility exceptions. Existing Core-certificate regression: 58/58. New PR, subsequent-commit and final-candidate scheduling use the same triggers and exact job contexts. Branch protection API returned 403 `Resource not accessible by integration`; rule lists were empty but classic protection cannot be inferred from that. Exact required-check configuration, no permanently pending checks, and final candidate acceptance remain **BLOCKED / NOT CERTIFIED** until authorized GitHub visibility and actual PR check results are available. No skipped check is being claimed as a successful release gate.

No stress or million-invoice certification was dispatched to benchmark savings. No merge, deployment, resource modification, database/customer/financial write, branch reset, active development branch write, or automatic merge was performed. Opening the draft PR may invoke existing automatic CI; its results are not pre-claimed.

## Billing reconciliation and governance

Owner-supplied October account baseline: gross $381.21; Linux 52,032.33 minutes / $224.88 billed; macOS 1,050 minutes / $65.10 billed; Windows/storage $0 billed. The supplied billed line items sum to **$289.98**, leaving **$91.23** between gross and those billed rows. Credits, included usage, Windows gross charges, billing windows and any other SKUs require the account's detailed usage export before a final net conclusion. At current documented $0.006 Linux / $0.062 macOS list rates, those supplied raw minutes imply $377.29 before credits; that is an arithmetic cross-check, NOT reconciliation or an invoice.

This audit measures nonnegative per-job elapsed minutes across all listed attempts; skipped jobs contribute zero. Queue time is excluded. Those values are NOT billed minutes: invoice rounding, setup accounting, credits, free/public usage, changing workflow versions and billing ingestion timestamps can differ. One sampled timing endpoint returned all `billable.total_ms=0` despite positive job elapsed time, so it was not used to manufacture billing conclusions. The supplied totals are account-wide and cannot yet be assigned dollar-for-dollar to individual workflows.

Proposed initial Actions **alert-only** budget: $30/month, notify at available warning thresholds including 90% ($27) and 100% ($30); an earlier $24 operating warning is desirable if supported. Keep stop-usage disabled for this alert-only proposal. This is a proposal, not a claimed account setting: billing APIs/settings are not exposed by the connected GitHub tool. If an existing spending restriction blocks required CI, keep the release BLOCKED and resolve funding with the owner; never waive a gate. Do not change public/private visibility to save money.

Official sources: [Actions billing and public-runner policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [budget setup](https://docs.github.com/en/billing/how-tos/set-up-budgets). No artifacts, receipts or logs were deleted and no retention period was shortened.

## Remaining work before owner merge approval

1. Read classic branch protection through authorized owner access and compare exact required contexts to actual checks.
2. Validate GitHub expression acceptance and scheduling on the draft PR; for impacted release candidates require all existing integration, PG18, browser, native, language, security and financial gates. Local structural checks do not substitute for those runs.
3. Approve the alert-only budget in account billing. Obtain the October detailed usage/credits export to reconcile $91.23 and per-repository costs.
4. After approved integration, measure comparable runs and billing exports; report actual billed savings separately from historical potential. No post-change billed saving is available while the changes are unmerged.

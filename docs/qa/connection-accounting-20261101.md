# Connection recovery and accounting journey acceptance — 2026-11-01

Baseline source and live deployment were both `9d4965fa9505e1c151fa80ea57be844ab6738455` (PR169). Measurements below are isolated synthetic PostgreSQL18 results, not production or physical-phone latency claims.

## Findings and implementation

- Business API authentication repeated the session-only central workday history lookup. Remove that presentation lookup from non-session requests; retain fresh session, active employee, brand membership and resource authorization on every request. The 2-second live revocation check and fail-closed recovery remain.
- Duplicate simultaneous reads and identical submissions shared no transport. Coalesce only in-flight identical requests within the exact employee/session/brand scope; do not cache completed authorization or data responses. Page cancellation never cancels independently owned reads.
- Background/hidden/offline/pending-access work continued alert, dispatch and chat reads. Pause unnecessary polling and keep the current screen/forms during recovery. Large accounting statements are excluded from the global refresh timer.
- Uncertain mutation outcomes retain the original idempotency key. Changed data cannot silently become a new financial action. A fresh authorized opaque receipt probe returns only whether the actor/brand key was recorded, never its prior response. Bounded safe financial/operational JSON drafts survive reload in sessionStorage, exclude credentials and uploads, and are removed on confirmation/logout. Recovery replays the original key and body; it does not send an edited action automatically.
- Manager cards loaded the full history. Load20 authorized shifts per page with pending-first ordering, archive pagination and authorized focus pinning. Cards expose manager shift time/scope, driver identities/counts, orders, financial state, exact blockers and responsible role. Open children/missing reports, integrity failures and live financial guards prevent premature completion. Bank matching remains separate.
- Preserve opened cards, scroll, selected page and report filters while reviewing drivers and returning. Main navigation and accounting expose `كشف السواقين`; the direct page opens its filters immediately. City selects authorized branches; period, multiple drivers and shortage selections feed the same report contract for screen/print/PDF/XLSX. Include per-driver and grand totals, approval references and amounts under review.

## Reproducible measurements

| Workload | Baseline | Candidate |
| --- | ---: | ---: |
|101 manager shifts plus101 driver reports: median server function time,3 runs |287.99ms|51.10ms|
|SQL statements for that initial card load |814|167|
|JSON bytes |265860|61929|
|Cards returned |101|20, remaining reachable through pagination|
|Authenticated task read: median SQL count,5 runs |11|7|
|Same task fixture: median local TestClient time |15.831ms|10.730ms|

Reproduce the first comparison with `python -m pytest -s tests/manual_manager_cards_performance.py`; it loads the fixed baseline source. Reproduce the second with `tests/test_work_request_performance.py`, restoring only the exact removed authentication drain lookup on the same fixture/handler. Both require an explicitly isolated local PostgreSQL test DSN. Artifacts: `accounting-manager-card-performance.json` and `work-request-source-performance.json` in the ignored test-artifacts directory.

Read-only production observations: Render CPU about0.07–0.09 core and memory about522MB in the observed pre-deploy window; no current runtime errors or recent failure/restart event was returned. Historical October30 connection-refused logs coincide with database restart and do not establish today's cause. Provider HTTP latency series was empty. Unauthenticated warm public GET samples were about175–280ms; these do not measure the authenticated operating journey. Private database query connector was blocked by its empty external IP allowlist; no network protection was changed.

## Manager workspace follow-up

The owner clarified that all screens, especially delivery management, were slow. Live read-only UI inspection confirmed13drivers (12without a shift) and24authorized branches across2brands. The canonical roster readiness path repeated the same driver/manager/membership point reads for each allowed branch. A request-owned `ReadinessObservation` now copies and reuses only7exact allowlisted point SELECTs inside the existing REPEATABLE READ, READ ONLY roster transaction. Nothing persists across requests; assignment still uses its original fresh authorization and locking path.

A genuine authenticated local workload of500pending orders,21drivers and20KPSbranches compared the removed baseline auth lookup plus unwrapped readiness with the final implementation. Roster SQL2065→95; median local request622.16→172.65ms; response28675bytes and eligibility/status projection match. Other manager endpoint SQL: orders42→24, alerts19→10, conversations35→17, locations22→13. Real Chromium390 workspace readiness median2404.11→1304.30ms and1440desktop1782.13→1285.64ms across3runs each (100orders visible/21drivers); both pass with no JS errors or horizontal overflow. Baseline browser serves the published ops.js asset and restores the exact removed auth/readiness behavior; business API responses remain genuine. These are comparative local measurements, not a production or physical-device guarantee. Reproduction: `tests/manual_manager_workspace_performance.py` and `tests/manual_manager_workspace_browser_performance.py`.

Independent review plus67scope/morning-dispatch/dual-brand checks,42readiness checks and4new observation/freshness checks passed across isolated runs. A disabled driver is reflected by the next roster request and cannot be assigned; revoked manager brand membership returns403. The prior independent-alert browser fixture assumed duplicate task HTTP requests; it now verifies one shared read, independent navigation consumers and revocation before response delivery. All4race cases and the other5screen recovery scripts pass.

## Validation before release

-101 selected PostgreSQL/unit checks passed across manager day, statements, exports, session metadata, accountant permissions, independent accounting audit, settlements, deadlines and accounting. An independent reviewer separately ran79 financial checks and reviewed the new receipt/draft code.
- All85 existing JavaScript test files passed at the integration checkpoint; targeted new recovery tests additionally passed after final recovery changes.
-18 card layout/browser cases;4 real accountant cash/paper/treatment scenarios;3 accounting/owner browser cases;2 direct statement browser cases passed. Desktop1440 and mobile390 are Chromium viewport tests, not physical-device certification.
-6 actual financial response-loss/browser cases passed at390/1440: response lost after commit, edited retry receipt probe, and lost-before-server request followed by full reload and original-body recovery. Database assertions verify exactly one original12345-halalas event and no edited12000-halalas event.7 existing browser revocation cases passed.
- Actual PDF parsing/XLSX XML checks cover selected drivers, pending versus confirmed shortage, subtotals and grand totals. Direct navigation acceptance checks the same date/branch/driver query in JSON, PDF print preview and Excel.
- Financial mutations used isolated synthetic records only. No production financial action is part of this evidence.

## Release discipline

Source checks are not deployment certification. Required current-HEAD CI/security/image/rollback gates and the normal protected merge must pass before deployment. Verify the final source revision, live deployment, readiness/data-preservation checks and served assets afterwards. Screenshots depict explicitly synthetic acceptance data. File uploads retain their existing protected attachment/retry mechanism; secret and binary-upload bodies are not persisted as JSON drafts.

# Performance certification register — 15 October 2026

This register preserves evidence, not a blanket release approval. Read it before reopening a solved performance issue. No runtime, workload, gate, infrastructure or operational-data changes were made for this reconciliation. No large tests were rerun. Documentation-only commit; existing exact-commit PASS and FAIL evidence stays authoritative.

## Compared revisions and runs

| Run | Exact tested SHA | Environment | Workers | 500-user P95 / P99 milliseconds | Result |
|---|---|---|---|---|---|
| 38466066467, jobs 115533868003 / 115533867494 | 8d1e1d4fc1cb6fc2c7b5eb0da425fb77febec2d7 | Unconstrained CI profile | 2 / 2 | 4549.511 / 6582.549; 3388.985 / 3689.035 | Both PASS |
| 38535743728, jobs 115758645702 / 115758645730 | 27238462513097343e070a402ccaf6bcb97cb6fc | Unconstrained CI profile | 2 / 2 | 3355.727 / 5421.210; 5072.146 / 5881.890 | First PASS; second FAIL P95; whole run FAILED |
| 38760747965, jobs 116511088135 / 116511088321 | 667286532787e2aad33636c2b4adcd34428d3dcf | Unconstrained CI profile | 2 / 2 | 4157.863 / 5067.176; 3840.145 / 4922.894 | Both PASS |
| 38760747965, job 116511088833 | 667286532787e2aad33636c2b4adcd34428d3dcf | App 0.5 CPU / 512 MiB; DB 0.1 CPU / 256 MiB | 2 | 19292.306 / 21871.229 | FAIL P95 and P99 |
| 38760747965, jobs 116511088268 / 116511089647 | 667286532787e2aad33636c2b4adcd34428d3dcf | App 2 CPU / 4 GiB; DB 1 CPU / 2 GiB | 2 / 2 | 3417.426 / 3793.161; 3866.924 / 5242.291 | Both PASS; proposal only |

The cited 4.55s and 3.39s both belong to run 38466066467, dated 13 October UTC (23:53–23:55 Riyadh). They are not two certificates from different listed runs. Run 38535743728 on 14 October was NOT fully successful.

## Hardware and workload comparability

All retrieved JSON artifacts record Ubuntu/Linux 6.17 Azure, Python 3.12.15, four logical runner CPUs, PostgreSQL 18.6 and max_connections=100. Historical artifacts report CPU quota and memory limit as unavailable: physical runner RAM is NOT recorded and must not be invented. Historical workflows did not impose per-app/DB resource budgets. App, DB and generator shared each job's runner. Separate jobs are not proof of shared physical-host contention; no noisy-neighbor attribution is established.

The constrained profiles verify kernel cgroups and process ownership, with the generator outside the workload cgroups and swap disabled. These are CI simulations, not live Render benchmarks or resource changes. See the extracted JSON evidence for exact limits and provenance.

All compared workloads use 50/100/250/500 users, two rounds per user, catalog/quote/order/read journey, two concurrent transmissions with the SAME idempotency key per order, actual loopback source IPs, real HTTP/PostgreSQL, and synthetic isolated schemas. At 500 users: 5,500 HTTP requests, 1,000 distinct persisted orders, zero request errors and no duplicate business intents in the retrieved artifacts. Across all four stages: 9,900 requests and 1,800 business orders. No load reduction occurred.

The load path explicitly tests legacy guest checkout, not live SMS delivery or every customer workflow. It is a simultaneous closed-loop burst, not an open-loop sustained soak. Actual customer/UI/authorization gates remain separate.

Thresholds remain P95 5,000 ms and P99 10,000 ms, with max unexpected error rate 1%, no integrity failures/deadlocks and all levels required. Current P95 comparison is stricter at equality than the historic implementation. Existing P99 implementation rejects >10,000 (not equality); none of these results is on that boundary. The owner's strict <10,000 requirement is used in interpreting certificates here. No thresholds changed in this reconciliation.

Core HTTP journey remains comparable, but the whole harness is not byte-identical: later commits add kernel resource isolation, hosting/export/report checks and stricter P95 comparison. Pool capacity also changed from up to 40 backends to 8 total. Historical certificates therefore cannot certify the smaller resource envelope or all later features.

## Diagnosis supported by measurements

At the same current SHA the unconstrained and proposed-budget profiles PASS while the small-budget profile FAILS. In the 500-user small-budget sample interval, PostgreSQL was CPU-throttled in 932/1058 periods, with 179,215,822 microseconds accumulated throttling; app throttling occurred in 236/1040 periods (13,027,293 microseconds). The proposed-budget first repetition had zero throttled periods for both during the corresponding stage. No OOM events were recorded. Throttled time is an aggregate kernel counter, not request latency.

This supports CPU-budget saturation, especially the 0.1-CPU DB envelope, as the dominant explanation for this comparison. It does not demonstrate a reintroduced application defect or isolate every smaller code-cost change. No controlled experiment proves host contention as the cause. Do not infer a code regression by comparing 19.29s under small quotas against historical unbounded CI timings.

The small-budget failure predates the legal changes: docs/RELEASE_WORKER_PROFILE.md already records candidate 7e3e0d72173ed8fb211c1b17f999c97383773265 at 18.18/24.49s P95 versus unbounded PASS 3.52/4.39s.

## Preserved fixes

- b89791e1439734a440f33f01ac1c2935b11e261a: two Uvicorn workers in one service; still present in Dockerfile and RELEASE_WORKERS=2.
- 17dd172e484e7dbddf17ba8512597e3640a57759: native uvicorn standard transport and batched fresh authenticated reads; still present.
- 29ffff6dd61487e74cf0e7c4a34e775e094da0f3: batched catalog reads and atomic rate-limit upsert; still present.
- 7e3e0d7: bounded pool (4 per process / 8 total), retained in current code; no rollback.

No runtime performance repair is justified by these comparisons. Do not rebuild/reapply the above fixes. Passing a larger CI budget does not authorize a paid upgrade or certify the current smaller budget.

## Deployment and evidence retention

Current application candidate remains 667286532787e2aad33636c2b4adcd34428d3dcf. Legal/settlement suite 38760739332: 168 PASS; fleet 38760747925: 144 PASS; order amendments 38760748022: PASS; dependency security and both unrestricted capacity jobs: PASS. Retain them for their tested revision/scopes.

Deployment is NOT approved by these results: small-budget capacity is failed. In addition, independent regression run 38760748006 job 116511258452 has 391 PASS / 1 FAIL in test_real_created_brand_storefront_identity_empty_catalog_and_draft_isolation: another brand's body still contains KPS contact-dialog text. This is a separate functional isolation/presentation gate; it must not be hidden by a performance pass. Its root cause/visibility is not resolved by this performance-only audit. No deployment, merge, DNS, resource change, public-ordering or fiscal activation occurred.

For future decisions record exact SHA, source digest, resource profile, workers/pool, workload, thresholds, job IDs, artifacts and failures. Never promote an unconstrained PASS to current-budget certification. Rerun only affected profiles after a measured runtime/configuration change; documentation alone needs no load rerun. Any exception for limited experimental visual review must be explicit and must not be reported as 500-user capacity approval.

Machine-readable durable excerpts: [certification-evidence-2026-10-15.json](certification-evidence-2026-10-15.json). They omit raw synthetic user data and keep environment, methodology, counts, limits, integrity and throttling evidence. Additional current-job timings above come from the linked run's immutable job logs.

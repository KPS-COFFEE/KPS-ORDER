# Customer progress and driver evidence observations

This additive change preserves the existing order, printing and evidence paths.
No migration, user permission grant, service activation or production data edit
is part of this source change.

## Customer milestones

Manager-dispatched delivery shows received, preparing, on the way and delivered.
Preparing requires an explicit operator confirmation of the current invoice's
paper output at the execution branch. Windows `submitted` alone is only spool
acknowledgement, never physical evidence. The confirmation records employee,
time, branch, job and amendment revision; it does not alter preparation status.
When the existing branch-employee workflow is active, actual preparation and
ready transitions drive those milestones. Disabled services remain disabled.
On the way requires this order's recorded trip departure and picked-up task;
another order's trip or branch handover alone never establishes customer delivery.

## Repeated driver images

The same normalized image fingerprint actually used at pickup and delivery
creates one private administrative observation. It does not block delivery,
request a retake, establish misconduct or change collection/payment checks.
Authorized owner/delivery manager acknowledgement records the first reader and
time. Reading is not resolution or image approval. Customer history excludes
these private events. Branch authorization is rechecked before idempotent replay.
Existing image retention and cleanup rules remain unchanged.

## Acceptance and release boundaries

Focused API tests execute actual upload, pickup, cash collection, delivery,
idempotency and branch/brand isolation. Browser tests use real Chromium and HTTP,
PostgreSQL 18.6, synthetic records and a test-only SMS provider. They cover both
languages at 390, 768 and 1280px, real staff SMS login, invoice confirmation and
observation acknowledgement. Screenshots are emitted to `test-artifacts/`.
Browser seeds and printer acknowledgements are not physical hardware tests.

The new focused suites are required by the existing usability workflow; the
backend suites are also included in exact-image certification. Previous image
certificates do not certify this application change. Deployment remains separate
and must satisfy current approval, branch protection, security and rollback gates.

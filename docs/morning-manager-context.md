# Morning manager operational context

## Owner-directed permission parity — 2026-10-22

The owner explicitly requested normal delivery-manager capabilities in the
approved morning view after a driver could not be closed. The earlier minimal
capability exclusions described below are superseded by this section.

An approved, active morning duty now receives the delivery-manager role's
standard capability set plus the existing reasoned branch-preparation fallback.
No stored employee role, branches, denials or extra grants are rewritten. The
bootstrap remains limited to viewing/opening one's own morning duty. Owner-only
configuration, accounting approval, refunds and employee permission management
are not added. The morning launch remains KPS Branch 2, Hafar, with the existing
period/customer-admission rules and real duty ownership.

The operations UI exposes the normal manager pages and closure actions. Driver
closure still requires the actual manager recipient, explicit receipt
confirmation, the existing counted/card reconciliation, unresolved-task and
handover checks, and an exception reason where required. Receipt, closure and
immutable report creation use the existing atomic/idempotent transaction.
Pausing new morning orders does not prevent finishing an existing approved duty.

Orders, conversations, reports, driver shifts and location/trip reads are scoped
to the approved morning duty before access; compound body/path references are
rechecked before stored mutation responses. Shared branch printer configuration
and permitted branch follow-up retain their existing role/resource contracts.
Other periods, duties, branches and brands cannot be substituted into a closure.

Validation: local Python/JavaScript compilation, workflow YAML validation and
62 focused non-database cases pass. The local runtime cannot start an isolated
PostgreSQL server; database and actual browser evidence are required from the
existing PostgreSQL 18.6 combined workflow and exact-candidate release checks.
No production deployment or real employee permission update has been performed.

The historical minimal-view description below is retained for context; its
exclusion of cash, closure, reports and chat is no longer the requested policy.

This release adds an explicit same-site view at `/ops?delivery_period=morning`.
The normal `/ops` page and stored employee permission/branch assignments are unchanged.

Each scoped API request sends `X-Delivery-Period: morning`. The server rechecks the
active KPS delivery manager, owner morning-policy allowlist, existing branch access,
and an actual nonclosed morning manager duty for operational actions. The only execution branch in this
launch is Branch 2 in Hafar Al Batin. Caller-selected headers, branch IDs, shift IDs,
or order IDs cannot choose another principal or widen this scope.

The limited view exposes morning orders, the manager's duty status and scoped
manual driver assignment. It also permits a proposed handover within that same
morning duty; the receiving driver must accept before responsibility moves.
An approved manager with no current morning duty can open their own Branch 2
morning duty with zero opening custody during the existing morning window.
The bootstrap grants no order/task actions until that actual duty exists.
It never grants vehicle, closure, custody or accounting authority.
It permits the approved morning manager to accept a new morning Branch 2
order on behalf of the branch. Acceptance queues the same single approved printer
receipt under the existing transaction. The preparer starts preparation only after the
assigned driver actually accepts, then marks ready. The morning manager can
record preparation or readiness on behalf of a delayed branch with a required
reason, under the same driver-acceptance and state checks. The manager may create a delivery order received manually over
WhatsApp using the existing customer-order form. The server must independently
confirm Branch 2, Hafar, the manager duty, and a morning quote; a header cannot
force an evening quote into the morning period. Customer lookup is limited to
morning orders within the same scope.

It excludes customer amendment, chat, unrelated financial actions, reports,
configuration, other periods and other branches. Existing receipt confirmation,
vehicle, unresolved-task and financial discrepancy guards remain authoritative.

The owner must approve the staff policy once. The approved manager opens their
own duty from this view; the owner is not involved in daily driver dispatch. Pausing new customer intake retains access to finish an existing duty
while the manager and branches remain on the owner allowlist. Removing approval,
revoking the employee/branch or closing the duty revokes this context immediately,
including saved mutation replays. Closing the manager duty returns the page to
normal access; it does not alter the employee's original permission lists.

Validation requires the dedicated PostgreSQL 18 workflow and a browser journey
through the scoped order acceptance and manual customer-order view. No migration
is required.

Owner staffing decision: Ahmed Abdelnasser, verified registered phone ending 9596,
is the intended permanent morning manager; Mohammed was for the initial trial only. These are operational
instructions, not hardcoded identities or automatically created employee accounts.
The owner's existing employee account and approved policy must be verified before
any assignment. The current policy still uses its standard 04:00–16:00 Riyadh
window; passing isolated tests does not activate a live nighttime trial or prove
physical paper output.

Automatic assignment starts after acceptance of real morning delivery orders executed
by KPS Branch 2 in Hafar Al Batin under the enabled morning policy. It reuses the
existing assignment, driver readiness, manager ownership and period checks. It
can choose a driver already carrying one or two active tasks, with an isolated
morning capacity of three. Existing evening dispatch settings are unchanged.
The flow is acceptance and printing, assignment, driver acceptance, preparation,
ready, actual driver pickup, delivery. Skipping preparation or confirming pickup
before ready is rejected server-side for these morning orders.
No eligible driver leaves the order awaiting manager intervention; a bounded
server retry handles later driver availability without a customer or owner click.
The separate simulated rehearsal does not create operational driver tasks.

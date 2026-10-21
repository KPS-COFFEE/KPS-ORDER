# Morning manager operational context

This release adds an explicit same-site view at `/ops?delivery_period=morning`.
The normal `/ops` page and stored employee permission/branch assignments are unchanged.

Each scoped API request sends `X-Delivery-Period: morning`. The server rechecks the
active KPS delivery manager, owner morning-policy allowlist, existing branch access,
and an actual nonclosed morning manager duty. The only execution branch in this
launch is Branch 2 in Hafar Al Batin. Caller-selected headers, branch IDs, shift IDs,
or order IDs cannot choose another principal or widen this scope.

The limited view exposes morning orders and the manager's duty status. Driver
dispatch and shift opening/closing remain owner actions for this trial; this
context does not grant assignment, vehicle, closure or accounting authority.
It permits the approved morning manager to accept a new morning Branch 2
order on behalf of the branch. Acceptance queues the same single approved printer
receipt under the existing transaction; marking the order ready remains the
preparer's action. The manager may create a delivery order received manually over
WhatsApp using the existing customer-order form. The server must independently
confirm Branch 2, Hafar, the manager duty, and a morning quote; a header cannot
force an evening quote into the morning period. Customer lookup is limited to
morning orders within the same scope.

It excludes customer amendment, chat, unrelated financial actions, reports,
configuration, other periods and other branches. Existing receipt confirmation,
vehicle, unresolved-task and financial discrepancy guards remain authoritative.

The owner must approve the policy and open the manager duty before this view can
be entered. Pausing new customer intake retains access to finish an existing duty
while the manager and branches remain on the owner allowlist. Removing approval,
revoking the employee/branch or closing the duty revokes this context immediately,
including saved mutation replays. Closing the manager duty returns the page to
normal access; it does not alter the employee's original permission lists.

Validation requires the dedicated PostgreSQL 18 workflow and a browser journey
through the scoped order acceptance and manual customer-order view. No migration
is required.

Owner staffing decision: Ahmed, phone ending 1449, is the intended permanent
morning manager; Mohammed is for the owner trial only. These are operational
instructions, not hardcoded identities or automatically created employee accounts.
The owner's existing employee account and approved policy must be verified before
any assignment. The current policy still uses its standard 04:00–16:00 Riyadh
window; passing isolated tests does not activate a live nighttime trial or prove
physical paper output.

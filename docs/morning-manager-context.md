# Morning manager operational context

This release adds an explicit same-site view at `/ops?delivery_period=morning`.
The normal `/ops` page and stored employee permission/branch assignments are unchanged.

Each scoped API request sends `X-Delivery-Period: morning`. The server rechecks the
active KPS delivery manager, owner morning-policy allowlist, existing branch access,
and an actual nonclosed morning manager duty. The only execution branch in this
launch is Branch 2 in Hafar Al Batin. Caller-selected headers, branch IDs, shift IDs,
or order IDs cannot choose another principal or widen this scope.

The temporary view exposes Orders, Tasks, and Shifts. It supports morning order
assignment to a driver belonging to the same actual duty, driver shift opening with
the existing real vehicle inspection, and existing receipt/closure workflows.
It excludes customer creation/amendment, chat, unrelated financial actions, reports,
configuration, other periods and other branches. Existing receipt confirmation,
vehicle, unresolved-task and financial discrepancy guards remain authoritative.

The owner must approve the policy and open the manager duty before this view can
be entered. Pausing new customer intake retains access to finish an existing duty
while the manager and branches remain on the owner allowlist. Removing approval,
revoking the employee/branch or closing the duty revokes this context immediately,
including saved mutation replays. Closing the manager duty returns the page to
normal access; it does not alter the employee's original permission lists.

Validation requires the dedicated PostgreSQL 18 workflow and a browser journey
through the scoped Orders/Tasks/Shifts view. No migration is required.

# Active vehicle swap exception

The existing `vehicle_maintenance_manage` permission remains required. An additional explicit owner grant, `fleet_active_swap`, permits a delegated admin to swap the cars of two drivers whose shifts are both open. It is not granted automatically to current fleet admins and is not grantable to drivers. The existing available-car handover remains supported.

The operator chooses the second driver, reviews both cars and readings, records a reason, and explicitly confirms. Both vehicle revisions, current driver assignments, current brand/branch access, open shifts, vehicle intervals and nondecreasing odometers are checked. The operator is recorded as both entrant and approver; no supplementary OTP is requested for this operational action. Sign-in and granting permissions retain their existing identity controls.

Both car intervals close and both new intervals open in one transaction. Failure rolls back both. Shift/driver identities, tasks, collections, custody, breaks and manual availability remain unchanged. Prior vehicle inspections/refuels stay in append-only segment history. Replay returns the original result and rechecks the additional permission and referenced scopes.

Vehicle assignment changes generate durable recipient-specific driver notices in the same transaction, including actor, reason and old/new plate. The existing driver polling surface displays the notice; an acknowledgement only marks it read and does not claim a driver signed a receipt. Another driver or brand cannot read/acknowledge it. Physical notification delivery while the app is closed is not implemented by this patch.

The existing `branch_printers_manage` permission already controls setup, testing, approval and paper confirmation; it is a separate explicit grant. This patch has not granted any live account permissions. Read-only verification of Fawzi's live account through Render was blocked by the database's existing external-connection policy; that policy was not changed.

Company-owned, driver-owned and rented cars still require the owner's detailed conditions (fuel, maintenance, rent and receipt requirements). This patch does not invent or activate those contractual/financial rules. It does not implement driver account administration or changing a shift's legal/financial owner.

Validation so far: 38 local permission/unit/math tests passed; Python compilation, JavaScript syntax and diff checks passed. Real PostgreSQL transaction/replay/scope tests and Arabic/English KPS/Enbishan browser tests are prepared for CI. This is a draft and is not deployed.

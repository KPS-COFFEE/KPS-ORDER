# Staff / fleet / CRM usability follow-up

Base: production merge `c19f75bc0f8957d33fe48121a5843d51e629039d`.

- Staff cards retain existing role, branch, permission, lifecycle and shift controls. No account or permission migration.
- Driver cards open the existing authorized fleet list, showing paused, assigned and available vehicles. An explicit transfer confirmation records expected assignees and revision, clears the former assignment without swapping cars, and audits the change. Active shifts remain a hard blocker; operating history is never rewritten.
- CRM offers all authorized branches, branch-city labeling, a 450 ms debounced server query for 3+ digits, bounded results, rate limiting and an audit event. Exact delivery-order lookup remains unchanged. Contact discovery never proves number ownership or grants a session.
- SMS provider failure remains fail-closed. The owner verification dialog retains the employee form and offers an explicit resend, never a bypass. This is recovery UX, not proof that the production SMS provider has been repaired.
- No database migrations, production staff changes, production car transfers or deployment are performed by this source change.

## Verification

Run `tests/test_ops_usability.py`, `tests/test_ops_usability_browser.py` and retained staff, CRM, fleet and SMS suites. Chromium evidence covers AR/EN at 390, 768 and 1280 px. The focused workflow uses PostgreSQL 18.6 and real Chromium; only external SMS I/O is synthetic.

Local Chromium startup failed with SIGSEGV before page load in this execution environment. Such runs are ERROR, not PASS. Remote CI results must be checked for the exact candidate. A new source/image certification and normal protected review are required before release; the expired original release cutoff does not authorize an automatic late deploy.
# Follow-up: order layout and distinct escalating bells

Owner-requested follow-up preserves the current KPS styling and data paths.
Orders have sticky headers, readable column widths, isolated numeric durations,
wrapping names and mobile labels without the previous neighborhood/amount overlap.
Order and message bells each sound twice initially, twice after 30 seconds,
then continuously after 60 seconds while unresolved. Message reading is not a reply;
the existing server-scoped awaiting-reply endpoint remains authoritative. Timers
start on first observation in this browser session, not an invented server arrival
timestamp. Browser gesture/audio permission and an open, awake page are required.
Follow-up owner decision: loss of a refresh is not acknowledgment and must not
mute an outstanding bell; only a confirmed action/reply or ended session stops it.

Bell policy is stored per brand in existing app_settings JSON, without a migration.
Only an owner can update it through a dedicated endpoint with session-bound SMS
step-up, idempotency and an audit event. Delegated appearance permission does not
grant access. Delays and individual pulse lengths are configurable; bells cannot
be disabled via this endpoint. Nonowners have no visible audio settings or mute
button. Playback is attempted automatically and retried on ordinary pointer/key
interaction; blocked browser audio remains honestly visible as a status notice.
Browser/site/OS mute controls, sleep and closed pages cannot be overridden.
Owner changes propagate on the next successful policy refresh (up to 60 seconds).
The shared scheduler now also handles new driver assignments.

Local PostgreSQL 18.6: 19 policy/scope/reply/printing checks passed. New owner SMS,
nonowner hidden controls, order layout and bell browser checks await real CI.

An independent amendments-inbox request now runs alongside the other alert reads,
removing a sequential network round trip without increasing polling frequency.
This is not evidence that production server/database latency has been resolved.
Local PostgreSQL 18.6: five operational scope/reply/printing checks passed.
Node alert schedule and six mandatory-SMS handler scenarios passed. New AR/EN
390/768/1280 order screenshots and Chromium bell cases require CI execution.
This application change invalidates the previous image certificate for release:
certify the new exact SHA/image again. No production deployment is authorized by
this document, and no production records were changed.

## Event history, unresolved delivery problems and conversation cards

Order lists and details now prioritize actual delivery status over preparation
handover. Branch-to-driver handover is not customer delivery. An open problem
shows a manager-action badge and decision form in orders/timing/tasks. Only an
owner or delivery manager may resolve it, with a recorded reason and audit event.
A manager may resume an already evidenced pickup without creating a new pickup
time/photo; missing pickup evidence blocks this action. Cancellation retains its
payment/refund guards. Driver self-resolution is denied. Tasks lookup filters
brand and authorized branches in SQL, and optionally targets one order.

Event history uses a wider responsive dialog and compact rows with full reasons,
and translates common event labels. Conversation cards reuse the same rendering
on initial load and refresh, show authorized customer/branch/latest-message data,
and retain escaping and read/reply rules. No production order was manually edited.
Local PostgreSQL 18.6: 34 affected operational/support/timing tests passed. New
AR/EN event-history and conversation screenshots at 390/768/1280 await CI.

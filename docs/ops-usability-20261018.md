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

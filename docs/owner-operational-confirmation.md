# Owner operational confirmation — 2026-10-22

Owner instruction: routine work should use confirmation instead of repeatedly requesting an OTP. Shared changes apply to KPS and Enbishan within the authenticated account's existing brand and branch scope.

This patch replaces supplementary owner verification for five operational settings: mandatory bell timing, order journey targets, customer advertising visibility, gift offer configuration, and existing morning delivery configuration. The latter retains its existing KPS-specific availability; this patch does not enable a new morning service in another brand.

Each corresponding form asks for confirmation before saving. Cancelling keeps the draft and performs no write. Password fields are removed from these forms. Owner-only API dependencies, CSRF middleware, input validation, version conflicts where supported, idempotency and audit records remain in place. No changes are made to login or identity/account security verification.

Local validation: 85 Python tests and 5 JavaScript behavioral tests passed. The tests cover KPS/Enbishan, Arabic/English, cancellation without saving, successful confirmation, nonowner rejection, audit, version conflicts and brand isolation. JavaScript syntax, Python compilation and diff checks passed.

Real PostgreSQL and browser release checks remain pending; the relevant existing cases were updated to verify confirmation and cancellation rather than additional SMS. This patch is not deployed. It does not implement active two-car swaps, Fawzi account grants, printer grants, driver notifications or vehicle ownership/rental conditions; those remain separate requested work.

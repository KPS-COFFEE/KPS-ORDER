# Delivery operating hours

**Separate KPS branch opening policy (owner approved 2026-10-10):** branches are open all day Monday–Thursday and Saturday–Sunday. On Friday, branches are open 00:00–05:30 and 12:30–24:00, closed 05:30–12:30 Asia/Riyadh. The branch opening gate applies independently to pickup, car pickup, and delivery; all existing disabled service flags remain unchanged. The 16:00–04:00 delivery service window is independent and more restrictive when applicable.


The approved KPS default accepts delivery daily from **16:00 inclusive to
04:00 exclusive in Asia/Riyadh**. At 04:00, new delivery admission and fresh
configured delivery shifts stop; a clear message shows the next opening.
Existing orders, driver tasks, cash/card collections, handovers, custody
settlements and final closures continue after cutoff using their normal rules.
Manager closure still requires every child driver closure and confirmed custody.

Migration 014 adds nullable delivery operating dates for new orders/shifts and a
frozen shift window snapshot. Historical operating records are not backfilled.
New delivery records after midnight belong to the day that began at 16:00.
New configured delivery closure reports use their frozen shift date. Pickup and
car pickup keep their own hours and reporting settings.

The default is evaluated once per eligible KPS branch after seed import. It
fills missing/empty delivery hours, preserves every enablement/verification/
coverage/fee/payment flag, and retains an existing custom owner schedule. Later
owner changes, including deliberately empty hours, survive restarts. The owner
continues to edit or pause delivery via the existing branch administration form.

Public branch/nearest results expose `delivery_schedule`, containing the exact
window start/end and next opening in Saudi time; it is not an estimated arrival
time. Unconfigured legacy internal staffing flows retain their existing behavior.

`tests/test_delivery_hours.py` covers 03:59, 04:00, 15:59, 16:00, midnight, UTC
conversion, weekday overrides, driver/manager shift admission, owner changes,
service independence, a real pre-cutoff order completed/collected/closed after
04:00, and preservation after restart on an isolated PostgreSQL test database.

## Manager preparation (owner approved 2026-10-19)

For a configured 16:00 opening, manager shifts may open from 15:45 inclusive
Asia/Riyadh. This preparation period belongs to the upcoming operational day.
Customer delivery quotes/orders and driver shift admission retain their existing
16:00 opening. Service enablement, branch authorization, prior-shift closure and
custom opening schedules remain enforced. The frozen service window still starts
at 16:00; preparation_start records the separate 15:45 staff preparation boundary.

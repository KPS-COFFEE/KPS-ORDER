# Offline tracking pilot 0.2.4 — acceptance still required

Code and pure policy tests do not certify device tracking. The matching backend
must expose authenticated `/api/operations/driver-location-batch` before this
pilot can sync. Keep customer tracking at its existing setting.

Use a test driver and a clearly identified open shift. Record device model,
Android version, battery restrictions, APK SHA-256 and backend revision. Never
copy session cookies or signing passwords into evidence.

| Scenario | Required observed evidence |
| --- | --- |
| Visible Activity, then lock screen for 15 minutes | Capture timestamps continue; manager location age reflects actual captures. |
| Use WhatsApp for 15 minutes | GPS still collected; next customer task and native brand rings remain usable. |
| Network absent for 10 minutes | Notification explains queued points; encrypted queue survives service/process loss and reopening in the same session. |
| Reconnect without moving | Heartbeat uploads pending IDs; historical points appear only in history, never as a fresh live marker. |
| Drop upload response after server commit | Retry sends identical IDs/captured_at; server acknowledges duplicates; route distance does not increase twice. |
| Revoke session or close shift before reconnect | Pending points are rejected/cleared, service stops, no cross-shift writes. |
| Sign in again or open a different shift | Old session queue is discarded; only new scope points upload. |
| Disable GPS / revoke precise location | Driver sees a concrete enable-location/reopen action; manager marker becomes stale. |
| Force-stop then reopen | No unsupported automatic restart; visible app must revalidate the session and start the service again. |
| Overnight / queue capacity / clock change | Expired or overflow samples create an explicit tracking gap; never fabricated measured distance. |

The service is non-sticky and does not restart after boot/force-stop. Android's
foreground-service restrictions and vendor battery restrictions remain in force.
Pending samples expire after 24 hours and are discarded on authorization denial;
this limits retained location data and can leave a visible history gap. This is
not a guarantee of uninterrupted sampling under every device power policy.

This document records required checks, not a successful field run. Device
capture, GPS stability, Keystore behavior while locked, reconnect and physical
APK installation remain unverified until real evidence is attached.

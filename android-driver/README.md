# KPS Driver Android pilot

Independent Android application (`sa.kps.driver`) for the existing KPS ORDER
driver workflow at `https://order.kps.sa/ops`. TILQA application, API, database,
licensing and build configuration are unchanged. No separate backend is added.

The location service adapts the Android `LocationManager` + foreground service
pattern reviewed in TILQA at commit
`95fa6fdddc605fbb5527e2bf7ccbbaa3b7b88585`, file
`mobile/native-platform/android/com/tilqa/nativeplatform/TilqaLocationService.kt`.
Its React Native/HeadlessJS authority is deliberately replaced with KPS's actual
work-context session, CSRF, current-shift and location endpoints. This is a new
adapter, not a claim that the TILQA APK can connect to KPS unchanged.

## Pilot behavior

- Existing login, shifts, tasks, collection and closeout remain in the existing
  web UI. No server changes or production rollout are included.
- After a driver logs in and opens a shift, the app requests precise location
  once. A location foreground service then continues when the Activity is
  backgrounded or the screen is locked. The app starts it only while visible.
- Each position is fresh, non-mock, accurate within 1 km and sent at most once
  per 20 seconds. No route history or offline coordinate queue is stored.
- Before publishing, the app rechecks the exact driver, brand, overlapping
  branch and original open shift using the current work-context credential.
  Server-side location scope and revocation protections remain authoritative.
- A 30-second heartbeat stops collection after a closed shift or revoked session.
  Network failures pause collection; the running service retries verification
  and resumes automatically after connectivity returns. Rejected authorization
  stops the service entirely. No stored coordinates are replayed as fresh.
- No JavaScript/native credential bridge, arbitrary API origin, cleartext,
  TLS bypass, reboot receiver, sticky restart or extra copied credential store.
- Android's foreground service notification is visible during tracking.
  Force-stop and vendor battery restrictions are not bypassed.

## Build and validation

JDK 17, Gradle 8.11.1 and Android SDK 35 are required. From this directory:

```
gradle :app:assembleDebug --no-daemon
```

The dedicated CI workflow compiles a **debug pilot APK**. It does not deploy
the website, install on a phone, publish to Play or create a production signing
key. Production signing and managed installation are separate rollout tasks.

Before installation beyond the pilot, verify on the actual company Android:
login/OTP, open shift, first permission, lock screen, WhatsApp, GPS/network
loss/recovery, shift close, logout/revocation, location freshness at the manager,
and photo evidence attachment. The current chooser uses the system image picker;
direct in-app camera capture and downloads need device acceptance. No physical
device or production acceptance is claimed by a successful compile.

The host is intentionally fixed for this pilot. If portal routing is enabled
and staff move to `staff.kps.sa`, change the app's trusted origin through a
reviewed build rather than accepting arbitrary origins or forwarding cookies.

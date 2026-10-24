# Driver native order reminders — 0.2.0 pilot

The active-shift foreground service reads the existing authenticated task API
and owner alert timing policy. It posts a native high-importance order
notification for the driver's assigned tasks and incoming handovers. It uses
the multibrand task endpoint when the session has multiple brand memberships.
No customer details are copied into notifications, and no extra Android
permissions or backend writes are added.

There is no employee mute/snooze control. Opening the app or tapping/swiping
the notification does not acknowledge work. Each successful read reconciles
the pending IDs with the server. Acceptance, completed handover, cancellation,
or reassignment clears the matching pending alert. Closing the bound shift,
logout, or revocation stops the service and all native order reminders.

The default verified inbox read interval is 30 seconds, sharing the shift
heartbeat. The first reminder is at the first successful read, the second at
the configured second threshold, and reminders repeat on verified reads after
the owner's continuous threshold. This is repeating notification audio, not
an uninterrupted alarm tone or push-delivery guarantee. On network/verification
failure the reminder is cancelled until a new successful read; this does not
accept or discard a server task. Old/unknown tasks do not ring.

Readiness detects disabled app notifications, a disabled/silent channel, zero
notification volume, silent mode, and Do Not Disturb. The app guides the user
to device notification settings and separately requests Android 13 notification
permission after the location permission; the original shared flag skipped
that request after first-time location authorization.

Android retains control of volume, notification channels, cooldown, force-stop,
and background scheduling. The application does not override those controls.
The service starts from the visible app after an authorized open driver shift
and precise location permission, as before. No boot/force-stop restart is added.

Validation: TrackingPolicyTest and BellPolicyTest run in the Android CI job,
followed by debug/release compilation, lint, and binary permission/signature
audit. Physical locked-screen/audio/battery and Play Protect acceptance remain
device checks. No driver installation is requested at this stage.

Version code is 3. Existing owner-signed 0.1.1 is not modified. Once this update
passes CI, a new release APK must be signed locally with the same owner key;
no production key, password, or signed owner APK is uploaded by this PR.

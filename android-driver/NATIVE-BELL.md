# Driver native order reminders — 0.2.1 pilot

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

Version code is 4. Existing owner-signed 0.1.1 is not modified. Once this update
passes CI, a new release APK must be signed locally with the same owner key;
no production key, password, or signed owner APK is uploaded by this PR.
# Owner-approved sound and remote updates — 0.2.1-pilot

The approved sound is `KPS-Restaurant-Order-Ring-2.wav`, SHA-256
`e31f18ed4c2a3ab8acf0950adebc50dade1046e94f70d9eeb1d86e7196dc577c`.
`ApprovedOrderRing.bytes()` reproduces it byte-for-byte without a network request.
The build's sound-policy test compares this SHA and validates the PCM WAV format.

`OrderAudio` checks the existing authenticated work host for a new descriptor at
most once per 60 seconds, during the existing verified-shift heartbeat. Downloads
are same-origin HTTPS only, have no redirects, are capped at 1 MB and verified
against the descriptor's SHA-256 and strict mono PCM WAV structure (0.1–10 seconds).
Only verified bytes are published by atomic cache-file rename. Failure retains
the approved or last verified sound. A missing backend endpoint does not block
tracking or the approved ring. Downloaded data never executes as code.

One existing notification channel remains authoritative. No new channel is
created on a sound change. Group-child notifications suppress duplicate system
audio; the selected file plays using `USAGE_NOTIFICATION` with transient audio
focus. App/channel disablement, null channel sound, low channel importance,
silent mode, notification volume zero, DND or denied permission prevent playback.
These conditions are checked before playback, again after preparation and every
250 ms while playing. No device volume, DND or notification setting is overridden.

Playback stops on cleared pending work, failed authorization, verification loss,
closed shift, service destruction, audio-focus loss and device mute. Previewing or
opening a notification is not task acceptance. Reminder timing is unchanged.

Owner controls are a separate server change: **Operation bell settings → Driver
app sound**. The owner can preview current/new audio, upload a validated WAV,
confirm and save, or restore the approved ring. Updates are brand-scoped,
owner-only, idempotent and audited without audio bytes in audit/idempotency data.
Sounds are stored separately from hot/public settings. The work endpoints never
return sound data from a different brand. Shared drivers use their authenticated
work-session brand's sound. This does not change browser bell tones.

The owner must install/sign this new APK once before remote sound updates work.
Existing owner-signed 0.1.1 does not gain this capability from website changes.
No owner key/password, production deploy or physical-device acceptance is part of
this preparation. Android device acceptance remains required before rollout.


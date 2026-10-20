# Enbishan customer SMS readiness

Customer OTP continues to use the existing Tawked Verify start/check contract. KPS customer and shared staff call sites keep their existing credential, payload and behavior.

Enbishan customer OTP is default-OFF. It cannot borrow `TAWKED_API_KEY`, even when the Enbishan identity channel is active. Both verification start and check require:

- `KPS_CUSTOMER_SMS_ENBISHAN_ENABLED=true`.
- `KPS_CUSTOMER_SMS_ENBISHAN_ACCEPTANCE_SHA` and `KPS_CUSTOMER_SMS_ENBISHAN_OWNER_APPROVAL_SHA` matching the runtime revision.
- A dedicated `ENBISHAN_TAWKED_API_KEY`, different from the KPS key.
- `ENBISHAN_TAWKED_SENDER=ENBISHAN`.
- `ENBISHAN_TAWKED_SENDER_APPROVAL_REFERENCE` referencing retained provider approval evidence.
- Existing identity-channel delivery proof and enabled state checks.

Sender identity must be configured and verified on the dedicated Tawked account/service. The application deliberately does not invent a `sender` API parameter. A locally supplied approval reference is an operator attestation, not independent provider verification. Before activation, verify with Tawked that the dedicated credential sends ENBISHAN and retain that evidence. If the provider cannot isolate sender identity by credential, keep activation blocked until its documented sender contract is implemented and tested.

No approval was obtained or claimed during development. No credentials were installed and no real message was sent. Synthetic transport tests establish credential routing and rejection behavior only. Production sender appearance/delivery remains BLOCKED pending provider approval and owner-authorized delivery proof.

Isolated Enbishan startup rejects both KPS and Enbishan provider credentials. Test fixtures remove inherited Enbishan credentials. No global sender setting is changed.

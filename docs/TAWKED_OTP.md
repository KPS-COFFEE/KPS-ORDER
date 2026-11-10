# KPS ORDER — Tawked Verify

This extends the existing identity routes; it does not replace staff identity or guest checkout.

## Configuration and owner proof

1. In Tawked's reviewed **Kps.sa** application, set code length **4**, expiry **5 minutes**, attempts **3**. These settings belong to the Tawked dashboard, not `verify/start` parameters. SMS sender ID belongs to Tawked; the approved application name is included in the message. Do not promise a custom sender ID.
2. Save `TAWKED_API_KEY` only in the Render service's private environment. Never paste it into git, issue comments, test fixtures, logs or chat. The Blueprint declaration does not populate an existing service's secret.
3. Deploy the reviewed PR revision and additive migration `044_sms_verification_provider.sql`. Automatic deployment remains off. No migration enables SMS or changes guest/email state.
4. Owner → identity settings → SMS → provider `tawked`, state `testing`, enabled **false**. `OTP_CHANNEL` remains `sms`. Provider selection in these settings overrides `OTP_PROVIDER` for this brand.
5. Use **Owner SMS test**, enter the owner's consenting test number, then type the code received. The server contacts the official start/check endpoints. A successful test consumes the challenge, records delivery proof and an audit event, and preserves the owner's session. It creates no customer identity and does not activate customer messaging.
6. Record actual receipt, message application name, four-digit length and successful check without recording the code or key. Only after explicit owner approval set SMS active/enabled. Guest ordering stays independent; staff email login remains six digits.

A provider switch clears delivery proof and expires pending SMS challenges. Existing customer identities and sessions remain intact. New providers must implement a server-only adapter; the browser never gets a key or provider verification reference.

## Controls

- Fixed HTTPS Tawked endpoints, Bearer authentication, 12-second timeout, redirects disabled, sanitized failures.
- Tawked `start` uses a challenge-bound Idempotency-Key and reference. No automatic send retry is performed. Separate explicit send requests remain rate limited and may generate separate SMS messages.
- Local expiry is capped at five minutes, including provider latency, and never exceeds the provider's expiry.
- Local atomic attempt reservation allows at most three provider checks. Failed transport attempts count conservatively; request a fresh code if necessary. Only four ASCII digits are accepted.
- Provider name, reference, phone, brand and delivery channel are stored server-side. Provider verification must succeed, then an atomic single-use claim must succeed before customer login.
- Testing routes require the brand's authenticated owner and CSRF proof. Unconfigured/paused/fault channels fail closed. A configured key alone does not activate SMS.

## Verification evidence

`tests/test_tawked_otp.py` uses synthetic responses only and never proves live delivery. The OTP acceptance workflow runs it with existing identity and staff-email tests against isolated PostgreSQL 18. Real delivery requires steps 1–6 above and is reported separately.

Official reference: https://tawked.com/en/docs
Policy settings: https://tawked.com/en/changelog (August 7, 2026)

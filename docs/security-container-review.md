# Security candidate: container and dependency evidence

Scope: SEC-01 / AUTH-OBS closure only. No deployment is performed by these tools.

## Locked runtime

`requirements.lock` freezes the same 35 distributions already present in the
original audit's Python environment. Direct requirements in `requirements.txt`
are unchanged. Each distribution has the SHA256 of the exact downloaded wheel.
The target is **CPython 3.12, Linux amd64, glibc (Debian bookworm)**. It is not a
portable lock for macOS, Windows, ARM or another Python version. Install with:

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
```

All dependencies, including transitive dependencies and selected extras, are
pinned. No source distribution or build dependency can be silently introduced.
A hash pins downloaded bytes; it does not prove the publisher is trustworthy.
To refresh, regenerate the complete runtime closure in a separate review, retain
the intended direct versions, download its wheels, capture their SHA256 values,
and validate in a clean Python 3.12 Linux amd64 environment. Do not mix an
unrelated upgrade campaign into this patch.

The Dockerfile pins the Docker Official Image index:

- Source tag: `docker.io/library/python:3.12-slim-bookworm`
- Index: `sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`
- Linux amd64 manifest: `sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d`
- Python version in the upstream manifest annotation: `3.12.15-slim-bookworm`

These hashes were retrieved from Docker Hub Registry's authenticated read API
and independently SHA256 checked against the response bytes on 2026-10-17 UTC.
The historical production image is **not** proved equal to this base or image.

`fonts-dejavu-core` still uses the existing Debian apt installation. Its resolved
version and all OS components must be captured in the candidate image SBOM. The
complete resulting image digest, not just the base digest, identifies a candidate.
A rebuild may differ if the apt repository changed, so preserve and deploy the
same reviewed image artifact rather than rebuilding it after approval.

## Build and inspect the exact Dockerfile candidate

After committing the complete reviewed patch on its dedicated branch, run on a
build-capable isolated Linux amd64 host with Docker Buildx, Syft and Trivy:

```sh
tools/security_image_review.sh /absolute/private/kps-security-image-evidence
```

The command refuses an uncommitted tree, builds locally, exports the actual
image, records its config and build metadata, generates a CycloneDX image SBOM,
and scans OS/library vulnerabilities and secrets. It does not start the app,
push an image, or deploy. A HIGH/CRITICAL result exits nonzero and remains an
open review item; do not suppress it to obtain a green gate.

The image archive hash, Docker image/config ID, OCI manifest digest, base index
digest, source commit and source-file hashes are different identities. Record
them with their correct labels. Retain the scanner versions, database update
metadata and the raw findings alongside the SBOM.

## Execution-environment boundary

The implementation workspace has no Docker/Podman/BuildKit daemon or binary;
`unshare` and `chroot` are denied by its kernel sandbox. Therefore a Dockerfile
build or container-runtime parity claim cannot be made here. A manually composed
OCI review artifact, if supplied, is a separate static scan target and **does
not** close the exact-Dockerfile build/run gate. Runtime-package-only SBOMs and
pip-audit results are not a scan of a full image.

Actual isolated PostgreSQL tests use **PostgreSQL 18.6** from the official PGDG
Ubuntu24.04 packages, with synthetic data and local-only connections. The host
Python/Ubuntu environment differs from Debian bookworm/Python3.12.15. Record
this explicitly until the same tests run inside the built candidate against an
isolated PostgreSQL18 service. Never point these tests at a production DSN.

## Evidence sources

- pip hash checking: https://pip.pypa.io/en/stable/topics/secure-installs/
- Docker digest pinning: https://docs.docker.com/build/building/best-practices/
- Base registry: https://registry-1.docker.io/v2/library/python/manifests/3.12-slim-bookworm
- PGDG package index: https://apt.postgresql.org/pub/repos/apt/dists/noble-pgdg/main/binary-amd64/Packages.gz
- Syft releases: https://github.com/anchore/syft/releases
- Trivy releases: https://github.com/aquasecurity/trivy/releases

## Final release continuation (2026-10-18 Riyadh)

The 63 original HIGH/CRITICAL rows cover 16 unique CVEs, all in OS packages;
none has a Bookworm FixedVersion in the original raw scan. The pinned base and
35 application distributions remain unchanged. We do not change distributions,
compile an unreviewed replacement crypto stack, or add CVE exclusions.

The runtime removes the actual unused SQLite library and ncurses-bin/infocmp
packages using apt, with package inventory retained. ncurses-bin is marked
Essential by Debian for a general terminal system; the explicit build-only
allow-remove-essential applies only to this named package in the disposable
image. No general upgrade, autoremove or production package operation is used. The application contains no
SQLite consumer and its persistence contract is PostgreSQL. Python sqlite3 is
therefore intentionally unavailable in the resulting minimal runtime; no claim
that SQLite itself was patched is made. The immutable image must pass the entire
mandatory PostgreSQL18 suite before this removal can be accepted. Build-time
pip and ensurepip are removed only after the hashed application lock installs.
All setuid/setgid file permissions are stripped; this mitigates privilege paths,
but is not a patch or a CVE waiver.

The CI captures component presence/features in that exact image, scans ALL
severities into candidate-image-scan.json, and separately enforces the unchanged
HIGH/CRITICAL exit-code-1 gate. The exact image archive is retained for 14 days
as a workflow artifact (not deployed/published). A remaining raw HIGH/CRITICAL
result stays a release blocker even when the triage documents reduced exposure.
Do not confuse absence of an application code path with absence of vulnerable
code. No blanket ignore, VEX filter, or ignore-unfixed switch is used.

Three additional existing employee/provider OTP suites are mandatory in both
host and image runs; none of the previous suites was removed. Real SMS delivery
and a safe immutable fallback remain separate pre-deployment gates, as detailed
in security-release-otp-rollback.md.

## JUnit and owner delivery-probe regression found in continuation

The first continuation image (8c2494d) built and ran, but its raw JUnit had
749 passed / 1 failed / 5 skipped in both host and container runs. The failed
case exposed owner delivery certification using a customer-context OTP route.
GitHub step status incorrectly remained green because default-shell pipelines
did not propagate the pytest exit status through tee. These historical failures
are retained and are not reported as successful acceptance.

All job run steps now select explicit Bash with pipefail, with a nonzero piped
command gate self-check. Dedicated owner delivery-probe aliases under
/api/identity/owner/sms-channel-test/ use the existing work context and portal
boundary. They require current owner session, CSRF, matching brand and testing
channel on every recheck, including after provider verification. Active channels
are rejected, so concurrent activation cannot turn a test into customer login.
No central session/context selection, password/SMS policy or account data is
changed. The admin UI uses these aliases; customer OTP paths stay isolated.
Existing tests plus new portal, CSRF, role, brand, replay, revocation and activation
race cases are mandatory in both host and immutable-image runs.

Delivery probes now carry an owner_probe_ challenge namespace, bound in both
directions before attempts/provider calls. Changing to the public customer URL
after channel activation cannot convert a test into customer-login proof; a
customer otp_ challenge cannot be consumed by the owner probe. This closes
bounded purpose confusion, not a work/owner privilege bypass.

The full host+browser job and full immutable-image job now run independently
in parallel, each with its own isolated PostgreSQL18 service. Both retain every
mandatory suite and explicit Bash pipefail; neither job depends on or shares a
database with the other. Image evidence retains its own artifact; host/browser
JUnit is a separate host artifact. No checks were removed to save time.

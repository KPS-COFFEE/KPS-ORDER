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

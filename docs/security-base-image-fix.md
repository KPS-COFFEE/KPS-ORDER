# Final runtime base fix — 2026-10-18

Continues 214c6f59a056d9e4e3ded706587c27b3628977ab. No application code,
requirements, SMS policy, business data, Render configuration or DNS changes.

Runtime: supported Google distroless base-nossl-debian13 nonroot, pinned index
8c563c1fb5e120606f0d85733049775faed6192e2bd2223ef283a5393eec22b9.
Python donor: official Python3.12.15 slim-trixie, pinned index
a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1.
All OS package inputs are Debian13; no cross-release package mixing.

Plain Trixie retains vulnerable util-linux/libacl. The final rootfs contains
complete Debian runtime library packages discovered through ELF dependencies,
their actual dpkg controls/file lists, fonts and the locked Python distribution.
No package-manager, shell, mount/libmount/libblkid, ACL, gzip or terminal tools
are copied. The optional Python terminal/SQLite/libuuid/Tk adapters are omitted;
Python uuid4 behavior is unchanged, with uuid.py retaining its standard fallback.
The real bytes are absent; no CVE, scanner severity or metadata is suppressed.
OpenSSL is installed from supported Trixie/security repositories during build.
The default command retains PORT (including the empty-variable fallback), two Uvicorn workers and graceful exec signals. The existing worker-profile test exercises the actual shell-free launcher with default/custom PORT instead of requiring the old shell syntax.

The exact immutable archive must pass the original 40 mandatory test suites plus the existing worker-profile suite,
PostgreSQL18, AR/EN browser policy tests, runtime health/readiness, SBOM and the
same raw full scan and HIGH/CRITICAL gate. No acceptance claim before results.

The independently built rollback target uses the same secure application and
Debian13 library closure, adding the supported Debian13 dash binary package
and restoring the original shell-based Uvicorn command. It preserves four-digit
SMS and all server-side authentication/isolation checks. Candidate and rollback
are different images; both run the full mandatory suite, PostgreSQL18,
health/readiness, SBOM and unmodified blocking scanner. Only passing images may
be approved. The host AR/EN browser suite covers their identical application.
No old password-bypass revision is used. No DB or business-data rollback.
Retain a valid owner session and prove new real SMS login during an approved
release before discarding that session.

Primary sources: docker-library/official-images library/python;
GoogleContainerTools/distroless README.md and base/config.bzl;
https://security-tracker.debian.org/tracker/CVE-2026-54369
https://security-tracker.debian.org/tracker/CVE-2026-76642
https://security-tracker.debian.org/tracker/CVE-2026-84782

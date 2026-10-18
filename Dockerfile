# All OS inputs are Debian 13; no Bookworm/Sid packages are mixed.
FROM gcr.io/distroless/base-nossl-debian13:nonroot@sha256:8c563c1fb5e120606f0d85733049775faed6192e2bd2223ef283a5393eec22b9 AS runtime-base
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=10000
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core libstdc++6 libssl3t64 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 kpsorder \
    && useradd --uid 10001 --gid kpsorder --no-create-home kpsorder
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip
COPY --chown=kpsorder:kpsorder app/ ./app/
COPY --chown=kpsorder:kpsorder migrations/ ./migrations/
COPY --chown=kpsorder:kpsorder data/ ./data/
COPY --chown=kpsorder:kpsorder public/ ./public/
RUN python -m app.image_previews
COPY --chown=kpsorder:kpsorder docs/metrics.md ./docs/metrics.md
COPY --chown=kpsorder:kpsorder tools/portable_backup.py ./tools/portable_backup.py
COPY --chown=kpsorder:kpsorder tools/windows-print-bridge/ ./tools/windows-print-bridge/

COPY --from=runtime-base / /runtime-root/
COPY tools/security_runtime_root.py /tmp/security_runtime_root.py
COPY tools/container_entrypoint.py /tmp/container_entrypoint.py
FROM build AS candidate-build
RUN python /tmp/security_runtime_root.py

FROM build AS rollback-build
RUN KPS_RUNTIME_SHELL=1 python /tmp/security_runtime_root.py

# Independent compatible rollback: original shell command, same secure app.
FROM runtime-base AS rollback
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 \
    PATH=/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH=/usr/local/lib
WORKDIR /app
COPY --from=rollback-build /runtime-root/ /
COPY --from=build --chown=10001:10001 /app/ /app/
USER 10001:10001
EXPOSE 10000
ENTRYPOINT []
CMD ["/bin/sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 2 --timeout-keep-alive 15"]

FROM runtime-base AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 \
    PATH=/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH=/usr/local/lib
WORKDIR /app
COPY --from=candidate-build /runtime-root/ /
COPY --from=build --chown=10001:10001 /app/ /app/
USER 10001:10001
EXPOSE 10000
ENTRYPOINT []
CMD ["/usr/local/bin/python", "/usr/local/bin/kps-order-start.py"]

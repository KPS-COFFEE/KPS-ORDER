FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app

# The installed font is used by Arabic PDF reports. Runtime data belongs in
# PostgreSQL, including attachment and report bytes; this image needs no disk.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 kpsorder \
    && useradd --uid 10001 --gid kpsorder --no-create-home kpsorder

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=kpsorder:kpsorder app/ ./app/
COPY --chown=kpsorder:kpsorder migrations/ ./migrations/
COPY --chown=kpsorder:kpsorder data/ ./data/
COPY --chown=kpsorder:kpsorder public/ ./public/
RUN python -m app.image_previews
COPY --chown=kpsorder:kpsorder docs/metrics.md ./docs/metrics.md
COPY --chown=kpsorder:kpsorder tools/portable_backup.py ./tools/portable_backup.py
COPY --chown=kpsorder:kpsorder tools/prelaunch_inventory.py ./tools/prelaunch_inventory.py
COPY --chown=kpsorder:kpsorder tools/windows-print-bridge/ ./tools/windows-print-bridge/

USER kpsorder
EXPOSE 10000

# Core lifespan runs versioned, advisory-locked additive migrations. exec
# delivers shutdown signals directly to Uvicorn for graceful transaction exit.
# Two web processes share this one service, not two paid Render instances.
CMD ["/bin/sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 2 --timeout-keep-alive 15"]

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
COPY --chown=kpsorder:kpsorder docs/metrics.md ./docs/metrics.md

USER kpsorder
EXPOSE 10000

# Core lifespan runs versioned, advisory-locked additive migrations. exec
# delivers shutdown signals directly to Uvicorn for graceful transaction exit.
CMD ["/bin/sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]

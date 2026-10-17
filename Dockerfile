FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app

# The installed font is used by Arabic PDF reports. Runtime data belongs in
# PostgreSQL, including attachment and report bytes; this image needs no disk.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core \
    # PostgreSQL is the only application store. Neither SQLite nor infocmp is
    # used by the runtime; remove their actual packages, never scanner metadata.
    # ncurses-bin is Debian-essential for general terminals, not this web image.
    && apt-get purge --yes --allow-remove-essential libsqlite3-0 ncurses-bin \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 kpsorder \
    && useradd --uid 10001 --gid kpsorder --no-create-home kpsorder

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock \
    # Packaging tools are build-time only; the final runtime cannot install code.
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    # The web service never needs a setuid/setgid executable.
    && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +

COPY --chown=kpsorder:kpsorder app/ ./app/
COPY --chown=kpsorder:kpsorder migrations/ ./migrations/
COPY --chown=kpsorder:kpsorder data/ ./data/
COPY --chown=kpsorder:kpsorder public/ ./public/
RUN python -m app.image_previews
COPY --chown=kpsorder:kpsorder docs/metrics.md ./docs/metrics.md
COPY --chown=kpsorder:kpsorder tools/portable_backup.py ./tools/portable_backup.py
COPY --chown=kpsorder:kpsorder tools/windows-print-bridge/ ./tools/windows-print-bridge/

USER kpsorder
EXPOSE 10000

# Core lifespan runs versioned, advisory-locked additive migrations. exec
# delivers shutdown signals directly to Uvicorn for graceful transaction exit.
# Two web processes share this one service, not two paid Render instances.
CMD ["/bin/sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 2 --timeout-keep-alive 15"]

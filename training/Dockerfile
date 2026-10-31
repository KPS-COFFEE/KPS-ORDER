FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql postgresql-client fonts-dejavu-core && rm -rf /var/lib/apt/lists/* && useradd -m -u 10001 learner
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock
COPY --chown=learner:learner app app
COPY --chown=learner:learner migrations migrations
COPY --chown=learner:learner data data
COPY --chown=learner:learner public public
COPY --chown=learner:learner docs/metrics.md docs/metrics.md
COPY --chown=learner:learner tools tools
COPY --chown=learner:learner training training
USER learner
CMD ["python", "-m", "training.server"]

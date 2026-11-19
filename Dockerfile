# syntax=docker/dockerfile:1
#
# TradeLedger production image (Phase 8).
#
#   docker build -t tradeledger:local .
#
# Two stages: "build" installs the pinned Python packages into a virtual
# environment; "runtime" copies only that environment and the application
# code, so compilers, pip caches and build files never reach the final
# image. The app runs under Gunicorn (never Flask's development server) as
# an unprivileged user. No secret is baked in: SECRET_KEY and the database
# password arrive at run time (environment variables or *_FILE secrets).
#
# Reproducibility: the Python packages are pinned (requirements.txt +
# constraints.txt). For a fully reproducible base image, replace the tag
# below with its digest (python:3.13-slim@sha256:...) after the first pull.

FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt constraints.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --requirement requirements.txt --constraint constraints.txt

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    TRADELEDGER_CONFIG=production
# A fixed, unprivileged user. Files stay owned by root, so the running
# process cannot modify its own code even if it is compromised.
RUN groupadd --system --gid 10001 tradeledger \
 && useradd --system --uid 10001 --gid tradeledger --no-create-home --shell /usr/sbin/nologin tradeledger
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY tradeledger ./tradeledger
COPY gunicorn.conf.py ./
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/tradeledger-entrypoint
USER 10001:10001
EXPOSE 8000
# Liveness only (no database call): see /health/live in web/views.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["tradeledger-entrypoint"]
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "tradeledger.wsgi:app"]

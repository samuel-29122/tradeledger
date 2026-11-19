#!/bin/sh
# Container entrypoint (Phase 8).
#
# 1. Optionally create or upgrade the database schema
#    (TRADELEDGER_MIGRATE_ON_START=1, used by Docker Compose). init-db never
#    deletes data, and PostgreSQL migrations take an advisory lock, so two
#    containers starting together cannot both migrate. On AWS, migrations
#    run as a separate one-off task after a snapshot instead (see
#    docs/PHASE_8_AWS_DEPLOYMENT.md), so this stays 0 there.
# 2. "exec" replaces this shell with Gunicorn, so Gunicorn is process 1 and
#    receives SIGTERM directly from Docker/ECS: it stops accepting new
#    connections and lets in-flight requests finish (graceful shutdown).
set -eu

if [ "${TRADELEDGER_MIGRATE_ON_START:-0}" = "1" ]; then
    flask --app tradeledger init-db
fi

exec "$@"

#!/usr/bin/env bash
# End-to-end smoke test of the Docker Compose stack (Phase 8).
#
# Builds the image, starts PostgreSQL + the app, and checks, through real
# HTTP requests to http://localhost:8000:
#   1. liveness and readiness (the app can reach PostgreSQL)
#   2. the database schema was created by the entrypoint
#   3. an account created with the CLI can sign in (CSRF + session cookie)
#   4. a trade can be created and appears in the journal and the analytics
#   5. an API token works and anonymous API calls get 401
#   6. after "docker compose restart", the trade is still there
#   7. PostgreSQL publishes no port to the host
# Used by the CI "docker" job; also runs on any machine with Docker.
# It creates throwaway secrets and removes the stack and its volume at the end.
#
#   bash scripts/compose_smoke_test.sh
set -euo pipefail
# (No "grep -q" or "head" below: with pipefail, a reader that stops early can make the
# command before it fail with SIGPIPE and turn a pass into a failure.)
cd "$(dirname "$0")/.."

BASE=http://localhost:8000
JAR=$(mktemp)
PASSWORD="smoke test passphrase $(date +%s)"
created_secrets=0

cleanup() {
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
    if [ "$created_secrets" = 1 ]; then rm -f secrets/secret_key.txt secrets/db_password.txt; fi
    rm -f "$JAR"
}
trap cleanup EXIT

if [ ! -f secrets/secret_key.txt ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
    python3 -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/db_password.txt
    created_secrets=1
fi

step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAILED: $*" >&2; docker compose logs app | tail -50 >&2; exit 1; }

step "build and start"
docker compose up --build -d

step "wait for readiness"
for _ in $(seq 1 60); do
    if curl -fsS "$BASE/health/ready" >/dev/null 2>&1; then break; fi
    sleep 2
done
curl -fsS "$BASE/health/live" | grep '"ok"' >/dev/null || fail "liveness"
curl -fsS "$BASE/health/ready" | grep '"ready"' >/dev/null || fail "readiness"

step "schema created by the entrypoint"
docker compose exec -T app flask --app tradeledger db-info | grep "Schema version: 3" >/dev/null || fail "schema version"

step "create an account with the CLI"
printf '%s\n' "$PASSWORD" | docker compose exec -T app flask --app tradeledger create-user \
    --email smoke@example.com --password-stdin

csrf() { curl -fsS -b "$JAR" -c "$JAR" "$BASE$1" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | awk 'NR==1'; }

step "sign in through the form"
TOKEN=$(csrf /login)
[ -n "$TOKEN" ] || fail "no CSRF token on the sign-in page"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" \
    --data-urlencode "csrf_token=$TOKEN" --data-urlencode "email=smoke@example.com" \
    --data-urlencode "password=$PASSWORD" "$BASE/login")
[ "$STATUS" = 303 ] || fail "sign-in returned $STATUS"
curl -fsS -b "$JAR" "$BASE/" | grep "smoke@example.com" >/dev/null || fail "overview after sign-in"

step "add an instrument and a trade"
TOKEN=$(csrf /instruments)
curl -fsS -o /dev/null -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" \
    -d symbol=EURUSD -d name=Euro -d asset_class=FOREX -d quote_currency=USD \
    -d price_increment=0.00001 -d contract_multiplier=100000 "$BASE/instruments"
TOKEN=$(csrf /trades/new)
INSTRUMENT=$(curl -fsS -b "$JAR" "$BASE/trades/new" | sed -n 's/.*<option value="\([0-9]*\)"[^>]*>EURUSD.*/\1/p' | awk 'NR==1')
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" \
    -d instrument_id="$INSTRUMENT" -d direction=LONG -d status=CLOSED \
    -d trade_date=2026-03-02T08:15 -d closed_at=2026-03-02T11:40 \
    -d entry_price=1.08500 -d stop_loss=1.08300 -d exit_price=1.08900 -d position_size=1 -d fees=7 \
    "$BASE/trades/new")
case "$STATUS" in 302|303) ;; *) fail "creating a trade returned $STATUS";; esac
curl -fsS -b "$JAR" "$BASE/trades" | grep "EURUSD" >/dev/null || fail "journal does not list the trade"
curl -fsS -b "$JAR" "$BASE/analytics" | grep '+1.97R' >/dev/null || fail "analytics do not show +1.97R"

step "API: anonymous 401, token 200"
[ "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/v1/trades")" = 401 ] || fail "anonymous API call"
TOKEN=$(csrf /account)
API_TOKEN=$(curl -fsS -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" -d name=smoke -d lifetime_days=30 \
    "$BASE/account/tokens" | sed -n 's/.*id="new-token-value"[^>]*value="\(tl_[^"]*\)".*/\1/p; s/.*value="\(tl_[^"]*\)".*/\1/p' | awk 'NR==1')
[ -n "$API_TOKEN" ] || fail "no API token issued"
curl -fsS -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/trades" | grep '"total": *1' >/dev/null || fail "API list"

step "restart: data persists"
docker compose restart app
for _ in $(seq 1 30); do curl -fsS "$BASE/health/ready" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/trades" | grep '"total": *1' >/dev/null || fail "data after restart"

step "PostgreSQL is not published on the host"
if docker compose port db 5432 >/dev/null 2>&1; then fail "db port is published"; fi

step "the app runs as a non-root user"
[ "$(docker compose exec -T app id -u)" = 10001 ] || fail "container user is not 10001"

echo
echo "Compose smoke test passed."

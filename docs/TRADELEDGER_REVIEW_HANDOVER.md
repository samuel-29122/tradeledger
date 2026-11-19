# TradeLedger: independent review handover (version 0.8.5)

This is for a technical reviewer who has the source tree, i.e. `tradeledger_final.zip` unpacked into `tradeledger/`. Every path is relative to that folder. Every command was run by the author on Linux with Python 3.13 unless the text says otherwise.

**Status in one paragraph:**
- **Tested:** the application and its test suite pass on SQLite and on PostgreSQL 16 with the **genuine psycopg 3.3.8 / psycopg_pool 3.3.0** drivers (installed from their official source tags), and on Python 3.11, 3.12 and 3.13. The app was also served by **genuine Gunicorn 26.0.0**.
- **Not tested:**
  - the Docker image has never been built (the registry is unreachable from the build environment);
  - `terraform validate` has never run (only HCL syntax and formatting were checked);
  - the GitHub workflows have never run (they were checked with actionlint);
  - nothing has been deployed.
- **It is not production-verified.** See §10.

## 1. Architecture

```
Browser ── session cookie ──► Flask app (tradeledger/)
                                 hooks: load_logged_in_user → require_login → CSRF   (web/__init__.py, web/auth.py, web/csrf.py)
                                 views: web/*.py (HTML)   api/*.py (JSON, Bearer tokens only)
                                 repositories/*.py   ← every query takes user_id (keyword-only)
                                 performance/*.py    ← Decimal analytics engine (no SQL)
                                 db.py               ← one adapter for SQLite (sqlite3) and PostgreSQL (psycopg + psycopg_pool)
                              ► SQLite file  or  PostgreSQL (schema version 4)
```

| Component | Files |
|---|---|
| App factory, security headers, production refusals | `tradeledger/__init__.py`, `tradeledger/config.py` |
| Database adapter, pooling, transactions, errors | `tradeledger/db.py`, `tradeledger/schema.sql`, `tradeledger/schema_postgres.sql`, `tradeledger/migrations.py` |
| Accounts and passwords (Argon2id) | `tradeledger/passwords.py`, `tradeledger/repositories/users.py` |
| Sessions (signed cookie plus server row) | `tradeledger/web/auth.py`, `tradeledger/repositories/sessions.py` |
| Two-step verification (TOTP) | `tradeledger/totp.py`, `tradeledger/web/two_step.py`, `tradeledger/repositories/two_step.py` |
| Password-reset links | `tradeledger/web/reset.py`, `tradeledger/repositories/password_resets.py` |
| API tokens | `tradeledger/repositories/api_tokens.py`, `tradeledger/api/__init__.py` |
| Throttling | `tradeledger/repositories/throttle.py` |
| Trading data | `tradeledger/repositories/instruments.py`, `tradeledger/repositories/trades.py`, `tradeledger/validation.py` |
| Analytics | `tradeledger/performance/report.py`, `tradeledger/performance/dashboard.py`, `tradeledger/static/js/analytics.js` |
| Logging, redaction, request ids | `tradeledger/observability.py` |
| Operator commands | `tradeledger/cli.py` |
| Deployment | `Dockerfile`, `.dockerignore`, `docker/entrypoint.sh`, `gunicorn.conf.py`, `compose.yaml`, `infra/*.tf`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` |

## 2. Install and run locally (SQLite)

```bash
cd tradeledger
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -c constraints.txt
pip install -r requirements-browser.txt -c constraints.txt && python -m playwright install chromium   # browser tests only
cp .env.example .env && python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" >> .env
flask --app tradeledger init-db                      # creates instance/tradeledger.db, schema version 4
flask --app tradeledger create-user --email reviewer@example.com
flask --app tradeledger seed-db --email reviewer@example.com   # synthetic data only
flask --app tradeledger db-info                      # tables, row counts, schema version
flask --app tradeledger run --debug                  # http://127.0.0.1:5000
```

**Production-style local run:** Gunicorn with a fresh SQLite file.

```bash
TRADELEDGER_CONFIG=production SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") \
DATABASE_PATH=/tmp/tl.db TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1 \
gunicorn -c gunicorn.conf.py --bind 127.0.0.1:8000 tradeledger.wsgi:app
```

The production cookie uses the `__Host-` prefix and the `Secure` flag. Browsers accept it on `http://localhost`; plain `http://` to other hosts will not keep you signed in.

## 3. Tests

```bash
python -m pytest                         # everything on SQLite; warnings are errors (pyproject.toml)
python -m pytest -m "not browser"        # without Playwright
python -m pytest -m chartjs_real -ra     # real Chart.js rendering in Chromium
node --test tests/js/*.test.js           # JavaScript units (Node 18+)
ruff check . && ruff format --check .
python scripts/qa_dashboard_agreement.py # independent recomputation of every dashboard figure
```

**PostgreSQL integration** needs a throwaway database. Each test creates and drops its own schema in it.

```bash
createdb tradeledger_test
TRADELEDGER_TEST_DATABASE_URL=postgresql://USER@localhost/tradeledger_test python -m pytest
python scripts/backup_restore_check.py --server postgresql://USER@localhost/postgres    # pg_dump → pg_restore → compare
python scripts/qa_dashboard_agreement.py --database-url postgresql://USER@localhost/EMPTY_DB
```

- **`tests/pg_libpq_bridge.py`** is a test-only ctypes driver, used only when `TRADELEDGER_PG_DRIVER=libpq-bridge` is set. Do **not** set it if psycopg is installed: the reviewer should test the real driver.
- **Markers** (`pyproject.toml`): `browser`, `chartjs_real`, `chartjs_stub`, `chartjs_offline`, `sqlite_only`, `postgres_only`. Skips print their reason (`-ra`).

**Expected results** (author's run, 16 November 2026; details in `docs/TRADELEDGER_FINAL_QA_REPORT.md`):
- **SQLite:** 1,476 collected; 1,434 passed, 42 skipped, 0 failed.
- **PostgreSQL with psycopg:** 1,441 passed, 35 skipped, 0 failed.

## 4. Authentication and isolation: how they work

- **Default deny.** `require_login` (`web/auth.py`) runs before every website request. Only `PUBLIC_ENDPOINTS` are open; `tests/test_auth.py::test_every_route_is_private_unless_deliberately_public` walks every route.
- **Passwords.** Argon2id with m=19 MiB, t=2, p=1 (`passwords.py`). One generic failure message; unknown emails are verified against a dummy hash, so timing reveals nothing.
- **Sessions.** The cookie holds `sid`, `uid`, `sv`, `auth_at`, `seen_at` and the CSRF token, signed with `SECRET_KEY`. Every request also needs a live `user_sessions` row (SHA-256 of `sid`): not revoked, not expired, not idle, and owned by `uid`. Sign-out, per-browser sign-out, a password change, a reset, deactivation, `set-password` and `disable-two-step` all revoke rows.
- **Two-step.** After a correct password, accounts with TOTP get only a 5-minute "pending" note (`web/two_step.py`); the session starts after the code. The last accepted step is stored atomically (no replay). Secrets are encrypted with AES-GCM (key derived from `SECRET_KEY` with HKDF, user id as associated data). Recovery codes are stored as SHA-256 and used once.
- **Reset links.** `flask create-reset-link` creates `tlr_` + 256 bits (stored as SHA-256), valid for 30 minutes and usable once. The token is moved out of the URL at once. Using it revokes every session and API token and does not sign the user in.
- **API.** Only `Authorization: Bearer tl_…`; cookies are ignored. Tokens are stored as SHA-256, expire, and are revoked on any password change.
- **Isolation.** Every repository function requires `user_id=` (`repositories/__init__.py::require_user_id` raises `TypeError` otherwise). Another user's id answers 404, byte-identical to a missing one. Trades reference instruments through the composite foreign key `(instrument_id, user_id)`. Proof: `tests/test_isolation.py`, plus the decoy account in the audit script.
- **Database privileges.** `flask grant-app-role --role NAME` (`cli.py`) grants DML on the eight app tables only. Proof: `tests/test_postgres_roles.py` runs the app as that role and checks that DDL is refused.

## 5. Chart.js: how it is bundled and verified

- **The files:**
  - the library: `tradeledger/static/vendor/chartjs/chart.umd.min.js`;
  - its licences: `LICENSE.md` and `LICENSE-kurkle-color.md`;
  - its provenance: `BUILD.md`.
- **Origin:** built from the official Git tags (chart.js `v4.5.1` @ `9c5cf9fa…`, @kurkle/color `v0.3.2` @ `eb0d1db2…`) by `scripts/build_chartjs.mjs` (esbuild). It is **not byte-identical** to the npm file, because the npm and CDN downloads were blocked.
- **Rebuild and compare:**

  ```bash
  git clone --depth 1 --branch v4.5.1 https://github.com/chartjs/chart.js /tmp/chart.js
  git clone --depth 1 --branch v0.3.2 https://github.com/kurkle/color /tmp/color
  node scripts/build_chartjs.mjs /tmp/chart.js /tmp/color /path/to/esbuild/lib/main.js
  openssl dgst -sha384 -binary tradeledger/static/vendor/chartjs/chart.umd.min.js | base64   # must match config.py
  ```

- **Checks:**
  - `CHART_JS_BUNDLED_INTEGRITY` in `tradeledger/config.py` is the SRI hash, and `tests/test_chartjs_bundle.py` fails on any mismatch;
  - `tests/test_chartjs_real.py` draws the seven charts in Chromium.
- **CI also runs the real-chart tests against the official npm file** (`.github/workflows/ci.yml`, browser job, step 4/4). That step has never run.

## 6. Docker, Compose and Terraform: how to inspect them

| What | Where | Checked so far |
|---|---|---|
| Image | `Dockerfile` (multi-stage, user 10001, Gunicorn, HEALTHCHECK), `.dockerignore` (allow-list), `docker/entrypoint.sh` | Static tests in `tests/test_deployment_files.py` only. **Never built** (Docker Hub blocked; `docker build --check` also needs the registry) |
| Local stack | `compose.yaml`, `secrets/README.md`, `scripts/compose_smoke_test.sh` | `docker compose config` parsed it earlier; **never started** |
| AWS | `infra/*.tf` (VPC without NAT, ALB, ECS Fargate, RDS, Secrets Manager, IAM, OIDC, alarms), `infra/README.md`, `docs/PHASE_8_AWS_DEPLOYMENT.md`, `docs/PHASE_8_COST_ESTIMATE.md` | HCL syntax and canonical formatting with `hclfmt` built from `hashicorp/hcl` v2.24.0 (14 files); static tests. **No `terraform validate`/`plan`**: the Terraform binary and providers are unreachable |
| CI/CD | `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` (manual, `DEPLOY_ENABLED`, `production` environment, OIDC) | actionlint 1.7.7 (built from source): no findings. shellcheck was not available, so the `run:` scripts were not shell-linted. **Never run on GitHub** |

**Commands a reviewer with network access should run:**

```bash
docker build -t tradeledger:review . && docker run --rm tradeledger:review id -u        # expect 10001
bash scripts/compose_smoke_test.sh                                                      # starts compose.yaml
terraform -chdir=infra fmt -check -recursive && terraform -chdir=infra init -backend=false && terraform -chdir=infra validate
pip-audit -r requirements.txt -r requirements-dev.txt
```

## 7. The most security-sensitive files

1. **`tradeledger/web/auth.py`:** the default-deny hook, the session checks and sign-in.
2. **`tradeledger/repositories/sessions.py`:** session rows; `problem_with` decides validity.
3. **`tradeledger/web/two_step.py`, `tradeledger/totp.py`, `tradeledger/repositories/two_step.py`:** the second factor, replay protection, encryption.
4. **`tradeledger/web/reset.py`, `tradeledger/repositories/password_resets.py`:** account recovery.
5. **`tradeledger/api/__init__.py`, `tradeledger/repositories/api_tokens.py`:** API authentication.
6. **`tradeledger/repositories/__init__.py`, `trades.py`, `instruments.py`:** owner scoping in every SQL statement.
7. **`tradeledger/web/csrf.py`, `tradeledger/web/forms.py` (`safe_next`):** CSRF and open-redirect defences.
8. **`tradeledger/__init__.py`, `tradeledger/config.py`:** production refusals, headers, ProxyFix hops, cookie flags.
9. **`tradeledger/db.py`:** the test-driver guard, pooling, transactions and SAVEPOINTs.
10. **`tradeledger/cli.py`:** `create-reset-link`, `disable-two-step`, `set-password`, `grant-app-role`; these are as powerful as passwords.
11. **`tradeledger/observability.py`:** log redaction.
12. **`infra/iam.tf`, `infra/github_oidc.tf`, `infra/security_groups.tf`, `infra/rds.tf`:** cloud permissions and network exposure.

## 8. Known limitations and unverified checks

These are summarised from `docs/TRADELEDGER_LIMITATIONS.md`.
- **Never run:**
  - the Docker build and the Compose stack;
  - `terraform validate`, `plan` and `apply`;
  - the GitHub Actions workflows;
  - any AWS resource.
- **psycopg was tested in its pure-Python implementation** over the system libpq. Production installs `psycopg[binary]` (the C implementation with bundled libpq), which was not available.
- **Dependency vulnerabilities** were checked offline against a clone of the PyPA Advisory Database (commit `682f16b1`, 14 November 2026): 31 pinned packages, 0 known vulnerabilities. `pip-audit` itself was not run, and npm advisories for Chart.js were not checked.
- **Python 3.11 and 3.12:** the browser tests and two jsonschema-based test files did not run, because their compiled dependencies (greenlet, rpds-py) exist here only for 3.13.
- **Also not done:** a WAF; email-based recovery; passkeys; mandatory two-step; the least-privilege role wired into AWS (the app still uses the RDS master user there); Firefox, Safari and screen-reader testing; an independent penetration test.

## 9. Areas that deserve special attention

1. **Session layering.** Confirm that every path that should end sessions revokes the rows **and** bumps `session_version`: `repositories/users.py::_revoke_sessions` and its callers.
2. **The two-step pending state.** It lives in the signed (not encrypted) cookie. Check that nothing in it grants access by itself: `web/two_step.py::_pending_user`.
3. **Reset-link handling.** Check the token-in-session redirect, the `Referrer-Policy` override in `__init__.py`, and that no logger receives the raw URL (`observability.py` adds the redaction filter to the `werkzeug` logger).
4. **`SECRET_KEY` coupling.** The same key signs cookies and derives the TOTP encryption key; rotating it disables codes (tested to fail closed). Decide whether a separate key is preferable.
5. **API tokens are not bound to two-step.** A token created by a two-step user keeps working without codes until it expires or is revoked. That is by design, but worth confirming.
6. **The proxy configuration.** `TRADELEDGER_PROXY_HOPS` and `gunicorn.conf.py` (`forwarded_allow_ips`) against the ALB in `infra/ecs.tf`. A wrong value lets clients fake their IP and get around the throttles.
7. **Throttling** is per account and per IP only. Assess the abuse cost without a WAF.
8. **Migrations.** Check `migrations.py` SQLite rebuild 3 (foreign keys off before `BEGIN`; `foreign_key_check`), the PostgreSQL advisory lock, and `grant-app-role` having to be re-run after each migration.
9. **The bundled Chart.js** is a third-party build artefact. Compare it with the npm file (CI step 4/4) before trusting it.
10. **The Terraform defaults:** tasks in public subnets with public IPs, protected only by security groups; the app uses the RDS master user.

## 10. What "verified" means here

Only these claims are backed by runs the author saw:
- the application behaviour and the tests, on SQLite and PostgreSQL;
- psycopg and Gunicorn behaviour, as listed above;
- Python 3.11, 3.12 and 3.13, within the limits listed;
- Chart.js rendering in Chromium;
- the HCL and workflow linting.

**Infrastructure, containers, CI and any cloud deployment are unverified.** TradeLedger must not be described as production-verified until §6's commands pass.

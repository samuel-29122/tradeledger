# Changelog

All notable changes to TradeLedger. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the build phases.

## [0.8.5] - 2026-11-16 - Phase 8.5: completion, verification and polish

### Final audit (before release, same version)
- **Fixed:** the PostgreSQL connection pool was never closed, so every process (each Gunicorn worker) took about 20 seconds to exit. It was found by testing the genuine psycopg_pool 3.3.0; a regression test was added.
- **Verified** with the genuine psycopg 3.3.8 and Gunicorn 26.0.0, on Python 3.11 and 3.12.
- **Checked offline:** dependencies against the PyPA Advisory Database, Terraform with hclfmt, workflows with actionlint.
- **Added:** `docs/TRADELEDGER_REVIEW_HANDOVER.md` for an independent reviewer.
- **No features were added.**

### Added
- **Bundled Chart.js 4.5.1.** It is built from the official `v4.5.1` source tag (with `@kurkle/color` 0.3.2), with both MIT licences, a provenance note (`static/vendor/chartjs/BUILD.md`), a reproducible build script (`scripts/build_chartjs.mjs`) and an SRI hash. It is served by TradeLedger by default, so no CDN is needed. All 11 real-chart browser tests now run and pass.
- **Signed-in browsers.** Every sign-in has a server-side `user_sessions` row; the Account page lists them and signs out any one of them.
- **One-time password-reset links:** `flask create-reset-link` plus the `/reset-password` page.
- **Two-step verification (TOTP, RFC 6238),** with ten single-use recovery codes, plus `flask disable-two-step` for lost phones.
- **`flask grant-app-role`:** a least-privilege PostgreSQL role for the web app, with row access only.
- **Schema version 4** on SQLite and PostgreSQL: new tables and nullable columns only. PostgreSQL now applies a real migration on top of its baseline.
- **Tests:** 184 more (1,475 in total), including:
  - RFC test vectors;
  - races for reset links, sessions and codes;
  - a process killed during a SQLite migration;
  - a PostgreSQL migration terminated half-way;
  - the application run as the restricted role;
  - 20 more security mutants (40/40 caught).

### Fixed
- **Sign-out did not end a session on the server.** A copied cookie stayed valid until it expired; it is now refused.
- **Idle and absolute limits trusted values inside the cookie;** they are now also enforced from the server's record.
- **`set-password` did not revoke API tokens,** although the Phase 8 docs said it did.
- **`flask run` printed reset-link tokens in its access log;** `werkzeug` log lines are now redacted.
- **The drawdown chart's axis read `-0.20000000000000002R`** (found by the real Chart.js screenshots).
- **Layout:**
  - analytics tables were clipped at 1440 px;
  - table links were smaller than 24 px;
  - the session table clipped its button at 1024 px;
  - the "This browser" badge squeezed the browser name on phones.
- **The offline chart message still mentioned the CDN.**

### Changed
- **The test-only PostgreSQL bridge is now refused under the production configuration,** and only modules marked `TEST_ONLY` may replace psycopg.
- **Version 0.8.5.** The login page now explains how to get a reset link.

### Changed tests (why)
- **Schema version expectations** moved from 3 to 4 (migration 4 added).
- **The test sign-in helpers** (`sign_in`, `LiveServer.session_cookie`, the audit and benchmark scripts) now also create a session row, exactly as the real form does. A guard test checks the two match.
- **`test_absolute_session_lifetime`** used to fake activity by editing `seen_at` inside the cookie, which no longer counts. It now stays active through real requests, which is stricter.
- **The default-deny route walk and the public-endpoints list** include the two new public pages (two-step and reset), which are useless without their secret.
- No test was deleted or weakened.

## [0.8.0] - 2026-11-15 - Phase 8: accounts, PostgreSQL, Docker and AWS design

### Security
- **Added:** user accounts. Passwords are hashed with Argon2id (m=19 MiB, t=2, p=1, from `cryptography`), with NIST-style rules and automatic re-hashing when the settings change.
- **Added:** sign-in, sign-out (POST + CSRF), registration (off by default in production) and an account page (password change, "sign out everywhere else").
- **Added:** session security:
  - the session is cleared at sign-in;
  - absolute (8 h) and idle (2 h) limits, checked on the server;
  - a per-user session version that ends every session on a password change or deactivation;
  - in production, a `__Host-` Secure, HttpOnly, SameSite=Lax cookie.
- **Added:** sign-in throttling stored in the database (per account and per address, keyed HMAC digests), and a dummy hash for unknown emails to keep response times uniform.
- **Added:** per-user ownership of instruments and trades. Every repository function requires `user_id`; the database refuses a trade on another user's instrument; other users' ids answer 404.
- **Changed:** the JSON API now requires a personal Bearer token (hashed, expiring, revocable; revoked on password change) and ignores cookies. OpenAPI documents the scheme.
- **Removed:** `TRADELEDGER_ENABLE_UNAUTHENTICATED_API` and `TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL`. If either is still set, it is ignored with a warning. The Phase 7 production guard (`web/guard.py`, `home_disabled.html`) was replaced by sign-in.
- **Added:** production refuses to start in debug mode; `reset-db` is refused in production; proxy headers are trusted only with `TRADELEDGER_PROXY_HOPS`; secrets can come from `*_FILE` paths.
- **Added:** JSON logs with request ids and redaction of tokens, Bearer values, passwords and database URLs. The access log never records query strings.

### Added
- **Database:**
  - PostgreSQL 16 support through a small adapter (`db.py`), with psycopg 3 and a connection pool in production, and `NUMERIC` for exact decimals.
  - SQLite migration 3 rebuilds two tables to add owners, keeping every row byte for byte.
  - `claim-legacy-data` gives pre-Phase 8 records to an account deliberately (dry run by default).
- **Commands:** `create-user`, `set-password`, `deactivate-user`, `activate-user`, `list-users`. `seed-db`, `analytics` and `report` now take `--email`.
- **Scripts:**
  - `scripts/migrate_sqlite_to_postgres.py`: read-only source, explicit owner, empty-target rule, dry run, exact verification, JSON audit.
  - `scripts/backup_restore_check.py`.
  - `scripts/compose_smoke_test.sh`.
- **Health checks:** `/health/live` (liveness) and `/health/ready` (readiness, database check).
- **Docker:** a multi-stage `Dockerfile` (non-root, Gunicorn, healthcheck), `.dockerignore` allow-list, `gunicorn.conf.py`, and a `compose.yaml` with PostgreSQL on an internal network and file-based secrets.
- **AWS:** Terraform in `infra/`: VPC without NAT, security groups, ECR, ECS Fargate, RDS PostgreSQL, ALB with a host rule, Secrets Manager, CloudWatch alarms, and a GitHub OIDC deploy role. **Not applied.**
- **CI:** new jobs for PostgreSQL (real service, psycopg, audit on both engines), Docker (hadolint, build, Compose smoke test) and Terraform (fmt, validate). A separate manual `deploy.yml` with approval and OIDC. **Neither has run on GitHub.**
- **Tests:** authentication, accounts and tokens, two-user isolation, the legacy upgrade and claim, PostgreSQL, operations and configuration, deployment files, and browser sign-in journeys (more than 220 new tests).
- **Documents:** the Phase 8 guide, code listing, QA report, security review, AWS deployment guide, database migration guide, cost estimate and release checklist.

### Changed
- **Tests:** Phase 1–7 tests now run as a signed-in test account (and send its API token). Engine-specific tests are marked `sqlite_only`. Each changed expectation is explained in the test file.
- **Search:** now `LOWER(...) LIKE LOWER(?)` so it ignores case on both databases.
- **Benchmark:** compares SQLite and PostgreSQL and measures the API and authentication.

### Fixed
- **API tokens:** refreshing the page that shows a new API token re-sent the form and silently created a second token. The form is now single-use (found by a browser test).

## [0.7.0] - 2026-11-15 - Phase 7: quality, testing, CI and security

### Security
- **Fixed:** decimal inputs had no magnitude limit. `"1e999999"` has one significant digit, so it passed validation, was stored as a one-million-digit string, and then crashed (`decimal.Overflow`) every page that evaluated the trade: journal, trade page, dashboard and analytics API. Values are now limited to 15 digits before and 15 after the decimal point. The engine also reports an out-of-range row as an excluded trade instead of crashing (defence in depth).
- **Added:** Host header allow-list (`TRADELEDGER_TRUSTED_HOSTS`, default `localhost,127.0.0.1`) against DNS rebinding.
- **Changed:** production refuses a `SECRET_KEY` shorter than 32 characters or equal to a published placeholder.
- **Added:** `Cache-Control: no-store` on HTML pages; `Strict-Transport-Security` in production.
- **Fixed:** the order of the website's request hooks (production guard before CSRF check) depended on module import order. Sorting the imports silently swapped them, so a production POST answered 400 instead of 403. They are now registered explicitly.

### Performance
- Stored timestamps are parsed with a strict pattern plus `datetime.fromisoformat` instead of `datetime.strptime` (new `tradeledger/timestamps.py`). The dashboard page at 10,000 trades is about 28% faster in back-to-back measurements on the same machine. The results are identical (tested against `strptime`).

### Added
- `pyproject.toml` with pytest, coverage and Ruff settings; pytest now treats warnings as errors and has registered markers.
- `requirements-dev.txt`, `requirements-browser.txt` and `constraints.txt` (every dependency pinned).
- GitHub Actions workflow: lint, formatting, tests with coverage on Python 3.11 and 3.13, JavaScript tests, pip-audit, and browser tests split into stub, offline and real Chart.js steps.
- Tests:
  - financial edge cases;
  - database consistency across 486 filter combinations;
  - security regressions;
  - error paths;
  - timestamp equivalence;
  - an end-to-end browser journey;
  - accessibility checks with a negative control;
  - real Chart.js rendering tests (skipped unless the library file is present).
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog, pull request and bug report templates.
- Documents: `docs/PHASE_7_GUIDE.md`, `PHASE_7_CODE.md`, `PHASE_7_QA_REPORT.md`, `PHASE_7_SECURITY_REVIEW.md`, `PHASE_7_RELEASE_CHECKLIST.md`.
- Benchmark: 100-trade size, SQL time, peak memory, chart-data size, `--profile`.

### Changed
- The codebase is formatted with `ruff format` (layout only; the syntax tree is unchanged apart from whitespace in one docstring).
- Tests now close file responses (they leaked file handles; hidden until warnings became errors).
- The chart fallback message no longer names the CDN, because the library may be self-hosted.
- Browser test fixtures moved to `tests/browser_support.py`.
- `pytest.ini` is replaced by `pyproject.toml`; `requirements.txt` now lists runtime dependencies only.

## [0.6.0] - 2026-11-15 - Phase 6: analytics dashboard
- `/analytics` dashboard with 12 KPI cards, per-currency net P&L, seven Chart.js charts, filters, accessible tables and a data-quality section; `strategy` and `session` filters on the analytics API.

## [0.5.0] - Phase 5: web interface
- Server-rendered journal, trade forms, trade details, instruments page, CSRF protection and production guard.

## [0.4.0] - Phase 4: JSON API
- Versioned `/api/v1` with OpenAPI 3.1; `closed_at` exit times and migration 2.

## [0.3.0] - Phase 3: analytics engine
- Exact-decimal P&L, R-multiples and performance statistics.

## [0.2.0] - Phase 2: database
- SQLite schema with constraints, repositories and sample data.

## [0.1.0] - Phase 1: project setup
- Flask application factory, configuration and health check.

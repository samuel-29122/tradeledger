# Phase 8 QA report

**Date:** 15 November 2026.

**Environment:** Python 3.13.18 on Linux x86-64; SQLite 3.x (Python's built-in); PostgreSQL **16.15** (a local server started for testing); Chromium through Playwright; Node.js.

**Network:** no package index and no container registry access. Because of that, psycopg, psycopg-pool, gunicorn, coverage.py, Terraform and every Docker base image were unavailable.

## 1. Baseline (Phase 7, re-measured before any change)

The Phase 7 ZIP was unpacked into a fresh folder and run as it was:
- **Tests:** 1,038 collected; 1,028 passed, 10 skipped (all `chartjs_real`), 0 failed.
- **Ruff:** clean on 81 files.
- **Node:** 11 of 11.

These matched the figures reported in Phase 7.

## 2. Results

| Check | Result | How it was run |
|---|---|---|
| pytest, SQLite | **1,291 collected: 1,259 passed, 32 skipped, 0 failed, 0 warnings** | `python -m pytest` (warnings are errors) |
| pytest, PostgreSQL 16.15 | **1,248 passed, 43 skipped, 0 failed** | `TRADELEDGER_TEST_DATABASE_URL=… TRADELEDGER_PG_DRIVER=libpq-bridge python -m pytest`; every test in its own schema; browser tests included |
| Browser tests (Playwright, Chromium) | **110 collected: 99 passed on each engine; 11 skipped** (10 real Chart.js, 1 screenshot capture) | part of the runs above |
| JavaScript | **11/11** | `node --test tests/js/*.test.js` |
| Ruff lint / format | **clean** (104 files) | `ruff check .`, `ruff format --check .` |
| Independent analytics audit, SQLite | **94,384 comparisons, 0 disagreements** (900 filter combinations; 333 chart sets checked by the real `analytics.js` in Node) | `scripts/qa_dashboard_agreement.py` |
| Independent analytics audit, PostgreSQL | **94,384 comparisons, 0 disagreements** | `--database-url …audit --pg-bridge` |
| Security mutation testing | **20/20 caught** (list in §5) | a QA mutation script (not shipped) |
| Statement coverage (approximate) | SQLite run 96.17%; PostgreSQL run 95.38%; **combined 98.15% (4,762 / 4,852)** | standard-library `sys.monitoring` tracer |
| Backup and restore | **identical** row counts, trade values and reports after `pg_dump` → `pg_restore` | `scripts/backup_restore_check.py` |
| Compose smoke test (HTTP logic only) | **passed** against a production-configuration server | see §6: this is **not** a Docker test |
| Deployment-file static checks | **26 passed** | `tests/test_deployment_files.py` |

**Why tests were skipped:**
- **SQLite run, 32 skips:** 10 need real Chart.js; 1 is screenshot capture (needs an environment variable); 20 need PostgreSQL; 1 checks a PostgreSQL-only NOT NULL rule.
- **PostgreSQL run, 43 skips:** the same 11, plus 32 SQLite-only tests: the Phase 3→8 file upgrades, legacy claims and SQLite file tricks, which PostgreSQL databases never go through.

**Why the coverage figure is approximate:** coverage.py could not be installed, so the figure is statement coverage from a standard-library tracer, not coverage.py; branch coverage was not measured. The 90 statements not covered are:
- `wsgi.py` (the production entry point, deliberately never imported by tests);
- the `psycopg_pool` branch of `db.py` (needs psycopg);
- the empty loop for future PostgreSQL migrations;
- a few defensive branches.

CI measures coverage with coverage.py against the threshold in `pyproject.toml`.

## 3. New tests (Phase 8)

| File | Tests | What it proves |
|---|---|---|
| `test_auth.py` | 53 | Default-deny route walk; sign-in success and generic failures; CSRF on sign-in; session fixation; re-hashing; safe redirects (9 cases); throttling per account and per IP, its expiry and reset; sign-out; absolute and idle lifetimes; session version; tampered cookies; production cookie flags; registration rules, rate limit and size limit; no hash in pages |
| `test_account_and_tokens.py` | 35 | Password change (current password, rules, ends other sessions, revokes tokens, throttled); sign out elsewhere; tokens shown once and stored hashed; form validation; limits; no logging; cookie-only API calls refused; 8 malformed credentials; expiry; revocation; disabled accounts; resubmission does not duplicate |
| `test_isolation.py` | 36 | Two seeded users; guessed ids on pages and API; foreign and missing answers byte-for-byte identical; cross-user edit, delete and create; search, filters, strategy lists, paging, analytics; owner fields refused in JSON; repository guards |
| `test_passwords.py` | 21 | OWASP parameters in production; round trips; salts; malformed hashes; Unicode normalisation; re-hash detection; password rules |
| `test_legacy_and_cli_accounts.py` | 15 | Phase 7 → v3 byte-for-byte; foreign-key check and triggers; invisibility; dry run, apply and conflicts; `migrate-db` backup; account commands; `reset-db` refused in production |
| `test_operations.py` | 23 | Debug refused; production defaults; secrets from files; no stack traces; proxy hops (default off, last hop only, never Host); readiness 503 without details; request ids; log redaction; access log without query strings |
| `test_postgres.py` | 20 | Same version and columns as SQLite; NUMERIC types; concurrent migrations; exact decimals; database-level limits; named constraints; row locks serialise edits; rollback; the import script (dry run, exact copy, report parity, refusals, no passwords) |
| `test_deployment_files.py` | 26 | Dockerfile, `.dockerignore`, Compose, workflows and Terraform security properties; Terraform references resolve; no secrets in the repository |
| `test_browser_auth.py` | 15 | Real sign-in in Chromium (returns to the requested page); focused error summary; autocomplete; register → sign out → sign in; foreign trade URL 404; token create and revoke (this test found the resubmission bug); labels; one h1; 4 widths; keyboard only; screenshots |

## 4. Phase 1–7 tests that changed, and why

None was deleted or weakened. Each change follows a requirement that changed in Phase 8, and the reason is written in the test file.

- **Every repository call now passes `user_id=USER_ID`.** A script edited the calls through the AST (≈200 call sites); the assertions are unchanged.
- **`client` is now signed in and sends an API token,** so the Phase 1–7 page and API tests run through the real authentication checks. The new `anon_client` covers anonymous behaviour.
- **Phase 7 production-guard tests** (403 "journal disabled") became "production requires sign-in" tests (302 to `/login`, nothing written). The "enable without authentication" tests became "the removed switch is ignored" tests (`test_web_security.py`, `test_api_security.py`, `test_security_hardening.py`, `test_web_analytics.py`).
- **The hook-order test** now checks load session → require login → CSRF.
- **`test_db.py` and `test_migrations.py`:**
  - schema version 2 → 3;
  - `sqlite3.IntegrityError` → the adapter's `db.IntegrityError`;
  - raw SQL includes `user_id`;
  - the v1 → latest upgrade now applies `[2, 3]` and claims the legacy rows before reading them through the repositories;
  - SQLite-specific tests are marked `sqlite_only`.
- **CLI tests** pass `--email` to `seed-db`, `analytics` and `report`.
- **`test_no_fake_account_features`:** Phase 5 banned fake account features. Now that accounts are real, the test checks that "Sign out" is a working POST form with a CSRF token, and that the other fake features (avatar, notifications) stay absent.
- **`test_csrf_error_page_reveals_no_token`:** the sidebar's sign-out form legitimately carries the user's own token, so the test now checks that the submitted value is never echoed and that the real token appears **only** in that form.
- **Out-of-range-value tests** (Phase 7 defence in depth) are `sqlite_only`: PostgreSQL's NUMERIC CHECK constraints refuse such a row outright.

## 5. Security mutants (each must make a named test fail)

Every one of these was **caught**:

**Data isolation**
1. owner filter removed from lists and counts;
2. `get_trade` ignores the owner;
3. `delete_trade` ignores the owner;
4. `get_instrument` ignores the owner;
5. two-column owner foreign key dropped.

**Sessions and sign-in**

6. login no longer required;
7. session version not checked;
8. absolute lifetime not checked;
9. session not cleared at sign-in;
10. throttle never blocks;
11. a wrong password accepted;
12. open redirect.

**API tokens**

13. revoked or expired tokens accepted;
14. API needs no token;
15. password change keeps tokens;
16. token form can be resubmitted.

**Data handling and configuration**

17. legacy claim ignores conflicts;
18. production registration on by default;
19. `X-Forwarded-Host` trusted;
20. tokens not redacted in logs.

## 6. Performance

Measured with `scripts/benchmark_dashboard.py`. Each database holds **two** accounts of N trades each, so every query must filter by owner. Figures are medians of 5 runs on this machine only; they are **not guarantees**.

| Trades | Engine | SQL | Report | Dashboard page | Filtered page | API list (100) | API summary | Peak memory | Page |
|---|---|---|---|---|---|---|---|---|---|
| 100 | SQLite | 0.4 ms | 7.8 ms | 13.1 ms | 2.2 ms | 3.6 ms | 8.4 ms | 1.1 MB | 169 KB |
| 1,000 | SQLite | 4.0 ms | 56 ms | 73 ms | 5.1 ms | 3.7 ms | 57 ms | 5.6 MB | 488 KB |
| 10,000 | SQLite | 71 ms | 465 ms | 572 ms | 28 ms | 5.6 ms | 480 ms | 30.4 MB | 3,033 KB |
| 100 | PostgreSQL* | 2.7 ms | 9.8 ms | 19 ms | 6.0 ms | 10 ms | 14 ms | 1.1 MB | 169 KB |
| 1,000 | PostgreSQL* | 36 ms | 80 ms | 104 ms | 10 ms | 12 ms | 102 ms | 5.5 MB | 488 KB |
| 10,000 | PostgreSQL* | 284 ms | 671 ms | 757 ms | 47 ms | 12 ms | 636 ms | 30.3 MB | 3,033 KB |

\*Through the **test-only libpq bridge**, not psycopg. For the 10,000-trade query, `EXPLAIN ANALYZE` shows PostgreSQL itself executing it in **10.4 ms** (175 shared buffer hits, index on `(user_id, …)`), while the bridge took about **245 ms** to turn the 10,000 rows into Python values. So the PostgreSQL rows mainly measure the bridge's pure-Python decoding. **They say nothing about psycopg's speed,** which decodes in C. Do not conclude that PostgreSQL is slower than SQLite here: that has not been measured.

**Authentication costs:**
- **Argon2id at the production settings:** about **19 ms** to hash and **19 ms** to verify. That is paid once per sign-in, never per page.
- **Per signed-in request:** one primary-key lookup of the user, under 0.01 ms on SQLite.
- **Per API request:** one indexed token-hash lookup, about 0.02 ms on SQLite.

Neither changes the dashboard figures in any meaningful way. No caching of user data was added.

## 7. Not verified, or blocked

| Item | Why | Where it is prepared |
|---|---|---|
| Real Chart.js 4.5.1 rendering | The CDN, npm and unpkg are unreachable (checked again in Phase 8: no response); no copy exists on disk. **10 tests remain skipped; nothing was faked** | CI `browser` job step 3 (`npm pack chart.js@4.5.1`, `TRADELEDGER_REQUIRE_REAL_CHARTJS=1`) |
| psycopg / psycopg-pool behaviour | Not installable here; the PostgreSQL SQL was verified through libpq | CI `postgres` job, with a step that prints the psycopg version |
| Docker image build and Compose stack | Docker Hub is blocked (`python:3.13-slim` and even the `docker/dockerfile` frontend cannot be pulled). `docker compose config` passed, and the smoke script's HTTP steps passed against a local production-config server through a stand-in for the `docker` command | CI `docker` job (hadolint, build, user and contents checks, `compose_smoke_test.sh`) |
| `terraform fmt` / `validate` | Terraform is not installed; the files were checked as text instead (references, security properties) | CI `terraform` job |
| GitHub Actions | No GitHub repository was used. Both workflows parse as YAML; neither has run | `.github/workflows/` |
| AWS deployment, IAM, TLS, RDS restore | Not authorised to create resources (and none were created) | `infra/`, `docs/PHASE_8_AWS_DEPLOYMENT.md` |
| Python 3.11 / 3.12 | Interpreters exist here but without Flask | CI `python` job (3.11 and 3.13) |
| pip-audit, branch coverage | Not installable | CI `audit` and `python` jobs |
| Screen readers and other browsers | Not available | manual checklist in the release checklist |

## 8. Bugs found during Phase 8

- **API tokens:** refreshing the page that displays a new API token re-sent the POST and created a second token. Found by `test_browser_auth.py`. **Fixed** with a single-use form value, with a regression test.
- **Smoke script:** `grep -q` and `head` inside `set -o pipefail` pipelines can turn a pass into a failure (SIGPIPE). Found while exercising `compose_smoke_test.sh`. **Fixed.**
- **Isolation (anticipated before any leak, not a regression):** the dashboard's instrument filter checked only that an instrument existed. In a multi-user database that would have let one user's instrument id produce a valid filter for another user. **Scoped to the signed-in user**; the decoy-account audit checks it.

## 9. Clean-extraction check of the delivered ZIP

`tradeledger_phase8.zip` was unpacked into an empty folder, and these checks were run there, not in the working copy:
- `diff -r` against the working tree: identical.
- **Exclusions:** no databases, `.env`, secret files, caches, virtual environments or Terraform state (`secrets/` contains only its README).
- **Ruff:** clean (105 files).
- **Node:** 11/11.
- **pytest on SQLite:** 1,259 passed, 32 skipped.
- **pytest on PostgreSQL 16.15:** 1,248 passed, 43 skipped.
- **Audit:** 94,384 comparisons, 0 disagreements.
- **Production-configuration HTTP check** (Flask server, fresh SQLite file, `TRADELEDGER_CONFIG=production`, JSON logs):
  - health live and ready answer 200;
  - anonymous `/trades` gives 302 to `/login?next=/trades`;
  - `/register` gives 404 (off in production);
  - the cookie is named `__Host-tradeledger_session`;
  - a wrong password re-shows the form with the generic message;
  - the right password gives 303, then `/trades` gives 200;
  - the API without a token gives 401;
  - CSP, `no-store` and HSTS headers are present;
  - the password never appears in the log.

The check used curl, which, like browsers, sends Secure cookies to `http://localhost`.

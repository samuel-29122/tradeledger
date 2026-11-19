# TradeLedger final QA report (version 0.8.5, after the final audit)

**Dates:** 15–16 November 2026. The final audit and re-verification ran on the evening of 16 November.

**Environment:**
- Python 3.13 on Linux x86-64; also Python 3.11.18 and 3.12.3;
- SQLite (the Python built-in);
- PostgreSQL **16.15**, a local server;
- Chromium through Playwright; Node.js 22.

**Network:** PyPI, npm, Docker Hub, the Go module proxy and the Terraform registry are unreachable. GitHub repositories can be cloned. That made possible what Phase 8.5 could not do:
- **Genuine production drivers:** psycopg **3.3.8**, psycopg_pool **3.3.0** and Gunicorn **26.0.0**, installed from their official source tags (commits `b9e2f353…`, `380ccceb…`, `5d819cf3…`).
- **cffi 2.1.1 built from source** for Python 3.11 and 3.12.
- **Linting tools built from source:** `hclfmt` (HCL v2.24.0) and `actionlint` 1.7.7.
- **The PyPA Advisory Database cloned** (commit `682f16b1`, 14 November 2026) for an offline vulnerability scan.

**How to read this report:**
- **Passed** means it ran here and succeeded.
- **Skipped** means it did not run, and is never counted as a pass.
- **Not run** means the tool was unavailable. A simulated success is never reported.

## 0. Final audit: what changed

**One genuine defect was found and fixed.** It could only be seen with the real driver.

| Defect | Effect | Fix | Proof |
|---|---|---|---|
| The psycopg_pool connection pool was never closed | Every process using PostgreSQL took **about 20 seconds to exit** (four pool threads waited 5 s each). Gunicorn's `graceful_timeout` is 20 s and workers are recycled every ~1,000 requests, so workers would have been killed mid-shutdown and every deployment or restart slowed | The pool is closed at interpreter exit (`db.py`, `atexit`) | `test_a_process_with_an_open_connection_pool_exits_promptly`: passes with the fix, **fails without it** (21.6 s). Gunicorn now stops in 0.3–0.4 s after SIGTERM |

**One documentation error was corrected:** an earlier commit message claimed the code was lint-clean when one file needed formatting; the final tree is clean.

**Reviewed with no defect found:**
- authentication and session layering;
- two-step and reset-link flows (including signed-cookie contents and the throttling paths);
- API-token revocation;
- owner scoping in every new query;
- the import script with schema-version-4 files;
- the production proxy, cookie and logging settings;
- the Terraform network and IAM layout;
- the workflows.

**Design points for the reviewer** (not defects; listed in the security review): API tokens are not bound to two-step, and `SECRET_KEY` also derives the TOTP key.

## Final results (from a clean extraction of the final source)

| Check | Result |
|---|---|
| pytest, SQLite, Python 3.13 | **1,476 collected: 1,434 passed, 42 skipped, 0 failed, 0 warnings** (warnings are errors) |
| pytest, PostgreSQL 16.15, **genuine psycopg 3.3.8 + psycopg_pool 3.3.0** | **1,441 passed, 35 skipped, 0 failed** |
| pytest, Python 3.11.18 / 3.12.3, SQLite | **1,219 passed, 45 skipped, 0 failed** on each. Browser tests deselected; `test_openapi.py` and `test_report_filters.py` not run, because jsonschema needs rpds-py, which is compiled for 3.13 only here |
| pytest, Python 3.11.18 / 3.12.3, PostgreSQL with psycopg | **1,226 passed, 38 skipped, 0 failed** on each (same exclusions) |
| Real Chart.js 4.5.1 in Chromium | **11 of 11 passed** (with screenshots on) |
| JavaScript | **12/12** |
| Ruff lint and format | **clean** (122 files) |
| Analytics audit, SQLite | **94,384 comparisons, 0 disagreements** |
| Analytics audit, PostgreSQL with psycopg | **94,384 comparisons, 0 disagreements** |
| Backup and restore, psycopg | **identical** (row counts, every trade value, every report) |
| Production HTTP check: `flask run` + SQLite | **31/31** |
| Production HTTP check: **Gunicorn 26** + SQLite | **31/31** |
| Production HTTP check: **Gunicorn 26** + PostgreSQL via psycopg | **31/31**, graceful stop in 0.4 s |
| Coverage (standard-library tracer, PostgreSQL run with psycopg) | statements **98.50%** (5,456 / 5,539); branch outcomes **89.34%** (1,726 / 1,932) |
| Dependency vulnerabilities (offline, PyPA Advisory Database) | **32 pinned packages, 0 known vulnerabilities.** The checker was proven to flag 28 advisories in deliberately old versions |
| Terraform HCL syntax and formatting (`hclfmt -check -require-no-change`) | **14 files, 0 problems.** Negative controls: a broken file and an unformatted file were both rejected |
| GitHub workflows (`actionlint` 1.7.7) | **0 findings.** A negative-control workflow was rejected. shellcheck was not available, so `run:` scripts were not shell-linted |
| Security mutants | 40/40 caught (Phase 8.5 run), plus the new shutdown regression proven to fail without its fix |

**Still not run** (tools unreachable):
- `docker build`: the base image and even `docker build --check` need Docker Hub;
- `docker compose up`;
- `terraform validate`/`plan`: the Terraform binary and the AWS provider come from blocked registries;
- `pip-audit` itself;
- GitHub Actions;
- anything on AWS;
- psycopg's **C** implementation (`psycopg[binary]`): the pure-Python implementation over the system libpq 16 was used.

The sections below are the Phase 8.5 report. Where they mention the test-only bridge, the final figures above supersede them.

## 1. Phase 8.5 summary (superseded by the final results above)

| Check | Result | Command |
|---|---|---|
| pytest, SQLite | **1,475 collected: 1,434 passed, 41 skipped, 0 failed** | `python -m pytest` |
| pytest, PostgreSQL 16.15 | **1,475 collected: 1,440 passed, 35 skipped, 0 failed** | `TRADELEDGER_TEST_DATABASE_URL=… TRADELEDGER_PG_DRIVER=libpq-bridge python -m pytest` |
| Browser tests (Playwright, Chromium) | **131 collected: 129 passed on each engine, 2 skipped** (screenshot capture, which needs `TRADELEDGER_SCREENSHOT_DIR`; both also passed when it was set) | part of the runs above |
| **Real Chart.js 4.5.1 tests** | **11 of 11 passed** (10 by default; the screenshot test with `TRADELEDGER_SCREENSHOT_DIR` set) | `python -m pytest -m chartjs_real` |
| JavaScript unit tests | **12/12** | `node --test tests/js/*.test.js` |
| Ruff lint and format | **clean** (122 files) | `ruff check .`, `ruff format --check .` |
| Independent analytics audit, SQLite | **94,384 comparisons, 0 disagreements** (900 filter combinations; 333 chart sets checked by the real `analytics.js` in Node; a decoy second account never leaked) | `scripts/qa_dashboard_agreement.py` |
| Independent analytics audit, PostgreSQL | **94,384 comparisons, 0 disagreements** (run on the working tree; the only later code change was log redaction) | `… --database-url postgresql://…/audit85 --pg-bridge` |
| Security mutation testing | **40 of 40 caught**: 35 on PostgreSQL and 5 on SQLite (the SQLite-only rules and three re-runs, §5) | QA tool `mutate85.py` (not shipped) |
| Statement coverage | SQLite 96.24%, PostgreSQL 96.53%, **combined 98.52% (5,448 / 5,530)** | standard-library tracer (§4) |
| Branch coverage | SQLite 86.22%, PostgreSQL 86.53%, **combined 89.59% (1,729 / 1,930 outcomes)** | standard-library tracer (§4) |
| Backup and restore (PostgreSQL) | **identical**: row counts in all 9 tables, every trade value, every report | `scripts/backup_restore_check.py --pg-bridge` |
| Site crawl (signed in) | **66 pages, 0 error statuses, 0 console errors.** No page scrolls sideways at 375, 768, 1024 or 1440 px, or at 200% zoom | QA crawl (§7) |
| Production settings over raw HTTP | **30 of 30 checks** | QA tool `prod_check.py` (§9) |
| Clean extraction of `tradeledger_final.zip` | see §9 | |

## 2. Why tests were skipped

**SQLite run (41 skipped):**
- **38 need PostgreSQL:**
  - `test_postgres.py`: 20;
  - `test_postgres_roles.py`: all 17;
  - the PostgreSQL migration-kill test: 1.
- **1 is a PostgreSQL-only rule** (`test_db.py`). SQLite still allows the NULL owners of unassigned Phase 7 rows.
- **2 are screenshot-capture tests,** which need `TRADELEDGER_SCREENSHOT_DIR`.

**PostgreSQL run (35 skipped):**
- **33 are SQLite-specific tests:**
  - the Phase 3→8 file upgrades;
  - legacy claims (15);
  - the SQLite crash-recovery test;
  - SQLite file tricks.
- **2 are screenshot-capture tests.**

**Phase 8 skipped the 10 real Chart.js tests.** In 8.5 they run by default, because the library now ships with the project (§3).

## 3. Chart.js: closing the gap

**What was tried:**
- **Downloads:** jsDelivr, unpkg, cdnjs, esm.sh, the npm registry, Maven Central (WebJars) and the GitHub code download. All were blocked by the environment's proxy (HTTP 403).
- **Local copies:** none existed on disk or in the npm cache.
- **Cloning the source:** the official Git repositories **could** be cloned:
  - `chartjs/chart.js`, tag `v4.5.1`, commit `9c5cf9fa…`;
  - its only runtime dependency, `kurkle/color`, tag `v0.3.2`, the version pinned in Chart.js's lock file.

**How the file was built:** `scripts/build_chartjs.mjs` reproduces Chart.js's own UMD build with esbuild 0.28.2. Chart.js's `rollup.config.js` uses the same entry point (`src/index.umd.ts`), the same ES2022 target and the same banner.
- **Reproducible:** building twice gives identical files.
- **The honest caveat:** the result is **not byte-identical** to the npm file. The minifier differs: 207,635 bytes against about 200 KB. Its SRI hash is therefore our own, recorded in `config.py` and `static/vendor/chartjs/BUILD.md`.
- **CI also runs the real-chart tests against the official npm file.** That step has not run, because no GitHub run has happened.

**Verified with the bundled file (Chromium):**
- all seven charts are created and their data matches the page's tables;
- tooltips show the exact text and the trade numbers;
- the filters change the charts;
- resizing redraws the charts;
- the CSP and SRI checks are enforced;
- no console errors;
- screenshots at 1440 and 375 px.

**New regression test:** the real charts exposed a genuine defect. The drawdown axis printed labels such as `-0.20000000000000002R`, because of floating-point noise in the tick values Chart.js generates. The fix (`formatAxisValue`) is covered by a JavaScript test and a real-browser test. The browser test was checked to **fail** with the old code.

**Not verified:** Chart.js's own test suite, which needs Karma and many npm packages.

## 4. Coverage

coverage.py could not be installed, so `coverage85.py` uses the standard library's `sys.monitoring` instead:
- **Statements:** line events.
- **Branches:** every conditional jump in the bytecode has two outcomes, and each must happen at least once. The jumps that every `with` block generates are excluded, to match coverage.py's behaviour.

The figures are close to coverage.py's, but **not identical** to its method. CI measures with coverage.py.

**Coverage-driven additions in 8.5:** tests for:
- email rules;
- a tampered pending two-step note;
- setting up or turning off two-step when it is in the wrong state;
- password throttling on the two-step page;
- reset links for an account that was deactivated later;
- two browser tabs using the same reset link;
- look-ups of users with untrusted values.

An unused function, `sessions.revoke_all`, was found and removed.

**Main remaining uncovered code:**
- the psycopg pool branch of `db.py`, which needs psycopg;
- `wsgi.py`, which is deliberately never imported by tests;
- a few defensive error branches.

## 5. Security mutation testing

A mutant is a deliberate security bug planted in a copy of the project. A good test suite fails when one is present. **All 40 were caught.** The first Phase 8.5 run missed 2 of them, the cookie-side session checks, and the fix is described after the list.

**From Phase 8 (20):**

*Data isolation*
- owner filter on lists;
- `get_trade` owner;
- `delete_trade` owner;
- `get_instrument` owner;
- the two-column owner foreign key.

*Sign-in and sessions*
- login not required;
- session version;
- absolute lifetime;
- session fixation;
- throttle;
- wrong password accepted;
- open redirect.

*API tokens*
- revoked or expired tokens accepted;
- API without a token;
- tokens kept on password change;
- token-form resubmission.

*Data handling and configuration*
- legacy claim conflicts;
- registration on in production;
- `X-Forwarded-Host` trusted;
- token redaction.

**New in Phase 8.5 (20):**

*Sessions*
- the server-side row ignored;
- revoked rows accepted;
- sign-out not revoking the row;
- revoke ignoring the owner;
- one account's session key accepted for another;
- password change keeping rows.

*Reset links and set-password*
- reset link reusable;
- reset link never expires;
- reset keeping API tokens;
- reset token not redacted in logs;
- `set-password` keeping API tokens.

*Two-step verification*
- two-step skipped at sign-in;
- TOTP replay;
- recovery-code reuse;
- the pending step never expiring;
- the TOTP secret not bound to its user;
- two-step turned off without a code.

*Production and database role*
- production accepting the test bridge;
- `grant-app-role` accepting an admin role;
- `grant-app-role` granting everything.

**Two findings from mutation testing:**
1. **Session cookie checks.** After the server-side session rows were added, removing the older cookie checks (session version, absolute age) no longer failed any test: the new layer covered for them. Two tests now break **only** the cookie-side condition while the server row stays valid, so each layer is proven to work on its own.
2. **Reset-token redaction.** The redaction rule for reset tokens had no direct test, because the application never logs the token. Direct cases were added to the redaction test.

## 6. Performance

These are medians of 5 runs on this machine. Each database holds **two** accounts with N trades each.

| Trades | SQLite page | SQLite API list | SQLite API summary | PostgreSQL page* | PostgreSQL API list* | Peak memory |
|---|---|---|---|---|---|---|
| 100 | 15.8 ms | 4.7 ms | 10.1 ms | 18.4 ms | 9.2 ms | 1.1 MB |
| 1,000 | 87.4 ms | 4.5 ms | 68.5 ms | 97.1 ms | 9.9 ms | 5.5 MB |
| 10,000 | 662 ms | 6.8 ms | 589 ms | 858 ms | 12.1 ms | 30.4 MB |

\*PostgreSQL figures go through the test-only bridge, whose row decoding is pure Python. Phase 8's `EXPLAIN ANALYZE` showed the server itself needs about 10 ms for the 10,000-trade query, so these figures **do not measure psycopg** or PostgreSQL's real speed.

**New per-request cost in 8.5:** a signed-in request now reads the user **and** its session row. On SQLite that takes 0.018 ms in total. The row's "last seen" time is written at most once a minute.

**Argon2id (OWASP settings):** 17–23 ms to hash or verify, once per sign-in.

## 7. Visual and accessibility review

**The crawl.** A crawl followed every internal link while signed in: 66 pages, including filtered views.

**At 375, 768, 1024 and 1440 px, and at 200% zoom (720 px at 2×), it checked:**
- the page never scrolls sideways;
- exactly one h1;
- no duplicate ids;
- every visible field has a label;
- images have alt text;
- no console errors;
- tap targets in the content area are at least 24 px.

**Findings fixed in 8.5:**
1. **Analytics tables at 1440 px.** The breakdown tables were wider than their cards, so the last column sat behind a scroll bar. Headings may now wrap, and the "Small sample" tag moves under the name.
2. **Small tap targets.** Links inside tables were 20 px tall, below the WCAG 2.2 target size. They are now at least 24 px.
3. **Session list at 1024 px.** The "Sign out" button sat behind a scroll bar; the dates now wrap. A regression test was added.
4. **"This browser" badge on phones.** It squeezed the browser name into a narrow column. It now stays on one line and moves below the name.
5. **Drawdown axis labels:** floating-point noise in the tick labels (§3).
6. **Fallback message.** The offline fallback still talked about "the CDN"; it now describes the self-hosted file.
7. **Account page.** "Last sign-in: n/a" now reads "None recorded yet".
8. **Development server log** (found by the HTTP check). `flask run` printed reset-link tokens in its access lines; they are now redacted. A regression test fails without the fix.

**Accepted by design:** between 1024 and 1279 px the eleven-column analytics tables still scroll inside their cards. A visible hint says so (Phase 7 design). Hidden `username` fields on the password forms are invisible helpers for password managers, as the crawl flagged; they are `hidden` and outside the accessibility tree.

**Not done:** testing with real screen readers (NVDA, VoiceOver), Firefox and Safari.

## 8. Not verified, or blocked

| Item | Why | Prepared |
|---|---|---|
| psycopg 3 and psycopg_pool | Not installable here; the PostgreSQL tests used the test-only libpq bridge against the same real server | CI `postgres` job |
| Docker image build and the Compose stack | Docker Hub is blocked (`docker pull python:3.13-slim` is refused); the static tests of the Dockerfile and Compose file passed | CI `docker` job |
| `terraform fmt` / `validate` | Terraform is not installed; static tests only | CI `terraform` job |
| GitHub Actions | Nothing has been pushed (as instructed) | `.github/workflows/` |
| pip-audit | Not installable, and the OSV API is blocked. **A manual advisory review was done instead:** the pinned Werkzeug 3.1.9, Jinja2 3.1.6, Flask 3.1.3 and gunicorn 26.0.0 are at or above the fixed versions of the advisories found by web search. This is weaker than pip-audit | CI `audit` job |
| Official npm Chart.js file | Download blocked; a source build was tested instead (§3) | CI browser step 4/4 |
| AWS, IAM, TLS, an RDS restore drill | Not authorised; nothing was created | `infra/`, `docs/PHASE_8_AWS_DEPLOYMENT.md` |
| Python 3.11 / 3.12 | Flask is not installed for those interpreters | CI matrix |
| Screen readers, Firefox, Safari | Not available | release checklist |

## 9. Clean extraction of `tradeledger_final.zip`

The ZIP was built from the final commit with `git archive`, which includes tracked files only. It was then unpacked into an empty folder, and every check was run **there**:
- **Comparison:** `diff -r` against the working tree, ignoring `.git` and `instance/`: **identical**.
- **Forbidden files:** a scan found none: no `.env`, no `*.db`, no Terraform state or `.terraform/`, no `*.tfvars`, no caches, no virtual environments, no files in `secrets/` other than `README.md`, and no `node_modules`.
- **Ruff:** clean (122 files). **Node:** 12/12.
- **pytest on SQLite:** 1,434 passed, 41 skipped, 0 failed.
- **pytest on PostgreSQL 16.15:** 1,440 passed, 35 skipped, 0 failed.
- **Coverage:** measured again here (the figures in §1).
- **Backup and restore check:** identical.
- **Real Chart.js tests:** 11/11 passed (with screenshots switched on).
- **Audit:** 94,384 comparisons, 0 disagreements on SQLite.
- **Production-configuration HTTP check: 30 of 30.** Raw HTTP from `http.client`, with cookies handled like a browser on localhost, against a fresh database:
  - health endpoints and the redirect for anonymous visitors;
  - registration is off;
  - the `__Host-` cookie name;
  - sign-in and sign-out;
  - **a cookie replayed after sign-out is refused**;
  - two-step sign-in with a code;
  - a reset link that works once;
  - the API without a token gets 401;
  - the security headers;
  - no password or token appears in the logs.

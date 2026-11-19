# Phase 8 guide: secure authentication, PostgreSQL, AWS-ready deployment

Each section explains the idea **in plain English first**, then **technically**, then **why** this choice was made and what the alternatives were.

**Honesty rule for this whole guide.** Each piece of work is one of three things:
- **tested here** (the tests ran in the build environment);
- **written and statically checked** (the code exists and its files were inspected, but it never ran);
- **not done**.

Nothing was deployed to AWS. No GitHub Actions run took place. The Docker image was never built, because no images could be downloaded in the build environment.

---

## 1. Phase 8 objectives

**Plain English.** Phase 7 was a polished journal for one person on one computer. Phase 8 makes it safe for several people to share a server, each seeing only their own trades. It also gets the app ready to run in the cloud.

**Technical.**
- Accounts with Argon2id passwords and server-checked sessions.
- Default-deny route protection and per-user ownership on every row.
- Bearer-token API authentication.
- A database adapter that runs the same code on SQLite and PostgreSQL.
- A safe Phase 7 → v3 migration and a SQLite → PostgreSQL import script.
- A Docker image and Compose stack, and AWS Terraform (ECS Fargate, RDS, ALB).
- CI jobs for PostgreSQL, Docker and Terraform, plus a manual, approval-gated deploy workflow.
- Structured logs and health checks.
- Backup and restore checks, security and performance testing.

**Why.** These are the gaps between a hobby project and something an employer recognises as production-shaped: identity, data isolation, a real database, repeatable deployment and evidence.

## 2. Phase 7 baseline

**Plain English.** Before changing anything, the Phase 7 ZIP was unpacked and its checks were re-run, to confirm the starting point really worked.

**Technical.**
- 1,038 tests collected: 1,028 passed, 10 skipped (real Chart.js unavailable).
- Ruff clean (81 files); Node 11/11.
- Gaps found:
  - no accounts;
  - production protected only by "disabled unless you set an unsafe switch";
  - SQLite only;
  - no container or cloud files.

**Why.** Measuring first means every Phase 8 claim has a "before" to compare with, and nothing that already worked was broken unnoticed.

## 3. Architecture changes

**Plain English.** Every request now passes three checkpoints:
1. Who are you?
2. Are you allowed here?
3. Is this form really from you?

Every database query also asks "and does this belong to you?".

**Technical.**

```
Browser ──cookie──► load_logged_in_user ─► require_login ─► csrf ─► view ─► repositories(user_id=…) ─► db adapter ─► SQLite | PostgreSQL
API client ─Bearer─► api.authenticate ────────────────────────────► view ─► repositories(user_id=…) ─► …
```

- **New modules:**
  - `db.py` (adapter), `passwords.py`, `legacy.py`, `observability.py`, `wsgi.py`;
  - repositories: `users`, `api_tokens`, `throttle`;
  - web: `auth`, `account`.
- **Removed:** `web/guard.py` (the Phase 7 "journal disabled" switch).

**Why.** Putting the owner check in the repository layer (rather than in each view) means a forgotten check is a TypeError at the call, not a silent leak. **Alternative:** PostgreSQL row-level security. It is stronger in the database, but it does not exist on SQLite and it complicates connection pooling, so it is a possible later step rather than the only line of defence.

## 4. Authentication design

**Plain English.** Sign-in is checked by the server, not just by hiding links. If you are not signed in, every page sends you to the sign-in form. After signing in, you return to the page you wanted, but only if it is a page on this site.

**Technical.**
- **Default deny:** `require_login` runs before every request. Only `PUBLIC_ENDPOINTS` are open (sign-in, register, static files, health, about). A test walks every registered route and fails if a new one is public by accident.
- **Sign-in:**
  - NFKC-normalised, lower-cased email;
  - one generic message ("Email or password is incorrect.") for every failure, including disabled accounts;
  - unknown emails still verify against a dummy Argon2id hash, so response timing does not reveal whether an address exists.
- **Throttling:**
  - limits: 5 failures per account and 20 per IP address per 15 minutes; registration 5 per hour per address;
  - stored in the `login_attempts` table (as HMAC digests, so the table never holds emails or IPs in readable form);
  - because it lives in the database, it holds across Gunicorn workers and containers.
- **`safe_next`** accepts only local paths. It refuses `//evil`, backslashes, schemes and control characters.
- **Sign-out** is a POST with a CSRF token, so another site cannot sign you out with an image tag.
- **Registration** is on in development and **off in production**. Operators create accounts with `flask create-user`.

**Why.** The two classic sign-in leaks are "which emails exist" and "unlimited guessing"; both are handled. **Not built:** password reset and email verification. They need a real email service, and a form that pretends to send email would be fake security.

## 5. Password security

**Plain English.** Passwords are never stored. Only a slow, salted fingerprint is kept, and it is designed so that guessing billions of passwords is too expensive even if the database is stolen.

**Technical.**
- **Algorithm:** Argon2id from `cryptography` 50.0.1, stored in the standard PHC string format (`$argon2id$v=19$m=19456,t=2,p=1$salt$hash`).
- **Parameters:** production uses the OWASP minimum, m = 19 MiB, t = 2, p = 1. Tests use m = 1 MiB, t = 1, so that 1,291 tests stay fast; a test checks that production really uses the OWASP values.
- **Unicode:** passwords are NFKC-normalised, so the same password typed on two keyboards matches.
- **Rules:** 12 to 128 characters, and a short list of very common passwords is refused.
- **Re-hashing:** `needs_rehash` upgrades old hashes at the next successful sign-in.
- **Measured cost:** about 19 ms to hash and 19 ms to verify, at the production settings, on the build machine.

**Why Argon2id.** It is OWASP's first choice because it is memory-hard, which slows GPU attacks. `cryptography` was already trusted and pinned, so no new hashing library was added. **Alternatives:** bcrypt (fine, but caps input at 72 bytes); `argon2-cffi` (equally good, but one more dependency); Werkzeug's PBKDF2 (weaker against GPUs).

## 6. Session management

**Plain English.** After you sign in, your browser holds a tamper-proof ticket. The ticket expires after 8 hours, or after 2 hours of doing nothing. Changing your password tears up every ticket you have issued.

**Technical.**
- **Cookie contents:** a signed Flask cookie holding `uid`, `sv` (session version), `auth_at` and `seen_at`.
- **Lifetime limits:** checked on the server at every request: 8 h absolute, 2 h idle.
- **At sign-in:** the session is cleared first (stops session fixation).
- **Session version:** `users.session_version` increases on a password change, deactivation, `set-password` or "sign out everywhere else". Any cookie with an old `sv` is rejected.
- **Cookie flags:** HttpOnly and SameSite=Lax. In production also Secure, with the name `__Host-tradeledger_session` (the browser then refuses it over HTTP or with a Domain attribute).

**Why.** Signed cookies need no session table, so they cost nothing per request and work across containers. **Trade-off:** you cannot end one specific device's session, only all of them; this is listed as a residual risk. **Alternative:** server-side sessions in a database or Redis, which give per-device revocation for an extra lookup and more infrastructure.

## 7. Authorisation

**Plain English.** Authentication asks *who you are*. Authorisation asks *what you may touch*. In TradeLedger the rule is simple: you may touch only your own records.

**Technical.**
- **Required owner:** every repository function takes a keyword-only `user_id`. `require_user_id` raises TypeError for anything that is not a positive int, including `None`, `True` and strings.
- **Other users' records:** a foreign id gets the same 404 as a missing id, and the two responses are byte-for-byte identical, so ids cannot be probed.
- **Deactivated users:** they are refused at the next request (sign-in check and API token check).

**Why 404 and not 403.** A 403 would confirm that the id exists. **There are no roles,** such as admin: they were not needed, and an unnecessary admin role is a common source of privilege bugs. Operator tasks run through the CLI on the server instead.

## 8. Multi-user isolation

**Plain English.** Two people share the server. Tests act as the second person trying every trick to see the first person's trades: guessing numbers, filters, searches and the API. None of it works.

**Technical.**
- `user_id` is on `instruments` and `trades`.
- The trades → instruments link is a **two-column foreign key** `(instrument_id, user_id) → instruments(id, user_id)`, so the database itself refuses a trade that points at another user's instrument.
- Unique symbols are per user: `UNIQUE(user_id, symbol)`.
- Indexes start with `user_id`.
- **Tests:** 36 in `test_isolation.py`, run on SQLite and PostgreSQL. In addition:
  - 5 mutation tests (the filter was removed on purpose; tests had to fail, and did);
  - the independent audit seeds a decoy account and checks that it never appears (94,384 comparisons, 0 disagreements).

**Why defence in depth.** If one layer has a bug (a query that forgets the filter), the next one (the foreign key) still stops cross-user links.

## 9. REST API authentication

**Plain English.** Scripts use a long secret key instead of your password. You make one on the Account page. It is shown once, and you can cancel it at any time.

**Technical.**
- **Format:** `tl_` + 32 random bytes (base64url), sent as `Authorization: Bearer …`.
- **Storage:** only the SHA-256 and a 10-character prefix (so you can recognise the token in a list).
- **Limits:**
  - expiry 1–365 days (default 90);
  - at most 10 active per user;
  - revoked on a password change.
- **Cookies are ignored by the API,** so a malicious website cannot use your browser session against it (no CSRF on JSON).
- **Failures:** 401 with `WWW-Authenticate: Bearer`, and the same message whatever is wrong.
- **Single-use form:** the token form carries a one-time value. This came from a real bug the browser test found: refreshing the page used to create a second token.

**Why not JWT.** JWTs cannot be revoked without a lookup anyway. They add algorithm-confusion risk, and they are large. A random token plus a hash lookup (about 0.02 ms) is simpler and revocable. SHA-256 rather than Argon2 is correct here: the token has 256 bits of randomness, so slow hashing adds nothing.

## 10. PostgreSQL architecture

**Plain English.** SQLite is a single file, perfect for one person. PostgreSQL is a database server that many app copies can use at once, with proper backups. TradeLedger now runs on either, chosen by configuration.

**Technical.**
- **Adapter:** `tradeledger/db.py` gives one interface over both engines:
  - `?` placeholders are converted to `%s`;
  - rows work by name and by position;
  - explicit `BEGIN`/`COMMIT`, with nested `with conn:` blocks becoming SAVEPOINTs;
  - `IntegrityError(kind, constraint)` and `DataError` are raised the same way on both.
- **Money:** NUMERIC columns, bound as `Decimal` (`conn.numeric()`), so no value ever passes through a float.
- **Production driver:** **psycopg 3** with **psycopg_pool**. The pool size is set by `TRADELEDGER_DB_POOL_MIN`/`MAX`, and connections are checked before reuse.
- **Choosing the database:** `DATABASE_URL` or `DB_*` (with `DB_PASSWORD_FILE`) select PostgreSQL; otherwise SQLite. Log lines show the URL with the password removed (`redact_url`).
- **Edits:** a trade edit locks its row with `SELECT … FOR UPDATE` on PostgreSQL, so two simultaneous edits are serialised.

**Test-only bridge.** psycopg could not be installed in the build environment. The PostgreSQL tests therefore ran through `tests/pg_libpq_bridge.py`, a small ctypes wrapper around the real libpq. It lives in `tests/`, is never imported by the app, and is registered only by the test fixtures or by the hidden `--pg-bridge` flag of the scripts. Production refuses to start without psycopg_pool unless that bridge was registered. CI runs the same tests with real psycopg.

**Why an adapter and not an ORM.** The project's SQL was already hand-written and tested. SQLAlchemy would mean rewriting every query, with a risk to the financial results. **Trade-off:** the adapter is code the project must maintain.

## 11. Database schema

**Plain English.** There are five main tables: users, their API keys, sign-in attempts, instruments and trades. The database itself enforces the important rules, such as "a stop-loss must be on the correct side of the entry".

**Technical.** Schema version 3 (`schema_postgres.sql` and the SQLite migration):

| Table | Key points |
|---|---|
| `users` | unique email (case-folded), Argon2id hash, `session_version`, `is_active`, timestamps |
| `api_tokens` | `token_hash` unique with length 64, prefix, `expires_at`, `revoked_at`, FK to users ON DELETE CASCADE |
| `login_attempts` | `scope`, `key_digest`, `attempted_at`; index for the lookup |
| `instruments` | `user_id`; `UNIQUE(user_id, symbol)`; `UNIQUE(id, user_id)` (the target of the two-column FK) |
| `trades` | `user_id`; FK `(instrument_id, user_id)`; CHECKs `status_matches_exit`, `stop_on_correct_side`, `target_on_correct_side`, `closed_at_rules`; indexes `(user_id, trade_date, id)`, `(user_id, closed_at)`, `(user_id, status)` |
| `schema_migrations` | the PostgreSQL version record (SQLite uses `PRAGMA user_version`) |

**Why named constraints.** The adapter maps each constraint name to a friendly form message. That is how "stop must be below entry for a long trade" reaches the user from either engine.

## 12. Migrations

**Plain English.** Upgrading never throws data away. A copy of the database is made first, then the change happens all at once or not at all.

**Technical.**
- **SQLite v2 → v3** rebuilds the tables, because SQLite cannot add a foreign key to an existing table. The sequence is:
  1. backup;
  2. foreign keys off (before `BEGIN`, which is the only place SQLite allows it);
  3. one transaction: create the new tables, copy the rows, drop the old ones, rename;
  4. `PRAGMA foreign_key_check` is run from Python and stops the upgrade if any link is broken;
  5. `user_version = 3`, then `COMMIT`.
- **PostgreSQL** starts at v3 from `schema_postgres.sql`, inside a transaction protected by an **advisory lock**, so two containers starting together cannot both migrate.
- **On AWS,** the deploy workflow snapshots the database and then runs `migrate-db --backed-up` as a one-off task before the new version receives traffic.
- **Old records:** they get `user_id NULL`, which makes them **invisible to every account**. `claim-legacy-data --email … [--apply]` assigns them, as a dry run by default; it stops if the target account already has a clashing symbol.

**Why not auto-assign** to the first account registered: on a public server, a stranger could register first and receive your journal.

## 13. SQLite data import

**Plain English.** A script copies an existing TradeLedger file into an empty PostgreSQL database, checks that every number arrived exactly, and changes nothing in the original file.

**Technical.** `scripts/migrate_sqlite_to_postgres.py`:
- **Source:** opened read-only. A Phase 7 file contributes every row; a Phase 8 file contributes its unassigned rows, or one account's rows with `--source-email`.
- **Owner:** `--owner-email` must name an existing PostgreSQL account. Nothing is ever given to "the first user".
- **Target:** that account's journal must be empty. The script never merges, overwrites or deletes.
- **Validation:** every row passes the application's own validation before anything is written; a bad row stops the import, naming its id.
- **Copy:** one transaction. PostgreSQL assigns new ids, and the audit file lists the old → new mapping.
- **Verification:** every row is re-read and compared (counts, every decimal exactly, every text field). Any difference rolls the whole import back.
- **Modes:** a dry run is the default; `--apply` writes; `--report` saves a JSON audit.
- **Privacy:** passwords and full connection strings are never printed.
- **Tested** by 7 tests in `test_postgres.py`, including report parity, refusals and "other accounts are left alone".

## 14. Docker

**Plain English.** A Docker image is a sealed box holding the app and exactly what it needs, so it runs the same on a laptop and in AWS.

**Technical.** The multi-stage `Dockerfile`:
- **Builder stage:** installs the pinned packages into a virtual environment.
- **Final stage:** `python:3.13-slim` with only that environment and the app code, which comes in through an allow-list `.dockerignore`, so no `.env`, databases or tests get in.
- **Runtime:** non-root user 10001; Gunicorn (`gthread`, 2 workers × 4 threads, keep-alive 75 s); `HEALTHCHECK` on `/health/live`; an entrypoint that ends with `exec "$@"`, so signals reach Gunicorn.

**Status:** written and statically checked (26 tests in `test_deployment_files.py`), **not built**. Docker Hub was unreachable, so even the base image could not be pulled. The CI `docker` job builds it and checks the user and contents.

## 15. Docker Compose

**Plain English.** Compose starts the app and a PostgreSQL database together on your computer, wired up safely.

**Technical.** `compose.yaml`:
- **Database:** on an `internal` network with **no published port**, and a healthcheck.
- **App:** published on `127.0.0.1:8000` only, after the database is healthy.
- **Hardening:** `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, a tmpfs at `/tmp`.
- **Secrets:** `secrets/secret_key.txt` and `secrets/db_password.txt`, mounted as files and read through `SECRET_KEY_FILE` and `DB_PASSWORD_FILE`. They never appear in `docker inspect`.

**Status:** `docker compose config` parsed it. The HTTP steps of `scripts/compose_smoke_test.sh` (health, sign-in, CSRF, create a trade, isolation, API) passed against a local production-configuration server through a stand-in `docker` command. **The real stack has not run.**

## 16. AWS architecture

**Plain English.**
- A load balancer is the only public entrance, and it holds the HTTPS certificate.
- Containers run on Fargate (AWS manages the servers).
- The database is private.
- Secrets live in a vault.
- Logs and alarms go to CloudWatch.

**Technical.**
- **Network:** a VPC with 2 public and 2 private subnets in eu-west-2, and **no NAT gateway**.
- **Load balancer:** an ALB with a TLS 1.2+ policy and HTTP → HTTPS redirect. Only the configured host name is forwarded; anything else gets 404.
- **App:** an ECS Fargate service (0.25 vCPU, 0.5 GB) with a deployment circuit breaker and rollback. Tasks have a public IP but accept traffic only from the ALB's security group.
- **Database:** RDS PostgreSQL 16 (db.t4g.micro):
  - private, encrypted, `force_ssl`;
  - an RDS-managed master password;
  - 7-day backups, deletion protection.
- **Other services:**
  - ECR with immutable tags and scan-on-push;
  - Secrets Manager;
  - CloudWatch logs and 4 alarms.

**Why ECS Fargate.** It is a common industry pattern and needs no server patching. **Alternatives:**
- **App Runner:** closed to new customers from 30 April 2026, according to AWS's notice.
- **Lightsail or one EC2 instance:** cheaper, but you patch the operating system.
- **Lambda:** awkward for a stateful Flask app with a connection pool.

## 17. Infrastructure as Code

**Plain English.** The whole AWS setup is written as Terraform files, so it can be reviewed, versioned, recreated or deleted with commands instead of clicks.

**Technical.**
- **Files:** `infra/` holds `network`, `security_groups`, `rds`, `secrets`, `ecr`, `iam`, `ecs`, `alb`, `monitoring`, `github_oidc`, `variables` and `outputs`.
- **State:** an S3 backend (encrypted, versioned, native locking with `use_lockfile`).
- **Variables** are validated (region, domain, sizes).

**Status:** **written, not run.** Terraform is not installed in the build environment. The static tests check that every resource reference resolves and that the security properties are present. The CI `terraform` job runs `fmt -check`, `init -backend=false` and `validate`, with no credentials and no `plan`/`apply`.

**Why Terraform.** It is the most widely used IaC tool in job adverts and works across providers. **Alternatives:** CloudFormation or CDK (AWS-native); OpenTofu (an open-source fork with the same syntax).

## 18. Cloud security

**Plain English.** Every part gets the least access it needs, nothing secret is stored in code, and the database cannot be reached from the internet at all.

**Technical.**
- **Security groups:**
  - internet → ALB on 443/80;
  - ALB → app on 8000;
  - app → RDS on 5432;
  - nothing else.
- **IAM roles:**
  - **execution role:** ECR pull, logs, and reading exactly 2 secrets;
  - **task role:** no permissions;
  - **GitHub deploy role:** OIDC, trusted only for `repo:OWNER/REPO:environment:production`, scoped to this cluster, service, repository and roles.
- **Secrets:** RDS manages the database password itself. The Flask key is set with `aws secretsmanager put-secret-value`, outside Terraform, so **no secret value is ever in Terraform state**.
- **Encryption in transit:** HTTPS at the ALB, `DB_SSLMODE=require` and `rds.force_ssl=1`.
- **Proxy headers:** `TRADELEDGER_PROXY_HOPS=1`, so only the ALB's `X-Forwarded-For`/`-Proto` hop is trusted.

**Open item:** the app uses the RDS master user. A dedicated role with only the rights it needs is recommended (security review P8-11).

## 19. CI/CD

**Plain English.** Every push runs all the checks automatically. Deploying is a separate button that a person must press and a reviewer must approve, and it never happens by itself.

**Technical.**
- **`ci.yml`** jobs:
  - `python` (3.11 and 3.13, coverage);
  - `postgres` (a PostgreSQL 16 service, real psycopg, full suite);
  - `javascript`, `audit` (pip-audit), `browser` (Playwright with **real** Chart.js);
  - `docker` (hadolint, build, user and contents checks, Compose smoke test);
  - `terraform`.
- **`deploy.yml`:**
  - triggered only by `workflow_dispatch`, and only if `vars.DEPLOY_ENABLED == 'true'`;
  - uses the `production` environment, which requires reviewers;
  - authenticates through OIDC (`id-token: write`), so no AWS keys are stored;
  - steps: build and push (SHA tag) → RDS snapshot → register task definition → migration task → service update → wait until stable.

**Status:** both files parse as YAML and are statically tested. **Neither has run on GitHub.**

## 20. Logging and monitoring

**Plain English.** The app writes one tidy line per event, never including passwords or keys, and two web addresses tell AWS whether the app is alive and ready.

**Technical.**
- **Logs:** JSON lines in production (`LOG_FORMAT=json`). Each line carries a request id, which is also returned as `X-Request-ID`.
- **Access log:** method, path **without the query string**, status and duration.
- **`RedactingFilter`** masks Bearer values, `tl_…` tokens, password fields and database URLs.
- **Health checks:**
  - `/health/live` answers 200 if the process runs;
  - `/health/ready` also pings the database, and answers 503 "unavailable" without details (the details go to the log).
- **Events:** sign-in success and failure are logged with the user id, never the email or password.
- **CloudWatch:** saved queries and alarms (5xx, unhealthy hosts, RDS CPU, RDS free storage).

**Why.** Logs that might hold secrets become a breach in their own right. A tested redaction filter is cheap insurance.

## 21. Backups and recovery

**Plain English.** A backup only counts if you have proved you can restore it. That was done here with a real PostgreSQL server.

**Technical.**
- **SQLite:** `migrate-db` makes a timestamped copy before changing anything.
- **PostgreSQL:** `scripts/backup_restore_check.py` runs `pg_dump -Fc`, restores into a new database, and compares row counts, every trade value and the full performance report. **Result: identical** (dump 20,691 bytes, synthetic data).
- **On AWS (designed):** RDS automated backups (7 days, point-in-time recovery), a snapshot before every deploy, deletion protection and a final snapshot.
- **Not done:** an RDS restore drill.

## 22. Automated testing

**Plain English.** Over twelve hundred automatic checks run in about a minute, on both databases. Some act as an attacker; some deliberately break the code to make sure the tests notice.

**Technical.**
- **SQLite:** 1,291 collected; **1,259 passed, 32 skipped, 0 failed.**
- **PostgreSQL 16.15:** **1,248 passed, 43 skipped, 0 failed.**
- **Other checks:**
  - Ruff clean; Node 11/11;
  - the independent audit: 94,384 comparisons with 0 disagreements on both engines;
  - **20/20 security mutants caught**;
  - approximate statement coverage of 98.15% combined.
- **New Phase 8 test files:** 244 tests across 9 files (see the QA report for each file's purpose).
- **Changed Phase 1–7 tests:** each change is explained in the QA report §4. **None was deleted or weakened.**

## 23. Browser verification

**Plain English.** A real Chrome browser was driven by code to sign in, register, change things and check what a person would see, on phone and desktop sizes and by keyboard alone.

**Technical.** Playwright with Chromium; 110 browser tests in total (99 passed on each engine, 11 skipped). The 15 new ones in `test_browser_auth.py` cover:
- sign-in returning to the requested page;
- the focused error summary;
- `autocomplete` attributes;
- register → sign out → sign in;
- a foreign trade URL giving 404;
- token create and revoke;
- labels and a single h1;
- 375/768/1024/1440 widths and keyboard-only use.

Screenshots are in `docs/screenshots/phase8_*`. **Not done:** Firefox, Safari and screen readers.

## 24. Chart.js verification

**Plain English.** The dashboard charts use the Chart.js library from the internet. That library could not be downloaded here, so the tests that need it are skipped, honestly, rather than faked.

**Technical.**
- **Downloads tried:** jsDelivr, npm and unpkg were all unreachable (re-checked in Phase 8).
- **10 `chartjs_real` tests are skipped.** The page's no-library fallback (tables) is tested.
- **The chart *data*** is verified independently: the real `analytics.js` runs in Node inside the audit, on 333 chart sets.
- **CI's `browser` job** downloads Chart.js 4.5.1 with `npm pack` and sets `TRADELEDGER_REQUIRE_REAL_CHARTJS=1`, so those tests fail rather than skip there.

## 25. Performance

**Plain English.** At 10,000 trades per user, the dashboard takes about half a second to build on SQLite. Signing in adds about 19 ms, once. Each later request adds almost nothing.

**Technical.** The benchmark uses 2 accounts × N trades and the median of 5 runs. Dashboard page times:

| Trades | SQLite | PostgreSQL (bridge) |
|---|---|---|
| 100 | 13 ms | 19 ms |
| 1,000 | 73 ms | 104 ms |
| 10,000 | 572 ms | 757 ms |

- **The PostgreSQL figures are misleading on their own.** At 10k, `EXPLAIN ANALYZE` shows 10.4 ms of server time; about 245 ms is the test bridge decoding rows in pure Python. psycopg was **not measured**.
- **API list (100 rows):** 3.6–5.6 ms on SQLite.
- **Peak memory:** 30 MB at 10k.
- **Not done:** a load test with concurrent users.

## 26. Costs

**Plain English.** Running the AWS design all month would cost roughly **USD 55–70**. The biggest parts are the load balancer and public IP addresses, which charge even with no visitors.

**Technical.** See `PHASE_8_COST_ESTIMATE.md`. Main lines:
- ALB $16–20 plus LCU;
- public IPv4 $11 ($0.005/h each, 3 addresses);
- RDS db.t4g.micro about $13;
- Fargate $10–11.

London rates for Fargate and gp3 storage were **not confirmed**. Cheaper alternatives are listed there with their trade-offs. **No money has been spent.**

## 27. Local installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env            # set SECRET_KEY (python -c "import secrets; print(secrets.token_hex(32))")
flask --app tradeledger init-db
flask --app tradeledger create-user --email you@example.com
flask --app tradeledger seed-db --email you@example.com     # optional synthetic data
flask --app tradeledger run --debug                         # http://127.0.0.1:5000
python -m pytest                                            # all tests (SQLite)
```

- **PostgreSQL tests:** set `TRADELEDGER_TEST_DATABASE_URL=postgresql://…` to a database you can throw away; each test uses its own schema.
- **Compose stack:** see the README.
- **Upgrading a Phase 7 journal:** `migrate-db`, then `create-user`, then `claim-legacy-data` (a dry run first, then `--apply`).

## 28. Cloud deployment instructions

See `PHASE_8_AWS_DEPLOYMENT.md`. In short:
1. Budget alert, state bucket, certificate.
2. `terraform plan`, then **💷 `apply`**.
3. Set the Flask secret.
4. Add the DNS record.
5. Add the GitHub environment variables.
6. **💷** Run "Deploy (manual)" and approve it.
7. Create the first user with a one-off task.

**None of this has been done.** Stop the bills with `terraform destroy`: setting `desired_count = 0` stops only the Fargate charge.

## 29. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Every page goes to `/login` | Working as designed | Create an account with `create-user` |
| "Email or password is incorrect" though the password is right | The account is throttled (5 failures in 15 min) or deactivated | Wait 15 minutes; `activate-user` |
| `set-password` signs me out | Intended: it ends all sessions and tokens | Sign in again |
| Production will not start: "SECRET_KEY is too weak" | Placeholder or short key | Generate 64 hex characters |
| Production will not start: psycopg_pool missing | Requirements not installed | `pip install -r requirements.txt -c constraints.txt` |
| Old trades missing after the upgrade | They belong to nobody until claimed | `claim-legacy-data --email … --apply` |
| API answers 401 | Token missing, expired, revoked or rotated by a password change | Make a new token on the Account page |
| Session cookie not sent on `http://localhost` with production config in Firefox or Safari | The `__Host-` prefix needs Secure | Use development config locally, or HTTPS |
| Throttling triggers for everyone behind the ALB | `TRADELEDGER_PROXY_HOPS` not set | Set it to 1 |
| `/health/ready` 503 | Database unreachable | Read the `health.ready.failed` log line |

## 30. Portfolio presentation

**GitHub description (about 300 characters):**
> Multi-user Flask trading journal with Argon2id auth, per-user data isolation, and a Decimal-exact analytics engine. Runs on SQLite or PostgreSQL. 1,250+ tests (adversarial, mutation, Playwright). Docker, Terraform for AWS ECS/RDS, and CI/CD with OIDC are included as deployment-ready code.

**CV bullets** (accurate: deployment-ready code, not a claim of AWS production experience):
- **Python and Flask:** built a multi-user Flask application (blueprints, application factory, server-rendered UI and JSON API) with Decimal-exact financial calculations, checked by an independent audit of 94,000+ comparisons with zero disagreements.
- **SQL and PostgreSQL:** designed a portable SQLite/PostgreSQL data layer with NUMERIC money columns, named CHECK and composite foreign-key constraints, and transactional migrations protected by an advisory lock; wrote a verified SQLite → PostgreSQL import and a pg_dump/pg_restore check.
- **Authentication and authorisation:** implemented Argon2id password hashing (OWASP parameters), server-enforced session lifetimes with revocation, database-backed sign-in throttling, CSRF protection and hashed, revocable API tokens; proved per-user isolation with 36 adversarial tests and 20 security mutation tests.
- **Docker:** wrote a multi-stage, non-root Docker image served by Gunicorn, and a hardened Compose stack with a private database network and file-based secrets.
- **AWS infrastructure:** authored deployment-ready Terraform for ECS Fargate, RDS PostgreSQL, ALB with TLS, Secrets Manager, CloudWatch alarms and least-privilege IAM, including a cost estimate. Not yet deployed.
- **CI/CD:** designed GitHub Actions pipelines covering two Python versions, PostgreSQL, Playwright, pip-audit, Docker and Terraform validation, plus a manual, reviewer-approved deploy workflow using OIDC instead of stored keys.
- **Automated testing:** maintained 1,290+ pytest tests across two database engines (about 98% statement coverage), including browser tests at four screen widths.
- **Security and reliability:** structured JSON logging with secret redaction, request ids, liveness and readiness checks, and production start-up checks that refuse debug mode and weak keys.

**LinkedIn summary:**
> Self-taught developer building TradeLedger, a multi-user trading journal in Python/Flask. It has secure authentication, per-user data isolation, PostgreSQL support and 1,250+ automated tests. I've written its Docker and AWS (ECS/RDS) infrastructure as code with CI/CD, and I'm focused on Python, QA automation and cloud engineering roles.

**30-second introduction.**
> TradeLedger is a trading journal I built in Python and Flask. You record trades and it calculates exact performance statistics. In the latest phase I made it multi-user: secure sign-in, strict separation of each person's data, PostgreSQL as well as SQLite, and Docker and AWS infrastructure written as code. Over twelve hundred automated tests back it, including tests that deliberately try to break into another user's data.

**2-minute explanation.** Start with the problem: traders need accurate statistics, and floats give wrong answers for money, so everything is Decimal and an independent audit checks the numbers. Then the Phase 8 step: turning a single-user app into a multi-user one.
- **Accounts:** Argon2id passwords and sessions with an 8-hour lifetime, checked on the server.
- **Ownership:** every query requires an owner id, and a two-column foreign key backs it up.
- **Evidence:** tests act as a second user trying to read the first user's data, and mutation testing proves those tests would catch a regression.
- **Databases:** the same code runs on SQLite or PostgreSQL through a small adapter.
- **Deployment:** Docker, Terraform for ECS Fargate and RDS, and a GitHub Actions deploy that needs human approval.

Close by being clear that the infrastructure is deployment-ready but has not been deployed, and explain why: cost, and the approval rule.

**5-minute technical walkthrough.**
1. **Request path** (section 3 diagram): hooks in a fixed order (load user → require login → CSRF), default deny, and the route-walking test.
2. **Passwords and sessions:** the PHC string, the dummy hash for unknown emails, the `session_version` revocation and the `__Host-` cookie.
3. **Isolation:** `require_user_id`, 404 instead of 403, the composite FK, and the decoy account in the audit.
4. **Database:** adapter design, SAVEPOINTs, NUMERIC/Decimal, `FOR UPDATE`, the advisory-locked migration, and why the legacy rows are not auto-assigned.
5. **Deployment:** the image, then Compose, then Terraform. No NAT, so tasks have public IPs locked to the ALB's security group. The RDS-managed secret, the OIDC trust scoped to an environment, and the snapshot → migrate → deploy order.
6. **Evidence and honesty:** the test counts, mutation testing, the performance analysis (bridge versus server time), and what is unverified and why.

## 31. Interview questions

**Flask architecture.** *How is the app structured?*
- An application factory (`create_app`) loads a config class.
- It registers blueprints (web, api) and hooks for the login and CSRF checks.
- Each request opens one database connection through `g`.
- Views are thin; the work is in repositories and the performance engine, which makes them testable without HTTP.

**Password hashing.** *Why not SHA-256 for passwords?* SHA-256 is designed to be fast, so a stolen database can be guessed at billions per second. Argon2id is deliberately slow and memory-hungry (19 MiB per attempt here), and a salt stops precomputed tables. *Why is SHA-256 fine for API tokens?* They are 256-bit random values, not human-chosen, so there is nothing to guess.

**Sessions and cookies.** *How do you sign everyone out of a signed-cookie session?* By storing a `session_version` in the user row and the cookie, and rejecting a mismatch. *What does `__Host-` do?* The browser accepts that cookie only if it is Secure, has no Domain and has Path=/, which stops subdomain or HTTP cookie injection.

**CSRF and XSS.** *How do they differ?*
- **CSRF** tricks your browser into sending a request. It is stopped by per-session tokens on forms, SameSite cookies and an API that ignores cookies.
- **XSS** runs attacker script on the page. It is stopped by Jinja auto-escaping, a strict CSP without inline scripts, and never building HTML from user input.

*Why does sign-in need CSRF?* Login CSRF could sign you into the attacker's account, so that you record trades there.

**Authentication vs authorisation.** Authentication is who you are (sign-in, token); authorisation is what you may access (your rows only). Example: a signed-in user requesting `/trades/42` that belongs to someone else is authenticated, but not authorised. They get a 404.

**SQL joins and constraints.** *How do you stop a trade pointing at another user's instrument?* With a foreign key on `(instrument_id, user_id)` referencing `instruments(id, user_id)`, which needs `UNIQUE(id, user_id)` on instruments. *A join you use?* Trades `JOIN` instruments on both columns, filtered `WHERE t.user_id = ?`.

**PostgreSQL transactions.** *What happens on an error halfway through an edit?* Everything since `BEGIN` is rolled back. Nested blocks use SAVEPOINTs, so an inner failure can be undone alone. `SELECT … FOR UPDATE` serialises two edits of the same trade.

**Database migrations.** *How did you change a SQLite table that has data?*
1. Make a backup.
2. Turn foreign keys off.
3. In one transaction: create the new table, copy the rows, drop the old table, rename the new one.
4. Run `foreign_key_check`, set the version and commit.

The tests compare the data byte for byte. *How do you stop two containers migrating at once?* A PostgreSQL advisory lock.

**Docker.** *Image vs container?* An image is the read-only template; a container is a running instance of it. *Why multi-stage?* Build tools stay out of the final image, which makes it smaller and leaves less to attack. *Why non-root?* A compromised process then has fewer rights.

**AWS ECS and RDS.** *Why Fargate?* There are no servers to patch, and it bills per second. *Why are the tasks in public subnets?* To avoid a NAT gateway (about $33 a month); their security group still accepts only the ALB. *How do you protect RDS?* Private subnets, a security group that admits only the app, forced TLS, encryption, and a managed password.

**IAM.** *Least privilege example?*
- The task role has **no** permissions.
- The execution role can read exactly two secrets.
- The deploy role is trusted only for this repository's `production` environment through OIDC, and is limited to one cluster and service.

**Secrets management.** *Where does the Flask key live?*
- **On AWS:** in Secrets Manager. It is set outside Terraform, so it is never in state, and ECS injects it at start-up.
- **Locally:** a file mounted by Compose and read through `SECRET_KEY_FILE`.
- **In neither case** is it in Git, the image or the logs.

**HTTPS.** *Where is TLS terminated?* At the ALB, with an ACM certificate and a TLS 1.2+ policy; HTTP is redirected. The app trusts `X-Forwarded-Proto` from exactly one proxy hop and sends HSTS. The database connection is TLS as well.

**CI/CD.** *How do you stop accidental deploys?* Deploying is a manual trigger, it needs `DEPLOY_ENABLED`, and it needs approval from a reviewer on the `production` environment. OIDC means there are no stored keys to leak.

**Automated testing.** *How do you know your security tests work?* Through mutation testing: 20 deliberate security bugs were planted one at a time, and the suite caught every one. *Example?* With the owner filter removed from list queries, the isolation tests failed.

**Logging.** *What must never be logged?* Passwords, tokens, session cookies and database passwords. A redaction filter has tests, and the access log drops query strings.

**Backups.** *How do you know a backup works?* By restoring it and comparing: `pg_dump` → `pg_restore` into a new database, then counts, values and reports compared. They were identical.

**Performance optimisation.** *What did you measure, and what did you learn?* The dashboard at 100/1k/10k trades. The PostgreSQL numbers looked slower, but `EXPLAIN ANALYZE` showed 10 ms in the server and about 245 ms in my test bridge's row decoding. The lesson: profile before you optimise, and don't blame the database without evidence. Indexes start with `user_id` because every query filters on it.

## 32. Learning exercises

### Challenge: "Sign out this device" (per-device session revocation), with no AWS costs

**Why it matters.** Today you can only sign out *all* devices (security review P8-5). Adding per-device revocation teaches server-side session state, migrations on two engines, and test-first security work.

**Goal.** The Account page lists your active sign-ins (created time, last seen, a short browser description), and each has a "Sign out" button that ends only that one.

**Acceptance criteria.**
1. Each sign-in creates a row in a new `user_sessions` table with these columns:
   - `id`: a random 32-byte value, stored as SHA-256;
   - `user_id`, `created_at`, `last_seen_at`, `user_agent` (at most 200 characters);
   - `revoked_at`.
2. The cookie stores the session id. `load_logged_in_user` rejects a missing, revoked or other-user session.
3. Revoking session A leaves session B signed in. Revoking another user's session id answers 404.
4. A password change and "sign out everywhere else" revoke every row except the current one.
5. The schema goes to v4 on SQLite (a migration with backup) and on PostgreSQL. All existing tests still pass on both engines.
6. The list shows only your own sessions, never the raw id. Every action is a POST with CSRF.

**Tests to write first** (in `tests/test_device_sessions.py`, using the `client`, `anon_client` and `ensure_user` fixtures):
- two test clients signed in as the same user; revoke the first from the second, and the first now redirects to `/login` while the second still works;
- a second user POSTing to revoke the first user's session id gets 404, and the session stays alive;
- a revoked session cannot come back by replaying the old cookie;
- the migration test: a v3 file upgrades to v4 with all data unchanged (copy the pattern from `test_legacy_and_cli_accounts.py`);
- the page never contains the raw session id;
- the route-walking test still passes (the new route is private).

**Files.**
- `tradeledger/migrations.py` and `tradeledger/schema_postgres.sql`;
- a new `tradeledger/repositories/sessions.py`;
- `tradeledger/web/auth.py` (`start_session`, `load_logged_in_user`);
- `tradeledger/web/account.py` and `templates/account/settings.html`;
- `tests/conftest.py` (the `sign_in` helper).

**Debugging guidance.**
- If every request suddenly redirects to `/login`, print `session` in `load_logged_in_user`: the cookie probably still holds `sv` and not the new id.
- If only PostgreSQL fails, check the placeholders (`?` is converted for you) and the `TIMESTAMPTZ` vs text timestamps (use `timestamps.utc_now_text`).
- Run one engine at a time:
  - `python -m pytest tests/test_device_sessions.py -x -vv`;
  - then the same command with `TRADELEDGER_TEST_DATABASE_URL` set.
- Use `flask --app tradeledger run-sql "SELECT * FROM user_sessions"` to look at the rows.

**Interview value.** It answers "how would you revoke a single stolen session?", a common security interview question. It also gives you a story of migrating two databases test-first and keeping 1,290 existing tests green.

## 33. Known limitations

- **Not yet built:**
  - password reset;
  - email verification;
  - MFA;
  - per-device sign-out;
  - roles or sharing between users.
- **Registration in production:** it is off, because it would reveal which emails are registered.
- **The RDS master user** is used by the app; a dedicated role is recommended.
- **No WAF;** single AZ; one task; tasks have public IPs (locked to the ALB).
- **Unverified here:**
  - the Docker image;
  - Compose with real containers;
  - Terraform;
  - GitHub Actions;
  - psycopg;
  - real Chart.js;
  - AWS of any kind.
- **Coverage** was measured with a standard-library tracer (approximate, statements only).
- **The PostgreSQL timings** measure the test bridge, not psycopg.

## 34. Future roadmap

This list does not start Phase 9; it is only an order of value.

1. Push to GitHub and get CI green, which settles Docker, Terraform validation, psycopg and real Chart.js.
2. With approval and a budget: deploy, record evidence and do a restore drill, then tear it down if it is not needed.
3. A dedicated database role for the app.
4. Per-device sessions (the exercise above).
5. Password reset through a real email provider, with token expiry.
6. TOTP MFA.
7. A staging environment and WAF rate rules.
8. CSV import and export for each user.

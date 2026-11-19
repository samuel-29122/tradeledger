# Phase 8 security review

**Scope:** authentication, sessions, multi-user data isolation, the authenticated API, the PostgreSQL integration, the Docker image and Compose stack, the AWS Terraform and the CI/CD workflows.

**Method:**
- a threat-by-threat review of the code;
- adversarial automated tests;
- security **mutation testing**: 20 deliberate security bugs were planted one at a time, and each was caught by the tests;
- static checks of the deployment files.

**What this is not:** an independent penetration test or a certification. Several controls are only *designed*: the Docker image, the AWS environment and the workflows have never been run (see §4).

## 1. Threat model, in plain English

| Who | What they want | Main defences |
|---|---|---|
| Another user of the same server | Read or change your trades | Every query filters by owner; foreign ids answer 404; the database refuses cross-owner links |
| An attacker on the internet | Guess passwords, steal sessions, abuse the API | Argon2id, throttling, generic errors, secure cookies, CSRF, hashed revocable tokens |
| A malicious website you visit | Make your browser act for you (CSRF, login CSRF, logout CSRF, DNS rebinding) | CSRF tokens on every form including sign-in; SameSite cookies; the API ignores cookies; Host allow-list / host-name rule |
| Someone with the logs, an image or a backup | Find secrets | Redaction; secrets never in images, Git or Terraform state; hashed tokens and passwords |
| A mistake by a developer | A query that forgets the owner | `user_id` is a required keyword; a TypeError for anything that is not a positive int; a two-column foreign key; route-walking tests |

## 2. Findings and their status

| # | Finding | Severity before | Status | Evidence |
|---|---|---|---|---|
| P8-1 | Phase 7 had no authentication; production relied on switches that could reopen the journal and API without login | Critical for any shared deployment | **Fixed.** Accounts; every website route private by default; the API needs tokens; the switches removed (ignored with a warning if set) | `test_auth.py::test_every_route_is_private_unless_deliberately_public` walks every route; `test_api_security.py`, `test_web_security.py` |
| P8-2 | Cross-user access by guessing ids, filtering, paging, searching, or pointing a trade at someone else's instrument | Critical | **Fixed** | 36 tests in `test_isolation.py` (on SQLite and PostgreSQL); 5 isolation mutants caught; the audit's decoy account never leaked (94,384 comparisons) |
| P8-3 | Online password guessing | High | **Mitigated.** Argon2id (m=19 MiB, t=2, p=1); 5 failures per account and 20 per address in 15 minutes, stored in the database so it holds across workers and containers; a password change is throttled too | `test_auth.py` throttling tests; the "throttle never blocks" mutant was caught |
| P8-4 | Account enumeration through messages or timing | Medium | **Mitigated** at sign-in: one message for every failure, and unknown emails still run a full Argon2id verification against a dummy hash. **Accepted** at registration: "already registered" must be said without an email-verification step, so registration is **off in production** | `test_failed_sign_in_gives_one_generic_message`, `test_disabled_account_gets_the_same_message` |
| P8-5 | Session fixation, theft and over-long sessions | High | **Fixed.** Session cleared at sign-in; HttpOnly, SameSite=Lax; in production Secure with the `__Host-` prefix; 8 h absolute and 2 h idle limits checked on the server; per-user `session_version` ends all sessions on password change, deactivation or "sign out everywhere". **Residual:** one specific device cannot be signed out on its own | `test_session_is_replaced_at_sign_in`, `test_absolute_session_lifetime`, `test_idle_timeout`, `test_production_session_cookie_flags`; 3 session mutants caught |
| P8-6 | Login CSRF and logout CSRF | Medium | **Fixed.** CSRF tokens on the sign-in, registration, logout and account forms; logout is POST-only | `test_sign_in_without_csrf_token_is_refused`, `test_sign_out_needs_post`, `test_sign_out_needs_a_csrf_token` |
| P8-7 | Open redirect through `?next=` | Medium | **Fixed.** Only local paths are followed; `//`, backslashes, schemes and control characters are refused | 9 cases in `test_only_local_paths_are_followed_after_sign_in`; the mutant was caught |
| P8-8 | API reachable through a browser's cookies (CSRF on JSON endpoints) | High | **Fixed by design.** The API reads only `Authorization: Bearer`; cookies are ignored; no CORS headers are sent | `test_api_ignores_session_cookies` |
| P8-9 | API token theft or misuse | High | **Mitigated:** 256-bit random tokens, stored only as SHA-256; shown once; expire (≤ 365 days); revocable; revoked on password change; never logged; at most 10 active. **Fixed during testing:** refreshing the page that shows a new token silently created a second token, so the form is now single-use | `test_account_and_tokens.py` (34 tests); 3 token mutants caught |
| P8-10 | Spoofed client IP (`X-Forwarded-For`) to get around throttling, or a fake `X-Forwarded-Proto` | Medium | **Fixed.** Forwarded headers are ignored unless `TRADELEDGER_PROXY_HOPS` is set; only the last N hops are trusted; `X-Forwarded-Host` never | `test_forwarded_headers_are_ignored_by_default`, `test_only_the_last_hop_is_trusted`; mutant caught |
| P8-11 | The app connects to RDS as the master user | Medium | **Open.** Recommended: a `tradeledger_app` role with only DML rights on the app tables, and migrations run as a separate migration role | — |
| P8-12 | Secrets in images, Git, Terraform state or logs | High | **Mitigated.** `.dockerignore` allow-list; Compose secrets are mounted files (`*_FILE`); Terraform writes no secret values (RDS-managed password; SECRET_KEY set out of band); log redaction of tokens, Bearer values, passwords and database URLs; a repository scan for keys and tokens | `test_deployment_files.py`, `test_operations.py` redaction tests; the redaction mutant was caught |
| P8-13 | Werkzeug debugger in production (remote code execution) | Critical | **Fixed.** Production refuses to start with debug on; the image runs Gunicorn | `test_production_refuses_debug_mode`, `test_image_uses_gunicorn_not_the_flask_dev_server` |
| P8-14 | Old single-user data given to the wrong account after the upgrade | High | **Fixed.** Unassigned records are invisible until an operator runs `claim-legacy-data` (dry run first; conflicts stop it) | `test_unassigned_records_are_invisible_to_every_account`; mutant caught |
| P8-15 | No password reset or email verification | — | **Accepted limitation**, not faked: no form pretends to send email. Operators reset with `set-password`, which also ends the user's sessions | `test_set_password_ends_existing_sessions` |
| P8-16 | Resource exhaustion through Argon2id (19 MiB per verification) | Medium | **Mitigated** by throttling and Gunicorn's bounded concurrency (2 workers × 4 threads = at most 8 hashes ≈ 152 MiB in a 512 MiB task). A distributed flood needs AWS WAF rate rules (not included: cost) | design; benchmark: about 19 ms per hash or verification |
| P8-17 | Unbounded growth of the throttle table | Low | **Mitigated.** Rows older than the window are deleted on each insert; registration is rate-limited per address | code review |
| P8-18 | Supply chain | Medium | **Partly open.** Python packages are pinned; pip-audit runs in CI; ECR scans images on push. **Not yet done:** the base image is pinned by tag, not digest; psycopg, psycopg-pool and gunicorn were never installed here, so they have not been audited locally | CI `audit` job (not yet run on GitHub) |
| P8-19 | Session cookie readable by the user | Info | **Accepted.** The cookie is signed, not encrypted; it holds only the user id, session version, two timestamps and the CSRF token, never secrets | `web/auth.py` docstring |
| P8-20 | Removed: Phase 7 guard (`web/guard.py`) | — | Replaced by sign-in; the hook order (load session → require login → CSRF) is registered explicitly and tested | `test_login_check_runs_before_csrf_check` |

## 3. Security definition of done

| Requirement | Status |
|---|---|
| Passwords properly hashed | ✅ Argon2id at OWASP settings, automatic re-hashing, NFKC normalisation |
| Private pages require login | ✅ default deny, checked for every registered route |
| API access authenticated | ✅ Bearer tokens; 401 with `WWW-Authenticate` |
| Users cannot access other users' data | ✅ adversarial tests on SQLite and PostgreSQL, mutation-tested |
| CSRF still enforced | ✅ all forms, including sign-in and sign-out |
| Sessions securely configured | ✅ in tests (production flags checked through the test client) |
| Secrets not committed | ✅ scan test; `.gitignore` covers `.env`, `secrets/`, Terraform state and `*.tfvars` |
| SQL queries safe | ✅ parameterised everywhere; Ruff S608 reviewed; the adapter refuses named parameters |
| Financial calculations correct | ✅ no formula changed; independent audit 0 disagreements on both engines |
| Migrations preserve data | ✅ byte-for-byte upgrade tests; backup before migrating; pg_dump/pg_restore round trip |
| Production debug disabled | ✅ refused at start-up |
| Security headers effective | ✅ CSP, X-Frame-Options, nosniff, HSTS (production), no-store, also on the new pages |
| Cloud resources least privilege | ⚠️ **written, not applied.** Static tests show no wildcard actions and an empty task role; the app's database role (P8-11) remains open |
| Database access restricted | ⚠️ **written, not applied.** Private subnets, security group from the app only, TLS forced; checked statically |

## 4. What remains unverified

- **The Docker image:** it has never been built, so its user, healthcheck and file permissions have not been observed at runtime. The CI `docker` job checks them.
- **AWS:** no resource has been created; IAM policies, security groups and TLS have not been exercised.
- **psycopg:** the PostgreSQL tests ran through a test-only libpq bridge, not psycopg. CI runs them with psycopg.
- **Real browsers other than Chromium,** and screen readers.
- **An independent penetration test.**

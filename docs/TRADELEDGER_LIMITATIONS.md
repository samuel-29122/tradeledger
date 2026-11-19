# TradeLedger: genuine unresolved limitations (version 0.8.5)

Everything here is either **not built**, **built but not verified**, or **a deliberate trade-off**. Nothing on this list is hidden by a skipped or faked test.

## Not verified, because tools or downloads were unavailable (final audit, 16 November 2026)

| What | Status | How to close it |
|---|---|---|
| **Docker image and Compose stack** | **Never built or started.** Docker Hub is blocked: neither the base image nor the `docker/dockerfile` frontend can be pulled, so even `docker build --check` fails. Only static checks ran | CI `docker` job, or `docker build .` and `scripts/compose_smoke_test.sh` on a machine with internet access |
| **`terraform validate`, `plan`** | **Not run.** The Terraform binary and the AWS provider come from blocked registries. **Done instead:** HCL syntax and canonical formatting with `hclfmt` (HCL v2.24.0, built from source): 14 files, 0 problems | CI `terraform` job |
| **GitHub Actions** | **Never run** (nothing pushed, as instructed). **Done instead:** actionlint 1.7.7: 0 findings (shellcheck unavailable) | Push to a private repository |
| **psycopg's C implementation** (`psycopg[binary]`) | The **genuine psycopg 3.3.8 and psycopg_pool 3.3.0** passed the full suite, but in psycopg's pure-Python implementation over the system libpq 16. The binary wheel was not available | CI `postgres` job installs `psycopg[binary]` |
| **pip-audit** | Not installable, and its online services are blocked. **Done instead:** an offline check against a clone of the PyPA Advisory Database: 32 pinned packages, 0 known vulnerabilities. Chart.js (npm) advisories were not checked | CI `audit` job |
| **The official npm Chart.js file** | Download blocked. The bundled file is built from the official v4.5.1 source and passes every real-chart test, but it is not byte-identical | CI browser step 4/4 |
| **Python 3.11 and 3.12: browser tests and two jsonschema test files** | The rest of the suite passed on both, against SQLite and PostgreSQL. These parts need compiled packages (greenlet, rpds-py) that exist here only for 3.13 | CI matrix |
| **Firefox, Safari, screen readers (NVDA, VoiceOver)** | Chromium only | Manual checklist in `PHASE_8_RELEASE_CHECKLIST.md` |
| **Coverage with coverage.py** | A standard-library tracer measured 98.50% of statements and 89.34% of branch outcomes; close to coverage.py's method, but not identical | CI `python` job |
| **Anything on AWS** | Not authorised: no resources, deployment, RDS restore drill or load test | `docs/PHASE_8_AWS_DEPLOYMENT.md`, after your approval and a budget |

**Therefore TradeLedger is not production-verified.** The application code is tested; its packaging and infrastructure are not.

## Not built (deliberately, for now)

- **Email** of any kind. There is no self-service "forgot password" email; an operator issues one-time links instead. There is no email verification at registration, so self-registration stays **off in production**.
- **Passkeys/WebAuthn.** Two-step uses TOTP codes.
- **Mandatory two-step for every account.** It is optional per user.
- **The least-privilege database role in AWS.** The command and tests exist, but the Terraform and the deploy workflow still connect as the RDS master user.
- **A WAF, multi-AZ, autoscaling or a staging environment.**
- **Roles** (such as admin) and **sharing** between users.
- **Currency conversion and partial exits.** Times are shown in UTC only.
- **CSV import or export.**

## Trade-offs worth knowing

- **TOTP secrets are encrypted with a key derived from `SECRET_KEY`.** Rotating that key turns off the users' codes until they use a recovery code and set two-step up again, or an operator runs `disable-two-step`. Sign-in fails closed in the meantime.
- **The analytics tables scroll inside their cards between 1024 and 1279 px.** Eleven columns do not fit next to the sidebar there, and a visible hint says so.
- **The PostgreSQL timings in the reports are not production figures.** They were measured through the test bridge, or through psycopg's pure-Python implementation, both of which decode rows in Python. They must not be read as "PostgreSQL is slower than SQLite".
- **API tokens are not bound to two-step.** A token keeps working without codes until it expires or is revoked. That is by design: scripts cannot type codes.
- **PostgreSQL timestamps are UTC text,** for parity with SQLite (the new session and reset tables use whole seconds). They are not `TIMESTAMPTZ`.
- **The session list cannot show where a device is,** by design. TradeLedger stores no IP addresses.

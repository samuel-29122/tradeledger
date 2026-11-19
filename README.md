# TradeLedger

A multi-user trading journal built with Python and Flask. You record trades, with the plan (entry, stop-loss, optional target, size, fees) and the result (exit price and time). TradeLedger turns them into exact performance statistics, measured in **R-multiples**: profit or loss divided by the amount risked.

![Analytics dashboard](docs/screenshots/final_analytics_real_charts_1440.png)

*The analytics dashboard with sample (invented) data.*

> **Status:** a portfolio project, version 0.8.5. It runs locally.
> - **Never run yet:** the Docker image, the Terraform for AWS and the CI workflows are written and statically checked, but have not run. The workflows run on GitHub once this repository is pushed.
> - **Not deployed anywhere.**
>
> The honest list of what is and is not verified is in [`docs/TRADELEDGER_LIMITATIONS.md`](docs/TRADELEDGER_LIMITATIONS.md).

## What it does

- **Journal:** record trades, edit them, close them by adding an exit, delete them, and search and filter them by instrument, status, strategy and dates.
- **Instruments:** each market you trade, with its price step, contract multiplier and quote currency. TradeLedger needs these to turn price moves into money and risk.
- **Analytics:**
  - 12 headline figures: win rate, expectancy, profit factor, average win and loss, maximum drawdown and more.
  - 7 charts, drawn by Chart.js: cumulative R, drawdown, and results by month, weekday, instrument, strategy and session.
  - Every chart has a text summary and a data table, and still works when the chart library cannot load.
- **Exact maths:** every price and amount is a Python `Decimal`, never a float. `scripts/qa_dashboard_agreement.py` recomputes the dashboard's figures independently and compares them.
- **Accounts:** each user sees only their own data.

## Features

- **Sign-in:** passwords hashed with **Argon2id**, and failed sign-ins throttled per account and per network address.
- **Sessions:** each signed-in browser has a server-side record. The Account page lists them and can sign out any one of them, and signing out makes a copied cookie useless.
- **Two-step verification (optional):** 6-digit codes from an authenticator app (TOTP, RFC 6238), plus single-use recovery codes.
- **Password reset:** a one-time, 30-minute link that the administrator creates with a command. There is no email service.
- **JSON API** at `/api/v1`, with personal API tokens (Bearer). Tokens are stored hashed, expire and can be revoked. An OpenAPI 3.1 description is served at `/api/v1/openapi.json`.
- **Security defaults:** CSRF tokens on every form, a strict Content-Security-Policy, secure cookies in production, and production refusing to start with debug mode or a weak secret key.
- **Databases:** SQLite for local use, or **PostgreSQL**, through one small database adapter. Numbered migrations change the schema safely, with a backup first.
- **Operator commands** (`flask --app tradeledger …`):
  - `create-user`, `set-password`, `deactivate-user`;
  - `create-reset-link`, `disable-two-step`;
  - `migrate-db`, `seed-db`;
  - `grant-app-role` (a PostgreSQL role that can only read and write rows).
- **Deployment files (not yet run):**
  - a Dockerfile, Docker Compose and Gunicorn settings;
  - Terraform for AWS (ECS Fargate, RDS PostgreSQL, a load balancer);
  - GitHub Actions for tests, plus a manual, approval-gated deploy workflow.

## Tech stack

| Area | Used |
|---|---|
| Language | Python 3.11+ (developed on 3.13) |
| Web framework | Flask 3 with Jinja templates; server-rendered pages plus a JSON API |
| Database | SQLite (Python's built-in `sqlite3`) or PostgreSQL 16 (psycopg 3 + psycopg_pool) |
| Security | `cryptography` (Argon2id, AES-GCM), Flask's signed sessions, own CSRF and throttling code |
| Front end | HTML, CSS and plain JavaScript; **Chart.js 4.5.1** bundled in `tradeledger/static/vendor/chartjs/` |
| Testing | pytest, pytest-cov, Playwright (Chromium), Node's built-in test runner, Ruff |
| Deployment (written, not run) | Gunicorn, Docker, Docker Compose, Terraform (AWS), GitHub Actions |

## Project structure

```
tradeledger/
├── tradeledger/              The application package
│   ├── __init__.py           App factory: settings, logging, database, security headers
│   ├── config.py             Development / testing / production settings
│   ├── db.py                 One adapter for SQLite and PostgreSQL
│   ├── schema.sql            SQLite starting schema; migrations.py upgrades it
│   ├── schema_postgres.sql   PostgreSQL schema
│   ├── migrations.py         Numbered, backed-up schema changes
│   ├── cli.py                flask commands (accounts, migrations, sample data)
│   ├── passwords.py          Argon2id hashing and password rules
│   ├── totp.py               Two-step codes and secret encryption
│   ├── validation.py         Rules for every form and API field
│   ├── repositories/         All SQL: trades, instruments, users, sessions, tokens…
│   ├── performance/          The Decimal maths: trade_math, metrics, report, dashboard
│   ├── web/                  Website pages: auth, account, trades, instruments, analytics
│   ├── api/                  JSON API (/api/v1) and its OpenAPI description
│   ├── templates/            Jinja HTML templates
│   └── static/               CSS, JavaScript and the bundled Chart.js
├── tests/                    pytest suite, browser tests and JavaScript tests (tests/js)
├── scripts/                  Analytics audit, benchmark, backup check, SQLite→PostgreSQL import
├── docs/                     Guides, QA report, security review, limitations, screenshots
├── infra/                    Terraform for AWS (never applied)
├── .github/workflows/        CI (tests) and a manual deploy workflow
├── Dockerfile, compose.yaml, gunicorn.conf.py, docker/
├── requirements.txt          Packages the app needs to run (exact versions)
├── requirements-dev.txt      Plus test and lint tools
├── requirements-browser.txt  Plus Playwright for browser tests
└── constraints.txt           Pins for indirect dependencies
```

## Install and run (local, SQLite)

You need **Python 3.11 or newer** and **Git**.

**Windows (PowerShell):**

```powershell
git clone https://github.com/samuel-29122/tradeledger.git
cd tradeledger
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -c constraints.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# open .env and paste the printed value after SECRET_KEY=
flask --app tradeledger init-db
flask --app tradeledger create-user --email you@example.com
flask --app tradeledger seed-db --email you@example.com      # optional: invented sample trades
flask --app tradeledger run --debug
```

If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

**macOS / Linux:**

```bash
git clone https://github.com/samuel-29122/tradeledger.git
cd tradeledger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env    # then set SECRET_KEY in .env
flask --app tradeledger init-db
flask --app tradeledger create-user --email you@example.com
flask --app tradeledger seed-db --email you@example.com
flask --app tradeledger run --debug
```

Open <http://127.0.0.1:5000> and sign in.
- **Where the data goes:** `instance/tradeledger.db`. Git ignores it.
- **Other commands:** run `flask --app tradeledger --help`.

PostgreSQL, Docker Compose and the production settings are explained in [`docs/TRADELEDGER_FINAL_GUIDE.md`](docs/TRADELEDGER_FINAL_GUIDE.md).

## Running the tests

```bash
python -m pytest                          # the whole suite on SQLite
python -m pytest -m "not browser"         # skip the browser tests
node --test tests/js/*.test.js            # JavaScript tests (needs Node.js 18+)
ruff check . && ruff format --check .     # lint and formatting
python scripts/qa_dashboard_agreement.py  # independent check of every dashboard figure
```

**Browser tests** (real Chromium, including the real Chart.js rendering) need Playwright:

```bash
python -m pip install -r requirements-browser.txt -c constraints.txt
python -m playwright install chromium
python -m pytest -m browser
```

**PostgreSQL tests:** point the suite at an empty test database. Each test uses its own schema in it.

```bash
# PowerShell: $env:TRADELEDGER_TEST_DATABASE_URL = "postgresql://user:password@localhost/tradeledger_test"
export TRADELEDGER_TEST_DATABASE_URL=postgresql://user:password@localhost/tradeledger_test
python -m pytest
```

**What the tests check:**
- **Trade maths** against hand-worked examples and thousands of seeded random trades (property-style tests written with Python's `random` module, not Hypothesis).
- **Validation:** every form and API rule.
- **Database:** constraints and migrations, including a crash in the middle of an upgrade.
- **User isolation:** a second user tries to reach the first user's data through pages, the API, filters and guessed ids.
- **Accounts:** sign-in, throttling, sessions, two-step and reset links.
- **Security attacks:** CSRF, open redirects, log redaction and production refusals.
- **Real-browser journeys:** sign-in through to adding a trade, at phone, tablet and desktop widths.
- **Chart.js:** that it actually draws the seven charts.

Some tests are **skipped on purpose** when their requirement is missing: PostgreSQL, Playwright, or a screenshot folder. pytest prints the reason for each skip (`-ra`). A skipped test has not passed.

Results from the author's runs are in [`docs/TRADELEDGER_FINAL_QA_REPORT.md`](docs/TRADELEDGER_FINAL_QA_REPORT.md). Those runs were on Linux, and the suite has **not yet been run on Windows or on GitHub Actions**.

## What I'd improve next

- **Get CI green on GitHub,** including the Docker image build and `terraform validate`, which have never run.
- **Deploy to AWS once, record evidence,** and test a database restore. Then decide whether it stays up, since it costs money every month.
- **Use the least-privilege database role in the AWS setup.** The command exists, but the Terraform still connects as the database owner.
- **Email-based password reset and email verification,** through a real email provider.
- **Passkeys (WebAuthn)** as a stronger alternative to two-step codes.
- **CSV import and export,** so existing journals can be brought in.
- **Accessibility testing with real screen readers,** plus Firefox and Safari.

## Licence

No licence file has been chosen yet. Until one is added, the code is visible but not licensed for reuse. Chart.js and `@kurkle/color` are MIT-licensed; see `tradeledger/static/vendor/chartjs/`.

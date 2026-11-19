# TradeLedger: the final guide (version 0.8.5)

This guide is for someone new to TradeLedger, and also to web applications. Each topic starts in plain English, then gives the technical detail, then explains **why** it was built that way.

**Related documents:**
- **The earlier phase guides** (`docs/PHASE_*_GUIDE.md`): how each part was built, step by step.
- **[`TRADELEDGER_FINAL_QA_REPORT.md`](TRADELEDGER_FINAL_QA_REPORT.md):** every test result.
- **[`TRADELEDGER_FINAL_SECURITY_REVIEW.md`](TRADELEDGER_FINAL_SECURITY_REVIEW.md):** the security assessment.
- **[`TRADELEDGER_LIMITATIONS.md`](TRADELEDGER_LIMITATIONS.md):** everything that is not done or not verified.

---

## 1. What TradeLedger is

TradeLedger is a trading journal that runs in your web browser.
- **What you record:** for each trade, the plan (entry price, stop-loss, optional target, size, fees) and the result (exit price and time).
- **What it measures:** every result in **R**, which means "multiples of the risk you took". A trade that risked £100 and made £250 scored +2.5R.
- **Why R:** trades of any size, instrument or currency can be compared fairly. The dashboard then answers questions like:
  - Do I have an edge?
  - Which strategy works?
  - Which session loses money?

**Technically**, it is:
- a Python **Flask** application with server-rendered pages and a JSON API;
- data in **SQLite** (one file, ideal on your own computer) or **PostgreSQL** (a database server, for real deployments);
- money calculated with Python's `Decimal`, so it is exact to the penny with no rounding drift.

An independent audit compares every figure on the page, in the charts and in the API: 94,384 comparisons, all identical.

## 2. What Phase 8.5 finished

Phase 8 added accounts, PostgreSQL, Docker and AWS designs. Phase 8.5 closed the gaps that review and testing found.

| Gap | What exists now |
|---|---|
| Charts needed an internet connection and had never been tested with the real library | Chart.js 4.5.1 ships inside TradeLedger, built from the official source code. All seven charts are tested in a real browser |
| Signing out did not really end a session on the server | Every signed-in browser is recorded and can be signed out from the Account page; a copied cookie stops working |
| Forgotten passwords needed the administrator to choose the new one | One-time reset links: you choose the new password yourself |
| No second factor | Optional two-step verification with an authenticator app |
| The web app could reshape or delete database tables | A command that gives the app a "rows only" database role |
| Some failure situations were untested | Crashes in mid-upgrade, failures half-way through a change, and two clicks at the same moment are now tested |

Several real defects were found and fixed along the way. They are listed in `CHANGELOG.md` under 0.8.5.

## 3. Installing TradeLedger

### 3.1 On your own computer (SQLite: the simplest way)

You need Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate                    # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste the result as SECRET_KEY in .env
flask --app tradeledger init-db
flask --app tradeledger create-user --email you@example.com  # asks for a password twice
flask --app tradeledger seed-db --email you@example.com      # optional: invented sample trades
flask --app tradeledger run --debug
```

Open <http://127.0.0.1:5000> and sign in. The charts work offline, because Chart.js is part of the project.

**Why a virtual environment (`.venv`):** it keeps TradeLedger's exact package versions apart from everything else on your computer. `constraints.txt` pins them, so everyone runs exactly what was tested.

### 3.2 With PostgreSQL and a restricted database role (recommended for a server)

The idea: **two database users.**
- **The owner** creates and upgrades the tables.
- **The app user** can only read and write rows.

If the web app were ever tricked into running a harmful command, the database would refuse to drop or alter anything.

```sql
-- as a PostgreSQL administrator
CREATE ROLE tradeledger_owner LOGIN PASSWORD 'choose-a-long-random-one';
CREATE ROLE tradeledger_app   LOGIN PASSWORD 'a-different-long-random-one';
CREATE DATABASE tradeledger OWNER tradeledger_owner;
```

```bash
# as the OWNER: create the tables, then grant the app role row access only
export DATABASE_URL=postgresql://tradeledger_owner:...@db-host/tradeledger
flask --app tradeledger init-db
flask --app tradeledger grant-app-role --role tradeledger_app
flask --app tradeledger create-user --email you@example.com

# run the website as the APP role
export DATABASE_URL=postgresql://tradeledger_app:...@db-host/tradeledger
export TRADELEDGER_CONFIG=production SECRET_KEY=... TRADELEDGER_TRUSTED_HOSTS=journal.example.com
gunicorn -c gunicorn.conf.py tradeledger.wsgi:app
```

**After every upgrade (`migrate-db` as the owner), run `grant-app-role` again,** so that new tables are covered.

**What `grant-app-role` allows and refuses:**
- **Allowed:** SELECT, INSERT, UPDATE and DELETE on the eight application tables; reading the migration record; using the id counters.
- **Refused:** CREATE, DROP, ALTER and TRUNCATE.
- **Refused as the app role:** superusers, roles that can create roles or databases, and the owner itself.

**This was tested** by running the whole application as such a role and trying eight kinds of structural change, all refused.

**Production driver:** in production TradeLedger uses **psycopg 3** with a connection pool (psycopg_pool).
- **What was tested:** in the final audit, the genuine psycopg 3.3.8 and psycopg_pool 3.3.0 passed the full test suite against PostgreSQL 16, and the app ran under Gunicorn 26.0.0. They were installed from their official source code, so psycopg used its pure-Python implementation; production installs the faster C build.
- **The test-only bridge** (`tests/pg_libpq_bridge.py`) remains for machines without psycopg. It can never run a production server: the app refuses to start.

### 3.3 Docker Compose (local, production-style)

```bash
mkdir -p secrets
python -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
python -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/db_password.txt
docker compose up --build
docker compose exec app flask --app tradeledger create-user --email you@example.com
```

Open <http://localhost:8000>. **This stack has not been run in the build environment,** because Docker images could not be downloaded. The CI pipeline builds and tests it.

### 3.4 Upgrading an existing journal

```bash
flask --app tradeledger migrate-db          # makes a backup copy first, then upgrades
```

- **From Phase 8:** this adds schema version 4. It only creates new tables and columns; nothing existing changes.
- **From Phase 7 or earlier:** see `docs/PHASE_8_DATABASE_MIGRATION.md`, because old records must be assigned to an account with `claim-legacy-data`.

## 4. Using TradeLedger

1. **Add your instruments** (Instruments page): symbol, name, asset class, price step, contract multiplier, quote currency. TradeLedger needs these to turn price moves into money.
2. **Record a trade** (Journal → Add trade):
   - **Always:** the instrument, LONG or SHORT, the entry time and price, the stop-loss, and the size.
   - **Optional:** a target, fees, strategy, setup, session, emotions and notes.
   - **Closing:** to record a finished trade, give its exit price and time.
3. **Close an open trade later:** open it, choose Edit, and add the exit price and time.
4. **Find trades:** the search box searches every text field. You can also filter by instrument, status, strategy and dates. The filters stay in the address bar, so a filtered view can be bookmarked.
5. **Delete a trade:** open it and choose Delete. It asks for confirmation, and nothing else is affected.
6. **Analytics:**
   - 12 headline figures and 7 charts: cumulative R, drawdown, and results by month, weekday, instrument, strategy and session.
   - Every chart has a written summary and a data table, so nothing depends on seeing colours or the chart itself.
   - Hover over or tap a point to see the exact figure and the trade number.

**The rules the app enforces** (the database enforces them too):
- a long trade's stop must be below its entry;
- a closed trade needs an exit;
- prices must fit the instrument's price step.

## 5. Your account

### 5.1 Changing your password

Account → Change password. You must type the current password. Afterwards:
- every **other** browser is signed out;
- every API token is revoked;
- this browser stays signed in.

### 5.2 Signed-in browsers

Account → Signed-in browsers lists every browser signed in to your account:
- a short label such as "Firefox on Windows";
- when it signed in;
- when it was last active.

**Signing out:**
- **Sign out** next to a row signs out that one browser.
- **Sign out everywhere else** keeps only this browser.

**Use it when:** you lose a phone, or you forgot to sign out on a shared computer.

**How long sessions last:** they end by themselves after 8 hours, or after 2 hours without activity.

### 5.3 Two-step verification

**What it is:** signing in then needs your password **and** a 6-digit code from an app on your phone. The code changes every 30 seconds. Someone who steals your password still cannot get in.

**Setting it up:** Account → Set up two-step verification.
1. Install an authenticator app, for example Google Authenticator, Microsoft Authenticator, 1Password or Aegis.
2. In the app, add an account and type the **setup key** shown on the page.
3. Enter your current password and the code the app now shows.
4. **Save the ten recovery codes** shown next, for example in a password manager or printed at home. They are shown only once. Each works once if you lose your phone.

**Signing in afterwards:** password first, then the code. A recovery code works in place of the code.

**Turning it off:** Two-step verification page → Turn off. This needs your password and a current code.

**Lost your phone and your recovery codes?** Ask the administrator. They will check it is really you, then run `disable-two-step`.

### 5.4 Forgotten password

TradeLedger sends no email. Ask the administrator, who runs `create-reset-link` and passes you a link by a channel you both trust.
- **The link:** it works **once**, for **30 minutes**. Open it and choose a new password.
- **What resetting does:**
  - you are signed out everywhere;
  - your API tokens are revoked;
  - you then sign in normally, and two-step still applies if you use it.

### 5.5 API tokens

For scripts that read or change your trades through `/api/v1`. Create one on the Account page.
- **It is shown once:** copy it then.
- **Send it as** `Authorization: Bearer tl_...`.
- **It expires** after the number of days you choose (at most 365). You can revoke it at any time.

## 6. For the administrator

| Task | Command |
|---|---|
| Create an account | `flask --app tradeledger create-user --email E` |
| Forgotten password: they choose it | `flask --app tradeledger create-reset-link --email E` (set `TRADELEDGER_PUBLIC_URL` to your https address) |
| Forgotten password: you choose it | `flask --app tradeledger set-password --email E` (ends their sessions and tokens) |
| Lost phone and recovery codes | `flask --app tradeledger disable-two-step --email E` |
| Block or unblock someone | `deactivate-user` / `activate-user --email E` |
| Upgrade the database | `flask --app tradeledger migrate-db` (PostgreSQL: add `--backed-up` after taking a snapshot) |
| Row-only role for the web app | `flask --app tradeledger grant-app-role --role tradeledger_app` (after every migration) |
| Check a backup restores | `python scripts/backup_restore_check.py --server postgresql://...` |

**Before you hand over a reset link or switch off two-step, confirm who is asking,** for example by a phone call to a number you already know. These two commands are as powerful as a password.

## 7. How the charts are delivered

**Plain English.** The charts are drawn by a well-known free library called Chart.js. Before this release, the browser downloaded it from the internet each time (from a "CDN"). Now TradeLedger includes its own copy, so:
- the charts work offline;
- no outside company sees who opens your dashboard.

**Where the copy came from.** The usual download sites were blocked in the build environment, but the official Chart.js source code repository was not. The file was therefore **built from the official version 4.5.1 source code**, with the same recipe Chart.js uses.
- **The record:** `tradeledger/static/vendor/chartjs/BUILD.md` lists the exact source commits, the build tool and the file's fingerprint.
- **Licences:** both MIT licences sit next to the file.
- **Repeatable:** building it again gives an identical file.
- **The difference from the official download:** it is the same code, but the bytes differ slightly, because a different tool shrank it. That is why the fingerprint differs from the CDN's. The automatic tests run against the official file too, as soon as the project runs on GitHub.

**The fingerprint (Subresource Integrity).** The page tells the browser the file's SHA-384 fingerprint. If even one byte of the file changes, the browser refuses to run it. A test fails if the file and the configured fingerprint disagree.

**If the library cannot load** (blocked, or a corrupted file), every chart area explains why, and the same figures remain in the tables and summaries. This fallback is tested too.

**Why self-host:**
- **Reliability:** the charts work offline.
- **Privacy:** no third party sees visitors.
- **Security:** the page's Content Security Policy can stay at "scripts from this site only".

**The trade-off:** updating Chart.js is now a deliberate step (`THIRD_PARTY_NOTICES.md` explains how).

## 8. How signing in works

**Plain English.** When you sign in, the server gives your browser a ticket (a **cookie**) and also writes "this browser is signed in" in its own notebook (the **database**). On every page, the server checks both:
- the ticket must be genuine;
- the notebook entry must still be open.

**Signing out crosses out the notebook entry.** So even if someone copied your ticket, it no longer works.

**Technically:**
- **The cookie** is signed with `SECRET_KEY`. It holds a random 256-bit session key, your user id, a session version, two timestamps and the CSRF token.
- **The database** holds only the SHA-256 of the key, in the `user_sessions` table.
- **Each request checks:**
  - the signature;
  - that the account is active;
  - that the session version matches;
  - that the row exists, belongs to you, is not revoked, is younger than 8 hours, and was used within 2 hours.
- **Every check works on its own:** tests break one layer at a time to prove it.

**Why not only the cookie** (as in Phase 8): a signed cookie cannot be cancelled. Phase 8 could only end **all** of a user's sessions, and a cookie copied before sign-out stayed valid. The database row costs about 0.02 ms per request.

**Other protections:**
- the cookie is HttpOnly and SameSite;
- in production it is Secure and uses the `__Host-` prefix;
- the session is cleared at sign-in (this stops "session fixation");
- failed sign-ins are throttled per account and per network address;
- every failure gets the same message, so attackers cannot learn which emails exist.

## 9. How two-step codes work

**Plain English.** Your phone and TradeLedger share a secret. Each one mixes the secret with the current time (in 30-second steps) and turns the result into six digits. The same secret and time give the same code, so TradeLedger can check your code without your phone ever contacting it.

**Technically:**
- **The algorithm:** TOTP (RFC 6238) over HOTP (RFC 4226) with HMAC-SHA1, the setting every app supports. It is written in about 40 lines of standard-library Python and checked against the official test values in both RFCs.
- **Clock tolerance:** codes are accepted one step either side, about ±30 seconds.
- **Replay protection:** the last accepted step is stored, so a code cannot be used twice. Even two identical requests sent at the same instant get only one success (there is a test).
- **Encryption at rest:** the secret is stored encrypted with AES-GCM. Its key is derived from `SECRET_KEY`, and the user id is bound into the encryption, so a secret copied onto another account is useless.
- **Recovery codes:** stored as SHA-256 only.

**Why TOTP, not SMS:**
- no phone provider is needed;
- SMS codes can be stolen by SIM swapping;
- TOTP works without signal.

**Passkeys would be stronger still.** They are listed as future work.

## 10. How reset links work

**Plain English.** The administrator creates a link containing a long random secret. When you open it, TradeLedger checks the secret, then **immediately removes it from the address bar**, so it does not stay in your browser history or get sent to other websites. You choose a new password, and the link dies.

**Technically:**
- **The token:** `tlr_` + 256 random bits; the database stores its SHA-256.
- **Lifetime and use:** 30 minutes. One use, enforced with a conditional UPDATE, so four simultaneous attempts give exactly one success. A newer link cancels older ones.
- **Leaving the URL:** the token moves into the signed session, followed by a redirect to `/reset-password`. That page sends `Referrer-Policy: no-referrer`.
- **One transaction:** the new password hash, the used link, and revoking every session and API token all happen together. If anything fails half-way, nothing changes (tested).
- **Logs:** they never contain the token. Development server access lines are redacted too: a defect found and fixed in this release.

**Why the administrator, not email:** TradeLedger has no email service. A reset form that pretends to send email would be a fake feature, and that is worse than an honest manual step.

## 11. Database safety: upgrades, failures and races

| Situation | What happens | Test |
|---|---|---|
| The power fails during a SQLite upgrade | SQLite's journal undoes the half-finished upgrade at the next start. Version 3 and all data remain; the next start completes version 4 | A real process killed with `os._exit` in the middle of migration 4 |
| A PostgreSQL upgrade is killed half-way | The open transaction is rolled back; even a table created before the kill disappears | The server process terminated during the migration |
| Two containers start at once | A PostgreSQL advisory lock lets one migrate; the other waits | Two processes racing |
| An error half-way through a password reset | Everything is undone: old password, unused link, no revocations | A forced failure |
| The same reset link, recovery code or session revoked twice at once | Exactly one succeeds | 4 threads released together, on both engines |
| A trade edited in two places at once | Row locks serialise the edits (PostgreSQL `FOR UPDATE`) | Phase 8 test |

## 12. Testing: how to check everything yourself

```bash
python -m pytest                               # everything on SQLite (about 1.5 minutes)
TRADELEDGER_TEST_DATABASE_URL=postgresql://user@localhost/scratch python -m pytest   # on PostgreSQL
python -m pytest -m chartjs_real -ra           # the real-chart browser tests
node --test tests/js/*.test.js                 # JavaScript
ruff check . && ruff format --check .          # code style
python scripts/qa_dashboard_agreement.py       # independent audit of every figure
python scripts/benchmark_dashboard.py          # speed at 100 / 1,000 / 10,000 trades
```

**Reading the results:**
- **"passed":** the check ran and succeeded.
- **"skipped":** it did **not** run, usually because it needs PostgreSQL or a screenshot folder. A skip is never a pass.

The final run gave 0 failures on both databases (see the QA report).

**Browser tests** need Playwright and Chromium:

```bash
pip install playwright
python -m playwright install chromium
```

## 13. Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Every page sends me to sign in | You are not signed in, or your session ended | Sign in. Sessions end after 8 hours, or 2 hours idle |
| "Your session has ended" right after using another device | That device signed this one out, or you changed your password | Sign in again |
| Two-step code always "not right" | The phone's clock is wrong | Turn on automatic time on the phone; then try the newest code |
| Codes stopped working after a server change | `SECRET_KEY` was changed, so stored secrets cannot be read | Use a recovery code, then set two-step up again (or ask for `disable-two-step`) |
| Reset link "invalid, used or expired" | Older than 30 minutes, already used, or replaced by a newer link | Ask for a new link |
| `create-reset-link` refuses the base URL | It is not https | Set `TRADELEDGER_PUBLIC_URL=https://...` (or use http://localhost for testing) |
| `grant-app-role`: "administrative rights" | The role can create roles or databases, or is a superuser | Use a plain `LOGIN` role |
| Production refuses to start: "test-only PostgreSQL driver" | Something registered the test bridge | Install `requirements.txt` (psycopg) and remove that code |
| Charts show "could not be loaded" | The file was changed (SRI mismatch), or a custom `TRADELEDGER_CHART_JS_URL` is unreachable | Restore the file, or unset the variable |
| 429 "Too many attempts" | Throttling after repeated failures | Wait 15 minutes |

## 14. Words used in this guide

| Word | Meaning |
|---|---|
| **R-multiple** | The result divided by the amount risked |
| **Hash** | A one-way fingerprint. You can check a value against it, but cannot recover the value |
| **Argon2id** | A deliberately slow, memory-hungry hash for passwords |
| **Session** | The period you are signed in to one browser |
| **CSRF** | A trick where another website makes your browser send a request. Stopped by secret tokens in every form |
| **TOTP** | Time-based one-time password: the 6-digit codes |
| **SRI** | Subresource Integrity: a file fingerprint the browser checks |
| **Least privilege** | Give each part only the access it needs |
| **Migration** | A numbered, tested change to the database structure |
| **Mutation testing** | Planting deliberate bugs to prove the tests would catch them |

## 15. Explaining it in an interview

**Thirty seconds:**
> TradeLedger is a multi-user trading journal in Python and Flask with exact Decimal analytics. In the final phase I made the security real end to end: server-side sessions, so that signing out or losing a device really cuts access; optional TOTP two-step verification; one-time reset links; and a least-privilege PostgreSQL role. I proved it with adversarial tests, 40 planted security bugs that the tests all catch, and crash tests for database upgrades. I'm clear about what isn't verified: the Docker image and AWS were never run.

**Questions you can answer from this project:**
- "How do you revoke one stolen session?"
- "How would you implement password reset without email?"
- "How does TOTP work, and how do you stop replay?"
- "What happens if a migration dies half-way?"
- "How do you know your security tests work?"
- "Why self-host a JavaScript library, and what is SRI?"

**Stay accurate:** the Docker, Terraform, AWS and CI files exist and were statically checked (hclfmt and actionlint), but they **have not run**. Do not claim AWS production experience from this project. TradeLedger is not production-verified.

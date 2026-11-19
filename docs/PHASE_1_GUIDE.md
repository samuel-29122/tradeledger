# TradeLedger — Phase 1 Guide

Requirements, architecture and the first working implementation.

Every file in this project is complete. The 16 tests in `tests/test_app.py` were run before delivery (Python 3.13, Flask 3.1.3, pytest 9.1.1): **16 passed**. The dev server was also started and `/`, `/health` and a missing page returned 200, 200 and 404. That proves it works on my machine; you still need to run it on yours.

---

## Part A — Project vision

**What it does.** TradeLedger lets a trader record each trade (instrument, direction, entry, stop, target, exit, size, fees, strategy, session, notes) and turns that history into honest performance statistics: win rate, R-multiples, profit factor, expectancy, drawdown, and breakdowns by instrument, strategy, session and day.

**Who it is for.** Initially one user: you, a discretionary trader who wants evidence about what is working. Later, potentially other retail traders.

**Why it is a strong portfolio project.**

1. It solves a real problem you understand, so you can talk about it credibly.
2. It touches every layer an employer cares about: HTTP, server logic, SQL, a UI, testing, deployment.
3. The maths is easy to get subtly wrong (shorts, fees, contract sizes, division by zero). Getting it right *and proving it with tests* is exactly the evidence a QA or junior-developer interviewer looks for.

**An honest caveat.** Trading journals are a very common portfolio project. What separates yours is not the idea but the quality: correct formulas, real tests, clean structure, and your ability to explain every decision. That is where we will focus.

---

## Part B — MVP scope

**In the MVP**

| Area | Included |
|---|---|
| Trades | Create, view, edit, delete one trade at a time |
| Fields | Date/time, instrument, direction, entry, stop-loss, take-profit, exit, position size, fees, strategy, session, notes, created/updated timestamps |
| Calculations | Net P&L, planned R:R, realised R, win/loss/breakeven counts, win rate, average R, profit factor, expectancy, max drawdown, streaks |
| Dashboard | Summary cards + equity curve (Chart.js) + recent trades |
| Quality | Server-side validation, CSRF protection, parameterised SQL, pytest suite, GitHub Actions |

**Postponed (and why)**

| Feature | Reason |
|---|---|
| User accounts / login | Single local user; adds security work before there is anything to protect. Re-evaluated before deployment. |
| PostgreSQL | SQLite is enough for one user. Switch when we deploy with multiple users or concurrent writes. |
| Currency conversion | Requires live FX rates; MVP assumes one account currency and records P&L in it. |
| Partial exits / scaling in | Changes the data model significantly; do it once the single-exit model is solid. |
| Screenshots, CSV import | Nice to have; not needed to prove the core works. |
| Calendar view, monthly charts | Phase 6 extras once the core dashboard is correct. |

**A key design decision to flag now.** P&L in money is *not* `(exit − entry) × size` for every instrument. A 1-point move in EUR/USD, XAU/USD and NAS100 is worth very different amounts. In Phase 2 we will store a **point value** (money per 1.0 price move per 1 unit of size) per instrument in an `instruments` table, so the formula becomes:

```
gross P&L (long)  = (exit − entry) × size × point_value
gross P&L (short) = (entry − exit) × size × point_value
net P&L           = gross P&L − fees
```

R-multiples, by contrast, are unit-free and do not need the point value: `realised R = (exit − entry) / (entry − stop)` for a long. That is why R-based stats are trustworthy even before monetary values are perfected.

---

## Part C — Development roadmap

| Phase | Goal | Deliverable | Done when |
|---|---|---|---|
| 1 | Requirements & setup | Runnable Flask skeleton | App launches, 16 tests pass, first Git commit |
| 2 | SQL database | `schema.sql`, seed data, query scripts | Tables created, constraints enforced, analytical queries verified by tests |
| 3 | Python trading logic | `calculations.py`, `validation.py` | Every metric has a formula, example and passing test |
| 4 | Flask backend | CRUD routes, CSRF, error handling | Trades can be created/edited/deleted via HTTP; route tests pass |
| 5 | Frontend | Forms, trade history table, responsive layout | Full CRUD usable in a browser on desktop and phone |
| 6 | Dashboard | Metric cards, Chart.js charts, filters | Dashboard figures match the tested calculations |
| 7 | Professional QA | Expanded tests, test plan, bug log, GitHub Actions | CI runs green on every push |
| 8 | AWS deployment | Deployed, secured app | Reachable over HTTPS with protection on; costs understood |
| 9 | Portfolio prep | README, screenshots, architecture docs | A stranger can clone and run it |
| 10 | Interview prep | Question bank, your own answers | You can explain every layer without notes |

---

## Part D — Architecture

```
 Browser (HTML / CSS / JS)
        │  HTTP request: GET /trades, POST /trades/new …
        ▼
 Flask routes            tradeledger/routes.py
   – read the request, call services, choose a template
        │
        ├──► Validation       tradeledger/validation.py     (Phase 3)
        │      – is this input acceptable?
        │
        ├──► Calculations     tradeledger/calculations.py   (Phase 3)
        │      – pure functions: trades in, numbers out
        │
        └──► Database layer   tradeledger/db.py             (Phase 2)
               – the ONLY code that writes SQL
                     │
                     ▼
               SQLite file    instance/tradeledger.db
        │
        ▼
 Jinja2 template renders HTML  ──►  HTTP response  ──►  Browser
```

**How one request flows (e.g. later, viewing the dashboard):** browser sends `GET /dashboard` → Flask matches it to a route function → the route asks `db.py` for closed trades → passes them to `calculations.py` → gets back a dictionary of metrics → hands that to `dashboard.html` → Jinja2 produces HTML → Flask sends it back.

**Why split it this way (separation of concerns).**

1. *Problem solved:* in one big `app.py`, SQL, maths and HTML get tangled, so a bug in one breaks the others and nothing can be tested alone.
2. *Why this approach:* each layer has one job. Calculations are pure functions — no database, no Flask — so they are trivial to unit-test.
3. *Alternatives:* a single file (fine for 50 lines, painful at 500); an ORM like SQLAlchemy (less SQL to write, but hides the SQL you are trying to learn); a full MVC framework like Django (more built-in, more to learn up front).
4. *Trade-offs:* a few more files and imports in exchange for code you can change safely.
5. *Maintainability:* you can rewrite the dashboard without touching the maths, or swap SQLite for PostgreSQL by changing only `db.py`.
6. *Testing:* calculations get unit tests, `db.py` gets integration tests against a temporary database, routes get Flask test-client tests.
7. *In a larger system:* the layers might become separate services, the database layer an ORM with migration tooling (Alembic), and the routes a JSON API consumed by a separate frontend.

---

## Part E — Folder structure (Phase 1)

```
tradeledger/                      ← project root (open this in VS Code)
├── tradeledger/                  ← the Python package (the application)
│   ├── __init__.py               create_app(): builds and configures the app
│   ├── config.py                 Development / Testing / Production settings
│   ├── routes.py                 URL → function mapping (/, /health)
│   ├── templates/
│   │   ├── base.html             shared layout: header, nav, footer
│   │   ├── index.html            home page (extends base.html)
│   │   └── errors/
│   │       ├── 404.html          friendly "not found" page
│   │       └── 500.html          friendly "server error" page
│   └── static/css/style.css      all styling, dark theme, responsive
├── tests/
│   ├── conftest.py               shared fixtures: app, client
│   └── test_app.py               16 Phase 1 tests
├── docs/PHASE_1_GUIDE.md         this document
├── requirements.txt              exact dependency versions
├── pytest.ini                    tells pytest where tests and code live
├── .env.example                  template for your secret settings
├── .gitignore                    what Git must never track
└── README.md                     how to install, run and test
```

Why a package folder with the same name inside the project folder? It keeps application code separate from tests, docs and config, and lets tests do `from tradeledger import create_app` cleanly. This is the layout Flask's own tutorial uses.

### Key files explained

**`tradeledger/__init__.py` — the application factory.** Rather than creating one global `app`, `create_app("testing")` builds a fresh, configured app on demand. This matters because tests need an app with test settings and, from Phase 2, a throwaway database, while your real run needs development settings. `flask --app tradeledger run` finds `create_app` automatically. It also:
- refuses to start in production without `SECRET_KEY` (fail loudly rather than run insecurely);
- in development, generates a temporary key with a warning so you are never blocked;
- registers custom 404/500 pages so users never see a stack trace;
- adds three security headers to every response (`nosniff` stops browsers guessing file types, `X-Frame-Options: DENY` blocks your pages being embedded in someone else's site for clickjacking, `Referrer-Policy` limits what URL info leaks to other sites).

**`tradeledger/config.py`.** Plain classes holding settings. Subclasses override only what differs. The secret is read with `os.environ.get("SECRET_KEY")`, so it comes from your `.env` file locally and from AWS's secret storage later — never from code.

**`tradeledger/routes.py`.** Uses a **Blueprint** (`main_bp`): a named group of routes. Hence templates use `url_for('main.index')` — blueprint name, dot, function name. Later we add a `trades` blueprint, keeping each file focused. `/health` returns JSON so monitoring tools can check the app without parsing HTML.

**`templates/base.html` + `index.html`.** Template inheritance: `base.html` defines the page frame and empty `{% block %}` slots; `index.html` says `{% extends "base.html" %}` and fills them. Every future page gets the same header/footer for free. Jinja2 **auto-escapes** variables, so if a trade note ever contains `<script>`, it is displayed as text, not run — that is your XSS protection.

**`static/css/style.css`.** Colours and spacing are CSS variables in `:root`, so the theme is defined once. `--profit` and `--loss` are already set for Phase 6. Layout uses CSS Grid with `auto-fill`/`minmax`, which reflows from several columns to one on a phone without extra code.

**`.env` and python-dotenv.** When `python-dotenv` is installed, the `flask` command loads `.env` automatically. That is the only reason it is a dependency.

---

## Part F — The code

All code is in the project folder you received. Nothing is a placeholder. The only content that refers to future phases is the roadmap list on the home page, which is display data.

---

## Part G — Setup

You need Python **3.11 or newer** and VS Code. Check Python:

- Windows PowerShell: `py --version`
- macOS / Linux: `python3 --version`

If it is missing or older, install from python.org (on Windows, tick **"Add python.exe to PATH"**).

### 1. Put the project in place

Unzip `tradeledger.zip` somewhere sensible, e.g. `Documents/projects/`. You should have `Documents/projects/tradeledger/README.md`. In VS Code: **File → Open Folder… → `tradeledger`**. Open the terminal with **Terminal → New Terminal**.

### 2. Create and activate a virtual environment

A virtual environment is a private copy of Python for this project, so its packages do not clash with other projects.

**Windows PowerShell**
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```
If you see *"running scripts is disabled on this system"*, run this once, then retry:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. In VS Code, press Ctrl/Cmd+Shift+P → **Python: Select Interpreter** → choose the one inside `.venv`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create your `.env`

Windows: `Copy-Item .env.example .env`  ·  macOS/Linux: `cp .env.example .env`

Generate a key (Windows use `py`, macOS/Linux use `python3`; inside the venv plain `python` works on both):
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the output into `.env` as the `SECRET_KEY` value.

### 5. Initialise Git and make the first commit

```bash
git init
git add .
git status
```
Check `git status` does **not** list `.env` or `.venv`. Then:
```bash
git commit -m "Phase 1: Flask app skeleton with config, routes, templates and tests"
```
To push to GitHub: create an empty repository called `tradeledger` on github.com (no README), then:
```bash
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/tradeledger.git
git push -u origin main
```

---

## Part H — Run it and what you should see

```bash
flask --app tradeledger run --debug
```

**Terminal** (the debugger PIN will differ):
```
[...] INFO in __init__: TradeLedger started with 'development' configuration
 * Serving Flask app 'tradeledger'
 * Debug mode: on
WARNING: This is a development server. Do not use it in a production deployment.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
 * Restarting with stat
 * Debugger is active!
```
The "started" line appears twice — normal: debug mode runs a second process that restarts the app when you save a file. The "development server" warning is also normal for local work.

**Browser** at http://127.0.0.1:5000:
- dark page, header with a blue "TL" mark and Home / Health links;
- "TradeLedger" heading, a short description and a green-dot pill saying *Application running · development mode*;
- a "Build progress" grid: Phase 1 *Done* (green), Phase 2 *Next* (blue border), the rest *Planned*.

http://127.0.0.1:5000/health shows `{"app": "TradeLedger", "status": "ok", "version": "0.1.0"}`.
http://127.0.0.1:5000/anything shows the custom "Page not found" page.

Stop the server with **Ctrl+C**.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `flask: command not found` / not recognised | venv not active | Activate it (step 2); prompt must show `(.venv)` |
| `ModuleNotFoundError: No module named 'flask'` | packages installed outside the venv | Activate venv, rerun `pip install -r requirements.txt` |
| `Could not import 'tradeledger'` | terminal is in the wrong folder | `cd` into the folder containing `README.md` |
| `Address already in use` / port 5000 busy (common on macOS — AirPlay Receiver) | something else is on 5000 | `flask --app tradeledger run --debug --port 5001` |
| Warning "SECRET_KEY not set" | no `.env`, or it is in the wrong folder | Create `.env` next to `README.md` |
| Page has no styling | CSS path wrong or file moved | Check `tradeledger/static/css/style.css` exists; hard-refresh (Ctrl/Cmd+Shift+R) |

---

## Part I — Testing

```bash
pytest
```

Expected (timing will differ):
```
collected 16 items

tests/test_app.py::test_testing_config_is_applied PASSED
tests/test_app.py::test_unknown_config_name_is_rejected PASSED
...
tests/test_app.py::test_security_headers_present[/missing-page] PASSED

============================== 16 passed in 0.06s ==============================
```

**What the tests prove**

| Group | Tests | Why it matters |
|---|---|---|
| Configuration | 5 | Production cannot start without a secret; dev still works without one; bad config names fail fast |
| Routes | 6 | Home, health and CSS load; health JSON is exact and leaks no secret |
| Errors | 2 | Unknown URLs get the custom 404; POST to a GET-only page is refused (405) |
| Security headers | 3 | The headers appear on normal pages, JSON and error pages alike (one parametrised test, three cases) |

**How the fixtures work.** In `conftest.py`, `app` builds a fresh app with `create_app("testing")`, and `client` wraps it in Flask's test client, which sends fake requests straight into the app with no real server. Any test that lists `client` as a parameter receives one automatically.

**`monkeypatch`** (built into pytest) temporarily changes something — here a config value — and undoes it after the test, so tests cannot pollute each other.

Useful variations:
```bash
pytest -k health           # only tests with "health" in the name
pytest -x                  # stop at the first failure
pytest --tb=short          # shorter error output
```

**Prove the tests can fail.** Change `"version": "0.1.0"` to `"0.2.0"` in the expected dictionary in `test_health_endpoint_returns_json` and rerun. It should fail and show the difference. Change it back. A test you have never seen fail is a test you have not verified.

---

## Part J — Learning exercise

Add an **About** page.

1. In `routes.py`, add a route `/about` with function name `about` that renders `about.html`.
2. Create `tradeledger/templates/about.html` that extends `base.html` and explains, in two or three sentences, what TradeLedger is for.
3. Add an About link to the nav in `base.html` using `url_for('main.about')`.
4. In `tests/test_app.py`, write `test_about_page_loads` checking for status 200 and some text from your page.
5. Run `pytest` — it should report 17 passed.
6. Commit: `git add . && git commit -m "Add About page and test"`

**Questions to answer in your own words (we will discuss them):**
1. Why does `url_for('main.about')` include `main.`?
2. Why is it safer for the app to *crash* at start-up in production without `SECRET_KEY` than to generate one automatically?
3. What would happen to users' sessions if the secret key changed every time the server restarted?

Send me your code when done and I will review it properly.

---

## Completion checklist

- [ ] `python --version` shows 3.11+
- [ ] Virtual environment created and active
- [ ] `pip install -r requirements.txt` succeeded
- [ ] `.env` created with your own `SECRET_KEY`
- [ ] `flask --app tradeledger run --debug` starts with no SECRET_KEY warning
- [ ] Home, `/health` and a 404 page display correctly
- [ ] `pytest` shows 16 passed (17 after the exercise)
- [ ] `git status` does not show `.env` or `.venv`
- [ ] First commit made (and pushed to GitHub, optionally)

---

## Part K — Next milestone: Phase 2, the SQL database

We will add `schema.sql`, `db.py` (connection handling + a `flask init-db` command) and database integration tests. Draft schema for discussion — not built yet:

```sql
CREATE TABLE instruments (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT    NOT NULL UNIQUE,          -- e.g. 'EURUSD', 'XAUUSD', 'NAS100'
    asset_class   TEXT    NOT NULL CHECK (asset_class IN ('forex','metal','index','stock','crypto','other')),
    point_value   TEXT    NOT NULL,                 -- money per 1.0 price move per 1 unit of size, stored as text for Decimal
    currency      TEXT    NOT NULL DEFAULT 'GBP'
);

CREATE TABLE trades (
    id              INTEGER PRIMARY KEY,
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id),
    direction       TEXT    NOT NULL CHECK (direction IN ('long','short')),
    opened_at       TEXT    NOT NULL,               -- ISO 8601, e.g. '2026-11-14T08:30:00'
    closed_at       TEXT,
    entry_price     TEXT    NOT NULL,
    stop_loss       TEXT    NOT NULL,
    take_profit     TEXT,
    exit_price      TEXT,                           -- NULL while the trade is open
    position_size   TEXT    NOT NULL,
    fees            TEXT    NOT NULL DEFAULT '0',
    strategy        TEXT,
    session         TEXT CHECK (session IN ('asia','london','new_york','other')),
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_trades_closed_at ON trades(closed_at);
```

Decisions to discuss before we build it:
- **Prices stored as TEXT, not REAL.** SQLite's REAL is a binary float: `0.1 + 0.2` is not exactly `0.3`. Storing text and converting to Python `Decimal` keeps prices exact. The trade-off: SQL can't do exact arithmetic on them, so money calculations happen in Python, while SQL handles counting, grouping and filtering.
- **No trade status column.** "Open" vs "closed" is derived from whether `exit_price` is NULL, so the two can never disagree.
- **Strategy as plain text for now**, not its own table — normalise when we need strategy descriptions or rules.
- **Direction-aware CHECK constraints** (stop below entry for longs, above for shorts) — enforced in SQL or Python? We'll weigh both.

First analytical queries we'll write and test: number of closed trades; trades per instrument (`JOIN` + `GROUP BY`); trades per session and per weekday (`strftime('%w', closed_at)`); the most recent 10 trades.

Before Phase 2, tell me:
1. **Your operating system** (Windows, macOS or Linux), so I can stop giving both sets of commands.
2. **Your account currency** (GBP?) and **which instruments you trade**, so the seed data and point values are realistic.

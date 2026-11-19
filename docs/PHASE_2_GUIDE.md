# TradeLedger — Phase 2 Guide: SQL Database

**Verified before delivery** (Python 3.13.18, SQLite 3.53.1, Flask 3.1.3, pytest 9.1.1, Linux):

- Full suite: **145 passed** (the 16 Phase 1 tests plus 129 new ones).
- `init-db`, `seed-db` (run twice: 16 trades the first time, 0 the second), `db-info`, `analytics` and `run-sql` were all run against a real database file. Output is shown in Part E.
- `run-sql "DELETE FROM trades"` was refused as read-only.
- With the dev server running: the home page returned 200 and showed Phase 2 as Done, `/health` returned 200 and an unknown page returned 404.
- Mutation check: deleting the LONG half of the stop-loss SQL constraint made 3 tests fail, which proves those tests really check the constraint.

**Not verified:** Windows and macOS (only Linux was used), Python 3.11 and 3.12 (only 3.13), and any VS Code extension mentioned here.

Every file's complete code is in `docs/PHASE_2_CODE.md`. That file was generated directly from the tested files, so it cannot differ from what was run.

---

## Part A — Phase 2 overview

**Plain English.** Phase 1 gave TradeLedger a shopfront with nothing in the warehouse. Phase 2 builds the warehouse: a database file that stores instruments and trades, rules that stop bad records getting in, Python functions to add, find, change and remove trades, and the first questions we can ask of the data ("how many trades did I close on Mondays?").

**Technical version.** A SQLite schema with two related tables, a connection layer integrated with Flask's application context, a validation module, repository modules (the only code that writes SQL for instruments and trades), seed data, an analytics module, and Flask CLI commands, all covered by pytest integration tests on throwaway databases.

```
                       ┌───────────────────────────┐
  flask CLI commands ──►  cli.py                   │
  (init-db, seed-db…)  └────────────┬──────────────┘
                                    │
  Flask routes (Phase 4) ──► repositories/          analytics.py   seed.py
                              instruments.py         (read-only     (sample
                              trades.py               SQL)           data)
                                    │                    │             │
                                    ▼                    ▼             ▼
                              validation.py  ◄──── every write is validated first
                                    │
                                    ▼
                              db.py  ── connect(), get_db(), init_schema()
                                    │
                                    ▼
                         instance/tradeledger.db   (schema.sql defines it)
```

**Request lifecycle with a database (ready for Phase 4):** a request arrives → a route calls `get_db()` → the first call opens a connection and stores it on `flask.g` → repository functions use it → Flask's teardown hook calls `close_db()` when the request ends, even if an error occurred.

---

## Part B — Updated project structure

```
tradeledger/
├── tradeledger/
│   ├── __init__.py               MODIFIED  create_app(config_name, overrides), database setup
│   ├── config.py                 MODIFIED  DATABASE setting, version 0.2.0
│   ├── routes.py                 MODIFIED  roadmap: Phase 2 Done, Phase 3 Next
│   ├── db.py                     NEW       connections, schema initialisation, Flask hook
│   ├── schema.sql                NEW       tables, constraints, indexes
│   ├── validation.py             NEW       input validation and normalisation
│   ├── repositories/
│   │   ├── __init__.py           NEW       DuplicateError
│   │   ├── instruments.py        NEW       instrument CRUD
│   │   └── trades.py             NEW       trade CRUD and filtering
│   ├── seed.py                   NEW       synthetic sample data
│   ├── analytics.py              NEW       six analytical SQL queries
│   ├── cli.py                    NEW       flask commands
│   ├── templates/…               unchanged
│   └── static/css/style.css      unchanged
├── tests/
│   ├── conftest.py               MODIFIED  temporary-database fixtures
│   ├── test_app.py               MODIFIED  health test reads version from config
│   ├── test_db.py                NEW       28 tests
│   ├── test_validation.py        NEW       52 tests
│   ├── test_repositories.py      NEW       30 tests
│   └── test_seed_and_analytics.py NEW      19 tests
├── docs/
│   ├── PHASE_1_GUIDE.md          unchanged
│   ├── PHASE_2_GUIDE.md          NEW       this file
│   └── PHASE_2_CODE.md           NEW       complete code listing
├── README.md                     MODIFIED
├── .env.example                  MODIFIED  optional DATABASE_PATH
├── requirements.txt              unchanged (sqlite3 is built into Python)
├── pytest.ini, .gitignore        unchanged
```

### Changes to Phase 1 code, and why

1. **`test_health_endpoint_returns_json` changed.** It hard-coded `"0.1.0"`, so bumping the version to `0.2.0` would break it. It now compares against `app.config["APP_VERSION"]`. This is a common QA lesson: a test should check behaviour, not a value that is meant to change.
2. **`create_app()` gained an `overrides` argument** so tests can point the app at a temporary database. Calls without it behave exactly as before.
3. **The testing configuration now refuses to start without an explicit `DATABASE` path.** This guarantees a test can never fall back to your real database.

### A deliberate departure from my Phase 1 draft schema

In Phase 1 I suggested working out open/closed from whether `exit_price` is empty, with no `status` column. Your Phase 2 spec asks for a `status` column, so it's included. The risk with storing both is that they could disagree. The `status_matches_exit` constraint removes that risk: SQLite refuses any row where they disagree. Column names also follow your spec (`trade_date` rather than my draft's `opened_at`).

### A bug found during development

The CLI commands worked when run with the real `flask` command but failed under the pytest CLI runner with *"Working outside of application context"*. The cause: commands registered with plain `@click.command` don't get a Flask application context automatically in every situation. The fix was Flask's `@with_appcontext` decorator, the pattern Flask's own tutorial uses. The tests caught this before delivery, which makes it a good example for an interview.

---

## Part C — Complete code

See **`docs/PHASE_2_CODE.md`**. It lists every new and modified file in full: path, purpose and complete contents.

---

## Part D — Schema explanation

### `instruments`

| Column | Meaning | Why it matters |
|---|---|---|
| `id` | Primary key | Trades refer to the instrument by this number |
| `symbol` | e.g. `EURUSD` — unique, upper case | How you name it; UNIQUE prevents duplicates |
| `name` | Human-readable name | Display only |
| `asset_class` | FOREX, COMMODITY, INDEX, STOCK, OTHER | Grouping in analytics |
| `price_increment` | Smallest price step, e.g. `0.00001` | Later: validating prices and converting price moves to pips or points |
| `contract_multiplier` | **Money per 1.0 price move per 1.0 unit of position size**, in `quote_currency` | The bridge from a price move to money: `gross P&L = move × size × multiplier` |
| `quote_currency` | Currency of the P&L | You can't add USD to GBP without an exchange rate |
| `created_at` | UTC timestamp | Audit trail |

**How these feed the P&L calculation.** EUR/USD moves from 1.08500 to 1.08900, a move of 0.004. If your position size is 1 *standard lot* and your broker defines that as 100,000 euros, then each 1.0 price move is worth 100,000 USD, so `0.004 × 1 × 100000 = 400 USD`. If instead you record position size in *units*, the multiplier is 1 and size is 100000. The answer is the same; what matters is that size and multiplier use **matching definitions**. That's why there's no universal "point value": it depends on what your broker calls one unit. **The sample multipliers are illustrative assumptions only.** Check your broker's contract specifications before recording real trades.

### `trades`

| Column | Rule | Enforced in |
|---|---|---|
| `instrument_id` | Must reference a real instrument | SQL foreign key + friendly Python error |
| `trade_date` | ISO 8601 UTC `YYYY-MM-DDTHH:MM:SSZ` | Python parses and validates; SQL checks the shape |
| `direction` | LONG or SHORT | Both |
| `entry_price`, `stop_loss` | Greater than 0 | Both |
| `take_profit`, `exit_price` | Optional; greater than 0 if given | Both |
| `position_size` | Greater than 0 | Both |
| `fees` | 0 or more, default 0 | Both |
| `status` | OPEN or CLOSED, consistent with `exit_price` | Both |
| stop-loss side | LONG: stop < entry. SHORT: stop > entry. Never equal (zero risk breaks R-multiples) | Both |
| take-profit side | LONG: target > entry. SHORT: target < entry | Both |
| `strategy` | Optional free text, at most 50 characters | Both |
| `session` | Optional: ASIA, LONDON, NEW_YORK, OTHER | Both |
| `notes` | Optional, at most 2000 characters | Both |
| `created_at`, `updated_at` | UTC; `updated_at` is set on every update | SQL default + repository |

**Relationship:** one instrument → many trades. `ON DELETE RESTRICT` means you can't delete an instrument that still has trades; SQLite refuses rather than leaving orphaned trades.

**Indexes:** on `instrument_id`, `trade_date` and `status`, the columns we filter on most.

### What belongs in SQL vs Python

| Only Python can do it well | SQL should *also* enforce it |
|---|---|
| Parse many date formats and convert time zones to UTC | Allowed values (direction, status, session) |
| Friendly, field-by-field messages for a form | Not-null, uniqueness, foreign keys |
| Reject floats before they lose precision | Numbers positive; stop and target on the correct side |
| Reject values with more than 15 significant digits | Status consistent with exit price |
| Report *all* errors at once | Timestamp shape |

Rule of thumb: **SQL protects the data; Python protects the user.** SQL constraints hold even if a future bug, script or manual edit skips validation. Python gives good messages and handles anything SQL can't express easily.

### Precision: how numbers are stored

| Option | Exact? | Pros | Cons |
|---|---|---|---|
| `REAL` (float) | No | Simple, SQL arithmetic works | `0.1 + 0.2 ≠ 0.3`; errors accumulate in money calculations |
| Scaled integers (e.g. price × 100000) | Yes | Fast, SQL arithmetic stays exact | Each instrument needs its own scale; fees and sizes need different scales; easy to mix up |
| **Canonical decimal TEXT** (`'1.085'`) | Yes | Exact; human-readable in any DB browser; maps directly to Python `Decimal` and later to PostgreSQL `NUMERIC` | SQL can't do exact arithmetic on it |

**Choice: canonical decimal text.** Python converts input with `Decimal`, rejects floats, and stores a normalised form with no exponent or trailing zeros (`'1.08500'` → `'1.085'`). Normalising matters because otherwise `'1.085'` and `'1.08500'` would be unequal text.

**Limitations, stated honestly:**
1. SQL comparisons in CHECK constraints use `CAST(... AS REAL)`. That keeps the ordering correct for values with at most 15 significant digits, which is why Python rejects anything longer.
2. Any SQL that does arithmetic (the win-rate query) is approximate. It uses a tiny breakeven tolerance (0.000001), and a test checks it against exact `Decimal` results. Phase 3 does the authoritative maths in Python.
3. The SQL text checks are a safety net, not a complete parser. For example, `'1.2.3'` would pass the character check. Python validation is the gate that guarantees canonical values.

When we move to PostgreSQL, these columns become `NUMERIC`, which is exact *inside* the database, and limitations 1 and 2 disappear.

### Date and time convention

Stored: always UTC, `2026-03-02T08:15:00Z`. Python accepts `2026-03-02T08:15:00Z`, `2026-03-02 08:15` (treated as UTC) or `2026-03-02T09:15:00+01:00` (converted to UTC). A bare date is rejected because a trade needs a time. Because every value uses the same fixed-width format, sorting the text alphabetically sorts by time, so `ORDER BY trade_date` and range filters work without date functions.

**Known consequence:** day-of-week analysis uses the UTC date. A trade at 23:30 New York time on a Monday is 04:30 UTC on Tuesday, so it counts as Tuesday. We'll revisit this if you want analysis in your local trading day.

---

## Part E — SQL analytics

Run all of them with `flask --app tradeledger analytics`, or one with `flask --app tradeledger analytics by-weekday`. The SQL is in `tradeledger/analytics.py`. Results below are **real output** from the sample data.

### The sample data

16 synthetic trades. Net P&L = gross − fees, in the quote currency.

| # | Instrument | Dir | Day | Session | Strategy | Result |
|---|---|---|---|---|---|---|
| 1 | EURUSD | LONG | Mon | LONDON | Breakout | WIN +393.00 USD |
| 2 | EURUSD | SHORT | Tue | NEW_YORK | Pullback | LOSS −103.50 USD |
| 3 | GBPUSD | LONG | Wed | LONDON | Breakout | BREAKEVEN 0.00 |
| 4 | GBPUSD | SHORT | Thu | NEW_YORK | Reversal | WIN +293.00 USD |
| 5 | XAUUSD | LONG | Fri | NEW_YORK | Breakout | WIN +198.00 USD |
| 6 | XAUUSD | SHORT | Mon | ASIA | Reversal | LOSS −133.00 USD |
| 7 | NAS100 | LONG | Tue | NEW_YORK | Pullback | WIN +198.50 USD |
| 8 | NAS100 | SHORT | Wed | NEW_YORK | Breakout | LOSS −81.50 USD |
| 9 | EURUSD | LONG | Thu | LONDON | Pullback | **LOSS −5.00 USD** (price up, fees bigger) |
| 10 | UK100 | LONG | Fri | LONDON | Breakout | WIN +200.00 GBP |
| 11 | UK100 | SHORT | Mon | LONDON | — | BREAKEVEN 0.00 |
| 12 | GBPUSD | LONG | Tue | LONDON | Pullback | LOSS −157.00 USD |
| 13 | XAUUSD | LONG | Wed | ASIA | Pullback | WIN +158.00 USD |
| 14 | NAS100 | LONG | Thu | NEW_YORK | Breakout | OPEN |
| 15 | EURUSD | SHORT | Fri | LONDON | Reversal | OPEN |
| 16 | XAUUSD | SHORT | Fri | — | — | WIN +38.00 USD |

Trade 9 is the important one. A naive "exit above entry = win" rule calls it a win; net P&L correctly calls it a loss.

### 1. Total closed trades

```sql
SELECT COUNT(*) AS closed_trades
FROM trades
WHERE status = 'CLOSED';
```
Counts rows that pass the filter. **Result:** `14`.

### 2. Closed trades by instrument

```sql
SELECT i.symbol,
       COUNT(t.id) AS closed_trades
FROM instruments AS i
LEFT JOIN trades AS t
       ON t.instrument_id = i.id
      AND t.status = 'CLOSED'
GROUP BY i.id, i.symbol
ORDER BY closed_trades DESC, i.symbol;
```
A `LEFT JOIN` keeps every instrument, even one with no matching trades. Putting `status = 'CLOSED'` in the **ON** clause instead of WHERE is deliberate: in WHERE it would remove the instruments that have no trades and the zero rows would vanish. `COUNT(t.id)` counts only matched trades (it ignores NULLs), so an unmatched instrument shows 0. A test proves this with a US30 instrument that has no trades.

```
symbol  closed_trades
XAUUSD  4
EURUSD  3
GBPUSD  3
NAS100  2
UK100   2
```

### 3. Closed trades by day of the week

```sql
SELECT CAST(strftime('%w', trade_date) AS INTEGER) AS weekday_number,
       CASE strftime('%w', trade_date)
            WHEN '0' THEN 'Sunday'    WHEN '1' THEN 'Monday'
            WHEN '2' THEN 'Tuesday'   WHEN '3' THEN 'Wednesday'
            WHEN '4' THEN 'Thursday'  WHEN '5' THEN 'Friday'
            ELSE 'Saturday'
       END AS weekday,
       COUNT(*) AS closed_trades
FROM trades
WHERE status = 'CLOSED'
GROUP BY weekday_number
ORDER BY weekday_number;
```
`strftime('%w')` returns 0 (Sunday) to 6 (Saturday). `CASE` turns numbers into names. Grouping by the number keeps the days in calendar order.

```
weekday_number  weekday    closed_trades
1               Monday     3
2               Tuesday    3
3               Wednesday  3
4               Thursday   2
5               Friday     3
```

### 4. Trades by strategy

```sql
SELECT COALESCE(strategy, 'Unspecified') AS strategy,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
GROUP BY COALESCE(strategy, 'Unspecified')
ORDER BY total_trades DESC, strategy;
```
`COALESCE` replaces NULL with a label so untagged trades form their own group. `SUM(CASE … THEN 1 ELSE 0 END)` is the standard SQL trick for counting rows that meet a condition within each group.

```
strategy     total_trades  open_trades  closed_trades
Breakout     6             1            5
Pullback     5             0            5
Reversal     3             1            2
Unspecified  2             0            2
```

### 5. Trades by session

The same pattern as query 4, grouped by `session`.

```
session      total_trades  open_trades  closed_trades
LONDON       7             1            6
NEW_YORK     6             1            5
ASIA         2             0            2
Unspecified  1             0            1
```

### 6. Win rate by instrument (net P&L based)

**Assumptions:** closed trades only; net P&L = direction-adjusted price move × size × contract multiplier − fees; fees are in the quote currency; win means net > 0, loss means net < 0, breakeven means net = 0 (within 0.000001); **win rate = wins ÷ all closed trades** (breakevens stay in the denominator: you took those trades).

```sql
WITH closed AS (
    SELECT i.symbol,
           i.quote_currency,
           CASE t.direction
                WHEN 'LONG'  THEN CAST(t.exit_price AS REAL) - CAST(t.entry_price AS REAL)
                ELSE              CAST(t.entry_price AS REAL) - CAST(t.exit_price AS REAL)
           END
           * CAST(t.position_size AS REAL)
           * CAST(i.contract_multiplier AS REAL)
           - CAST(t.fees AS REAL) AS net_pnl
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
    WHERE t.status = 'CLOSED'
),
classified AS (
    SELECT symbol,
           quote_currency,
           CASE WHEN net_pnl >  0.000001 THEN 'WIN'
                WHEN net_pnl < -0.000001 THEN 'LOSS'
                ELSE 'BREAKEVEN'
           END AS outcome
    FROM closed
)
SELECT symbol,
       quote_currency,
       COUNT(*)                                             AS closed_trades,
       SUM(CASE WHEN outcome = 'WIN'       THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN outcome = 'LOSS'      THEN 1 ELSE 0 END) AS losses,
       SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) AS breakevens,
       ROUND(100.0 * SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                            AS win_rate_pct
FROM classified
GROUP BY symbol, quote_currency
ORDER BY win_rate_pct DESC, symbol;
```

A **CTE** (`WITH name AS (...)`) is a named temporary result you can build on, like a variable in Python. Step 1 calculates net P&L per trade, step 2 labels each trade, and step 3 counts per instrument. `100.0` (not `100`) forces decimal division; integer division would give 0 or 100.

```
symbol  quote_currency  closed_trades  wins  losses  breakevens  win_rate_pct
XAUUSD  USD             4              3     1       0           75.0
NAS100  USD             2              1     1       0           50.0
UK100   GBP             2              1     0       1           50.0
EURUSD  USD             3              1     2       0           33.3
GBPUSD  USD             3              1     1       1           33.3
```

**Why mixing USD and GBP is acceptable here but not for totals:** converting a profit into another currency never turns it into a loss, so each trade's win/loss label is correct in any currency. *Adding up* USD and GBP P&L would be meaningless without exchange rates, so no query does that.

**Caution on the numbers:** two or three trades per instrument says almost nothing statistically. Phase 3 will report sample sizes alongside every rate.

### Running your own queries

| Method | Command | Works on |
|---|---|---|
| TradeLedger (read-only) | `flask --app tradeledger run-sql "SELECT * FROM instruments"` | Everywhere |
| Python's SQLite shell | `python -m sqlite3 instance/tradeledger.db` | Python 3.12+ only |
| sqlite3 CLI | `sqlite3 instance/tradeledger.db` | Pre-installed on macOS and most Linux; Windows needs a download from sqlite.org |
| VS Code | Install a SQLite viewer extension (search Extensions for "SQLite"), then click `instance/tradeledger.db` | Any OS; I haven't tested a specific extension |

In the sqlite3 shells, `.tables` lists tables, `.schema trades` shows the definition, `.mode column` then `.headers on` makes output readable, and `.quit` exits.

---

## Part F — Setup and execution

Open the project folder in VS Code, then open the terminal (**Terminal → New Terminal**) and activate the virtual environment:

| | Windows PowerShell | macOS / Linux |
|---|---|---|
| Activate | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

**No new dependencies.** `sqlite3` ships with Python. If you've set up a new environment, run `pip install -r requirements.txt`.

The commands below are identical on every OS once the venv is active:

```bash
# 1. Create the database tables
flask --app tradeledger init-db
# Database ready: …/instance/tradeledger.db

# 2. Insert sample data, then run it again to see idempotency
flask --app tradeledger seed-db
# Sample data: 5 instrument(s) and 16 trade(s) inserted.
flask --app tradeledger seed-db
# Sample data: 0 instrument(s) and 0 trade(s) inserted.

# 3. Inspect
flask --app tradeledger db-info
flask --app tradeledger analytics
flask --app tradeledger run-sql "SELECT id, trade_date, direction, entry_price, exit_price, status FROM trades ORDER BY trade_date"

# 4. Try something the database must refuse
flask --app tradeledger run-sql "DELETE FROM trades"
# Error: SQL error: attempt to write a readonly database

# 5. Run the full test suite
pytest

# 6. Check Phase 1 still works
flask --app tradeledger run --debug
# Browse http://127.0.0.1:5000  (roadmap shows Phase 2 Done)
#        http://127.0.0.1:5000/health  (version 0.2.0)
```

**Quoting on Windows PowerShell:** wrap SQL in double quotes and use single quotes inside it, e.g. `run-sql "SELECT * FROM trades WHERE status = 'OPEN'"`. The same command works in macOS and Linux shells.

**Starting over:** `flask --app tradeledger reset-db` asks for confirmation and then empties everything.

**Try the repository functions yourself** in a Python shell (type `python` with the venv active):

```python
from tradeledger.db import connect
from tradeledger.repositories.trades import list_trades, get_trade
conn = connect("instance/tradeledger.db")
for t in list_trades(conn, start="2026-03-16", end="2026-03-18"):
    print(t["trade_date"], t["symbol"], t["direction"], t["status"])
get_trade(conn, 9)
conn.close()
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no such table: trades` | Database not initialised | `flask --app tradeledger init-db` |
| `Database file not found` from `run-sql` | Same | Same |
| `No such command 'init-db'` | Wrong folder, or `--app tradeledger` missing | `cd` to the folder with `README.md`; include `--app tradeledger` |
| `RuntimeError: …needs an explicit DATABASE path` | Ran with `TRADELEDGER_CONFIG=testing` | Set it back to `development` in `.env` |
| `database is locked` | A DB viewer has the file open mid-write | Close the viewer or its transaction, then retry |
| Tests can't find `tradeledger` | `pytest` run from the wrong folder | Run from the project root, where `pytest.ini` is |

### Git

```bash
git add .
git status        # confirm instance/ and .env are NOT listed
git commit -m "Phase 2: SQLite schema, repositories, validation, seed data, analytics and tests"
git push
```

---

## Part G — Automated testing

```bash
pytest                                  # everything
pytest tests/test_db.py                 # one file
pytest -k "weekday or win_rate"         # by name
pytest -q                               # compact output
```

**Actual result before delivery:**
```
tests/test_app.py ................                                       [ 11%]
tests/test_db.py ............................                            [ 30%]
tests/test_repositories.py ..............................                [ 51%]
tests/test_seed_and_analytics.py ...................                     [ 64%]
tests/test_validation.py ............................................... [ 96%]
.....                                                                    [100%]
============================= 145 passed in 0.77s ==============================
```

| File | Tests | Covers |
|---|---|---|
| `test_app.py` | 16 | Phase 1 (unchanged except the version check) |
| `test_db.py` | 28 | Tables, indexes, schema version, foreign keys, Flask connection lifecycle, raw-SQL constraint enforcement, commit and rollback |
| `test_validation.py` | 52 | Decimal parsing, float rejection, timestamps and time zones, date ranges, every trade rule |
| `test_repositories.py` | 30 | Instrument and trade CRUD, duplicates, unknown instrument, nothing written on invalid input, filters, an SQL-injection attempt |
| `test_seed_and_analytics.py` | 19 | Seeding, idempotency, all-or-nothing rollback, every query on empty and sample data, SQL vs Decimal cross-check, CLI commands |

**Test isolation.** Each test gets pytest's `tmp_path`, a brand-new temporary folder, so each test has its own database file that's deleted afterwards. Tests can't affect each other or your `instance/` data.

**Coverage of your required list:** initialisation ✓, table creation ✓, foreign keys ✓, instrument creation ✓, duplicate rejection ✓, insert, retrieve, update and delete ✓, invalid prices, sizes, direction and status ✓, missing exit on closed ✓, stop-loss placement ✓, dates ✓, instrument and date-range filtering ✓, transactions ✓, analytics results ✓, seed idempotency ✓.

---

## Part H — Documentation and package

`tradeledger_phase2.zip` contains the whole runnable project. It excludes `.env`, `.venv`, `instance/` (the database) and caches. Unzip it over your Phase 1 folder, or into a fresh one, and follow Part F.

---

## Part I — Learning walkthrough

Each idea: plain English first, then the technical version, then where it lives in TradeLedger.

### Relational database
**Plain:** a set of spreadsheets (tables) with strict column rules, where rows in one sheet point to rows in another by ID instead of copying their details.
**Technical:** data organised into relations (tables) of tuples (rows) with typed attributes (columns), linked by keys, and queried declaratively with SQL: you say *what* you want, not *how* to fetch it.
**TradeLedger:** a trade stores `instrument_id = 3`, not "Gold / US Dollar, 100, USD…". Fix the gold multiplier once and every gold trade sees it. Avoiding that duplication is **normalisation**.

### Primary keys
**Plain:** a row's permanent ID number, like an order number.
**Technical:** a column whose values are unique and never NULL. In SQLite, `INTEGER PRIMARY KEY` is an alias for the internal row ID, so it's assigned automatically.
**Why not use the symbol as the key?** Symbols can change (brokers rename `NAS100` to `USTEC`); an ID never needs to. That's a *surrogate* key versus a *natural* key. We still mark `symbol` as `UNIQUE`.

### Foreign keys
**Plain:** "this trade belongs to that instrument, and the database won't let the link break".
**Technical:** `instrument_id INTEGER NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT`. Inserting a trade for instrument 999 fails, and deleting an instrument that has trades fails.
**SQLite gotcha:** foreign keys are **off by default**, per connection. That's why `connect()` always runs `PRAGMA foreign_keys = ON`, and why `test_foreign_keys_are_switched_on` exists. Forgetting this is one of the most common SQLite bugs.

### Constraints
**Plain:** rules the database enforces no matter who is writing.
**Technical:** `NOT NULL`, `UNIQUE`, `CHECK (...)`, `FOREIGN KEY`. A row that violates any of them is rejected with `IntegrityError`, and the transaction can be rolled back.
**Why both SQL and Python?** In `test_db.py`, `test_sql_rejects_invalid_raw_trades` inserts bad rows *directly*, skipping Python, and SQLite still refuses them. That's defence in depth: a future script, a manual fix in a DB browser, or a bug in Phase 4 can't corrupt your journal.

### SQL injection and parameterised queries
**Plain:** if you build SQL by gluing user text into a string, a user can type SQL that changes your command.
**Dangerous (never do this):**
```python
conn.execute(f"SELECT * FROM instruments WHERE symbol = '{symbol}'")
# symbol = "X' OR '1'='1"  ->  WHERE symbol = 'X' OR '1'='1'  ->  every row
```
**Safe (what TradeLedger does):**
```python
conn.execute("SELECT * FROM instruments WHERE symbol = ?", (symbol,))
```
**Technical:** the SQL text and the values travel separately. SQLite compiles the statement first; values are bound afterwards purely as data, so they can never become SQL syntax. `test_sql_injection_attempt_is_treated_as_plain_data` proves it.
**Subtlety you'll be asked about:** in `list_trades` the WHERE clause *is* built dynamically, but only from fixed text fragments written in the code (`"t.instrument_id = ?"`). User values only ever go into `params`. Placeholders can't stand in for table or column names. In `cli.py`, `db-info` puts table names into SQL text, but those names come from SQLite's own catalogue, not from a user, and the code comments say so.

### Transactions
**Plain:** "all of this happens, or none of it does".
**Technical:** `BEGIN … COMMIT`, or `ROLLBACK` on failure. Atomicity is the A in ACID.
**In Python:** `with conn:` commits if the block finishes and rolls back if an exception escapes it:
```python
with conn:
    conn.execute("INSERT ...")
    conn.execute("INSERT ...")   # if this fails, the first insert is undone too
```
**TradeLedger:** `seed_sample_data` writes 21 rows in one transaction. `test_seed_is_all_or_nothing` adds an invalid trade at the end and checks that *zero* rows remain, not 21 half-written ones.
**One trap avoided:** `with conn:` blocks don't nest the way you'd expect; an inner one commits everything so far. That's why `seed.py` writes its own INSERTs inside a single block instead of calling `create_trade()` (which has its own block) in a loop.

### How Flask connects to SQLite
1. `create_app()` works out the database path (`instance/tradeledger.db` by default) and calls `db.init_app(app)`.
2. `init_app` registers `close_db` with `app.teardown_appcontext`, so Flask calls it when every request or CLI command ends.
3. The first `get_db()` call in a request opens a connection and stores it on `g` (Flask's per-request scratchpad); later calls reuse it.
4. Teardown pops it from `g` and closes it, even if the request crashed.
Tested by `test_get_db_reuses_one_connection_per_app_context` and `test_connection_is_closed_when_app_context_ends`.

Why not one global connection for the whole app? The development server can run requests in several threads, and a sqlite3 connection must not be shared across threads by default. One connection per request avoids that problem.

### Repository functions
**Plain:** a librarian. You ask "fetch trade 9" or "store this trade"; you don't walk into the stacks yourself.
**Technical:** a module that encapsulates all data access for one entity behind a small set of functions with predictable inputs, outputs and errors (listed at the top of each repository file). They take a connection as an argument instead of calling `get_db()` themselves. That's **dependency injection**: the same functions work from a Flask route, a CLI command or a test with a temporary database.

### Indexes
**Plain:** the index at the back of a book, so you can find "gold" without reading every page.
**Technical:** a separate sorted structure (a B-tree) mapping column values to rows. Lookups go from scanning every row to a quick search. The cost: slightly slower writes and extra disk space.
**Honest note:** with 16 trades an index makes no measurable difference. It matters at thousands of rows. We add them now because the columns we filter by are known. Prove it yourself: `run-sql "EXPLAIN QUERY PLAN SELECT * FROM trades WHERE trade_date >= '2026-03-10T00:00:00Z'"` should mention `USING INDEX idx_trades_trade_date`.

### Why database code is separate from routes
- **Testability:** 129 database tests ran without starting a web server.
- **Reuse:** the CLI, the future web pages and a future JSON API all call the same `create_trade()`.
- **Changeability:** moving to PostgreSQL means changing `db.py` and some SQL in the repositories. Routes and templates won't notice.
- **Security:** one place to review for SQL injection.

### SQLite vs PostgreSQL

| | SQLite | PostgreSQL |
|---|---|---|
| What it is | A library; the database is one file | A separate server process |
| Setup | None | Install, users, passwords, network |
| Concurrency | One writer at a time | Many simultaneous writers |
| Exact decimals | No (hence text storage) | `NUMERIC` type |
| Dates | Text with functions | Native `TIMESTAMPTZ` |
| Types | Flexible by default | Strict |
| Best for | Single user, local, tests, small apps | Multi-user web apps, deployed systems |

**When to switch:** when TradeLedger has several users writing at once, or when we deploy somewhere with an ephemeral filesystem where the database file would vanish on redeploy. Until then SQLite is the right tool, and saying so in an interview shows judgement.

### Writing and testing analytical SQL
1. **Define terms first** (what is a win? what is in the denominator?), in writing.
2. **Build sample data where you know the answers**, including awkward cases (trade 9).
3. **Work out the expected result by hand** before running the query.
4. **Write the query in steps** (CTEs), checking each step with `run-sql`.
5. **Test empty data** (no trades), **zero cases** (an instrument with no trades) and **edge cases** (breakevens, fees).
6. **Cross-check** approximate SQL against an exact method, as `test_sql_win_rate_agrees_with_exact_decimal_calculation` does.

---

## Part J — Your practical exercise

**Goal:** add US30 and write a query for its trades.

1. In a Python shell (venv active, project folder):
   ```python
   from tradeledger.db import connect
   from tradeledger.repositories.instruments import create_instrument
   from tradeledger.repositories.trades import create_trade
   conn = connect("instance/tradeledger.db")
   us30 = create_instrument(conn, {
       "symbol": "US30", "name": "Wall Street 30 CFD (my test)",
       "asset_class": "INDEX", "price_increment": "1",
       "contract_multiplier": "1", "quote_currency": "USD"})
   ```
2. Add **two** US30 trades with `create_trade`: one closed LONG win and one OPEN SHORT. Use invented prices.
3. **Deliberately** try a SHORT with its stop *below* entry. Read the `ValidationError`, and write down which file and which line produced the message.
4. Write SQL that lists every US30 trade (date, direction, entry, exit, status) using a **JOIN** on `instruments` and filtering by `symbol`, not by ID. Run it with `run-sql`.
5. Run `flask --app tradeledger analytics by-instrument` and confirm US30 now appears with the right closed count.
6. **Stretch:** add a test to `tests/test_repositories.py` that creates US30 plus one trade and asserts that `list_trades(conn, instrument_id=...)` returns exactly that trade. Run `pytest` and aim for 146 passed.

**Questions to answer in your own words:**
1. Why would `WHERE t.status = 'CLOSED'` in query 2 hide US30 before it has any closed trades, when the ON-clause version doesn't?
2. Trade 9 closed *above* entry on a long. Why is it a loss, and what would a "price-only" win rate have said?
3. Why does `connect()` turn on foreign keys every time instead of once when the database is created?

Send me your SQL, test and answers, and I'll review them.

---

## Part K — Next phase (Phase 3: Python trading logic)

Phase 3 builds `tradeledger/calculations.py`: pure functions with no database and no Flask, using `Decimal`. They take the trade dictionaries that `list_trades()` already returns:

- **Per trade:** gross P&L, net P&L, outcome, risk per unit (`|entry − stop|`), planned R:R (`|target − entry| / |entry − stop|`), realised R-multiple (`direction-adjusted move / |entry − stop|`).
- **Across trades:** win, loss and breakeven rates; average win and loss; average R; profit factor (gross profit ÷ |gross loss|, undefined with no losses); expectancy; equity curve; maximum drawdown; longest streaks.
- **Rules:** results are grouped by quote currency, and money is never summed across currencies; every rate comes with its sample size; division by zero returns `None` with a reason instead of crashing.

One decision is needed from you before Phase 3. Is "expectancy" in **R** (currency-neutral, comparable across instruments) or **money per trade** (needs a single currency)? I recommend R as the primary measure, with money shown per currency.

Phase 2 completion checklist (all verified on Linux; tick them on your machine):
- [ ] App launches; home page and `/health` work
- [ ] `init-db` creates both tables
- [ ] `seed-db` adds 16 trades the first time and 0 the second
- [ ] `analytics` shows the results above
- [ ] `run-sql "DELETE FROM trades"` is refused
- [ ] `pytest` shows 145 passed
- [ ] `git status` doesn't list `instance/` or `.env`
- [ ] Phase 2 committed

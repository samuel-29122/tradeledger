# TradeLedger — Phase 4 Guide: Flask Backend, REST API and Migration

## Verification summary

Run before delivery on Linux, with Python 3.13.18, SQLite 3.53.1, Flask 3.1.3, Werkzeug 3.1.9, pytest 9.1.1 and jsonschema 4.26.0. All results are in Part H.

- **Full suite: 512 passed.** That is the 261 Phase 1–3 tests plus 251 new ones.
- **Live server, disposable database:** created an instrument, then a trade, then retrieved, replaced and analysed it, deleted it, and confirmed it was gone. Invalid requests, missing resources and the website routes were checked too.
- **Production configuration:** the API is refused by default (403), and with it explicitly enabled, no error response leaked internals.
- **Live migration** of a database built from the exact Phase 3 schema: a backup was written, the schema moved from version 1 to 2, a second run did nothing, and every value was preserved with no exit time invented.
- **Clean-ZIP check:** the ZIP was extracted into an empty folder and the full suite run there.
- **Mutation checks** (deliberate bugs, all caught):
  - an extra response field fails 2 tests;
  - an undocumented route fails 1;
  - removing `nullable` from `realised_r` in the spec fails 2;
  - removing the unknown-field (mass-assignment) check fails 6.

**Not verified:** Windows and macOS; Python 3.11 and 3.12; the PowerShell examples in Part G, which are written carefully but were not run, because only Linux was available; and validation by a dedicated OpenAPI tool (schemas are checked with `jsonschema` against real responses instead). The 500 error handler is verified through tests with a deliberately failing route, not by crashing a live server.

**Three bugs found during development, all fixed:**

1. In the migration SQL I wrote two adjacent string literals, a Python habit. SQL doesn't join them, so every database setup failed. The first test run caught it immediately.
2. An OpenAPI mutation check showed that no response test ever produced a `null` realised R. The database's stop-loss constraint makes that impossible from stored data, and one test's docstring wrongly claimed to cover it. I added a test that serialises an invalid-stop trade directly.
3. A test reloaded the config module with `importlib.reload`, which can leave other modules holding stale class objects. I replaced it with a small testable function, `env_flag()`.

---

## Part A — Overview

**Plain English.** Until now you could use TradeLedger only through Python or the terminal. Phase 4 gives it a proper "front door" for other programs: a web API. Any client — the web pages in Phase 5, a phone app, a script — can now create, read, change and delete trades and ask for performance statistics by sending HTTP requests and getting JSON back.

**Technical version.** A versioned Flask Blueprint at `/api/v1`, separate from the website Blueprint. Requests flow:

```
HTTP request
  → route in api/trades.py, instruments.py or analytics.py   (thin: ~5 lines each)
  → api/http.py        shape checks: JSON? allowed fields? valid query params?
  → repositories/      validation.py business rules + parameterised SQL in a transaction
  → SQLite
  → performance/       build_report() for analytics (no maths in routes)
  → api/serialise.py   Decimal → exact string, explicit field lists
  → JSON response      or api/errors.py's single error format
```

| Module | Why it exists |
|---|---|
| `api/__init__.py` | Creates the Blueprint, refuses requests when the API is disabled, adds `Cache-Control: no-store`, turns `ValidationError` / `DuplicateError` / `ApiError` into JSON errors |
| `api/errors.py` | One error shape and one place that logs errors, so no handler can leak details |
| `api/http.py` | HTTP-specific checks that don't belong in `validation.py` (content type, JSON parsing, allowed field names, page numbers) |
| `api/serialise.py` | The public shape of each resource. An explicit field list means a new database column never appears in the API by accident |
| `api/trades.py`, `instruments.py`, `analytics.py` | The routes, one file per resource |
| `api/openapi.py` | The specification, built in Python so tests can compare it with the code |
| `migrations.py` | Versioned schema changes for existing databases |

**What I deliberately did not add:** no service layer (routes call repositories directly; for an app this size another layer would only forward calls); no ORM, no Marshmallow or Pydantic, no Swagger UI package. Each would be a dependency without a real benefit yet.

### Changes to existing code and tests, with justification

| Change | Why |
|---|---|
| `schema.sql` no longer sets `PRAGMA user_version = 1` | It is now the version-1 baseline; `init_schema()` records the version and applies migrations. Leaving the pragma in would reset a migrated database to version 1 on every `init-db`. |
| `db.drop_all()` resets the version to 0 | Otherwise `reset-db` followed by `init-db` would skip migration 2. Found while designing it; locked in by `test_reset_then_init_gives_a_fully_migrated_schema`. |
| `validate_trade()` requires `closed_at` for CLOSED trades | New model rule. `require_closed_at=False` is used only when editing a legacy trade. |
| Engine orders by `realised_at` (exit time, or entry for legacy) | The Phase 4 requirement for accurate chronology |
| `build_report()` date filters apply to the realised date | "March results" means trades closed in March |
| Sample trades have synthetic same-day exit times, in entry order | Keeps every Phase 2–3 expected result valid; it is invented data, so nothing is fabricated |
| **Test** `test_schema_version_is_recorded`: `1` → `LATEST_VERSION == 2`. **Test** `test_cli_init_seed_and_info`: "Schema version: 1" → 2 | The schema version really changed |
| **Fixture** `long_trade` and two inputs in `test_validation.py` gained `closed_at`; `test_update_can_reopen_a_trade` also sets `closed_at=None` | These build CLOSED trades, which now need an exit time. Every assertion is unchanged. |
| **Test** `test_report_serialises_to_json_without_floats`: the expected curve point gained `realised_at` and `close_time_known` | New fields. Every original field and value is still asserted. |

No test was deleted and no assertion was loosened.

---

## Part B — API design

Base URL: `/api/v1`. All bodies are JSON. The full contract is in `docs/openapi.json`.

| Method | Endpoint | Success | Errors | Notes |
|---|---|---|---|---|
| GET | `/trades` | 200 | 400 | Filters `instrument_id`, `status`, `start`, `end` (entry date); `page` (≥1), `per_page` (1–100, default 20) |
| POST | `/trades` | 201 + `Location` | 400, 413, 415 | Body: TradeInput |
| GET | `/trades/{id}` | 200 | 404 | Includes the calculated `result` for CLOSED trades |
| PUT | `/trades/{id}` | 200 | 400, 404, 413, 415 | **Every** field must be present (null for empty ones) |
| DELETE | `/trades/{id}` | 204, no body | 404 | |
| GET | `/instruments` | 200 | 400 | Sorted by symbol, not paginated |
| GET | `/instruments/{id}` | 200 | 404 | |
| POST | `/instruments` | 201 + `Location` | 400, 409, 413, 415 | 409 = symbol already exists (case-insensitive) |
| GET | `/analytics/summary` | 200 | 400 | Filters `instrument_id`, `start`, `end` (**realised** date) |
| GET | `/analytics/instruments` · `strategies` · `sessions` · `weekday` · `monthly` | 200 | 400 | A list of `{group, ...Summary}` in a stable order |
| GET | `/analytics/curve` | 200 | 400 | Cumulative realised R; labelled "NOT an account equity curve" |
| GET | `/openapi.json` | 200 | — | Available even when the API is disabled |

Any endpoint can also return 403 (API disabled), 405 (wrong method, with an `Allow` header) or 500 (generic, no internals).

### Field contract

**Trade response:** `id`, `instrument_id`, `symbol`, `quote_currency`, `trade_date`, `closed_at` (null if open or legacy), `direction`, `status`, `entry_price`, `stop_loss`, `take_profit` (null), `exit_price` (null), `position_size`, `fees`, `strategy` (null), `session` (null), `notes` (null), `created_at`, `updated_at`, plus `result`. For a CLOSED trade, `result` holds `outcome`, `gross_pnl`, `fees`, `net_pnl`, `initial_risk` (null), `realised_r` (null), `planned_rr` (null) and `r_unavailable_reason` (null). For an OPEN trade, `result` is null.

| JSON type | Fields |
|---|---|
| integer | ids, counts (`trades`, `wins`, `r_trades`, streaks, `open_trades`) |
| string, exact decimal | every price, size, fee, money value, R, ratio and rate |
| string, UTC timestamp | `trade_date`, `closed_at`, `created_at`, `updated_at`, `realised_at` |
| boolean | `close_time_known` |
| null | anything that cannot be calculated or was not recorded |

**Trade input** (`POST`; for `PUT` every key is required): `instrument_id`, `trade_date`, `closed_at`, `direction`, `status`, `entry_price`, `stop_loss`, `take_profit`, `exit_price`, `position_size`, `fees`, `strategy`, `session`, `notes`. Any other key returns 400 `unknown_fields`.

Decimals may be sent as strings (recommended) or JSON numbers. Numbers are parsed straight into `Decimal`, never `float`, and `NaN`/`Infinity` are rejected.

**Instrument fields:**
- `symbol`: unique and upper-cased.
- `asset_class`: FOREX, COMMODITY, INDEX, STOCK or OTHER.
- `price_increment`: the smallest price step.
- `contract_multiplier`: money in the quote currency per 1.0 price move per 1.0 unit of position size. This depends on your broker; the sample values are illustrative.
- `quote_currency`: the currency P&L is in.

### Response shapes

```json
{"data": {...}}                                    // one resource
{"data": [...], "pagination": {"page": 1, "per_page": 20, "total": 16, "total_pages": 1}}
{"data": ..., "meta": {"filters": {...}, "notes": [...]}}   // analytics
{"error": {"code": "validation_error", "message": "...", "details": {"field": "problem"}}}
```

Error codes: `validation_error`, `malformed_json`, `unknown_fields`, `missing_fields`, `unknown_parameters`, `unsupported_media_type`, `not_found`, `method_not_allowed`, `conflict`, `payload_too_large`, `api_disabled`, `internal_error`.

---

## Part C — Updated project structure

```
tradeledger/
├── tradeledger/
│   ├── api/                       NEW
│   │   ├── __init__.py            NEW  Blueprint, guard, error handlers
│   │   ├── errors.py              NEW
│   │   ├── http.py                NEW
│   │   ├── serialise.py           NEW
│   │   ├── trades.py              NEW
│   │   ├── instruments.py         NEW
│   │   ├── analytics.py           NEW
│   │   └── openapi.py             NEW
│   ├── migrations.py              NEW
│   ├── __init__.py                MODIFIED  registers API; JSON errors under /api/
│   ├── config.py                  MODIFIED  0.4.0, MAX_CONTENT_LENGTH, API_ENABLED, env_flag()
│   ├── db.py                      MODIFIED  init_schema() migrates; drop_all() resets version
│   ├── schema.sql                 MODIFIED  baseline only (no user_version pragma)
│   ├── validation.py              MODIFIED  closed_at rules
│   ├── repositories/trades.py     MODIFIED  closed_at, pagination, count, realised-date filter, legacy edit rule
│   ├── seed.py                    MODIFIED  synthetic exit times
│   ├── performance/trade_math.py  MODIFIED  closed_at, realised_at, close_time_known
│   ├── performance/metrics.py     MODIFIED  realised ordering
│   ├── performance/report.py      MODIFIED  realised grouping, unknown_close_time_trade_ids
│   ├── cli.py                     MODIFIED  migrate-db, openapi; report lists legacy trades
│   └── routes.py                  MODIFIED  roadmap
├── tests/
│   ├── fixtures/schema_v1.sql     NEW  exact Phase 3 schema
│   ├── test_migrations.py         NEW  18
│   ├── test_chronology.py         NEW  18
│   ├── test_api_trades.py         NEW  74
│   ├── test_api_instruments.py    NEW  24
│   ├── test_api_analytics.py      NEW  58
│   ├── test_api_security.py       NEW  18
│   ├── test_openapi.py            NEW  41
│   ├── conftest.py, factories.py  MODIFIED  API fixtures, table_snapshot()
│   └── test_db, test_validation, test_repositories, test_seed_and_analytics, test_report   MODIFIED (Part A table)
├── docs/openapi.json              NEW  generated by `flask openapi`
├── docs/PHASE_4_GUIDE.md, PHASE_4_CODE.md   NEW
├── requirements.txt               MODIFIED  + jsonschema (test-only)
└── README.md                      MODIFIED
```

**New dependency:** `jsonschema==4.26.0`, used only by the tests. Install it with `pip install -r requirements.txt`.

---

## Parts D and E — Code and tests

The complete contents of every new and modified file are in `docs/PHASE_4_CODE.md`, generated from the tested files, and in the conversation.

---

## Part F — Database migration

### The problem

Phase 3 stored one time per trade (the entry), so a trade opened Monday and closed Friday counted as a Monday result. Streaks, drawdown and the cumulative-R curve followed entry order, which is not the order in which results actually happened.

### The design

| Choice | Alternatives | Why |
|---|---|---|
| Add a nullable `closed_at` column with `ALTER TABLE … ADD COLUMN` | Rebuild the table (SQLite's 12-step "copy, drop, rename") | Adding a column never rewrites existing rows; a rebuild touches every row and is the riskiest kind of migration |
| Enforce the cross-column rules with **triggers** (OPEN ⇒ no `closed_at`; `closed_at` ≥ `trade_date`) | Table CHECK constraints | SQLite cannot add table-level CHECKs to an existing table; triggers give the same protection |
| "CLOSED ⇒ `closed_at` required" in **Python only** | A trigger | Legacy closed trades legitimately have NULL. SQL can't tell old from new; the application can. |
| Legacy rows keep **NULL** | Copy `trade_date` into `closed_at`; guess a duration | Both would fabricate history, which the requirement forbids |
| Version tracked in `PRAGMA user_version`; each migration in one transaction with its version bump | A migrations table; Alembic | The built-in counter is enough for one file; Alembic needs SQLAlchemy |
| `migrate-db` writes a backup first, using SQLite's online backup API | Ask users to copy the file | Safer by default and works while the database is open |

### Rules for legacy trades

1. `realised_at = closed_at` if recorded, otherwise `trade_date`. This is a documented ordering rule; nothing is stored.
2. Ties are broken by trade id, so the order is always the same.
3. Every report lists them: `unknown_close_time_trade_ids` in the API, and a section in `flask report`. Every curve point carries `close_time_known`.
4. A legacy trade can be edited without supplying `closed_at`, so nobody is forced to invent one. If you do supply its real exit time, the trade stops being legacy, and the time can't be removed again.

### Effect on the metrics

| Metric | Before (Phase 3) | Now |
|---|---|---|
| Streaks | Consecutive outcomes in **entry** order | In **close** order. `test_streak_depends_on_close_order_not_entry_order`: entry order W L W L (win streak 1) becomes close order W W L L (win streak 2). |
| Max drawdown | Cumulative R in entry order | In close order (`test_drawdown_follows_close_order`) |
| Cumulative R | Entry order | Close order; each point has `realised_at` and `close_time_known` |
| Monthly, weekday and daily groups | Entry date | Close date. A trade entered 31 January and closed 2 February counts in February, on a Monday. |
| Analytics date filters | Entry date | Realised date |
| Win rate, expectancy, profit factor, totals | — | **Unchanged.** They don't depend on order. |
| `GET /trades` and the Phase 2 SQL queries | Entry date | Still entry date: they describe *when you traded*, not when results landed |

For the sample data nothing changes, because every synthetic trade closes the same day, in entry order. A migrated Phase 3 database also gives identical numbers (verified live in Part H), because its legacy trades fall back to entry order.

### Running it

| | Windows PowerShell | macOS / Linux |
|---|---|---|
| Activate | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

The commands are the same on both:

```bash
flask --app tradeledger db-info        # shows "Schema version: 1" on a Phase 3 database
flask --app tradeledger migrate-db     # "Backup written: …"  "Schema version 1 -> 2 (applied: [2])"
flask --app tradeledger migrate-db     # "Already at schema version 2. Nothing to do."
```

`init-db` also migrates, but without a backup. Use `migrate-db` for a database you care about. To start again with the new sample data: `reset-db`, then `seed-db`.

**Rolling back** means restoring the backup file: stop the app, then copy `tradeledger.db.v1-<time>.bak` over `tradeledger.db`. There is no automatic down-migration. With SQLite, a file copy is simpler and more reliable.

---

## Part G — API examples

Start the server with `flask --app tradeledger run --debug`. These examples assume the sample data is loaded (`flask --app tradeledger seed-db`).

### macOS / Linux (bash or zsh)

```bash
B=http://127.0.0.1:5000/api/v1

# Create an instrument (example values, not a broker specification)
curl -s -X POST $B/instruments -H "Content-Type: application/json" \
  -d '{"symbol":"US30","name":"Wall Street 30 CFD (example values)","asset_class":"INDEX",
       "price_increment":"1","contract_multiplier":"1","quote_currency":"USD"}'

# Create a closed trade
curl -s -i -X POST $B/trades -H "Content-Type: application/json" \
  -d '{"instrument_id":1,"trade_date":"2026-04-01T08:15:00Z","closed_at":"2026-04-01T11:40:00Z",
       "direction":"LONG","entry_price":"1.08500","stop_loss":"1.08300","take_profit":"1.08900",
       "exit_price":"1.08900","position_size":"1","fees":"7","strategy":"Breakout","session":"LONDON"}'

# List, filter, paginate
curl -s "$B/trades?instrument_id=3&status=closed&per_page=2&page=1"

# Replace (PUT needs every field; null for empty ones)
curl -s -X PUT $B/trades/17 -H "Content-Type: application/json" \
  -d '{"instrument_id":1,"trade_date":"2026-04-01T08:15:00Z","closed_at":"2026-04-01T11:40:00Z",
       "direction":"LONG","status":"CLOSED","entry_price":"1.08500","stop_loss":"1.08300",
       "take_profit":"1.08900","exit_price":"1.08700","position_size":"1","fees":"7",
       "strategy":"Breakout","session":"LONDON","notes":"Closed early"}'

# Delete
curl -s -i -X DELETE $B/trades/17

# Analytics
curl -s "$B/analytics/summary?start=2026-03-01&end=2026-03-31"
curl -s "$B/analytics/instruments"
curl -s "$B/analytics/curve?instrument_id=3"
```

### Windows PowerShell

PowerShell's `curl` is an alias for a different command, so use `Invoke-RestMethod`. It converts JSON for you.

```powershell
$B = "http://127.0.0.1:5000/api/v1"
$trade = @{
  instrument_id = 1; trade_date = "2026-04-01T08:15:00Z"; closed_at = "2026-04-01T11:40:00Z"
  direction = "LONG"; entry_price = "1.08500"; stop_loss = "1.08300"; take_profit = "1.08900"
  exit_price = "1.08900"; position_size = "1"; fees = "7"; strategy = "Breakout"; session = "LONDON"
}
$created = Invoke-RestMethod -Method Post -Uri "$B/trades" -ContentType "application/json" `
           -Body ($trade | ConvertTo-Json)
$created.data.result.realised_r                     # 1.965
Invoke-RestMethod "$B/trades?status=CLOSED&per_page=5"
Invoke-RestMethod -Method Delete -Uri "$B/trades/$($created.data.id)"
(Invoke-RestMethod "$B/analytics/summary").data.overall.expectancy_r
```

`Invoke-RestMethod` throws an exception on 4xx and 5xx responses. To see the error body, use `try { ... } catch { $_.ErrorDetails.Message }`.

### Expected responses (real, from the live run)

POST /trades returns **201** with `Location: /api/v1/trades/1`:

```json
{"data":{"closed_at":"2026-04-01T11:40:00Z","direction":"LONG","entry_price":"1.085","exit_price":"1.089",
 "fees":"7","id":1,"instrument_id":1,"result":{"fees":"7","gross_pnl":"400","initial_risk":"200",
 "net_pnl":"393","outcome":"WIN","planned_rr":"2","r_unavailable_reason":null,"realised_r":"1.965"},
 "status":"CLOSED","symbol":"EURUSD", "...": "..."}}
```

An invalid trade returns **400**:

```json
{"error":{"code":"validation_error","message":"The request is invalid.",
 "details":{"position_size":"must be greater than zero",
            "stop_loss":"must be below the entry price for a LONG trade"}}}
```

`GET /trades/1` after deletion returns **404**: `{"error":{"code":"not_found","message":"Trade 1 does not exist."}}`

(Flask sorts JSON keys alphabetically, so the field order in real output differs from the documentation tables.)

---

## Part H — Verified results

### Automated tests: 512 passed

| File | Tests | | File | Tests |
|---|---|---|---|---|
| test_app.py | 16 | | test_api_trades.py | 74 |
| test_db.py | 28 | | test_api_instruments.py | 24 |
| test_validation.py | 52 | | test_api_analytics.py | 58 |
| test_repositories.py | 30 | | test_api_security.py | 18 |
| test_seed_and_analytics.py | 19 | | test_openapi.py | 41 |
| test_trade_math.py | 37 | | test_migrations.py | 18 |
| test_metrics.py | 24 | | test_chronology.py | 18 |
| test_report.py | 30 | | | |
| test_properties.py | 25 | | **Total** | **512** |

Phase 1–3 files total 261; the new files total 251.

### Live end-to-end run (development server, disposable `/tmp` database)

| # | Request | Observed |
|---|---|---|
| 1 | POST /instruments (symbol `eurusd`) | 201, `Location: /api/v1/instruments/1`, symbol stored as `EURUSD` |
| 2 | POST /trades | 201, `Location: /api/v1/trades/1`, `realised_r "1.965"`, `net_pnl "393"` |
| 3 | GET /trades/1 | 200, same body |
| 4 | PUT /trades/1 (exit 1.087, notes) | 200, `net_pnl "193"`, `realised_r "0.965"`. By hand: 0.002 × 100000 − 7 = 193; 193 ÷ 200 = 0.965 ✓ |
| 5 | GET /analytics/summary, /analytics/curve | trades 1, total_r `"0.965"`, USD net `"193"`; curve labelled not equity |
| 6 | DELETE /trades/1 | 204, empty body |
| 7 | GET /trades/1 | 404 `not_found` |
| 8 | GET /, /health, /missing | 200; `{"version":"0.4.0"}`; 404 **HTML** (the website is unchanged) |
| 9 | Invalid trade; `text/plain` body; body with `"id"` | 400 `validation_error` with two field details; 415; 400 `unknown_fields` |
| 10 | GET /instruments/42; /api/v1/nothing-here | 404 JSON both |

### Production configuration

| Check | Observed |
|---|---|
| API with no opt-in | 403 `api_disabled`; `/api/v1/openapi.json` 200; `/health` 200 |
| Opt-in set | Log line: "The unauthenticated JSON API is ENABLED in production…" |
| 404, 400 (unknown instrument), malformed JSON, 70 KB body | Clean JSON: `not_found`, `validation_error`, `malformed_json`, `payload_too_large` (413) |
| Headers | `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` |
| Leak scan of error bodies for traceback, sqlite, SELECT, /home/, the secret | 0 matches |

### Live migration of a Phase 3 database

```
Schema version: 1
Backup written: /tmp/tl_v1.db.v1-20261115T022030Z.bak
Schema version 1 -> 2 (applied: [2])
Already at schema version 2. Nothing to do.
Closed trades                 14
Expectancy per trade          +0.55R          (identical to Phase 3)
Max drawdown (R)              1.05R           (identical to Phase 3)
== Closed trades with no recorded exit time (ordered by entry time) ==
#1, #2, #3, #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #16
```

---

## Part I — Documentation and ZIP

- `README.md`: updated features, API quick start, upgrade instructions, security, limitations.
- `docs/openapi.json`: the specification; a test fails if it is out of date. Regenerate it with `flask --app tradeledger openapi`.
- `docs/PHASE_4_CODE.md`: every new and modified file in full.
- `tradeledger_phase4.zip`: the complete project. It excludes `.env`, databases, `.venv` and caches. It was extracted into an empty folder and the suite was run there (Part H).

**Viewing the spec:** open `docs/openapi.json` in any OpenAPI viewer. For example, paste it into editor.swagger.io, which runs in your browser and doesn't need TradeLedger to be online. No viewer is bundled, to avoid a dependency.

### Git

```bash
git add .
git status          # check: no instance/, no .env, no *.bak
git commit -m "Phase 4: versioned JSON API, closed_at migration, OpenAPI 3.1 spec and API tests"
```

The `*.bak` files sit in `instance/`, which is already ignored. If you keep a database elsewhere, add `*.bak` to `.gitignore`.

---

## Part J — Teaching walkthrough

### REST APIs
**Plain:** a menu of things other programs can ask your app to do, each at a fixed address.
**Technical:** REST organises an API around *resources* (trades, instruments) identified by URLs, acted on with standard HTTP methods, and exchanged as representations (JSON). Each request carries everything needed to answer it; the server keeps no conversation state.
**TradeLedger:** `/api/v1/trades/9` *is* trade 9. GET reads it, PUT replaces it, DELETE removes it.

### How an HTTP request works
A request is text: a **method and path** (`POST /api/v1/trades`), **headers** (`Content-Type: application/json`), and an optional **body**. The response has a **status code** (`201 CREATED`), headers (`Location: /api/v1/trades/17`) and a body. Flask's test client builds the same requests in memory, which is how the full suite of 512 tests runs in about six seconds without starting a server.

### GET, POST, PUT and DELETE
| Method | Meaning | Safe (no change)? | Idempotent (repeat = same effect)? |
|---|---|---|---|
| GET | Read | Yes | Yes |
| POST | Create a new resource | No | **No**: twice creates two trades |
| PUT | Replace the whole resource | No | Yes: the same body twice leaves the same state |
| DELETE | Remove | No | Yes in effect (the second call returns 404, but the state is the same) |

This is why PUT requires every field: if a missing field meant "leave it alone", PUT would really be a partial update (that's PATCH), and the behaviour of a repeated request would depend on what was stored before.

### JSON
Text that represents objects (`{}`), lists (`[]`), strings, numbers, `true`/`false` and `null`. The catch for money is that most JSON parsers turn `1.085` into a binary float. TradeLedger avoids this in both directions. It *sends* decimals as strings (`"1.085"`), and it *reads* numbers with `json.loads(…, parse_float=Decimal)`, so `1.08500` arrives as `Decimal("1.08500")`. `test_json_numbers_are_read_exactly_not_as_floats` proves it.

### Flask Blueprints
A Blueprint is a bundle of routes with its own URL prefix and error handlers, registered on the app:
```python
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")     # api/__init__.py
app.register_blueprint(api_bp)                                 # __init__.py
```
The website (`main_bp`) and the API (`api_bp`) share the database layer but nothing else. The API can return JSON errors and send `no-store` headers without touching the HTML pages. A Blueprint's error handlers only see errors raised *inside* its views; a 404 for an unknown URL happens before any view runs. That's why the app-level 404, 405 and 413 handlers check `is_api_request()`.

### How routes use repositories
```python
@api_bp.post("/trades")
def create_trade():
    body = json_body()                         # 415 / 400 if not a JSON object
    check_fields(body, TRADE_INPUT_FIELDS)     # 400 if any unknown field
    conn = get_db()
    trade_id = trade_repo.create_trade(conn, body)   # validates; ValidationError → 400
    response = jsonify(data=trade_json(trade_repo.get_trade(conn, trade_id)))
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_trade", trade_id=trade_id)
    return response
```
The route never touches SQL or formulas. The same `create_trade()` will serve the Phase 5 HTML form.

### Why API validation matters
The server is the only place you control. A browser form can be bypassed with one curl command, so every rule lives in `validation.py` on the server, and the database repeats the critical ones. Two API-specific protections:
- **Mass assignment:** without `check_fields`, a client could send `"created_at": "2001-01-01"` or `"id": 5`. TradeLedger rejects every field not on the allow-list (`test_mass_assignment_of_protected_fields_is_rejected`).
- **Unknown query parameters:** `?instrument=XAUUSD` (typo) returns 400 rather than silently returning *all* trades, which looks like a correct answer and isn't.

### HTTP status codes
2xx means it worked (200 OK, 201 Created, 204 No Content). 4xx means the client must change something (400 bad input, 404 no such thing, 405 wrong method, 409 conflicts with existing data, 413 too big, 415 not JSON, 403 not allowed here). 5xx means the server failed (500). Clients branch on the number, so a validation failure returned as `200 {"ok": false}` breaks every client.

### Handling API errors
One shape everywhere, built in one function (`error_response`). A stable `code` is for programs, `message` is for people, and `details` gives field-level problems for forms. Unexpected exceptions log the full traceback on the server and return only `{"code":"internal_error"}`. `test_unexpected_api_error_is_generic_json` raises an error containing SQL text and the secret key, then checks that neither reaches the response but both reach the log.

### Database transactions in HTTP requests
One request gets one connection (`get_db()`), closed at teardown. Each write runs inside `with conn:`, which commits on success and rolls back on any exception. So a POST that fails validation or hits a constraint changes nothing (`table_snapshot` before and after in the tests). A migration runs `BEGIN; ALTER…; CREATE TRIGGER…; PRAGMA user_version = 2; COMMIT;` as one unit; `test_failed_migration_rolls_back_completely` proves a failure halfway leaves nothing behind.

### Why pagination matters
Without it, `GET /trades` returns every trade in one response: slow, memory-hungry, and sooner or later a timeout. With it, the client asks for one page. Ordering must be **deterministic** (`trade_date DESC, id DESC`, with the id breaking ties), or rows can appear on two pages or none. `test_pagination_pages_do_not_overlap` walks four pages and checks that every trade appears exactly once. `per_page` is capped at 100 so a client can't ask for a million.

### Financial analytics through an API
The analytics routes contain no maths. Each calls `build_report()` and returns one part of the report:
```python
@api_bp.get("/analytics/instruments")
def analytics_instruments():
    return _grouped("by_instrument")
```
So the CLI report, the API and the future dashboard can't disagree. `test_summary_matches_the_engine_exactly` asserts that the API's `overall` equals the engine's output byte for byte.

### API integration tests
They exercise the whole stack — routing, parsing, validation, SQL, the engine, serialisation — through `client.get()` / `client.post()`, against a real temporary SQLite file. Patterns used:
- **Before/after snapshots** prove failed requests write nothing.
- **Parametrised tests** run one test over 14 invalid inputs.
- **Contract tests** validate real responses against the OpenAPI schemas.
- **Mutation checks** break the code on purpose to prove the tests notice.

### Versioned API paths
`/api/v1/…` lets you change the API later (rename a field, change a format) as `/api/v2/…` while old clients keep working on v1. Without a version, any breaking change breaks every client at once.

### OpenAPI
A machine-readable contract: every path, method, parameter, body, response and error, in JSON or YAML. Tools can generate documentation, client code and test cases from it. Its value depends entirely on being *true*, which is why `test_openapi.py` fails if a route is undocumented, a documented route doesn't exist, a response has an undocumented field, or the exported file is stale.

### Why an unauthenticated API is unsafe in public
Anyone who finds the URL can read your entire trading history and delete or alter it, and automated scanners find exposed servers within hours. HTTPS doesn't help (it encrypts the attacker's requests too), and neither do security headers (they protect browsers from other websites, not your API from direct requests). That's why production defaults to 403, opting in is explicit and loudly logged, and the README says plainly that TradeLedger is not production-ready.

### How Phase 5 will use these endpoints
See Part L.

---

## Part K — Practical exercise: a `strategy` filter on `GET /trades`

1. **Repository:** add `strategy=None` to `_where()`, `list_trades()` and `count_trades()` in `repositories/trades.py`, as a parameterised condition `t.strategy = ?`. Decide (and document) whether matching is case-sensitive.
2. **API:** add `"strategy"` to `LIST_PARAMETERS` in `api/trades.py`, read it from `args`, and pass it to `filters`. Reject an empty value (`?strategy=`) with a 400 `ValidationError`.
3. **OpenAPI:** add a `strategy` query parameter to `GET /trades` in `api/openapi.py`, then run `flask --app tradeledger openapi`. `test_documented_query_parameters_match_what_the_code_accepts` will fail until both sides agree, so run the tests before step 3 to see it catch the gap.
4. **Tests** in `tests/test_api_trades.py`: `?strategy=Breakout` returns 6 sample trades (5 closed, 1 open); combining it with `status=OPEN` returns 1; a strategy that doesn't exist returns an empty list, not an error; an empty value returns 400; and `?strategy=x' OR '1'='1` leaves the table unchanged.
5. Run `pytest` and aim for 512 + your new tests, all passing.

**Questions to answer in your own words:**
1. Why does `PUT` reject a body that leaves out `notes`, instead of keeping the old notes?
2. A trade was entered on 31 March and closed on 1 April. Which month does `/analytics/monthly` put it in, and which date range of `GET /trades` finds it? Why are they different?
3. Why does the migration leave legacy `closed_at` as NULL rather than copying `trade_date` into it?
4. Someone suggests "just set `TRADELEDGER_ENABLE_UNAUTHENTICATED_API=1` on the AWS server; it's only my data." What would you tell them?

---

## Part L — Next phase (Phase 5: frontend)

Phase 5 builds the pages people will use, on top of this backend:

- **Trade entry form** (`/trades/new`): an HTML form posting to a website route that calls the same `create_trade()`. Phase 5 adds **CSRF protection**, because browsers *can* submit HTML forms cross-site. `ValidationError.errors` maps straight onto per-field messages next to each input. The POST-redirect-GET pattern stops a refresh from submitting the form twice.
- **Trading history** (`/trades`): a server-rendered table using `list_trades()` and `count_trades()` with the same filters and pagination, plus `result.realised_r` and `net_pnl` from `serialise.trade_result`.
- **Edit and delete** pages reusing `update_trade()` and `delete_trade()`, including the legacy-trade rule.
- **Interactive parts** in vanilla JavaScript: client-side checks for instant feedback (the server still validates everything), and filter controls that fetch `/api/v1/trades?...` without reloading the page.
- The Phase 6 dashboard will `fetch('/api/v1/analytics/curve')` for Chart.js, converting the strings with `Number()` only at the chart. The exact values stay exact everywhere else.

### Phase 4 completion checklist

All verified on Linux; tick them on your machine:

- [ ] `pip install -r requirements.txt` (adds jsonschema)
- [ ] `flask --app tradeledger migrate-db` on your existing database (backup written)
- [ ] `pytest` shows 512 passed
- [ ] The curl or PowerShell examples in Part G work
- [ ] The home page shows Phase 4 Done; `/health` shows 0.4.0
- [ ] `git status` is clean of databases, backups and `.env`; commit

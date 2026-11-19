# Phase 6 Guide: Analytics Dashboard, Interactive Charts and Performance Reporting

Version **0.6.0**. Test suite: **888 passed, 0 failed, 0 skipped** in the build environment (746 at the Phase 5 baseline + 142 new; details in section 13).

> **Read this first: what was and was not verified.** The dashboard loads Chart.js 4.5.1 from the jsDelivr CDN, and the build environment could not reach that CDN (or any other download source). Everything TradeLedger itself does was verified, including in a real Chromium browser: the calculations, filters, tables, text descriptions, the Content-Security-Policy, the chart configurations our code builds, tooltip text and the offline fallback. **What Chart.js itself draws on the canvas could not be seen.** The screenshots therefore show the dashboard's honest "chart unavailable" message where the charts go. Section 14 says how to check the drawings yourself.

---

## 1. Phase 6 goals

Turn the `/analytics` preview into a real dashboard that answers, from stored trades only:

- Is the strategy's expectancy positive? How consistently does it win and lose?
- How has performance changed over time, and how deep have losing periods been?
- Which instruments, strategies, sessions and weekdays perform best?

Each figure must come from the existing Python engine, filters must run on the server, charts must have accessible text equivalents, and nothing may be invented: no fake values, no guessed exit times, no cross-currency totals and no "equity curve".

## 2. What Phase 5 already had

Checked by unpacking `tradeledger_phase5.zip` into a clean folder, comparing it with the working copy (identical) and running the suite there: **746 passed**.

| Already present | Where |
|---|---|
| Exact engine: P&L, risk, R, expectancy, profit factor, streaks, drawdown, groups by instrument/strategy/session/weekday/month, cumulative R in realised order | `performance/` |
| `build_report(conn, instrument_id, start, end)` with realised-date filters | `performance/report.py` |
| Analytics API: summary, groups, curve | `api/analytics.py` |
| `/analytics` preview: three tables, labelled "Preview" | `templates/analytics.html` (now deleted) |
| Strategy filter and search in the trade repository | `repositories/trades.py` |
| CSP `script-src 'self'`, CSRF, production guard, design tokens | `__init__.py`, `web/`, `static/css/base.css` |

So Phase 6 needed **no new formulas** and **no database changes**. Only the running peak and drawdown of an already-calculated series are new arithmetic.

## 3. New features

- **Filters:** instrument, strategy, session, realised from, realised to. Combined in SQL, kept in the URL, with a reset link, and validated with clear errors (400, and no report at all, so a bad filter cannot produce a misleading page).
- **KPI cards (12):** closed trades, wins, losses, breakevens, win rate, average R, expectancy, profit factor (R), total R, maximum drawdown, longest winning and losing streaks. Plus net P&L **per quote currency**.
- **Seven charts:** cumulative realised R (time axis), drawdown from peak (maximum marked), R by month, by UTC weekday, by instrument, by strategy and by session.
- **Text and tables:** every chart has a visible text description and a data table. Groups with fewer than 10 trades are flagged as small samples.
- **Data-quality section:** open trades, unknown exit times, trades with no usable R, definitions.
- **Graceful degradation:** without JavaScript, or without Chart.js, all figures remain available as text.
- **API:** every `/api/v1/analytics/*` endpoint accepts `strategy` and `session`, documented in OpenAPI.
- **Tooling:** JavaScript unit tests (Node's built-in runner) and `scripts/benchmark_dashboard.py`.

## 4. Architecture

```
SQLite ──► repositories/trades.py ──► performance/report.py ──► performance/dashboard.py ──► web/analytics.py ──► Jinja ──► analytics.js ──► Chart.js
           parameterised SQL           Decimal engine            arrange for display          route + filters      HTML+JSON   draw only     canvas
                    ▲                                                                                ▲
                    └──────────────── report_filters.py (one filter definition) ─────────────────────┤
                                                                       api/analytics.py ─────────────┘
```

| Layer | Responsibility | Must not |
|---|---|---|
| `repositories/trades.py` | Fetch rows with bound parameters (`session` filter added) | Calculate |
| `report_filters.py` (NEW) | Validate filter text into a `ReportFilters` object | Touch the report |
| `performance/report.py` | `build_report(..., strategy, session)`: one report | Know about HTML |
| `performance/dashboard.py` (NEW) | KPIs, series with running peak and drawdown, gap-filled months, weekdays, groups, descriptions, JSON payload | Re-implement any statistic |
| `web/analytics.py` (NEW) | Read query string, validate, build, render, set CSP source | Format numbers |
| `templates/analytics/*` (NEW) | Semantic HTML, tables, chart containers | Calculate |
| `static/js/analytics.js` (NEW) | Turn exact strings into chart numbers and draw | Calculate, format money/R |

**Files:** see `PHASE_6_CODE.md` for the full list with NEW / MODIFIED / DELETED status and complete contents.

## 5. Database changes

**None.** The schema stays at version 2, so no migration is needed for a Phase 5 database. An index was considered and measured (section 21): with 10,000 trades the full SQL read takes about 68 ms and a one-month filter about 3.6 ms, while most of the time goes on exact Decimal evaluation in Python. An index would not change that, so none was added.

## 6. Analytics formulas

All of these existed in Phase 3 except the last two lines, which `dashboard.py` derives from the engine's own series.

| Metric | Formula | Undefined when |
|---|---|---|
| Realised R | net P&L ÷ initial risk | risk is not positive → listed under "No usable R" |
| Win rate | wins ÷ closed trades | no closed trades → `n/a` |
| Average R | mean of usable realised R | no usable R → `n/a` |
| Expectancy | win rate × average win R + loss rate × average loss R | no closed trades → `n/a` |
| Profit factor (R) | sum of winning R ÷ \|sum of losing R\| | no losing trade → `n/a` (not infinity) |
| Total R | sum of realised R | no trades → shown as `n/a`, not `0` |
| Cumulative R | running sum, in realised order (closed_at, then id) | – |
| **Running peak** | highest cumulative R so far, **starting at 0** | – |
| **Drawdown** | running peak − cumulative R (always ≥ 0) | – |
| Maximum drawdown | largest drawdown | no trades → `n/a` |

**Total R vs running peak vs current drawdown vs maximum drawdown**, using the hand-worked test data (R: +2, −1, −1, +0.5): cumulative R goes 2, 1, 0, 0.5. The running peak stays 2. Drawdown goes 0, 1, 2, 1.5. Total R is where the line ends (0.5), current drawdown is the last drawdown (1.5), and maximum drawdown is the worst (2).

Why the peak starts at 0: if your first trade loses 1R, you are already 1R below where you started. Starting the peak at the first point would hide that drawdown.

## 7. Chart data flow and exact decimals

1. Python calculates in `Decimal` (60-digit context).
2. `chart_payload()` writes each value twice: an **exact string** (`"7.68817307692…"`) for positioning, and **display text** (`"+7.69R"`) rounded by `formatting.py`.
3. The page embeds the payload as `<script type="application/json">` (data, never executed). Jinja's `tojson` escapes `<`, `>` and `&`, so a strategy name such as `</script>` cannot break out.
4. `analytics.js` calls `toNumber()`, which returns `null` for anything absent or non-finite (never `NaN` or `Infinity`), only to place points and bars.
5. Tooltips, tables and descriptions all show the Python text, so a chart can be visually approximate (a 64-bit float holds about 16 significant digits) without ever being the source of a number.

The payload never contains floats (a test walks it and also serialises it with `allow_nan=False`), and it carries no notes or prices, only aggregated figures and trade numbers.

## 8. Filters

`GET /analytics?instrument_id=1&strategy=Breakout&session=LONDON&start=2026-03-01&end=2026-03-31`

- `parse_report_filters(args, conn, blank_means_unset=...)` returns `(ReportFilters, errors)` and never raises. On the website, a blank select means "All". In the API, a blank value is a 400, so the API's existing behaviour is unchanged.
- An unknown instrument is an error, not an empty report.
- Dates are **realised** dates: closed_at, or for legacy trades their entry time (flagged on the page).
- The same function validates `/api/v1/analytics/*`, so the dashboard and the API cannot disagree. A test compares them.
- The response for an invalid filter is **400** with an error summary linked to each field, and no KPI cards, charts or Chart.js script.

## 9. Frontend design decisions

- **Layout:** filters → summary cards → performance over time (cumulative R full width, drawdown below) → breakdowns (two columns from 72rem) → detailed tables → "About these figures".
- **KPI grid:** `auto-fit` columns with a 9.5rem minimum (two per row on a 375 px phone), and exactly six columns from 80rem, so the 12 cards form two even rows. Both values came from looking at real screenshots (section 14).
- **Colour is never the only signal:** values carry a sign and an `R` unit, and the KPI tone uses a coloured top border plus coloured text. Undefined values read `n/a`. Chart bars sit above or below a stronger zero line.
- **Charts:** no animation (`animation: false`) and no legends (one series per chart, explained by the caption). Months with no trades are blank bars. Horizontal bars are used for instruments, strategies and sessions, so long labels stay readable. A `*` marks a small sample.
- **Tables** reuse Phase 5's card layout below 64rem; row headers were adapted so they read as "Strategy … Breakout".
- **The trade-by-trade table** shows the latest 200 trades. The benchmark showed a full table made a 10,000-trade page 9.5 MB.

## 10. Security

- **CSP:** every page keeps `script-src 'self'`. Only the `/analytics` response, and only when there is something to chart, becomes `script-src 'self' https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js`, a full file path rather than the CDN host. `unsafe-inline` and `unsafe-eval` are never used. `chart_script_sources()` refuses `http:`, protocol-relative URLs, query strings and anything not https or a site path. The JSON data block is not executable, so it needs no CSP exception.
- **CDN trust:** the version is pinned, `crossorigin="anonymous"` and `referrerpolicy="no-referrer"` are set, and Subresource Integrity is supported through `TRADELEDGER_CHART_JS_INTEGRITY` (not set by default: no verified copy was available to hash). See `docs/THIRD_PARTY_NOTICES.md`.
- **Unchanged protections:** parameterised SQL (an injection string is just a strategy name), autoescaping, CSRF, length limits (strategy ≤ 50), production guard (`/analytics` is 403 in production unless the journal is explicitly enabled), API restrictions and security headers.
- **Read-only:** analytics requests never write; a test snapshots both tables before and after. POST to `/analytics` is 405.

## 11. Accessibility

- One `<h1>`, and section `<h2>`s and chart `<h3>`s in order. Each chart is a `<figure>` with a `<figcaption>`, a visible text description (`aria-describedby`) and a link to its table.
- The canvas has `role="img"` and an `aria-label`, but nobody depends on it: every finding is in text or tables.
- Tables have captions, `scope` attributes and row headers. Scrollable table regions are focusable and labelled.
- Filters have labels, `aria-invalid`, linked error messages and an error summary that receives focus.
- No motion. Keyboard order was tested in the browser (Instrument → Strategy …).
- **Not done:** a screen-reader session (NVDA/VoiceOver) or a formal WCAG audit.

## 12. Testing strategy

| File (NEW unless stated) | Tests | Purpose |
|---|---|---|
| `test_report_filters.py` | 45 | Filter validation; repository `session`; `build_report` filters; realised dates; injection; API parameters and schemas; read-only |
| `test_dashboard_data.py` | 31 | **Hand-calculated** dataset: chronology, ties, overlap, cumulative R, peak, drawdown, year boundary, empty month, weekdays, groups, KPIs, currencies, edge cases, payload, 1,000 trades |
| `test_web_analytics.py` | 50 | Route: rendering, KPI values, filters, invalid input (400), empty states, legacy, payload = dashboard = API, CSP, XSS, production |
| `test_javascript.py` + `js/analytics.test.js` | 1 (11 JS tests) | `toNumber`, configs, tooltips, empty bars, offline fallback |
| `test_browser.py` (MODIFIED) | +13 | Chromium: 7 charts built, CSP negative control, tooltips, filters, reset, invalid filter, empty, offline, no-JS, 4 widths, keyboard |
| `test_web_pages.py` (MODIFIED) | 2 replaced, +2 | The two "preview" tests now check the full dashboard; `analytics.css` and `analytics.js` added to the static-file check |
| `test_api_analytics.py` (MODIFIED) | 2 updated | `meta.filters` now also lists `strategy` and `session` (additive change) |

**Independent calculation:** `test_dashboard_data.py` opens with a table of five trades and every expected figure worked out by hand (written in the file's docstring). The tests compare against those numbers, not against the code's own output. A second test checks that two independent paths agree: the engine's `max_drawdown_r` and the maximum of the dashboard's drawdown series.

**Can the tests fail? (deliberate defects)** Eleven defects were planted one at a time, the relevant tests run, and the code restored. **All 11 were caught:** peak not anchored at 0R, empty months dropped, weekdays sorted alphabetically, CSP allowing the whole CDN host, session filter ignored in SQL, `build_report` dropping strategy, JavaScript letting Infinity through, JavaScript drawing empty months as 0, API accepting blank filters, the JSON block unescaped, and the offline fallback removed.

## 13. Test results (build environment)

- Baseline (Phase 5 ZIP, clean folder): **746 passed**.
- Final: **888 passed, 0 failed, 0 skipped**, about 28 s. This includes 55 Playwright/Chromium tests and the Node tests.
- Clean extraction of `tradeledger_phase6.zip`: all tests pass in an empty folder (see `docs/PHASE_6_QA_REPORT.md`).
- **Independent agreement audit** (`scripts/qa_dashboard_agreement.py`): 900 filter combinations over 616 trades; the page, chart payload, API and the tooltips produced by the real `analytics.js` all matched a separate recalculation from raw SQL rows (0 disagreements). Two planted defects were both caught. Details in `docs/PHASE_6_QA_REPORT.md`.
- Environment: Python 3.13, Flask 3.1.3, pytest 9.1.1, Playwright 1.63 with Chromium, Node.js 22, Linux, 2 CPUs.
- Not attempted: coverage measurement (`pytest-cov` is not installed and installs were blocked), other browsers.

## 14. Browser verification (what was actually done)

Run against the development server with the synthetic data, in headless Chromium (Playwright 1.63):

- **Real CDN:** `cdn.jsdelivr.net` was unreachable (`ERR_TUNNEL_CONNECTION_FAILED`). So the real Chart.js **never ran here**, and its drawings, legend layout and tooltip positioning were not seen.
- **Stubbed Chart.js (automated tests):** the exact CDN URL is intercepted and answered with a recording stand-in. This proves the CSP allows that URL (a negative control proves CSP violations are detected), that all seven charts get the right data (e.g. curve = 15 points: the start point plus 14 trades), that tooltip text is right (e.g. `Trade #1 · EURUSD`, `Trade R: +1.97R`), that filters update cards and charts, and that there are no console errors.
- **Offline behaviour:** with the URL blocked, all seven chart areas show the fallback message and the tables remain.
- **Layout:** 0 px horizontal overflow at 375, 768, 1024 and 1440 px, and at an emulated 200% zoom (720 and 683 px viewports). Screenshots were inspected by eye and led to five fixes: lowercase "r" in table links, a stray table border, one KPI per row on phones, an uneven 7 + 5 KPI grid on desktop, and misaligned mobile row headers. A full trade-by-trade table also made large pages too heavy (section 21).
- **No JavaScript:** KPI cards, notices and tables were verified with JavaScript disabled.

**How to check the real charts yourself** (needs internet): run the app, open `/analytics`, and confirm the seven charts draw. Hover the cumulative R line (the tooltip should show the trade number, symbol, trade R and cumulative R), check the triangle on the drawdown chart at trade #9 (1.05R), and look at the browser console (no errors) and the Network tab (Chart.js loaded with status 200).

## 15. Installation

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

No new Python packages are needed in Phase 6. Optional: Node.js 18+ for the JavaScript tests, and `pip install playwright` plus `python -m playwright install chromium` for the browser tests.

## 16. Running the application

```bash
flask --app tradeledger init-db        # new database
flask --app tradeledger migrate-db     # existing database: no-op for Phase 5 (already version 2)
flask --app tradeledger seed-db        # synthetic sample trades
flask --app tradeledger db-info        # inspect: tables, counts, schema version
flask --app tradeledger run --debug
```

Open http://127.0.0.1:5000/ (overview), /trades (journal) and /analytics (dashboard), for example `/analytics?strategy=Breakout&session=LONDON`.

Tests:

```bash
pytest                                     # everything
pytest tests/test_dashboard_data.py tests/test_web_analytics.py tests/test_report_filters.py
pytest tests/test_browser.py               # needs Playwright
node --test tests/js/*.test.js             # needs Node.js
python scripts/benchmark_dashboard.py      # timings
```

## 17. Known limitations

- Charts need internet access unless Chart.js is self-hosted; its drawing was not verified in the build environment (section 14).
- No Subresource Integrity hash by default.
- Statistics are in R and per currency only: no currency conversion, no percentage returns (no capital or deposits are recorded).
- Legacy trades without an exit time are placed at their entry time (flagged).
- Very large journals produce large pages (3 MB at 10,000 trades, mostly chart data). The trade-by-trade table is capped at 200 rows.
- The small-sample threshold (10 trades) is a rule of thumb, not a statistical test.
- Strategy filters match exactly (case-sensitive); there is no "Unspecified" filter.
- No screen-reader session or formal accessibility audit.

## 18. Debugging tips

| Symptom | Check |
|---|---|
| "Chart unavailable … could not be loaded" | No internet, or the CDN is blocked. Open the Network tab. To self-host, see `THIRD_PARTY_NOTICES.md`. |
| Missing Chart.js file (self-hosted) | `TRADELEDGER_CHART_JS_URL` must match the real file path; `/static/vendor/chartjs/chart.umd.min.js` should return 200. |
| Charts blank, no message | Console errors? Is `#dashboard-data` present (only when there are closed trades)? `TradeLedgerCharts.instances.length` in the console should be 7. |
| "Refused to load the script … Content Security Policy" | The URL in the page must equal `CHART_JS_URL` exactly; query strings are refused by design. |
| Integrity error in the console | The hash doesn't match the file: recompute it or unset the variable. |
| Filter gives 400 | Read the error summary. Session must be ASIA/LONDON/NEW_YORK/OTHER, dates YYYY-MM-DD, end ≥ start, and the instrument must exist. |
| `ModuleNotFoundError` | Activate the virtual environment and run `pip install -r requirements.txt`. |
| Migration problems | `flask --app tradeledger db-info`; Phase 6 needs no migration. Backups from `migrate-db` are next to the database file. |
| A figure looks wrong | Compare `/api/v1/analytics/summary` with the same filters. Check the "About these figures" section for legacy or unusable-R trades. Recompute one trade by hand: (exit − entry) × size × multiplier − fees, divided by the risk. |

## 19. Portfolio and interview explanations

### How to Explain Phase 6 in a Software Engineering Interview

- **Why Flask?** It's small and explicit. You can see every route, and the app factory makes testing easy. A single-user analytics tool doesn't need Django's ORM or admin.
- **Why SQLite?** Zero setup, one file, transactions and constraints. Fine for one user; the repository layer isolates SQL if PostgreSQL is needed later.
- **Why separate repositories from business logic?** SQL can change (a new filter, a new database) without touching the calculations, and the calculations can be tested with plain dictionaries, no database at all.
- **Why Python `Decimal`?** Money and ratios must not pick up binary rounding errors (0.1 + 0.2 ≠ 0.3 in floats). Rounding happens once, for display.
- **Why Chart.js?** It's a mature, MIT-licensed library that works from one file with no build step, has good tooltip support and fits a strict CSP. Here it is presentation only.
- **How do the filters work?** The query string is validated by one shared function into a typed `ReportFilters`, turned into bound SQL parameters, and also used by the API. Invalid input gives a 400 and no report.
- **How does data reach the charts?** SQLite → repository → engine (`Decimal`) → dashboard layer (series, gaps, labels, text) → JSON with exact strings → `analytics.js` converts them to numbers → Chart.js draws.
- **How are inconsistent calculations avoided?** There's a single `build_report()` path, one report per page, the API and dashboard are compared by a test, and JavaScript is not allowed to calculate.
- **How is missing data handled?** `n/a` instead of 0, empty months as gaps, legacy times flagged, unusable R listed, open trades excluded and counted.
- **How were the calculations tested?** A hand-worked dataset, two independent paths compared (the engine's maximum drawdown versus the dashboard series), and planted defects to prove the tests fail.
- **How was browser behaviour verified?** Playwright with Chromium: layout at four widths, keyboard, CSP violations (with a negative control) and an offline fallback. Be honest that Chart.js's own drawing was checked with a stand-in because the CDN was blocked.
- **How does the design support deployment?** Configuration comes from the environment (`CHART_JS_URL`, integrity), a production guard is in place, and the CSP is per response. Real deployment would still need authentication first.

**Being honest about AI assistance.** Say plainly that this is a learning project built with an AI assistant, and that you directed it, reviewed it and tested it. What you should personally understand and be able to change:

- The R formula and why expectancy and profit factor can be `n/a`.
- How the running peak and drawdown are computed (`drawdown_series`).
- The request flow in `web/analytics.py`, and how a new filter would travel through `report_filters.py`, the repository and `build_report`.
- Why the CSP allows exactly one URL, and what the JSON data block is.
- How to run and read the test suite, and one test you wrote yourself (see section 20).

Do not claim professional experience, sole authorship or verification you did not do (for example, real Chart.js drawing in this build).

## 20. Practice exercise: a "Direction" filter

**What to build:** a filter on `/analytics` (and the analytics API) to show LONG trades, SHORT trades or both.

**Acceptance criteria**
1. A "Direction" select with All, Long and Short; the choice is kept in the URL and in the form.
2. KPI cards, all seven charts and every table change with it, and it combines with the other filters.
3. `direction=sideways` gives a 400 with a clear message on the website and in the API; a blank value means "All" on the website only.
4. `/api/v1/analytics/*` accepts `direction`, it's documented in OpenAPI, and `docs/openapi.json` is regenerated.
5. Every existing test still passes.

**Files to inspect:** `report_filters.py`, `repositories/trades.py` (`_where`), `performance/report.py` (`build_report`), `web/analytics.py`, `templates/analytics/dashboard.html`, `api/openapi.py`, `tests/test_report_filters.py`.

**Hints**
- Follow how `session` was added in Phase 6. It is the same shape: a fixed set of allowed values.
- `validation.py` already knows the valid directions; reuse that rather than writing "LONG" and "SHORT" again.
- `FILTER_NAMES` drives the API's allowed parameters and an OpenAPI test, so adding the name there will make an existing test tell you what is still missing.
- SQL: add a condition and a parameter; never build SQL text from the value.

**Tests to write:** validation (valid, lowercase, invalid, blank on web vs API); repository counts (sample data: count the LONG trades yourself first); `build_report(direction="SHORT")` trade count; route test for the KPI "Closed trades" with `direction=SHORT`; an API test with schema validation; a combined filter (direction + strategy).

**Common mistakes:** filtering in Python after loading every trade; forgetting `count_trades`; adding the parameter to the API without OpenAPI (a test will fail); comparing case-sensitively; a hard-coded expected number you didn't check by hand.

**How to verify:** run `pytest`, open `/analytics?direction=SHORT`, compare with `/api/v1/analytics/summary?direction=SHORT`, and plant a defect (ignore the filter in SQL) to confirm your test fails.

## 21. Suggested improvements for the next phase

- **Measured performance** (median of 5 runs, Python 3.13, Linux, 2 CPUs):

  | Trades | Report | Dashboard data | Full page | Page size | Filtered page |
  |---|---|---|---|---|---|
  | 1,000 | 65 ms | 92 ms | 94 ms | 488 KB | 6 ms |
  | 10,000 | 601 ms | 749 ms | 764 ms | 3,032 KB | 33 ms |

  Ideas if needed: compress responses (gzip), shorten payload keys, or cache reports by filter and data version.
- Self-host Chart.js and add Subresource Integrity once the file can be obtained.
- Screen-reader testing, and checks in Firefox and Safari.
- A rolling-average R chart, an R distribution histogram, or best and worst trades (optional items not built in Phase 6).
- Larger later steps (each a separate, agreed phase): authentication and multi-user support, PostgreSQL, CI, AWS deployment.

Phase 7 has not been started.

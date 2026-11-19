# Phase 6 — Final Quality Assurance Report

Date: 15 November 2026. Version 0.6.0. Environment: Linux, Python 3.13, Flask 3.1.3, pytest 9.1.1, Playwright 1.63 (Chromium), Node.js 22, 2 CPUs.

Every result below was produced by a command actually run in this environment. Anything that could not be run is listed under **Not verified**.

## Summary

| # | Check | Result |
|---|---|---|
| 1 | Seven charts drawn by the real Chart.js 4.5.1 | **Not possible here.** No route to the library (details below). |
| 2 | Limitation documented, no claim of visual verification | Done (this file, README, guide section 14, `THIRD_PARTY_NOTICES.md`) |
| 3 | Figures, filters, tooltips and tables agree with the engine | **Verified.** 900 filter combinations, 93,361 comparisons, 0 disagreements |
| 4 | Full suite passes; no Phase 1–5 regression | **Verified.** 888 passed; unchanged Phase 5 tests: 738 pass, 8 fail only where behaviour changed on purpose |
| 5 | ZIP is complete and works from an empty folder | **Verified.** See section 5 |

## 1. Real Chart.js 4.5.1: could it be loaded?

Every route was tried; none worked:

| Route | Result |
|---|---|
| `cdn.jsdelivr.net` from the shell (curl) | Proxy refused: `CONNECT tunnel failed, response 403` |
| `cdn.jsdelivr.net` from Chromium | `net::ERR_TUNNEL_CONNECTION_FAILED` |
| npm registry (`registry.npmjs.org`) | Host not resolvable (network off) |
| GitHub release page | HTTP 403 |
| jsDelivr data API via the web tool | Refused (not on the allowlist) |
| A copy already on disk (filesystem search, npm cache) | None found |

**Consequence:** no screenshot or test in this project shows Chart.js's own drawing. The screenshots show the dashboard's offline message in each chart area, and say so.

**What partially closes the gap:**
- The configuration our code passes to Chart.js is fully unit-tested in Node and in Chromium (with a recording stand-in).
- Two less common features we rely on were checked against the official Chart.js documentation: tooltip callbacks may return an array of strings (shown as multiple lines), and grid-line colour is scriptable per tick (`context.tick.value`), as in the official "Grid Configuration" sample.
- The other options used (`indexAxis: "y"`, `fill: "origin"`, `pointStyle` arrays, `maxBarThickness`, `suggestedMax`, `interaction.mode: "nearest"`, a `linear` x axis with `{x, y}` points) are standard Chart.js 4 options, but were **not** re-checked one by one against the docs.

**Manual check (needs internet, about five minutes):** see "How to check the real charts yourself" in `PHASE_6_GUIDE.md` section 14.

## 2. Agreement audit: `scripts/qa_dashboard_agreement.py`

**Plain English:** a second, completely separate calculator re-does every number from the raw database rows. It never calls TradeLedger's engine. Then it checks that everything the user can see matches.

**Data:** the 16 sample trades plus 600 random trades. That makes 616 trades: 583 closed, 17 closed with no exit time (legacy), the rest open. They span three new instruments in USD, GBP and EUR, with multipliers 10, 1 and 0.5, both directions, random fees, five strategies (including none) and all four sessions (plus none), from November 2025 to April 2026.

**Filter combinations:** 900 = 9 instrument choices × 5 strategies × 5 sessions × 4 date ranges. The date ranges are none, a two-month range, an open-ended start, and a single day. 330 of the combinations had results.

**What was compared for each combination:**
- **KPI cards:** all 12 (counts, win rate, average, expectancy, profit factor, total R, maximum drawdown, both streaks). Each was compared with text formatted by the audit's own rounding (half up), not the app's.
- **Net P&L:** one figure per currency.
- **Cumulative R curve:** the same trades in the same order (realised time, then id); exact cumulative values within 1e-30; display text.
- **Maximum drawdown:** in the chart payload.
- **Instrument, strategy and session tables:** rows; trade counts; won/lost/breakeven; win rate; total R. The chart payload and the JSON API were checked for the same groups and values, with "best first" ordering.
- **Months:** every calendar month from first to last, with empty months left blank (null), not 0.
- **Weekdays:** all seven, Monday to Sunday, in UTC; empty days say "No closed trades".
- **Tooltips:** produced by running the real `analytics.js` in Node over every payload. Each tooltip quotes the server's exact text. Legacy trades carry the "exit time not recorded" note. The plotted value equals the exact decimal converted once.
- **API:** total trades and total R.

**Result:** 93,361 comparisons, **0 disagreements**.

**Can the audit fail?** Two defects were planted in throwaway copies:
- Display rounding switched from half-up to half-even: **297** disagreements.
- Bar tooltips showing average R instead of total R: **2,876** disagreements.

**A finding about the audit itself:** the first run reported 6,904 disagreements of about 1e-28. The cause was in the *audit*: it added up cumulative R in Python's default 28-digit Decimal context. The engine correctly keeps 60 digits. The audit was fixed, not the application. This is the same precision trap the engine guards against, and the reason `decimal_to_text` normalises with the 60-digit context.

**Run it:** `python scripts/qa_dashboard_agreement.py` (Node.js on PATH, or `--node /path/to/node`). It takes about 15 seconds. Exit code 0 means no disagreements.

## 3. Test suite and regressions

- **Phase 6 suite:** **888 passed, 0 failed, 0 skipped** in about 28 seconds. That includes 55 Chromium tests and 11 Node tests (run through `tests/test_javascript.py`). It was run in the project folder and again in a clean extraction of the final ZIP.
- **Regression check:** the **unchanged Phase 5 test files** (from `tradeledger_phase5.zip`) were run against the Phase 6 code: **738 passed, 8 failed**. All 8 failures are intended changes, not lost features:

| Failing Phase 5 test | Why it now fails |
|---|---|
| `test_summary_meta_explains_conventions`, `test_date_filter_uses_realised_date` | Exact comparison of `meta.filters`; Phase 6 added two keys (`strategy`, `session`). This is an additive change. |
| `test_analytics_is_clearly_a_preview`, `test_sidebar_labels_analytics_as_preview` | The preview was deliberately replaced by the dashboard. |
| `test_no_horizontal_scrolling_and_no_errors[/analytics-*]` (4 widths) | Phase 5's browser test has no Chart.js stand-in, so the blocked CDN logs a console error. The no-overflow check in the same test passed first. |

- **Smoke test from a clean copy** (fresh database, real `.env` with a generated `SECRET_KEY`):
  - CLI: `init-db`, `migrate-db`, `seed-db`, `db-info`, `report`, `analytics` and `openapi` all worked. The regenerated `openapi.json` is byte-identical to the shipped one.
  - Running server:
    - Every page returns 200, and an unknown page returns 404.
    - `/analytics?session=MOON` returns 400.
    - API trades, summary and the OpenAPI document return 200.
    - `/health` returns `{"status": "ok", "version": "0.6.0"}`.
  - Security policy: the `/analytics` CSP lists only the exact Chart.js file URL; other pages keep `script-src 'self'`.
  - Production configuration: trade pages, the dashboard and the API return 403; `/` and `/health` still work.
  - The server log contained no secrets or tracebacks.

## 4. Screenshots (retaken from the final code)

These are in `docs/screenshots/`, captured in headless Chromium against a fresh sample database:

| File | Shows |
|---|---|
| `phase6_desktop_1440.png` | Dashboard with no filters |
| `phase6_filtered_1440.png` | Strategy Breakout and session London (3 trades) |
| `phase6_empty_1440.png` | Filters matching nothing |
| `phase6_mobile_375.png` | Phone width |
| `phase6_dashboard.png` | A 2 × 2 overview of the four |

All four had 0 px horizontal overflow. **Chart areas show the offline message, not drawn charts.**

## 5. ZIP contents

`tradeledger_phase6.zip` was built from the project folder. These are excluded:
- virtual environments, `.env`, `instance/` (local databases)
- `__pycache__`, `.pytest_cache`, `.git`
- `*.db` and temporary files

It was then extracted into an empty folder and compared file by file with the source (identical), and the full suite was run there.

**Included:**
- **Application:** all application code, templates, CSS and JavaScript.
- **Tests:** every test, including `tests/js/`.
- **Scripts:** `benchmark_dashboard.py` and `qa_dashboard_agreement.py`.
- **Dependencies:** `requirements.txt` (Flask 3.1.3, python-dotenv 1.2.3, pytest 9.1.1, jsonschema 4.26.0), plus `pytest.ini`, `.env.example` and `.gitignore`.
- **Documentation:** the README, guides and code listings for Phases 1–6, this report, `THIRD_PARTY_NOTICES.md`, `openapi.json`, the vendor README for self-hosting Chart.js, and the screenshots.

**Deliberately not included:**
- Chart.js itself (loaded from the CDN; see `THIRD_PARTY_NOTICES.md`).
- Playwright and Node.js (optional, documented in the README).
- Any trading database or personal data.

**Not tested:** a fresh `pip install -r requirements.txt` into a new virtual environment, because package downloads are blocked here. The pinned versions match those installed and used for every run above.

## 6. Not verified

1. Chart.js 4.5.1 actually drawing the seven charts, tooltip placement, and how the charts look.
2. A Subresource Integrity hash for the CDN file (the file could not be downloaded to hash it).
3. Firefox and Safari (only Chromium was used).
4. A screen-reader session and a formal WCAG audit.
5. Code coverage percentage (`pytest-cov` not installed).
6. A fresh dependency install in a new virtual environment (network off).

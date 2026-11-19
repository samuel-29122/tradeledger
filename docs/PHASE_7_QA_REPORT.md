# Phase 7 QA Report

**Version:** 0.7.0 · **Date:** 15 November 2026 · **Environment:** Linux (2 CPUs), Python 3.13.18, Flask 3.1.3, pytest 9.1.1, Playwright 1.63 with Chromium, Node.js 22, Ruff 0.16.9.

Every result here was produced by a command run in this environment. Section 3 lists what could **not** be run or verified. A skipped check is never counted as a pass.

## 1. Phase 6 baseline (start of Phase 7)

The baseline is the delivered `tradeledger_phase6.zip` (SHA-256 `57abfdb6…b00a`), extracted into a clean folder. The Phase 6 report's numbers were treated as claims and re-measured.

| Area | Measured |
|---|---|
| Version | 0.6.0 |
| Tests | 888 collected, **888 passed**, 0 failed, 0 skipped |
| Hidden warnings | 12 `ResourceWarning`s (tests leaving static-file responses open), visible only with `-W default` |
| Browser tests | 55, real Chromium, with a Chart.js **stand-in** |
| JavaScript tests | 11 of 11 pass |
| Coverage | 98.57% statements (stdlib tracer, see 2.3) |
| Lint / format | No configuration. Ruff: 164 findings (1 unused import, blind `except`, `zip` without `strict`, 15 SQL-construction warnings: all false positives after review). The formatter would change about 10,000 lines. |
| CI | None |
| Chart.js | CDN-dependent; real rendering never verified |

## 2. Verified results at release

### 2.1 Tests

| Check | Result |
|---|---|
| `python -m pytest` | **1,038 collected; 1,028 passed; 0 failed; 10 skipped**; 0 warnings (warnings are errors) |
| Skipped tests | The 10 `chartjs_real` tests: the real Chart.js file isn't installed (one of them also needs `TRADELEDGER_SCREENSHOT_DIR`) |
| Non-browser tests | 943 passed |
| Browser tests (Chromium) | 95 collected: 84 page and stand-in tests passed, 1 offline test passed, 10 real-Chart.js tests skipped |
| JavaScript (Node) | 11 of 11 pass (also run through `tests/test_javascript.py`) |
| Lint (`ruff check .`) | Clean |
| Format (`ruff format --check .`) | Clean (94 files) |

**New tests in Phase 7:** 150 (888 → 1,038 collected). Each protects a named requirement or failure mode:

| File | Tests | Protects |
|---|---|---|
| `test_security_hardening.py` | 40 | Security findings F1–F6 |
| `test_browser_journey.py` | 30 | End-to-end journey (15 steps), validation in the browser, and accessibility (labels, headings, tables, chart text alternatives, focus, reduced motion, colour, 200% zoom, phone text size), with a negative control |
| `test_timestamps.py` | 26 | The performance change gives identical results to `strptime` |
| `test_financial_edge_cases.py` | 17 | Tiny values, 8-decimal prices, fractional multipliers, fees turning a short winner into a loss, a loss beyond the stop, planned R:R edge cases, single-trade datasets, drawdown recovery |
| `test_error_paths.py` | 17 | Error paths found by coverage: unknown database errors re-raised, unusual inputs, the trade page with bad data, 413, bad timestamps, CLI messages |
| `test_database_integrity.py` | 10 | List/count/independent filter agree over 486 combinations; pagination never skips or repeats (including ties); migration registry |
| `test_chartjs_real.py` | 10 | Real Chart.js rendering (skipped here; see 3) |

**Existing tests changed** (none weakened or removed):
- Two production tests now use a 64-character `SECRET_KEY`, because short keys are refused by design (F3). Their assertions are unchanged.
- Three tests now close the file responses they open; the leak was a test bug.
- One About-page test also asserts Phase 7 and the Host-check text.
- `test_browser.py` uses shared fixtures and carries `chartjs_stub` / `chartjs_offline` labels; one blind `except Exception` was narrowed to Playwright's error type.

### 2.2 Independent checks

| Check | Result |
|---|---|
| Planted-defect test (`mutate7.py`, run outside the project) | **22 of 22 caught**: the 11 Phase 6 defects, re-applied after formatting, plus 11 for Phase 7 protections. The first run caught 21; the miss exposed a missing test input (`…Z0` trailing text), which was then added. |
| Independent analytics audit (`scripts/qa_dashboard_agreement.py`) | 616 trades, 900 filter combinations, **93,361 comparisons, 0 disagreements** (page, chart data, API and tooltips from the real `analytics.js`). The same count as Phase 6, so the Phase 7 changes altered no figure. |
| New security tests on the **unchanged Phase 6 code** | 25 of 40 fail, as they should: they detect F1–F5. The 15 that pass cover behaviour that was already correct. |
| Formatting change | Syntax trees of all 65 Python files compared before and after: identical, apart from whitespace inside one docstring (`cli.py`) |
| Clean extraction of `tradeledger_phase7.zip` | See section 5 |

### 2.3 Coverage

coverage.py and pytest-cov **could not be installed** (no network). Statement coverage was measured with a small tracer built on Python 3.13's `sys.monitoring` (kept outside the project). It counts every line in the code objects' line tables, ignoring line 0, and every line executed. On a test module it gave the same result as coverage.py's statement rules (5 of 8 = 62.5%).

| Measurement | Result |
|---|---|
| Phase 6 baseline | 98.57% (2,894 / 2,936 statements) |
| Phase 7, full suite | **99.68%** (3,427 / 3,438) |
| Phase 7, without browser tests (what CI measures) | **99.68%** |
| Branch coverage | **Not measured.** CI measures it with coverage.py (`branch = true`); the CI threshold is 90% |
| Financial engine (`performance/`) and security modules (`config`, `__init__`, `web/guard`, `web/csrf`, `validation`, `timestamps`) | 100%, except `formatting.py` (2 lines) and `validation.py` (1 line) |

The 11 statements still not executed are defensive branches. Each one was reviewed:

| File: lines | Branch | Why there is no test |
|---|---|---|
| `performance/formatting.py`: 68, 81 | `None` inputs to `format_r_amount` / `format_ratio` | Callers check for `None` first; the branch is defensive |
| `repositories/instruments.py`: 55 | Re-raising a database error that isn't about uniqueness | Validation prevents every other constraint failure |
| `repositories/instruments.py`: 69 | `get_instrument_by_symbol(non-string)` | Defensive type check |
| `repositories/trades.py`: 268–269 | Database error while *updating* a trade | Needs a constraint failure that validation lets through (none known); the same handler is tested for *creating* |
| `web/forms.py`: 120 | Redirect target with a scheme or host after passing the `/` checks | Unreachable with the current checks; kept as defence in depth |
| `web/trades.py`: 135 | Journal filter with an unusual instrument id | Covered by the API equivalent; low risk |
| `web/trades.py`: 262–263 | Repository rejects an edit after form validation passed | Would need the instrument deleted mid-request |
| `validation.py`: 118 | `parse_date` given a `date` object | Internal convenience |

The configured CI minimum is 90% so that branch coverage, which is normally lower, can't fail the build for the wrong reason. Raise it once the first CI run has measured branch coverage.

### 2.4 Performance

`python scripts/benchmark_dashboard.py`, median of 5 runs:

| Trades | SQL | Report | Dashboard data | Full page | Filtered page | Peak memory | Page size | Chart data |
|---|---|---|---|---|---|---|---|---|
| 100 | 0.6 ms | 9 ms | 10 ms | 17 ms | 2 ms | 1.1 MB | 168 KB | 38 KB |
| 1,000 | 6 ms | 72 ms | 84 ms | 106 ms | 6 ms | 5.4 MB | 488 KB | 290 KB |
| 10,000 | 76 ms | 569 ms | 677 ms | 777 ms | 33 ms | 30.7 MB | 3,032 KB | 2,833 KB |

- **Bottleneck found:** profiling showed `datetime.strptime` called 50,202 times for 10,000 trades, about a third of the request. It was replaced with a strict pattern plus `fromisoformat` (`tradeledger/timestamps.py`).
- **Measured gain:** Phase 6 and Phase 7 code back to back, two rounds each, same machine:
  - 10,000 trades: 923–963 ms down to 661–695 ms (about 28% faster);
  - 1,000 trades: 113–118 ms down to 93 ms (about 20% faster).
- **No speed regression** from the other Phase 7 changes: measured back to back before the fix, the two versions were within ±10% of each other.
- **Not changed, with evidence:**
  - **SQL** is 76 ms at 10,000 trades, so no index was added.
  - **Page size:** 3.0 MB at 10,000 trades, but 204 KB gzipped, and 488 KB (36 KB gzipped) at a realistic 1,000. Shrinking the chart data would mean giving up the exact-decimal payload and its agreement checks. Recommendation: enable compression in a reverse proxy.
- **Variability:** the same Phase 6 code measured 764 ms in the Phase 6 session and 923–983 ms in this one; the machine's load varies. **These numbers are not guarantees** and are only comparable within one session.
- **Browser rendering speed:** not measured, because real Chart.js couldn't run.

### 2.5 CI workflow (`.github/workflows/ci.yml`)

- **YAML:** parses. Triggers: push, pull request and manual. `permissions: contents: read`. Every job has a timeout, and only official actions are used, at major versions confirmed as released (`checkout@v6`, `setup-python@v6`, `setup-node@v6`, `upload-artifact@v6`).
- **Local rehearsal** of every `run:` command:
  - **pass:** lint, the non-browser tests, the JavaScript tests, browser step 1/3 (84 passed) and step 2/3 (1 passed);
  - **expected failure:** `--cov`, because pytest-cov isn't installed here.
  - The rehearsal also **caught a real problem**: one unformatted file that would have failed CI. It was fixed.

## 3. Not verified (and why)

| Item | Status | What would verify it |
|---|---|---|
| **Real Chart.js rendering** of the seven charts | **NOT VERIFIED.** jsDelivr, unpkg and npm were unreachable from this environment (retried at the start of Phase 7), and no file was supplied. The 10 `chartjs_real` tests are written and their harness was dry-run, but they were skipped. | The CI browser job (downloads 4.5.1 from npm, runs `chartjs_real` with skipping turned into failure), or put the file in `tradeledger/static/vendor/chartjs/` and run `python -m pytest -m chartjs_real -ra` |
| GitHub Actions on GitHub | **Never run**; GitHub was unreachable | Push the repository; check the Actions tab |
| Branch coverage; coverage.py's own figures | Not measured (not installable) | The CI `python` job |
| `pip-audit` | Not run (no network); a manual advisory check was done instead (security review, section 3) | The CI `audit` job |
| Fresh `pip install` in a new virtual environment | Not possible (no network); every pinned version matches the installed one | The CI `python` job, or locally |
| Python 3.11 | Only 3.13 was available here | The CI matrix (3.11 and 3.13) |
| Firefox, Safari, real phones | Only Chromium | Manual check |
| Screen readers; WCAG audit; colour contrast | Automated rule checks only (section 2.1) | Manual test with NVDA or VoiceOver, plus a contrast checker |

**The "harness dry run" explained:** to make sure the `chartjs_real` tests aren't broken, they were run once in a throwaway copy against a deliberately fake library file, which was then deleted.
- **Passed (6):** loading a self-hosted file under SRI and the strict CSP works, and so does reading chart state.
- **Failed, as designed (1):** the genuineness test, which correctly rejected the fake.
- **Failed because of the fake (1):** resizing, since the fake doesn't resize like Chart.js.
- **Failed, and fixed (1):** an unsuitable piece of test data (no negative strategy).

This proves only that the test harness runs; **it says nothing about Chart.js drawing**.

## 4. Manual procedure to verify the real charts (about 10 minutes)

1. On a computer with internet access: `npm pack chart.js@4.5.1`, unpack it, and copy `package/dist/chart.umd.min.js` and `package/LICENSE.md` into `tradeledger/static/vendor/chartjs/`. Or download them from the URLs in `docs/THIRD_PARTY_NOTICES.md`.
2. Run `python -m pytest -m chartjs_real -ra`. Expect 9 passed and 1 skipped (screenshots), or add `TRADELEDGER_SCREENSHOT_DIR=docs/screenshots` for 10 passed.
3. Open `/analytics` and look:
   - seven drawn charts;
   - a gap for any empty month, and Monday to Sunday on the weekday chart;
   - a triangle at the deepest drawdown;
   - green and red bars, with the values also written in tooltips;
   - tooltips matching the tables;
   - no console errors;
   - no sideways scrolling at phone width.

## 5. Release package

`tradeledger_phase7.zip` contains the complete project, minus caches, databases, `.env` and virtual environments. Verified by extracting it into an empty folder, then:
1. checking the file list against the source (identical);
2. creating a fresh database with the CLI and loading the sample data;
3. running the full suite, lint, the format check and the JavaScript tests;
4. running the audit;
5. starting the server and requesting every page.

The exact results are reported with the delivery.

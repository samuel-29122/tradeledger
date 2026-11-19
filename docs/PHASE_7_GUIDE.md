# TradeLedger — Phase 7 Guide: Testing, CI, Security and Portfolio Readiness

Phase 7 added no trading features. It changed **how TradeLedger is built and trusted**: it measured the project, found real problems, fixed them with tests that prove the fixes, and set up the tooling a team would expect: lint, formatting, coverage, CI, a security review and release discipline.

Each section explains things in plain English first, then technically, then why, then how it's tested, debugged and explained in an interview.

---

## 1. Phase 7 objectives

**Plain English:** make TradeLedger something an employer can trust and a team could work on, not just something that works on one laptop.

**What "done" meant:**
1. Re-measure the Phase 6 claims instead of trusting them.
2. Add tests only where they protect a real requirement or failure mode.
3. Measure coverage honestly.
4. Configure lint and formatting.
5. Automate everything in CI.
6. Review security and fix what is justified.
7. Test real user journeys and accessibility in a browser.
8. Measure performance and fix only proven bottlenecks.
9. Package and document a release.

**What Phase 7 did *not* do:** authentication, new trading features, deployment. Those stay out of scope, and the documents say so.

---

## 2. Phase 6 baseline and verification

The first rule of QA: **a report is a claim until you reproduce it.** The Phase 6 ZIP was extracted into an empty folder and every number re-measured (details in `docs/PHASE_7_QA_REPORT.md`, section 1).

| Claim | Re-measured |
|---|---|
| 888 tests pass | ✓ 888 passed |
| 55 browser tests | ✓ but with a Chart.js **stand-in** (now labelled clearly) |
| 11 JavaScript tests | ✓ |
| Clean | ✗ **12 hidden `ResourceWarning`s.** pytest hides them by default; `-W default` shows them |
| Coverage | Never measured before: **98.57%** |
| CI, lint, formatter | None existed |

**Lesson:** "all tests pass" can hide problems. Making warnings visible was the first finding.

---

## 3. Final architecture

```
Browser ─ HTML forms (GET/POST + CSRF) ─┐     ┌─ JSON (curl, scripts)
                                        ▼     ▼
             Host allow-list → production guard → CSRF check     (request hooks)
                                        │
            ┌───────────────────────────┴──────────────────────────┐
            ▼                                                      ▼
   Website routes (web/)                                 JSON API (api/, /api/v1)
            └───────────────┬──────────────────────────────────────┘
                            ▼   shared by both
     validation.py · report_filters.py · timestamps.py
                            │
     repositories/ (parameterised SQL) ──────────► SQLite (schema.sql + migrations)
                            │
     performance/ trade_math → metrics → report → dashboard
                            │
                            ▼
     Jinja2 HTML + JSON chart data → static/js/analytics.js → Chart.js (drawing only)
```

**Corrections to the original brief's diagram:** the API is not a separate stack. It shares validation, repositories and the engine with the website. And three request checks (Host, guard, CSRF) sit in front of the routes.

**New module:** `tradeledger/timestamps.py` (section 16).

---

## 4. File inventory (NEW / MODIFIED / DELETED)

The full code of every file is in `docs/PHASE_7_CODE.md`.

**NEW**

| File | Purpose |
|---|---|
| `pyproject.toml` | pytest, coverage and Ruff settings |
| `requirements-dev.txt`, `requirements-browser.txt`, `constraints.txt` | Pinned tool and test dependencies; exact versions of indirect dependencies |
| `.github/workflows/ci.yml` | Continuous integration |
| `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/bug_report.md` | Review discipline |
| `tradeledger/timestamps.py` | Fast, strict stored-timestamp parser |
| `tests/browser_support.py` | Shared browser fixtures and Chart.js modes |
| `tests/test_financial_edge_cases.py`, `test_database_integrity.py`, `test_security_hardening.py`, `test_error_paths.py`, `test_timestamps.py`, `test_browser_journey.py`, `test_chartjs_real.py` | New tests (section 5) |
| `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md` | Project hygiene |
| `docs/PHASE_7_GUIDE.md`, `PHASE_7_CODE.md`, `PHASE_7_QA_REPORT.md`, `PHASE_7_SECURITY_REVIEW.md`, `PHASE_7_RELEASE_CHECKLIST.md` | Documentation |
| `docs/screenshots/phase7_*.png` | Screenshots from the final code |

**MODIFIED, with behaviour changes:**

| File | Change |
|---|---|
| `tradeledger/__init__.py` | Strong production key, `Cache-Control`, HSTS |
| `tradeledger/config.py` | Trusted hosts, `env_hosts()`, version 0.7.0 |
| `tradeledger/validation.py` | Decimal magnitude limits |
| `tradeledger/performance/trade_math.py` | Overflow guard; fast parser |
| `tradeledger/performance/report.py`, `performance/dashboard.py`, `web/filters.py` | Fast parser |
| `tradeledger/web/__init__.py`, `web/guard.py`, `web/csrf.py` | Explicit hook order |
| `tradeledger/web/trades.py` | Plan-panel guard |
| `tradeledger/api/openapi.py`, `docs/openapi.json` | Documents the number limits |
| `tradeledger/static/js/analytics.js` | Fallback text |
| `tradeledger/web/views.py`, `templates/about.html` | Roadmap; Host-check note |
| `scripts/benchmark_dashboard.py` | 100 trades, SQL time, memory, chart size, `--profile` |
| `.env.example`, `.gitignore`, `README.md`, `requirements.txt`, `docs/THIRD_PARTY_NOTICES.md`, `static/vendor/chartjs/README.md` | Settings and documentation |

**MODIFIED, layout only (`ruff format`):** most other Python files (section 13).

**MODIFIED tests:**
- `test_app.py`, `test_web_analytics.py`: 64-character production keys.
- `test_web_pages.py`, `test_web_security.py`: close responses; About test.
- `test_browser.py`: shared fixtures and markers.
- `test_migrations.py`: `zip(strict=True)`.
- `test_openapi.py`, `test_seed_and_analytics.py`, `test_dashboard_data.py`, `test_db.py`, `test_web_integration.py`, `test_report_filters.py`: lint fixes only (same assertions).
- `tradeledger/api/http.py`, `performance/metrics.py`, `report_filters.py`, `web/instruments.py`, `cli.py`: Ruff's automatic modernisations (`X | None`, `dict.fromkeys`, import order, `zip(strict=True)`), with no change in behaviour.
- `tests/js/analytics.test.js` is unchanged.

**DELETED:** `pytest.ini`. Its settings moved to `pyproject.toml`, so there is one configuration file.

---

## 5. Automated testing strategy

**Plain English:** different tests catch different mistakes, so each kind is used for what it is good at.

```
          ▲  few, slow, realistic       Browser journey (Chromium)
          │                             Real Chart.js tests (when the file exists)
          │                             Route tests (Flask test client), API + OpenAPI
          │                             Database tests (real SQLite, temp files)
          ▼  many, fast, precise        Unit tests: trade maths, metrics, validation, JS
```

**Rules used throughout:**
1. **One reason per test.** Each new test names the requirement or bug it protects, in its docstring.
2. **Independent expected values.** Worked out by hand or by separate code, never by calling the function under test.
3. **Isolation.** Every test gets a temporary database (`conftest.py`). Each browser module gets its own server and database.
4. **Prove tests can fail.** Negative controls (CSP, accessibility, strptime counter), planted defects (22 of 22 caught), and new security tests run against the old code (25 fail there).
5. **Warnings are errors** (`filterwarnings = ["error"]`).

**Counts:**

| Stage | Collected | Result |
|---|---|---|
| Phase 6 | 888 | 888 passed |
| Phase 7 | 1,038 | 1,028 passed, 10 skipped (real Chart.js) |

---

## 6. Unit testing

**Plain English:** test one small piece at a time, with numbers you can check on paper.

**Example:** `test_high_precision_short_with_fractional_multiplier`:

```
SHORT, entry 1.23456789, exit 1.23356789, stop 1.23556789, size 3, multiplier 0.1, fees 0.0002
move  = 0.001             gross = 0.001 × 3 × 0.1 = 0.0003
net   = 0.0003 − 0.0002 = 0.0001
risk  = 0.001 × 3 × 0.1 = 0.0003      R = 0.0001 / 0.0003 = 1/3
```

1/3 can't be written exactly in decimal, so the test compares with `Fraction(1, 3)` and allows a difference in only the 55th decimal place.

**Why fractions?** Comparing two decimals produced by the same code proves nothing. An exact fraction is an independent truth.

**What the new financial tests add** (the Phase 6 suite already covered long/short, fees, breakeven, multipliers, zero risk and currencies):
- crypto-sized prices (8 decimal places);
- fees turning a SHORT winner into a loss;
- a loss beyond the stop (−1.5R);
- planned R:R with a missing target or one on the wrong side;
- one-trade datasets (profit factor `None` with no losses, `0` with no wins);
- drawdown that recovers and then sets a new, larger maximum.

---

## 7. Integration testing

**Plain English:** check that the pieces work *together*: route → validation → repository → database → engine → template.

**Example:** `test_an_out_of_range_row_already_in_the_database_cannot_break_pages` writes a bad row straight into SQLite. It then requests six real pages through Flask and asserts each answers 200, with the trade listed as excluded. A unit test of `evaluate_trade` alone couldn't show that **every** page survives.

**How:** Flask's **test client** sends real HTTP requests to the app in memory: fast, with no browser or network.

---

## 8. API testing

The API suites already checked status codes, content types, unknown and missing fields, malformed JSON, pagination, filters, empty responses, the error format, production 403 and OpenAPI agreement. Every response is validated against the OpenAPI schema with `jsonschema`.

**Phase 7 probed the unusual cases:**

| Request | Response |
|---|---|
| JSON array, string, `null` or number as the body | 400 `malformed_json` |
| `NaN` or `Infinity` literals | 400 |
| Nested object in a text field; `true` as a size | 400 field errors |
| `"1e999999"` | **500, then broken pages: bug F1, now 400** |

**The lesson:** fuzz-style probing ("what if the input is absurd?") found the most serious bug of the phase.

---

## 9. Database testing

`tests/test_database_integrity.py` builds a deterministic database: the sample data plus 150 generated trades (fixed random seed), ten of them sharing an entry time.

- **List = count = independent filter**, for all 486 combinations of instrument × status × strategy × session × date range × date field. The independent filter is a few lines of plain Python, written separately from the SQL.
- **Pagination:** page sizes 1, 3, 7, 50, 166 and 500 together cover every row exactly once, in order.
- **Ties:** rows with equal entry times have a stable order (newest, then highest id), so a page boundary can't swap them.
- **Migrations:** versions are contiguous (2, 3, …); a fresh database ends on the latest; migration scripts never set the version themselves.

**A debugging story:** the tie test first failed with 11 tied rows instead of 10. That wasn't an app bug: generated trade 37 (37 mod 3 = 1, mod 28 = 9, mod 9 = 1) also landed exactly on 09:00. The fix gave the deliberate ties minute 30, which the generator never produces. Always check the test data before blaming the code.

**Migrations in Phase 7:** none were needed. Constraints, foreign keys, transactions and rollbacks were already tested (`test_db.py`, `test_migrations.py`, `test_repositories.py`).

---

## 10. Browser testing

**Plain English:** drive a real Chromium browser the way a person would, because some bugs only exist in a browser: layout, JavaScript, focus and the security policy.

**The journey** (`test_full_user_journey`), one test and 15 steps:
1. Open the Overview and use the sidebar.
2. Create instrument QA7 (GBP, multiplier 2).
3. Add an open trade.
4. Close it and check **46.50 GBP, +1.55R**, worked out by hand in the docstring.
5. Search and filter the journal.
6. Edit the fees and check **43.50 GBP, +1.45R**.
7. Apply two dashboard filters and check the KPI and table; reset them.
8. Delete the trade, then confirm the journal, the analytics and a 404 for its page.

**Accessibility checks:**
- every form control has a label;
- one `h1` and no skipped heading levels;
- `lang="en-GB"`;
- tables have names and headers with `scope`;
- each chart has `role="img"`, a label and a visible description;
- keyboard focus is always visible (15 Tab presses);
- `prefers-reduced-motion` is respected;
- outcomes are written as words, and R values carry a sign;
- no horizontal scrolling at 200% zoom (720 and 683 px at 2× density);
- body text is at least 16 px on a phone.

`test_accessibility_checks_can_fail` breaks a page on purpose and proves the checks notice.

**What these checks are not:** a WCAG audit. Screen readers and contrast still need a human (QA report, section 3).

**Shared fixtures** live in `tests/browser_support.py`. Each test module gets its own server and database (`scope="module"`), so the journey can create and delete data without affecting other tests.

---

## 11. Chart.js verification

**The honest position:** real Chart.js rendering is **not verified** yet. The library couldn't be downloaded in the build environment (jsDelivr, unpkg and npm were all blocked).

**What Phase 7 did instead:** it made the gap impossible to hide, and built the means to close it.

| Marker | Chart.js | Counted as |
|---|---|---|
| `chartjs_stub` (10 tests) | Replaced by a stand-in that records `new Chart(...)` calls | Tests **our** code |
| `chartjs_offline` (1) | Blocked | Tests the fallback |
| `chartjs_real` (10) | The real file from `static/vendor/chartjs/` | **Skipped** here; never reported as passed |

**The real tests** (`tests/test_chartjs_real.py`) serve the file from TradeLedger itself, under `script-src 'self'`, with an SRI hash the test computes. They check:
- the version header and the MIT licence;
- `Chart.version == "4.5.1"`;
- all seven charts exist as Chart.js instances and paint pixels;
- the data equals the server's JSON;
- Chart.js's own tooltip text equals the table cell;
- February is a gap (`null`) and weekdays run Monday to Sunday;
- positive and negative bars differ in colour;
- the drawdown triangle sits at the deepest point;
- filters change the charts;
- resizing keeps charts inside their cards;
- there are no console or CSP errors;
- optionally, real screenshots.

**Closing the gap:** CI downloads `chart.js@4.5.1` with `npm pack`, which checks the registry's integrity hash, and runs these tests with `TRADELEDGER_REQUIRE_REAL_CHARTJS=1`, so a missing file **fails** instead of skipping. The manual procedure is in the QA report, section 4.

**Interview angle:** "I could have shipped the stand-in tests and called the charts tested. Instead I labelled them for what they prove, wrote the real tests, and made CI fail if the real library is missing."

---

## 12. Coverage reporting

**Plain English:** coverage tells you which lines your tests never ran. It cannot tell you whether the tests check the right thing.

**Commands:**

```bash
python -m pytest --cov=tradeledger --cov-report=term-missing --cov-report=html
# open htmlcov/index.html
```

**Configuration** (`pyproject.toml`):
- `source = ["tradeledger"]` (application code only);
- `branch = true`;
- `concurrency = ["thread"]`, because the browser tests run the app in a thread;
- `fail_under = 90`.

The only exclusion is `if TYPE_CHECKING:`; no application module is excluded.

**In this environment**, coverage.py couldn't be installed, so a small tracer based on `sys.monitoring` measured statement coverage. It was first checked on a module whose coverage is known.

| Measurement | Result |
|---|---|
| Phase 6 | 98.57% |
| Phase 7 | **99.68%** |
| Remaining | 11 defensive lines, each listed with a reason in the QA report |

**How coverage was used:** not as a target, but as a map. The uncovered lines pointed to untested *error paths*:
- the 413 page;
- unknown database errors (they must be re-raised, never swallowed);
- CLI messages;
- and, most usefully, **the trade page crashing on bad data**. Writing that test found a second place where the overflow bug (F1) broke a page.

**Why the CI threshold is 90%, not 99%:** branch coverage counts each `if` twice (true and false), so it's normally lower than statement coverage, and it hasn't been measured yet. A threshold that fails for the wrong reason teaches people to ignore CI.

---

## 13. Code quality

**Tool:** Ruff, which handles linting, import sorting and formatting in one fast tool, so there are no clashing formatters. Black and flake8 were available but would add nothing.

**Rules chosen** (`pyproject.toml`): pycodestyle, pyflakes, isort, bugbear, pyupgrade, **flake8-bandit (security)**, blind-except, simplify, comprehensions, misc, and Ruff's own rules. E501 (line length) is left to the formatter, and the typographic UI characters (–, …, ×) are allowed. Each test- or script-only exception has a written reason.

**What it found and what was done:**
- 1 unused import, `zip()` without `strict=` (now strict where lengths must match), merged `isinstance` checks, and `assert` in a script (replaced: `python -O` removes asserts).
- 15 "SQL injection" warnings: each reviewed. All were fixed column or table names with bound values. Each now has a `# noqa: S608` with its reason, so a *new* unsafe query would still be flagged.
- A blind `except Exception` in a test, narrowed to Playwright's error type.

**The most valuable finding wasn't a lint message.** Sorting imports changed behaviour: the production guard and the CSRF check are `before_request` hooks registered at import time, and alphabetical order put CSRF first. The existing test caught it. The fix registers both hooks explicitly, in order, in `web/__init__.py`. **A hidden dependency on import order is a design smell; make order explicit.**

**Formatting:**
- `ruff format` (line length 110, chosen to keep the change smaller) was applied once, as a separate, formatting-only step.
- The syntax trees of all 65 Python files were compared before and after: identical apart from whitespace in one docstring.
- In Git this belongs in its own commit, listed in a `.git-blame-ignore-revs` file so `git blame` skips it.

**Warnings as errors:** `filterwarnings = ["error"]` exposed 12 tests leaving files open; they now use `with client.get(...) as response:`.

---

## 14. GitHub Actions

**Plain English:** every push runs every check on a clean machine, so "it works on my computer" stops being an argument.

**The workflow** (`.github/workflows/ci.yml`) runs on push, pull request and manual dispatch, with four parallel jobs:
1. **python** (Python 3.11 and 3.13): install with constraints → `ruff check` → `ruff format --check` → `pytest -m "not browser"` with coverage → upload the HTML report.
2. **javascript:** Node 22 → `node --test` (no npm install needed).
3. **audit:** `pip-audit --strict`.
4. **browser:** install Chromium → step 1/3 stand-in tests → step 2/3 offline test → fetch real Chart.js → step 3/3 real rendering (skipping becomes failure) → upload screenshots.

**Quality choices:**
- `permissions: contents: read` (least privilege);
- `timeout-minutes` on every job;
- pip caching keyed on the requirements files;
- `concurrency` cancels outdated runs;
- `fail-fast: false`, so both Python versions report;
- official actions only, at confirmed current major versions;
- no secrets and no deployment.

**Verification here:** the YAML was parsed and checked, and every `run:` command was rehearsed locally. The rehearsal caught one unformatted file that would have failed CI. **The workflow has not run on GitHub yet.**

**CI vs CD:** CI (continuous integration) tests every change automatically. CD (continuous delivery or deployment) also releases it. TradeLedger has CI only, deliberately.

---

## 15. Security review

The full review is in `docs/PHASE_7_SECURITY_REVIEW.md`. The six findings:
- **F1 (Medium):** unbounded decimals crashed pages persistently. Fixed with magnitude limits plus an engine guard.
- **F2 (Medium):** DNS rebinding could reach the unauthenticated local server through the user's browser. Fixed with a Host allow-list.
- **F3 (Low):** a weak or placeholder production `SECRET_KEY` was accepted.
- **F4 (Low):** HTML pages had no `Cache-Control`.
- **F5 (Low):** no HSTS.
- **F6 (Low):** hook order depended on imports.

**How F2 works, in plain English:** your browser trusts pages from the same "origin" (scheme + host + port) to read each other's responses. A malicious site can make its own name point at 127.0.0.1. The browser then thinks `evil.example:5000` is that site, but the requests reach *your* TradeLedger. Checking the `Host` header (`evil.example`) and refusing unknown names breaks the attack.

**Proof the tests matter:** run against the Phase 6 code, 25 of the 40 security tests fail.

**The boundary stays explicit:** no authentication. Production defaults stay at 403, and the README and `SECURITY.md` say it's for local, single-user use.

---

## 16. Performance and benchmarks

**Method:**
1. Measure (`scripts/benchmark_dashboard.py`: 100, 1,000 and 10,000 trades; SQL, report, dashboard, page, filtered page, peak memory, page and chart-data size).
2. Profile (`--profile`).
3. Fix the biggest proven cost.
4. Re-measure **back to back** with the old code.

**The finding:** sorting the profile by *self time* showed `datetime.strptime` called 50,202 times (about 5 per trade), roughly a third of the request. `strptime` re-reads its format string on every call.

**The smallest effective fix** (`tradeledger/timestamps.py`):

```python
_STORED_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

def parse_stored(text):
    if not isinstance(text, str):
        raise TypeError(...)
    if not _STORED_SHAPE.fullmatch(text):
        raise ValueError(...)
    return datetime.fromisoformat(text[:-1])   # C code; still rejects 30 February
```

**Why it's safe:**
- the database CHECK constraints guarantee every stored timestamp has exactly this shape;
- `tests/test_timestamps.py` compares it with `strptime` on edge dates, invalid values and 5,000 random moments;
- a guard test counts calls into the standard library's `_strptime`, so a reintroduction anywhere fails.

**Result** (same machine, same session, two rounds each):

| Trades | Phase 6 page | Phase 7 page | Change |
|---|---|---|---|
| 1,000 | 113–118 ms | 93 ms | about 20% faster |
| 10,000 | 923–963 ms | 661–695 ms | about 28% faster |

**Deliberately not done:**
- **An index:** SQL was 76 ms of the total, so an index couldn't help much.
- **Shrinking the chart data:** gzip turns 3.0 MB into 204 KB, and a realistic 1,000-trade journal is 488 KB.
- **Caching:** no evidence it was needed.

**Honesty about numbers:** the same Phase 6 code measured 764 ms in its own session and up to 983 ms today. Only same-session comparisons are meaningful, and none of these are guarantees.

---

## 17. Git workflow

The ZIP contains no `.git` folder. Here is how to put Phase 7 on GitHub so the history is reviewable:

```bash
git switch -c phase-7-quality
git add pyproject.toml requirements*.txt constraints.txt .gitignore && git rm pytest.ini
git commit -m "chore: add Ruff, coverage and pinned dev dependencies"
git add -A tradeledger tests scripts && git commit -m "style: apply ruff format (no behaviour change)"
#   ...then one commit per change: hook order, magnitude limits, trusted hosts,
#   key strength, headers, timestamp parser, new tests, CI, docs
git push -u origin phase-7-quality      # open a pull request; CI runs
```

Put the formatting commit's hash in `.git-blame-ignore-revs` (`git config blame.ignoreRevsFile .git-blame-ignore-revs`).

**Conventions** (`CONTRIBUTING.md`): branch prefixes, small single-purpose commits, a failing test first, the pull request template's checklist, and a changelog entry.

**Never commit:** `.env`, databases, real trades, `htmlcov/`, caches. `.gitignore` covers them; this was checked with `git status --ignored` in a scratch repository: 132 files tracked, only secrets and outputs ignored.

---

## 18. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `400 Bad Request` for every page | Host not in `TRADELEDGER_TRUSTED_HOSTS` (for example using your LAN IP) | Add it: `TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1,192.168.1.20` |
| `RuntimeError: SECRET_KEY is too weak` | Production with a short or example key | `python -c "import secrets; print(secrets.token_hex(32))"` |
| A test fails with `PytestUnraisableExceptionWarning` / `ResourceWarning` | A response or file left open | `with client.get(url) as response:` |
| `ruff format --check` fails in CI | File not formatted | `ruff format .` then commit |
| `--cov` "unrecognized arguments" | pytest-cov not installed | `pip install -r requirements-dev.txt -c constraints.txt` |
| Browser tests skipped | No Playwright or Chromium | `pip install -r requirements-browser.txt -c constraints.txt`; `python -m playwright install chromium` |
| `chartjs_real` skipped | Library file absent | Section 11, or QA report section 4 |
| Field error "at most 15 digits before the decimal point" | The new magnitude limit | Check the number; real values never get near it |
| A trade listed under "excluded: values are too large" | An out-of-range row from before Phase 7 | Edit or delete that trade |
| Benchmark numbers differ from the docs | Machine load | Compare old and new code back to back, same session |

---

## 19. Clean installation

```bash
unzip tradeledger_phase7.zip && cd tradeledger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env            # set SECRET_KEY
flask --app tradeledger init-db
flask --app tradeledger seed-db
python -m pytest                # expect browser tests skipped unless Playwright is installed
flask --app tradeledger run --debug
```

**Existing Phase 6 database?** No migration is needed (still schema version 2). Copy your `instance/` folder across.

---

## 20. Release packaging

Follow `docs/PHASE_7_RELEASE_CHECKLIST.md`. In short:
1. All quality gates pass.
2. Bump the version and the changelog.
3. Build the ZIP from a clean tree (no caches, databases or secrets).
4. Extract it into an empty folder, initialise a fresh database, run the full suite and a server smoke test there, and compare the file list with the source.
5. Write the release notes: the changelog plus the "not verified" list.

---

## 21. Interview preparation

Each answer is based on what this project actually demonstrates. Adapt the wording; don't recite it.

### Python

**Q: What is an application factory, and why use one?**
A function, `create_app(config_name, overrides)`, that builds and returns a configured Flask app instead of a global `app` object. Tests can create a fresh app per test with a temporary database, and development, testing and production differ only in configuration. In TradeLedger it also enforces rules at start-up, such as refusing a weak production `SECRET_KEY`.

**Q: Modules and packages?**
A module is one `.py` file; a package is a folder with `__init__.py`. TradeLedger's package has sub-packages by responsibility: `web`, `api`, `performance` and `repositories`. Phase 7 showed a trap: importing a module can run code, such as registering request hooks, so *import order* can change behaviour. I fixed that by registering the hooks explicitly.

**Q: How do you handle exceptions well?**
Catch the narrowest type you can handle and translate it at a boundary. For example, `sqlite3.IntegrityError` becomes a `ValidationError` with a field message, but unknown database errors are **re-raised, never swallowed**; a test proves it. Ruff's blind-except rule flags `except Exception`. In the overflow fix I caught `decimal.DecimalException` precisely, and turned it into the engine's existing "excluded trade" result.

**Q: Dataclasses?**
Classes that generate `__init__`, `__eq__` and `__repr__` from type-annotated fields. TradeLedger uses frozen dataclasses for results (`TradeResult`, `Summary`, `ReportFilters`), so they can't be changed after calculation, and they compare by value, which makes tests simple.

**Q: Type hints?**
Annotations such as `def parse_stored(text: str) -> datetime`. Python doesn't enforce them at run time, but they document intent and enable tools like mypy and IDE checks. Ruff's pyupgrade rules modernised them to `X | None` for Python 3.11+. The type hints are partial; adding mypy in CI would be a sensible next step.

**Q: Why `Decimal` and not `float`?**
Floats are binary, so 0.1 + 0.2 isn't 0.3, and money must be exact. TradeLedger uses `Decimal` with a 60-digit context and rounds only for display. Phase 7 found that `Decimal` also needs **magnitude** limits: `1e999999` is a valid Decimal that overflowed. Tests compare results with exact `Fraction`s.

**Q: How do you manage dependencies?**
Direct dependencies are pinned with `==` in `requirements*.txt`, split into runtime, dev and browser. Indirect ones are pinned in `constraints.txt`, used with `pip install -c`. Updates are deliberate and recorded in the changelog, and `pip-audit` checks for known vulnerabilities in CI.

### Testing and QA

**Q: Unit vs integration tests?**
A unit test checks one function with known inputs, for example the R-multiple of one trade worked out on paper. An integration test checks pieces together: my out-of-range row test writes to SQLite, then requests six real pages and checks none crashes. You need both: units pinpoint errors; integration catches the gaps between units.

**Q: Fixtures?**
Reusable setup that pytest injects by name. `app` builds an application with a temporary database, and `seeded_client` adds the sample data. Browser fixtures start a real server per test module. Scope matters: `module` scope gave the journey test its own database so it couldn't affect other tests.

**Q: Parametrised tests?**
One test function run with many inputs, for example 21 timestamp edge cases, or each weak key. Each case reports separately, so you see exactly which input fails.

**Q: Mocking?**
Replacing a real dependency with a controlled one. The Chart.js stand-in is a mock, and I labelled those tests `chartjs_stub` because a mock proves your code calls the dependency correctly, not that the dependency works. I also used `monkeypatch` to count calls into the standard library's `strptime`, as a regression guard.

**Q: Regression testing?**
Every bug gets a test that fails before the fix and passes after. I proved it by running the new security tests against the old Phase 6 code: 25 failed there, as expected.

**Q: Test isolation?**
No test depends on another or on shared state. Every test gets its own temporary SQLite file, and the testing configuration refuses to start without an explicit database path, so tests can never touch real data.

**Q: How do you know your tests are good, not just green?**
Three ways:
- **Negative controls:** I break the page on purpose and the accessibility checks must notice.
- **Planted defects:** 22 deliberate bugs, all caught. The one that was missed at first revealed a missing test input.
- **Independent expected values:** hand calculations and a separate audit that recalculates every figure and found 0 disagreements in 93,361 comparisons.

**Q: Browser automation?**
Playwright drives real Chromium. I tested a 15-step journey (create, close, edit, filter, delete, then check the analytics update) and accessibility rules, at four screen widths and 200% zoom.

**Q: Coverage: is 99% good?**
It means almost every line ran, not that it was checked correctly. I used coverage as a map to find untested error paths. One of them revealed a second crash from the same overflow bug.

**Q: Negative testing?**
Testing what *should* fail: absurd JSON, NaN, huge exponents, wrong Host headers, weak keys, wrong-side stops. The worst bug of the phase came from negative testing.

**Q: Root-cause analysis: give an example.**
- **Symptom:** after sorting imports, a production POST returned 400 instead of 403.
- **Investigation:** the only change was import order. Both the guard and the CSRF check are `before_request` hooks registered when their modules are imported, so the order swapped.
- **Root cause:** a hidden dependency on import order.
- **Fix:** register the hooks explicitly in a documented order, and add a test that checks the order directly.

**Q: What is CI and how does yours work?**
Every push runs lint, formatting, tests with coverage on two Python versions, JavaScript tests, a dependency audit and browser tests, on a clean machine. In mine, the real Chart.js tests fail rather than skip if the library is missing. I'm honest that it hasn't run on GitHub yet; I rehearsed every command locally.

### Flask and web development

**Q: How does Flask handle a request?**
It matches the URL to a route, then runs `before_request` hooks (TradeLedger: Host check, production guard, CSRF), then the view, then `after_request` hooks (security headers), and finally error handlers if something raised.

**Q: HTTP methods: why is delete a POST?**
GET must never change data: it can be prefetched, cached or triggered by an image tag. Delete goes through a confirmation page and a CSRF-protected POST. The API uses DELETE, which a cross-site form can't send.

**Q: Forms vs APIs?**
Both share validation, repositories and the engine. Forms return HTML with an error summary and keep what you typed; the API returns JSON errors with field details and status codes.

**Q: Input validation?**
On the server, always. Browser checks are a convenience. One function, `validate_trade`, collects every problem at once. Phase 7 added magnitude limits after the overflow bug.

**Q: Templates?**
Jinja2 with autoescaping and macros for labelled inputs. Chart data goes in `<script type="application/json">` through `tojson`, which escapes `<`, so it can't break out of the tag.

**Q: CSRF?**
Cross-site request forgery: another site makes your browser submit a form to TradeLedger. The defence is a random per-session token in every form, compared in constant time, plus SameSite cookies.

**Q: XSS?**
Cross-site scripting: injected script running in your page. The defences are escaping all output, never inserting HTML from JavaScript, and a Content-Security-Policy with no inline scripts, so even an injection couldn't run.

**Q: Security headers?**
CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, `Cache-Control: no-store` on pages with data, and HSTS in production. Headers reduce risk, but they don't replace authentication; TradeLedger says so.

**Q: Error handling?**
Custom 404, 405, 413 and 500 responses, HTML for the site and JSON for the API. The traceback goes to the server log only, never to the client.

### SQL

**Q: Joins?**
`trades JOIN instruments` gives every trade its symbol, currency and multiplier. The count query needs the join because search can match the symbol.

**Q: Constraints?**
Rules the database enforces even if application code has a bug. TradeLedger has foreign keys, CHECK constraints (stop and target on the correct side, CLOSED needs an exit price, exact timestamp shape) and triggers. The timestamp CHECK is what made the fast parser safe.

**Q: Indexes: did you add any?**
No. Profiling showed SQL was 76 ms out of about 700 ms at 10,000 trades; the time was in Python. An index has a cost on every write, so adding one without evidence is premature optimisation.

**Q: Transactions?**
Multiple statements that succeed or fail together. Writes use `with conn:`, and migrations run each version in one transaction with the version bump, so a failure leaves the database untouched.

**Q: Migrations?**
Numbered, forward-only schema changes, tracked in `PRAGMA user_version`. They can be repeated safely and back up the file first. I tested that versions are contiguous and that a fresh database ends on the latest.

**Q: Parameterised queries?**
Values go in `?` placeholders, so the database never treats them as SQL. Ruff flagged 15 places where SQL text was built with f-strings; each only inserts fixed column names from the code, and each is annotated with the reason. Injection strings are tested as plain data.

### DevOps

**Q: CI vs CD?**
CI tests every change automatically; CD also delivers or deploys it. I built CI only; deployment needs authentication first.

**Q: Why pin dependencies?**
So every install, mine, CI's or a reviewer's, gets identical code. That makes failures reproducible and updates deliberate.

**Q: How do you handle secrets?**
Never in the repository. `.env` is ignored by Git, `.env.example` has placeholders, production refuses weak or placeholder keys, and the workflow needs no secrets at all.

**Q: Build reproducibility?**
Pinned direct and indirect dependencies, Python versions fixed in the CI matrix, one configuration file (`pyproject.toml`), and a ZIP verified by extracting it into an empty folder and running everything there.

**Q: Release testing?**
A checklist: quality gates, dependency audit, migration checks, clean-extraction test, and honest release notes that list what wasn't verified.

---

## 22. Portfolio and CV presentation

### A. GitHub repository description

> Trading journal and performance analytics in Python/Flask/SQLite: exact Decimal P&L and R-multiples, an accessible analytics dashboard, a versioned JSON API with OpenAPI 3.1, and 1,000+ automated tests (unit, integration, API, database, security, Playwright browser) with Ruff, coverage and GitHub Actions CI.

(Under 350 characters, as GitHub's "About" field requires. Update the test count if it changes.)

### B. CV bullet points

- Built TradeLedger, a full-stack trading journal in **Python and Flask** with server-rendered pages, CSRF protection and a versioned **JSON API documented with OpenAPI 3.1**, sharing one validation and calculation layer between website and API.
- Designed an **SQLite** schema with foreign keys, CHECK constraints, triggers and transactional, backed-up migrations; verified list, count and pagination consistency across 486 filter combinations against an independent implementation.
- Wrote **1,000+ automated tests** (pytest, Playwright, Node.js), including an end-to-end browser journey and accessibility checks. Proved test strength with 22 planted defects (all caught) and an independent audit (0 disagreements in 93,361 comparisons).
- Implemented **exact Decimal analytics** (P&L, R-multiples, expectancy, drawdown) with hand-calculated tests. Profiled the dashboard and removed its main bottleneck, about 28% faster at 10,000 trades, with identical results proven by equivalence tests.
- Introduced **Ruff linting and formatting, coverage measurement (99.7% statement coverage), pinned dependencies and a GitHub Actions CI** pipeline. Led a security review that found and fixed six issues, including a persistent denial-of-service through unbounded decimal input and a DNS-rebinding exposure.

*Honest framing:* this is a personal portfolio project, built with AI assistance in a structured, phase-by-phase process, with every result verified by tests run during development. Say so if asked. What you can demonstrate is that you understand, can explain and can change every part of it.

### C. LinkedIn project description

> **TradeLedger: trading journal and performance analytics (personal project)**
> A Flask and SQLite application that records trades with their plan and outcome, and measures performance in R (multiples of risk), so results are comparable across instruments and currencies. It covers exact decimal calculations, an accessible dashboard with seven charts, and a documented JSON API.
> In the latest phase I focused on engineering quality:
> - 1,000+ automated tests, including real-browser journeys;
> - linting, formatting and coverage;
> - a GitHub Actions pipeline;
> - a written security review that led to six fixes;
> - profiling that made the dashboard about 28% faster with no change in results.
> It's built for local, single-user use, and the documentation is explicit about what isn't implemented or verified yet.

### D. Explaining the project

**30 seconds:** "TradeLedger is a trading journal I built in Python and Flask. It records each trade's plan and result and works out performance in R-multiples with exact decimals, so you can see whether a strategy has an edge. The part I'm proudest of is the quality work: over a thousand tests, CI, a security review, and proving the tests actually catch bugs."

**2 minutes:** add the architecture and three stories.
- **Architecture:** one validation and calculation layer shared by the website and the API.
- **Story 1, negative testing:** a seven-character input, `1e999999`, could break every analytics page until the row was deleted by hand.
- **Story 2, root-cause analysis:** an import-sorting tool silently changed the order of security checks, and an existing test caught it.
- **Story 3, honesty:** the chart tests used a stand-in, so I labelled them as such, wrote real rendering tests, and made CI fail if the real library is missing.

**5-minute technical walkthrough:**
1. **Domain:** R = net P&L ÷ initial risk; why `Decimal`; why closed-order chronology.
2. **Layers:** repositories → engine → dashboard data → routes → templates → JavaScript (drawing only).
3. **Database:** constraints and migrations; the timestamp CHECK that made a performance fix safe.
4. **Testing strategy:** the pyramid; independent expected values; isolation; warnings as errors.
5. **Proving tests:** planted defects, negative controls, the audit.
6. **Security:** F1 and F2 (explain DNS rebinding); the "no authentication" boundary.
7. **Performance:** measure → profile → smallest fix → re-measure back to back; why no index.
8. **CI:** four jobs, and what each proves.
9. **Limits and next steps:** authentication, self-hosted Chart.js, first CI run.

### E. Hiring-manager review (junior software engineer / QA automation)

**Strengths**
- Unusually disciplined testing for a junior portfolio: independent expected values, mutation-style checks, negative controls, browser journeys, and accessibility checks with a control.
- Real bugs found *by the process* (overflow DoS, DNS rebinding, hook order), each with a fix and a regression test, and each proven against the old code.
- Honest documentation: what isn't verified is listed, never hidden; screenshots are captioned truthfully.
- Sound fundamentals: parameterised SQL, constraints, migrations, CSRF, CSP, exact decimals.

**Weaknesses**
- No authentication, so it isn't deployable as a multi-user service.
- CI has never run on GitHub, and real Chart.js rendering is unverified.
- Partial type hints and no static type checking.
- A single SQLite database, and no deployment experience shown.

**Risks (from the reviewer's side)**
- **Authorship:** AI assistance means an interviewer will probe understanding. Being able to explain any file, and to make a small change live, is essential.
- **Size:** 1,000+ tests for a small app could look like padding unless you can explain why each group exists. The QA report's table helps.

**What makes it credible**
- Commit-level history (if pushed as recommended).
- Reproducible commands, and a QA report that separates verified from unverified.
- Specific numbers measured honestly, including the ones that look worse (machine variance).

**What would most improve it next**
1. Push to GitHub and get a green CI run, including real Chart.js rendering.
2. Add authentication (Flask-Login, hashed passwords), then re-enable the journal in production behind it.
3. Add mypy to CI and complete the type hints.
4. Deploy a demonstration instance with synthetic data behind HTTPS.
5. A short screen recording of the journey test running.

---

## 23. Learning exercises

1. **Branch coverage (CI):** after the first CI run, open the coverage artefact and find a branch that is never taken both ways. Write a test that takes it, with the expected value worked out by hand.
2. **Planted defect:** in a *copy* of the project, change `classify()` in `trade_math.py` so that `net >= 0` is a win. Which tests fail? If none, write one.
3. **Trusted hosts:** start the server with `--host 0.0.0.0`, open it from your phone using your computer's LAN address (expect 400), then fix it through `.env` only.
4. **Type checking:** run `mypy tradeledger/timestamps.py tradeledger/report_filters.py`, fix what it reports, and consider adding a `mypy` CI step.
5. **Direction filter (carried over from Phase 6):** add `direction` (LONG/SHORT) to the dashboard and analytics API following the `session` filter's pattern: validation in `report_filters.py`, SQL in `_where`, OpenAPI, template. Then extend `scripts/qa_dashboard_agreement.py` to cover it, and check it still reports 0 disagreements.
6. **Real Chart.js:** follow the QA report's section 4 procedure and record the result in the QA report.

---

## 24. Known limitations

- No authentication or multi-user support. Production defaults to 403 for all trading data.
- Real Chart.js rendering is not verified, and CI hasn't run on GitHub.
- Branch coverage isn't measured yet; statement coverage came from a standard-library tracer.
- Accessibility is checked by automated rules only (no screen reader, contrast or WCAG audit); only Chromium was tested.
- Python 3.11 is promised but was only tested here on 3.13 (CI tests both).
- Large journals produce large pages (3 MB at 10,000 trades uncompressed; serve it with compression).
- No currency conversion, partial closes, time-zone settings or editable instruments (unchanged from Phase 6).

---

## 25. Future roadmap

Proposed, not agreed:
1. **Verify:** first green CI run on GitHub, including real Chart.js; record the results.
2. **Authentication:** user accounts with hashed passwords, sessions and logout; the journal and API behind login.
3. **Static typing:** complete the type hints; mypy in CI.
4. **Deployment:** a reverse proxy with HTTPS and compression; a demonstration instance with synthetic data; backups.
5. **PostgreSQL:** only if multiple users or concurrent writers make SQLite insufficient (measure first).

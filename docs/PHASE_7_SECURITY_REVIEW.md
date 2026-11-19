# Phase 7 Security Review

**Application:** TradeLedger 0.7.0 · **Date:** 15 November 2026 · **Scope:** the whole application at the end of Phase 7, starting from the Phase 6 release (0.6.0).

**Method:**
- Read the code for every item in the threat list below.
- Probed the running application with hostile inputs through Flask's test client and Chromium.
- Ran Ruff's security rules (`S`, the Bandit rule set re-implemented in Ruff).
- Checked the pinned dependencies against published advisories.
- Wrote a regression test for every finding, then ran the new tests against the unchanged Phase 6 code to prove they detect the problem: 25 of them fail there.

**Boundary:** TradeLedger has **no authentication**. It is intended for one person on their own computer, or behind access control they provide. Nothing in this review makes it safe to expose on the internet, and passing these tests is not a claim that it is.

## 1. Summary of findings

| ID | Finding | Severity | Status |
|---|---|---|---|
| F1 | Decimal inputs had no magnitude limit: `1e999999` was stored as a 1,000,000-digit string and crashed every page that evaluated it | **Medium** (availability; persistent) | Fixed + tests |
| F2 | No Host header allow-list: DNS rebinding could reach the unauthenticated local journal and API through the user's own browser | **Medium** (confidentiality and integrity) | Fixed + tests |
| F3 | Production accepted a short or published placeholder `SECRET_KEY` | **Low** | Fixed + tests |
| F4 | HTML pages containing trading data had no `Cache-Control` header | **Low** | Fixed + tests |
| F5 | No HSTS header in production | **Low** | Fixed + test |
| F6 | The production guard ran before the CSRF check only because of import order | **Low** (design fragility; a production POST got 400 instead of 403) | Fixed + tests |

Severity uses a simple scale (High / Medium / Low) based on impact × likelihood for the intended local, single-user deployment. CVSS was not used, because the deployment context matters more here than a generic score.

## 2. Findings in detail

### F1 · Unbounded decimal magnitude (Medium)

- **What:**
  - `validation.parse_decimal` limited **significant digits** (15) but not magnitude. `"1e999999"` has one significant digit, so it passed.
  - `to_db_decimal` writes values without an exponent, so the database stored `1` followed by 999,999 zeros. That's about 1 MB from a 7-character request, which also slips under the 64 KB request limit.
  - Evaluating the trade raised `decimal.Overflow`.
- **Impact:** a single request (API or web form) made the trade journal, the trade's page, the dashboard and the analytics API answer 500 **until the row was removed by hand**. Through the website form, `1e-999999` ended in an unhandled `sqlite3.IntegrityError`, which is also a 500.
- **Likelihood:** low in normal local use (it needs a deliberate or mistaken input), but the effect persists.
- **Fix:**
  1. `parse_decimal` now rejects values with more than 15 digits before the decimal point or more than 15 decimal places (`MAX_INTEGER_DIGITS`, `MAX_DECIMAL_PLACES`). Real prices, sizes, fees and multipliers sit far inside these limits.
  2. **Defence in depth:** `evaluate_trade` turns any `decimal` arithmetic error into its existing "excluded trade" result, and the trade page's "Plan" panel shows `n/a`. A bad row already in an older database is reported, not fatal.
- **Tests:** `tests/test_security_hardening.py` (boundaries both sides, API, web form, instruments, an existing out-of-range row across six pages) and `tests/test_error_paths.py` (trade page, CLI report).

### F2 · DNS rebinding against the local server (Medium)

- **What:** the development server listens on `127.0.0.1`, which stops other computers reaching it, but not other **websites**. A malicious page can point its own domain name at 127.0.0.1 ("DNS rebinding"). The browser then treats requests to that domain as same-origin and can read the responses. TradeLedger answered any `Host` header.
- **Impact:** a web page the user visits could read the journal or change trades through the JSON API. The API accepts JSON, which the attacker's same-origin script can send.
- **Fix:** Flask's `TRUSTED_HOSTS` setting, read from `TRADELEDGER_TRUSTED_HOSTS` with the default `localhost,127.0.0.1`. Any other `Host` header gets 400 before routing. `*` switches the check off, and production sets no default (see residual risk R3).
- **Tests:** local names (with and without ports) are accepted; a rebound host is refused for pages, the API and a DELETE; a configured extra host works; environment parsing.

### F3 · Weak production `SECRET_KEY` (Low)

- **What:** production refused a *missing* key but accepted `"x"`, the `.env.example` placeholder (36 characters) or the public testing key.
- **Impact:** the key signs the session cookie that holds the CSRF token. A guessable key allows forging that cookie, which weakens CSRF protection.
- **Fix:** production refuses keys shorter than 32 characters or equal to a known placeholder. The error message never includes the key itself.
- **Tests:** four weak keys rejected, a strong key accepted, the key absent from the error, and development unaffected. Two existing tests that used short production keys were updated to a 64-character key; their assertions are unchanged.

### F4 · Cacheable pages with trading data (Low)

- **Fix:** `Cache-Control: no-store` on every HTML response. Static CSS and JavaScript stay cacheable, and the API already had it.
- **Tests:** six page types, including the 404 page, plus static files remaining cacheable.

### F5 · HSTS (Low)

- **Fix:** `Strict-Transport-Security: max-age=31536000` in the production configuration, which already assumes HTTPS (Secure cookies). It's not sent in development, where HTTP on localhost is normal.

### F6 · Request hook order depended on imports (Low)

- **How it was found:** applying Ruff's import sorting swapped the two modules that register `before_request` hooks. The existing test `test_production_post_is_also_blocked` then failed: a production POST answered 400 (CSRF) instead of 403 (disabled).
- **Fix:** the hooks are registered explicitly, in order, in `web/__init__.py`, with a comment explaining why.
- **Tests:** a direct check of the hook order, and a production POST without a token returning 403.

## 3. Threats reviewed and existing protections

| Area | Threat | Protection (✓ verified by tests) |
|---|---|---|
| Input / DB | SQL injection | ✓ All values are bound `?` parameters. SQL text only ever contains fixed column or table names from the code (15 places, each reviewed and annotated). Injection strings tested on the API, journal and dashboard filters. |
| | Mass assignment | ✓ Unknown JSON fields are rejected (400); `id`, `created_at` and `updated_at` can't be set |
| | Invalid types | ✓ Floats, booleans, nested objects and NaN/Infinity are rejected for numbers; text fields must be text |
| | Oversized requests | ✓ 64 KB body limit (413, API and website); notes ≤ 2,000 characters; search ≤ 100; F1 limits on numbers |
| | Database integrity | ✓ Foreign keys, CHECK constraints (sides of stop and target, status vs exit price, timestamp shape) and triggers. Transactions roll back on failure. |
| Web | XSS | ✓ Jinja2 autoescaping; chart data passes through `tojson` (escapes `<`); JavaScript never inserts HTML; malicious names tested |
| | CSRF | ✓ Synchroniser token on every form, compared in constant time; SameSite=Lax cookie; delete only by POST |
| | Headers / CSP | ✓ `default-src 'self'`, no inline scripts or styles, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`; the Chart.js URL is allowed on `/analytics` only, as an exact file path; nosniff, `X-Frame-Options: DENY`, Referrer-Policy; plus F4 and F5 |
| | Unsafe redirects | ✓ "Return to" links accept local paths only (no `//`, no scheme, no backslash) |
| | Unsafe methods | ✓ 405 for unsupported methods; GET never changes data |
| | Session cookie | ✓ HttpOnly, SameSite=Lax, Secure in production; holds only the CSRF token |
| | Error disclosure | ✓ 404/405/413/500 pages and JSON errors contain no traces, SQL, paths or secrets; tracebacks go to the server log only |
| Config | Development vs production | ✓ Production requires a strong key (F3) and disables the journal and API by default (403); debug is never on unless `--debug` is passed explicitly |
| | Unauthenticated access | ✓ Production 403 by default for the journal, dashboard and API; enabling either logs a warning. ⚠ No authentication exists (R1). |
| | File paths | ✓ The database path comes from configuration only; tests can never use the default database |
| | Environment variables | ✓ `.env` is ignored by Git; `.env.example` holds placeholders only |
| Dependencies | Known vulnerabilities | Manual advisory check (pip-audit could not run offline), summarised below. CI runs `pip-audit --strict` on every push. |
| | Third-party JavaScript | Chart.js 4.5.1 pinned by exact URL; SRI supported; self-hosting documented and tested (`chartjs_real`) |
| | Licences | Chart.js is MIT: the notice is in `docs/THIRD_PARTY_NOTICES.md`, and `LICENSE.md` is required next to a self-hosted copy (checked by a test) |

**Manual dependency advisory check (15 November 2026):**

| Package | Pinned | Latest advisory found | Fixed in | Status |
|---|---|---|---|---|
| Werkzeug | 3.1.9 | CVE-2026-27199 (`safe_join` and Windows device names) | 3.1.6 | Not affected |
| Flask | 3.1.3 | CVE-2026-27205 (missing `Vary: Cookie`) | 3.1.3 | Not affected |
| Jinja2 | 3.1.6 | CVE-2025-27516 (sandbox breakout) | 3.1.6 | Not affected; TradeLedger doesn't use the sandbox anyway |
| python-dotenv | 1.2.3 | CVE-2026-28684 (`set_key` follows symlinks) | 1.2.2 | Not affected; TradeLedger never calls `set_key` |

This manual search isn't exhaustive. The `audit` CI job is the authoritative check.

## 4. Tests performed

- **Regression tests:**
  - `tests/test_security_hardening.py`: 40 tests for F1–F6.
  - `tests/test_error_paths.py`: 17 tests, including unknown database errors being re-raised, never swallowed.
  - The Phase 4–6 security suites: `test_api_security.py`, `test_web_security.py`, and the CSP and XSS tests in `test_web_analytics.py`.
- **Proof the tests detect the problems:** run against the unchanged Phase 6 application code, 25 of the 40 new security tests fail. The other 15 cover behaviour Phase 6 already had: accepting local host names, configured trusted hosts, strong and development keys, cacheable static files, and the hook order (correct there by accident of import order).
- **Planted-defect check:** each of 11 Phase 7 security and robustness protections was removed one at a time from a copy of the code; every removal turned a test red. Together with the 11 Phase 6 defects, that's 22 of 22 caught (`docs/PHASE_7_QA_REPORT.md`).
- **Static analysis:** Ruff `S` rules over the whole project, now clean. Every remaining exception is justified in `pyproject.toml` or next to the code.
- **Browser:** the CSP is enforced in Chromium, a negative control proves violations are detected, and there are no console errors on any page at four widths.

## 5. Residual risks

| ID | Risk | Severity | Why it remains | Recommendation |
|---|---|---|---|---|
| R1 | **No authentication** | High *if exposed*; Low for local use | Out of scope for Phase 7; the production 403 defaults contain it | Add authentication (for example Flask-Login with hashed passwords, or a reverse proxy with single sign-on) before any shared deployment |
| R2 | Chart.js from a CDN without SRI by default | Low | The file couldn't be downloaded here to compute a verified hash | Self-host the file (CI already fetches it from npm with integrity checking), or set `TRADELEDGER_CHART_JS_INTEGRITY` |
| R3 | Production has no default trusted host | Low | Only the deployer knows the host name | Set `TRADELEDGER_TRUSTED_HOSTS` (documented in `SECURITY.md`) |
| R4 | SQLite file stored unencrypted | Low | A local file protected by the operating system user account | Use disk encryption; restrict file permissions in any deployment |
| R5 | No rate limiting or audit log | Low | Single local user | Add both with authentication |
| R6 | Dependency audit not executed in the build environment | Low | No network access | The CI `audit` job runs it on every push |
| R7 | Accessibility and security not reviewed by an independent human | Info | Automated checks only | A peer review or penetration test before any real deployment |

## 6. Recommendations for later phases

1. Authentication and authorisation before any network deployment; then re-enable the journal and API in production *behind* it rather than with the `ENABLE_UNAUTHENTICATED` flags.
2. Self-host Chart.js with its SRI hash, so the CSP can return to `script-src 'self'` on every page.
3. Serve through a reverse proxy that terminates HTTPS, compresses responses and enforces the host name.
4. Keep `pip-audit` in CI, and add Dependabot or Renovate to propose dependency updates as pull requests.
5. Back up the database regularly; `migrate-db` already backs it up before every migration.

# Phase 5 Guide — Professional Frontend & Interactive Trading Journal

TradeLedger now has a real web interface. You can record, browse, search, filter, edit and delete trades, manage instruments and preview analytics in a browser. Every number on screen still comes from the Phase 3 engine, and every write still goes through the Phase 2 validation rules.

Version: `0.5.0` · Tests: **746 passed** (512 from Phases 1–4 plus 234 new, 42 of them in a real Chromium browser).

---

## Part A — Overview

**In plain English.** Until now, TradeLedger was a database with a calculator and an API: useful to a programmer, not to a trader. Phase 5 adds the screens. You get a sidebar with six sections, forms that explain themselves, a journal you can search, and a details page that shows the plan and the result of each trade.

**What was built**

| Area | What it does |
|---|---|
| App shell | Sidebar (Overview, Trade journal, Add trade, Instruments, Analytics *Preview*, About), page header with title, description and actions, a mobile menu, a skip link, flash messages, a footer. No fake login, avatar or notifications. |
| Overview | Open, closed and instrument counts; win rate, expectancy, total R and profit factor; net P&L per currency (never added across currencies); the 5 most recent trades. |
| Journal | Every column the brief asked for; filters for instrument, status, strategy and entry date range; a search across symbol, strategy and notes of **every** trade, done in SQL, not just the visible page; pagination that keeps the filters; a reset link. |
| Add / Edit | One shared form. Exit fields show only for CLOSED trades. Errors appear in a summary at the top and next to each field, and everything you typed is kept. Legacy trades can be edited without being given an invented exit time. |
| Details | Plan and result side by side. Open trades show the plan with no invented result; legacy trades say the exit time was never recorded. |
| Delete | A confirmation page (GET), then a CSRF-protected POST, then back to the journal with a message. |
| Instruments | List and add; explains the contract multiplier; warns you to check broker specifications; no editing, on purpose. |
| Analytics | Tables by instrument, strategy and session, clearly labelled as a Phase 6 preview. |

**What did not change:** the JSON API, the database schema (still version 2), the analytics engine and the validation rules. The website is a second client of the same core.

---

## Part B — Design system

**Plain English.** A design system is a small set of agreed decisions (colours, spacing, text sizes, how a button looks) that every page reuses. That's why the app looks like one product rather than nine pages.

**Technically**, everything is in CSS custom properties (variables) in `static/css/base.css`:

| Token group | Examples | Why |
|---|---|---|
| Colour | `--color-bg #0a101d`, `--color-surface #101a2c`, `--color-accent #6aa8ff`, `--color-positive #4cd79a`, `--color-negative #ff8a8a` | A dark, low-glare theme suits long screen sessions. The positive/negative shades were chosen to keep text contrast at or above WCAG AA on the surface colour. |
| Spacing | `--space-1` … `--space-8` (a 4 px-based scale) | Consistent rhythm; no one-off margins. |
| Type | `--text-xs` … `--text-2xl`, tabular numbers on prices | Columns of prices line up. |
| Shape | `--radius`, `--radius-lg`, `--shadow` | |
| Layout | `--sidebar-width 15.5rem`, `--control-height 2.5rem` | Inputs and buttons share one height, so rows align. |

**Colour is never the only signal.** Every outcome has a word (Win, Loss, Breakeven, Open), every direction an arrow and a word (▲ Long / ▼ Short), and signed values carry a + or −. A colour-blind user, or someone with a greyscale printout, loses nothing.

**Breakpoints**

| Width | Layout |
|---|---|
| < 48 rem (768 px) | Single column; tables become one card per trade |
| 48–64 rem | Off-canvas menu; trade cards in a two-column grid |
| ≥ 64 rem (1024 px) | Fixed sidebar; full tables |
| 64–80 rem | Full table, which may scroll inside its own box; a hint says so and the box is keyboard-scrollable |
| ≥ 80 rem (1280 px) | The whole journal table fits |

**Stylesheet organisation:** `style.css` imports, in order, `base.css` (tokens, reset, focus ring, utilities), `layout.css` (shell, sidebar, header), `components.css` (buttons, cards, badges, notices, forms, tables, pagination) and `pages.css` (page-specific layouts). Later files may rely on earlier ones, never the reverse.

---

## Part C — Folder structure (new and changed files)

```
tradeledger/
├── tradeledger/
│   ├── __init__.py                 CHANGED  registers web blueprint; Content-Security-Policy
│   ├── config.py                   CHANGED  0.5.0; JOURNAL_ENABLED; cookie flags; page size
│   ├── routes.py                   DELETED  replaced by web/
│   ├── repositories/trades.py      CHANGED  strategy filter, whole-database search, list_strategies
│   ├── web/                        NEW      the website (Blueprint "main")
│   │   ├── __init__.py
│   │   ├── guard.py                production 403 for trade pages
│   │   ├── csrf.py                 synchroniser-token CSRF protection
│   │   ├── filters.py              money, r, rate, ratio, plain, when, day, clock, datetime_local
│   │   ├── forms.py                read/clean form input, safe_next redirects
│   │   ├── views.py                overview, about, analytics preview, health
│   │   ├── trades.py               journal, add, details, edit, delete
│   │   └── instruments.py          list and add instruments
│   ├── templates/                  REWRITTEN base.html, macros/, trades/, instruments/, errors/ …
│   └── static/
│       ├── css/                    REWRITTEN style.css + base, layout, components, pages
│       └── js/                     NEW      early.js, app.js, trade-form.js
├── tests/
│   ├── web_helpers.py              NEW      CSRF-aware form posting for tests
│   ├── test_web_pages.py           NEW
│   ├── test_web_forms.py           NEW
│   ├── test_web_security.py        NEW
│   ├── test_web_integration.py     NEW      also covers repository search
│   ├── test_browser.py             NEW      optional, Playwright
│   ├── test_app.py                 CHANGED  roadmap test now reads /about
│   └── test_openapi.py             CHANGED  version read from config, not hard-coded
└── docs/
    ├── PHASE_5_GUIDE.md, PHASE_5_CODE.md
    └── screenshots/phase5_overview.png
```

Both test changes are explained in the files themselves. The roadmap moved from the home page to About, because the home page is now the Overview. The OpenAPI test had `"0.4.0"` hard-coded, so it now compares against `APP_VERSION`. No assertion was weakened.

---

## Part D — Python

### The key architectural decision: server-rendered forms, not the Fetch API

**Plain English.** There were two ways to build the forms:

1. **Classic forms.** The browser sends the form to Flask, Flask checks it and either shows the page again with errors or saves it and moves you on.
2. **JavaScript plus the API.** JavaScript collects the fields, sends JSON to `/api/v1/trades` and draws the result itself.

I chose **1**.

**Why:**

- **One validation layer.** The form handler calls the same `validate_trade()` the API uses. The browser never decides whether a trade is valid and never calculates P&L or R. There are no JavaScript formulas anywhere.
- **The Phase 4 protection stays intact.** The API is unauthenticated and disabled in production. A Fetch-based frontend would have needed the API switched on wherever the website runs, which the brief forbade.
- **It works without JavaScript.** Turn JavaScript off and every page, form, filter and delete still works. JavaScript only adds conveniences.
- **Security is simpler.** Server-rendered HTML is escaped by Jinja2. A JavaScript-rendered table is where `innerHTML` XSS bugs usually live.
- **It's honest for the scope.** This is a single-user local journal, so page reloads after a save cost nothing that matters.

**Trade-offs accepted:** each save is a full page load. There's no instant search-as-you-type (search runs on submit, across the whole database). Errors appear after the round trip rather than as you type. Phase 6 charts may add small, read-only fetches, which is a different risk from writes.

### POST-Redirect-GET

After a successful save, the server answers `302 → /trades/<id>` instead of rendering a page. Refreshing therefore reloads the details page rather than re-sending the form, which would create a duplicate trade. Validation failures render the form again with **400**, so the status code tells the truth.

### Forms (`web/forms.py`)

- `read_form()` strips whitespace from known fields only. Unknown fields are ignored, so there's no mass assignment.
- `trade_input()` turns blanks into `None` and, **if the status is OPEN, discards any exit values**. Without JavaScript you can type an exit price and then choose Open; the server makes sure that can't store a half-closed trade.
- Edit passes `require_closed_at = not is_legacy`. A trade closed before Phase 4 can be edited and left with an unknown exit time. TradeLedger never invents one.

### Journal search (`repositories/trades.py`)

`search` becomes `symbol LIKE ? ESCAPE '\' OR strategy LIKE ? OR notes LIKE ?` with **parameters**. `%`, `_` and `\` are escaped first, so searching for `%` finds a literal percent sign rather than everything. Because the search runs in SQL, `count_trades` and `list_trades` share it and pagination counts are exact.

### CSRF (`web/csrf.py`)

**Plain English.** If you have TradeLedger open and visit a malicious page, that page could quietly submit a form to `127.0.0.1:5000/trades/3/delete`, and your browser would send it. The defence is a secret token in every form that another site can't read.

**Technically:** the synchroniser-token pattern. One `secrets.token_urlsafe(32)` per session is stored in Flask's signed cookie and placed in a hidden field. Every POST, PUT, PATCH or DELETE on the website blueprint must return it. The comparison uses `hmac.compare_digest`. A failure gives **400** with a "form expired" page, and nothing is written. `SameSite=Lax` cookies add a second layer.

**Why not Flask-WTF?** It's the normal choice and does the same job, but it isn't installed, package installs were blocked in this environment, and forty lines of standard library code are easy to read, test and explain in an interview. Moving to Flask-WTF later is a reasonable change.

### Production guard (`web/guard.py`)

In the production configuration, every trade page answers **403** unless `TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL=1`. `/`, `/about`, `/health` and static files keep working, and `/` then shows a page **with no trading data**. This mirrors the API's protection from Phase 4.

### Safe redirects (`safe_next`)

`?next=` lets Delete return you to the filtered journal page you came from. It's accepted only if it's a local path: it starts with a single `/`, has no `//`, no backslash, no scheme and no host. Otherwise it falls back. This blocks "open redirect" phishing links.

---

## Part E — HTML (Jinja2)

- `base.html` holds the shell. Pages fill `title`, `heading`, `description`, `actions` and `content`.
- `macros/ui.html` provides the outcome and direction badges, signed values, empty states and pagination.
- `macros/forms.html` provides the field macros. Each one renders the label, hint, error and input, and wires `aria-describedby` to the hint and error and `aria-invalid` when there's an error. The error summary links to each field (`#field-<name>`).
- Accessibility basics: `lang="en-GB"`, a skip link, one `<h1>` per page, `<caption>` on tables, `scope="col"`, `aria-current="page"` in the nav, `role="status"` for flash messages and result counts.
- Escaping: no `|safe` is used on user data. The test suite stores `<script>` and `<img onerror>` payloads and checks seven pages.

---

## Part F — CSS

Hand-written, with no framework (see Part B). A few points are worth knowing:

- **Specificity bug found in the browser.** `.icon-button { display: inline-flex }` in `components.css` loads after `layout.css` and was re-showing the Menu button on desktop. Fixed with deliberately more specific selectors (`.topbar .nav-toggle`).
- **`[hidden]` needs help.** `fieldset.form-grid { display: grid }` beat the browser's `[hidden] { display: none }`, so the exit fields stayed visible for OPEN trades. `base.css` now has `[hidden] { display: none !important; }`, the one deliberate `!important`, with a comment.
- **Page-wide overflow bug.** The `.visually-hidden` labels inside the table are absolutely positioned. With no positioned ancestor they escaped the scroll box and widened the whole page by 214 px. `.table-wrap { position: relative }` fixed it.
- **No inline styles anywhere**, because the Content-Security-Policy forbids them.

---

## Part G — JavaScript (progressive enhancement)

| File | Role |
|---|---|
| `early.js` | Loaded first, blocking. Adds the `js` class to `<html>`, so CSS can tell when JavaScript is available without a flash of the wrong layout. |
| `app.js` | Mobile menu (open/close, Escape, backdrop, focus moved in and back, resets at the desktop width); flash messages can be dismissed; submit-once (a second click is ignored, the button shows "Saving…" and gets `aria-busy`, and the state resets if you come back with the Back button); moves focus to the error summary. |
| `trade-form.js` | Shows, enables and requires the exit fields only for CLOSED; doesn't require an exit time for legacy trades. |

None of it computes trading figures or builds HTML from data. No `innerHTML` is used.

**A real bug the browser caught:** the mobile menu's `visibility` was animated, so it was still `hidden` when `focus()` ran and focus stayed on the Menu button. Keyboard users were stranded. The fix makes the sidebar visible instantly on open and delays hiding it until the slide-out finishes on close.

---

## Part H — Tests

| File | Tests (incl. parametrised) | Covers |
|---|---|---|
| `test_web_pages.py` | 62 | Every page with data and with an empty DB, 404s, navigation and `aria-current`, no fake account features, no inline scripts or styles, overview figures, journal columns, filters, search, pagination, invalid filters (400), details for closed, open and legacy trades, instruments, analytics preview |
| `test_web_forms.py` | 49 | Create closed and open, stale exit values discarded, seconds kept, blank fees, optional fields, short trades, preselected instrument, each missing field, exit-after-entry, invalid values, values and radio choices kept, edit, reopen and close, invalid edit unchanged, legacy edit without invented time, delete, safe `next`, double delete, instruments add, duplicate and invalid |
| `test_web_security.py` | 63 | CSRF missing, wrong, from another session or empty; valid; every form has a token; API unaffected; GET delete never deletes; 405s; stored XSS on seven pages; escaped search echo; `safe_next` on 11 hostile inputs; CSP and cookie flags; production 403s and data-free home page; API still 403 in production; no form data or tokens in logs; generic 500 |
| `test_web_integration.py` | 18 | Full lifecycle from an empty DB; form-entered and API-entered trades stored identically; website changes reflected in the API analytics; repository search (case, wildcards, injection, pagination, limits) |
| `test_browser.py` | 42 | Real Chromium: no horizontal scroll and no console or CSP errors on 8 pages × 4 widths, menu visibility per width, menu focus and Escape, skip link, exit-field toggling, server errors focused, create and delete, double-submit blocked |

**Honest notes:**
- `test_browser.py` skips when Playwright isn't installed. It isn't in `requirements.txt` because it downloads a ~150 MB browser.
- Every test that sends a form gets the CSRF token from a real page, as a browser does. CSRF isn't switched off for tests.
- Bugs that tests found were fixed in the code: an out-of-range page number now returns 404 instead of an empty 200.

---

## Part I — Run instructions

```bash
# macOS / Linux
source .venv/bin/activate
flask --app tradeledger init-db
flask --app tradeledger seed-db        # optional synthetic data
flask --app tradeledger run --debug    # http://127.0.0.1:5000
pytest
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
flask --app tradeledger init-db
flask --app tradeledger seed-db
flask --app tradeledger run --debug
pytest
```

Upgrading from Phase 4 needs no database change (still schema version 2).

---

## Part J — Browser verification (what was actually done)

Run against the development server with Playwright 1.63 and Chrome for Testing in a headless Linux container.

- **Layout:** 9 pages × 4 widths (375, 768, 1024, 1440), every screenshot inspected by eye. Horizontal page overflow measured at **0 px** on all 36 combinations after fixes (it was 214 px on the journal at 1440 before).
- **Interaction script:** 35/35 checks passed. These covered the mobile menu (open, focus moved in, Escape, focus returned, backdrop), the desktop menu hidden, the skip link and focus ring, exit fields toggling with status, the server error summary focused with values kept and `aria-invalid` set, create (with an XSS payload rendered as text), search, edit to open, delete with a flash message and a later 404, filters kept, no console errors, no CSP violations.
- **Empty database:** the overview, journal, analytics, instruments and add-trade pages show helpful empty states ("add an instrument first").

**Bugs found and fixed through the browser:** the Menu and close buttons were visible on desktop; the journal overflowed the page; the exit fields weren't hiding (the `[hidden]` issue); focus wasn't moving into the mobile menu; the ✓ on the choice buttons had no gap; the table was cramped at 1024–1280 (now the date and time are stacked, direction sits under the symbol, and P&L and R share a column).

**Not verified:** Safari and Firefox, real phones, a screen reader (NVDA/VoiceOver), 200% zoom, Windows high-contrast mode. The automated checks are a good proxy, not a substitute for those.

---

## Part K — Documentation & ZIP

`README.md` (updated), this guide, `PHASE_5_CODE.md` (every Phase 5 source file) and `tradeledger_phase5.zip`. The ZIP excludes `.env`, `instance/`, databases, virtual environments, `__pycache__` and pytest caches. It was extracted into a new folder and the full suite was run there.

---

## Part L — Teaching notes (interview-ready)

1. **"Why not React?"** One user, mostly forms and tables, and a hard requirement not to expose the API. Server rendering gives one validation path, built-in escaping and no build step. I'd revisit for heavy interactivity (live charts, drag-and-drop).
2. **"How does your CSRF protection work?"** The synchroniser token: random per session, stored in a signed cookie, compared in constant time on every state-changing request, backed up by SameSite=Lax.
3. **"Why is delete two steps?"** GET must be safe: browsers, prefetchers and crawlers follow links. Deleting on GET would let any `<img src>` delete your data.
4. **"What's progressive enhancement?"** Build the page so it works with HTML alone, then let JavaScript improve it. If the script fails, nothing breaks.
5. **"How do you handle errors in forms?"** Status 400, a focused summary linking to each field, messages tied to inputs with `aria-describedby`, and the user's input kept.
6. **"What's a Content-Security-Policy for?"** It limits where scripts can come from, so even if an XSS bug slipped through, an injected inline `<script>` wouldn't run.
7. **"How do you know it works?"** 746 automated tests, including 42 in a real browser, plus screenshots at four widths, and an honest list of what wasn't tested.

---

## Part M — Exercise

Add a **"Duplicate trade"** action on the details page. It should open the Add form prefilled with the instrument, direction, strategy, session, stop distance and size of an existing trade, but **not** its times, exit or notes.

Requirements:
1. Use a GET link, `/trades/new?from=<id>`. It changes nothing, so GET is correct.
2. Prefill on the server in `new_trade()`; no JavaScript.
3. An unknown or non-numeric `from` id falls back to a blank form (not a 500).
4. Tests: the prefilled values appear; times and exit are blank; a bad id gives a blank form; submitting still needs a CSRF token.

*Stretch:* explain why copying the **exit price** would be a data-quality risk.

---

## Part N — Phase 6 preview

Phase 6 turns `/analytics` from a preview into a full **performance dashboard**:

- Equity-style cumulative-R curve and drawdown chart (clearly R, not account money)
- Monthly and weekday results, distribution of R-multiples
- Filters shared with the journal (date range, instrument, strategy)
- Charts rendered accessibly (with data tables and text summaries), without breaking the CSP

Phase 6 won't start until you ask.

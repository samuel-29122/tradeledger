# TradeLedger: final code listing (Phase 8.5, version 0.8.5)

Every source, configuration, script and test file added or changed since Phase 8 (`tradeledger_phase8.zip`), in full, including the final-audit fix (pool shutdown in `tradeledger/db.py`). It is generated from the files in `tradeledger_final.zip`, so the two always agree. Documentation (README, CHANGELOG, SECURITY, the guide and reports in `docs/`) is delivered as its own files. One file is deliberately not printed: `tradeledger/static/vendor/chartjs/chart.umd.min.js` (207,635 bytes of minified third-party code on a single line). It is in the ZIP, its SHA-384 is `sha384-rBeQHxx+fD4yTWwymMLsGUggrWAk1ssEcKZuxBdYt+43t//g2T2q49rj78sPe7D3`, and `scripts/build_chartjs.mjs` rebuilds it byte for byte from the official sources (see `static/vendor/chartjs/BUILD.md`).

**Verified vs not:** the application code and tests ran on SQLite and on PostgreSQL 16 with the genuine psycopg 3.3.8 driver (pure-Python implementation), including the real-chart browser tests. Docker, Compose, Terraform and the GitHub workflows have **not** run (see `docs/TRADELEDGER_FINAL_QA_REPORT.md` §8).

## Inventory

- **NEW (21):** `tradeledger/repositories/password_resets.py`, `tradeledger/repositories/sessions.py`, `tradeledger/repositories/two_step.py`, `tradeledger/static/vendor/chartjs/BUILD.md`, `tradeledger/static/vendor/chartjs/LICENSE-kurkle-color.md`, `tradeledger/static/vendor/chartjs/LICENSE.md`, `tradeledger/templates/account/two_step.html`, `tradeledger/templates/auth/reset_password.html`, `tradeledger/templates/auth/two_step.html`, `tradeledger/totp.py`, `tradeledger/web/reset.py`, `tradeledger/web/two_step.py`, `tests/test_browser_account_security.py`, `tests/test_chartjs_bundle.py`, `tests/test_database_safety.py`, `tests/test_device_sessions.py`, `tests/test_password_reset.py`, `tests/test_postgres_roles.py`, `tests/test_two_step.py`, `tests/test_users_repository.py`, `scripts/build_chartjs.mjs`
- **MODIFIED (35):** `tradeledger/__init__.py`, `tradeledger/cli.py`, `tradeledger/config.py`, `tradeledger/db.py`, `tradeledger/migrations.py`, `tradeledger/observability.py`, `tradeledger/repositories/users.py`, `tradeledger/static/css/analytics.css`, `tradeledger/static/css/components.css`, `tradeledger/static/css/pages.css`, `tradeledger/static/js/analytics.js`, `tradeledger/static/vendor/chartjs/README.md`, `tradeledger/templates/account/settings.html`, `tradeledger/templates/auth/login.html`, `tradeledger/web/__init__.py`, `tradeledger/web/account.py`, `tradeledger/web/auth.py`, `tradeledger/web/filters.py`, `.env.example`, `.github/workflows/ci.yml`, `pyproject.toml`, `tests/browser_support.py`, `tests/conftest.py`, `tests/js/analytics.test.js`, `tests/pg_libpq_bridge.py`, `tests/test_auth.py`, `tests/test_chartjs_real.py`, `tests/test_db.py`, `tests/test_legacy_and_cli_accounts.py`, `tests/test_migrations.py`, `tests/test_operations.py`, `tests/test_postgres.py`, `tests/test_seed_and_analytics.py`, `scripts/benchmark_dashboard.py`, `scripts/qa_dashboard_agreement.py`
- **DELETED (0):** 

## 1. New files

### `tradeledger/repositories/password_resets.py` (NEW)

**Why:** One-time reset links: create (cancelling older ones), find, consume atomically; SHA-256 only.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""One-time password-reset links, issued by an operator (Phase 8.5).

Plain English
-------------
TradeLedger has no email service, so it cannot email you a reset link on
its own. Instead the person who runs the server creates a link with one
command and passes it to you (in person, by phone, by a message you
trust). You open it and choose a new password yourself, so the operator
never learns or chooses your password. The link works once, for 30
minutes, and creating a new link cancels any older one.

Technical summary
-----------------
Token   "tlr_" + secrets.token_urlsafe(32): 256 random bits. The prefix lets
        the log filter recognise and hide it (observability.py).
Storage SHA-256 of the token only, like API tokens and session keys.
Use     consume() marks the link used and cancels every other open link for
        the account, in one transaction. Using it also ends every session
        and revokes every API token (web/reset.py).
Times   whole seconds since 1970 (UTC), the clock used for sessions.

Why not "security questions" or a self-service form that emails nobody:
both are weaker than they look (guessable answers; a form that cannot
deliver anything invites a fake feature). Why not reuse set-password:
with set-password the operator types the new password, so they know it.
"""

import hashlib
import secrets

PREFIX = "tlr_"

_COLUMNS = "id, user_id, created_at, expires_at, used_at"


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(text):
    return isinstance(text, str) and text.startswith(PREFIX) and 40 <= len(text) <= 100 and text.isascii()


def create_link(conn, *, user_id, now, lifetime_seconds):
    """Issue a new link (cancelling older open ones). Returns the token,
    which is never stored and must be handed to the account holder."""
    token = PREFIX + secrets.token_urlsafe(32)
    with conn:
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now, user_id)
        )
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, token_hash(token), now, now + lifetime_seconds),
        )
    return token


def find_valid(conn, token, *, now):
    """The open, unexpired link for `token`, or None (also for a malformed,
    unknown, used or expired token: the caller cannot tell these apart)."""
    if not looks_like_token(token):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM password_resets WHERE token_hash = ?",  # noqa: S608 - constant columns
        (token_hash(token),),
    ).fetchone()
    if row is None or row["used_at"] is not None or now >= row["expires_at"]:
        return None
    return dict(row)


def consume(conn, link_id, *, user_id, now):
    """Mark this link used and cancel any other open link of the account.
    False if it had already been used (for example by a second tab)."""
    cursor = conn.execute(
        "UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL", (now, link_id)
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now, user_id)
    )
    return True
```

### `tradeledger/repositories/sessions.py` (NEW)

**Why:** Server-side session rows: create, find, check, touch, list, revoke; short user-agent labels.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Signed-in browsers ("sessions"), one database row each (Phase 8.5).

Plain English
-------------
Every time you sign in, TradeLedger writes down "this browser is signed in
to this account" and gives the browser a long random key to prove it. The
Account page lists your signed-in browsers and can sign out any one of
them. Signing out, or changing your password, crosses the row out on the
server, so even a stolen copy of the cookie stops working.

Technical summary
-----------------
Key      secrets.token_urlsafe(32): 256 random bits, kept inside Flask's
         signed session cookie. The table stores only SHA-256(key), like the
         API tokens: a database leak does not reveal usable session keys.
Limits   expires_at = sign-in time + SESSION_MAX_AGE_SECONDS (absolute) and
         last_seen_at for the idle limit, both checked on the SERVER.
         last_seen_at is written at most once a minute per session.
Clock    Whole seconds since 1970 (UTC) from web.auth.now_seconds(), the
         same clock the cookie checks use, so tests can move it.
Privacy  No IP address is stored. user_agent holds a short description such
         as "Firefox on Windows", never the full header.
Revoke   revoked_at is set; rows are never reused. Ending ALL of a user's
         sessions happens in repositories/users.py (password change,
         "sign out everywhere", deactivation), together with session_version. Expired and revoked rows
         older than a day are deleted when the same user signs in again.

Why not only the signed cookie (Phase 8): a signed cookie cannot be
"un-signed". Phase 8 could end ALL of a user's sessions (session_version)
but not one device, and a cookie copied before sign-out stayed valid until
it expired. A server-side row fixes both, at the cost of one indexed
lookup per request (well under a millisecond).
"""

import hashlib
import secrets

KEY_BYTES = 32
TOUCH_INTERVAL_SECONDS = 60
CLEANUP_AFTER_SECONDS = 24 * 3600

_COLUMNS = "id, user_id, created_at, last_seen_at, expires_at, user_agent, revoked_at"


def key_hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def describe_user_agent(header):
    """A short, privacy-friendly label: "Firefox on Windows"."""
    text = header or ""
    if "Edg/" in text:
        browser = "Edge"
    elif "Firefox/" in text:
        browser = "Firefox"
    elif "Chrome/" in text or "Chromium/" in text or "CriOS/" in text:
        browser = "Chrome"
    elif "Safari/" in text:
        browser = "Safari"
    elif text.startswith("curl/") or "python" in text.lower():
        browser = "Script"
    else:
        browser = "Unknown browser"
    if "iPhone" in text or "iPad" in text:
        system = "iOS"
    elif "Android" in text:
        system = "Android"
    elif "Windows" in text:
        system = "Windows"
    elif "Mac OS X" in text or "Macintosh" in text:
        system = "macOS"
    elif "Linux" in text or "X11" in text:
        system = "Linux"
    else:
        system = "unknown system"
    return f"{browser} on {system}"


def create_session(conn, *, user_id, now, max_age_seconds, user_agent=""):
    """Record a new signed-in browser. Returns (key, session_id); the key
    goes into the cookie and is never stored."""
    key = secrets.token_urlsafe(KEY_BYTES)
    with conn:
        conn.execute(
            "DELETE FROM user_sessions WHERE user_id = ? AND (expires_at < ? OR revoked_at < ?)",
            (user_id, now - CLEANUP_AFTER_SECONDS, now - CLEANUP_AFTER_SECONDS),
        )
        row = conn.execute(
            "INSERT INTO user_sessions (user_id, key_hash, created_at, last_seen_at, expires_at, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (user_id, key_hash(key), now, now, now + max_age_seconds, describe_user_agent(user_agent)[:100]),
        ).fetchone()
    return key, row["id"]


def find_session(conn, key):
    """The session row for `key`, or None (also for a malformed key)."""
    if not isinstance(key, str) or not 20 <= len(key) <= 100:
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM user_sessions WHERE key_hash = ?",  # noqa: S608 - constant columns
        (key_hash(key),),
    ).fetchone()
    return None if row is None else dict(row)


def problem_with(row, *, user_id, now, idle_timeout_seconds):
    """Why this session can no longer be used ("revoked", "expired", "idle",
    "wrong_user"), or None if it is fine."""
    if row is None:
        return "unknown"
    if row["user_id"] != user_id:
        return "wrong_user"
    if row["revoked_at"] is not None:
        return "revoked"
    if now >= row["expires_at"]:
        return "expired"
    if now - row["last_seen_at"] > idle_timeout_seconds:
        return "idle"
    return None


def touch(conn, row, now):
    if now - row["last_seen_at"] >= TOUCH_INTERVAL_SECONDS:
        conn.execute("UPDATE user_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))


def list_active(conn, *, user_id, now, idle_timeout_seconds):
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM user_sessions "  # noqa: S608 - constant columns
        "WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ? AND last_seen_at >= ? "
        "ORDER BY last_seen_at DESC, id DESC",
        (user_id, now, now - idle_timeout_seconds),
    ).fetchall()
    return [dict(row) for row in rows]


def revoke(conn, session_id, *, user_id, now):
    """Sign out one browser of THIS user. False if there is no such active
    session (another user's id gives the same False)."""
    cursor = conn.execute(
        "UPDATE user_sessions SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (now, session_id, user_id),
    )
    return cursor.rowcount == 1
```

### `tradeledger/repositories/two_step.py` (NEW)

**Why:** Two-step state: enable/disable, atomic replay-proof step acceptance, hashed single-use recovery codes.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Two-step verification state in the database (Phase 8.5).

The cryptography lives in tradeledger/totp.py; this module only stores and
updates: the encrypted secret on the users row, the last accepted time
step, and the hashed single-use recovery codes. Every function takes the
user id explicitly; there is no "all users" mode.
"""

from ..timestamps import utc_now_text
from ..totp import recovery_hash


def enable(conn, *, user_id, encrypted_secret, accepted_step, recovery_codes):
    """Switch two-step on with a confirmed secret and a fresh set of codes."""
    with conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled_at = ?, totp_last_step = ?, updated_at = ? WHERE id = ?",
            (encrypted_secret, utc_now_text(), accepted_step, utc_now_text(), user_id),
        )
        _store_codes(conn, user_id, recovery_codes)


def disable(conn, *, user_id):
    with conn:
        conn.execute(
            "UPDATE users SET totp_secret = NULL, totp_enabled_at = NULL, totp_last_step = NULL, updated_at = ? "
            "WHERE id = ?",
            (utc_now_text(), user_id),
        )
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))


def accept_step(conn, *, user_id, step):
    """Record that the code for `step` was used. Atomic: if two requests
    race with the same code, only one of them gets True."""
    cursor = conn.execute(
        "UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_enabled_at IS NOT NULL "
        "AND (totp_last_step IS NULL OR totp_last_step < ?)",
        (step, user_id, step),
    )
    return cursor.rowcount == 1


def _store_codes(conn, user_id, codes):
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    for code in codes:
        conn.execute(
            "INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)", (user_id, recovery_hash(code))
        )


def replace_recovery_codes(conn, *, user_id, codes):
    with conn:
        _store_codes(conn, user_id, codes)


def use_recovery_code(conn, *, user_id, code, now):
    """Spend one recovery code. True only for an unused code of THIS user."""
    cursor = conn.execute(
        "UPDATE recovery_codes SET used_at = ? WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
        (now, user_id, recovery_hash(code)),
    )
    return cursor.rowcount == 1


def unused_recovery_codes(conn, *, user_id):
    return conn.execute(
        "SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL", (user_id,)
    ).fetchone()[0]
```

### `tradeledger/static/vendor/chartjs/BUILD.md` (NEW)

**Why:** Provenance of the bundled Chart.js: source commits, build tool, size, SHA-384, what was and was not verified.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```markdown
# Where `chart.umd.min.js` comes from

**In short:** this is genuine Chart.js 4.5.1. It was **built from the official source code**, because the ready-made file on npm and the CDNs could not be downloaded in the build environment. It works the same way, but it is not byte-for-byte the npm file, so its integrity hash is different from the CDN's.

| | |
|---|---|
| Library | Chart.js **4.5.1**, MIT licence ([`LICENSE.md`](LICENSE.md)) |
| Source | `https://github.com/chartjs/chart.js`, tag `v4.5.1`, commit `9c5cf9fac7ec04a71b516e2aff3f7d76876be369` ("Bump version to 4.5.1 (#12132)", 13 October 2025) |
| Its one runtime dependency | `@kurkle/color` **0.3.2**, the version pinned in Chart.js's `pnpm-lock.yaml`; MIT licence ([`LICENSE-kurkle-color.md`](LICENSE-kurkle-color.md)); `https://github.com/kurkle/color`, tag `v0.3.2`, commit `eb0d1db26a35704553c93782bad8c3ac2d971597` |
| Build | `scripts/build_chartjs.mjs` with esbuild 0.28.2. It reproduces the UMD build in Chart.js's own `rollup.config.js`: entry `src/index.umd.ts`, target ES2022, minified, MIT banner, global `window.Chart` |
| Size | 207,635 bytes (the npm file is about 200 KB; the minifiers differ) |
| SHA-384 (SRI) | `sha384-rBeQHxx+fD4yTWwymMLsGUggrWAk1ssEcKZuxBdYt+43t//g2T2q49rj78sPe7D3` |
| Reproducible | yes: running the script again on the same commits gives an identical file (checked) |

**What was verified with this file** (see `docs/TRADELEDGER_FINAL_QA_REPORT.md`):
- all ten `chartjs_real` browser tests pass in Chromium, covering the seven charts, the data, tooltips, filters, resizing, CSP and SRI;
- `Chart.version` reports `4.5.1`;
- the registered controllers are bar, bubble, doughnut, line, pie, polarArea, radar and scatter.

**What was not verified:** Chart.js's own test suite. That needs Karma and many npm packages, which were unavailable.

**To use the official npm file instead** (CI does this as an extra check):
1. Copy `package/dist/chart.umd.min.js` from `npm pack chart.js@4.5.1` over this file.
2. Put its hash in `CHART_JS_BUNDLED_INTEGRITY` in `tradeledger/config.py`.

`tests/test_chartjs_bundle.py` fails if the file and the configured hash ever disagree.
```

### `tradeledger/static/vendor/chartjs/LICENSE-kurkle-color.md` (NEW)

**Why:** MIT licence of @kurkle/color 0.3.2 (bundled inside Chart.js), copied from its repository.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```markdown
The MIT License (MIT)

Copyright (c) 2018-2021 Jukka Kurkela

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

### `tradeledger/static/vendor/chartjs/LICENSE.md` (NEW)

**Why:** Chart.js MIT licence, copied from the v4.5.1 tag.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```markdown
The MIT License (MIT)

Copyright (c) 2014-2024 Chart.js Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

### `tradeledger/templates/account/two_step.html` (NEW)

**Why:** Two-step set-up, recovery codes (shown once), new codes, switching off.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Two-step verification{% endblock %}
{% block heading %}Two-step verification{% endblock %}
{% block description %}Sign in with your password and a code from your phone.{% endblock %}

{% block content %}
<p><a href="{{ url_for('main.account') }}">← Back to your account</a></p>

{% if new_codes %}
<section class="card form-section" aria-labelledby="codes-title">
    <div class="notice notice--warning" role="status">
        <p><strong>Save these recovery codes now. They will not be shown again.</strong></p>
    </div>
    <h2 class="form-section__title" id="codes-title">Your recovery codes</h2>
    <p>Each code works once, instead of a code from your app, if you lose your phone.
       Keep them somewhere safe and separate from your phone, for example in a password manager or printed at home.</p>
    <ul class="recovery-codes" aria-label="Recovery codes">
        {% for code in new_codes %}<li><code>{{ code }}</code></li>{% endfor %}
    </ul>
</section>
{% endif %}

{% if enabled %}
<section class="card form-section" aria-labelledby="status-title">
    <h2 class="form-section__title" id="status-title">Two-step verification is on</h2>
    <dl class="detail-list">
        <div><dt>Switched on</dt><dd>{{ enabled_at | when }}</dd></div>
        <div><dt>Unused recovery codes</dt><dd>{{ unused_codes }}</dd></div>
    </dl>
    {% if unused_codes < 3 %}
    <div class="notice notice--warning" role="status"><p>You have few recovery codes left. Create a new set below.</p></div>
    {% endif %}
</section>

<section class="card form-section" aria-labelledby="new-codes-title">
    <h2 class="form-section__title" id="new-codes-title">New recovery codes</h2>
    <p>Creates ten new codes and cancels every old one.</p>
    {{ f.error_summary(errors if request.endpoint == "main.two_step_new_codes" else {}, labels) }}
    <form method="post" action="{{ url_for('main.two_step_new_codes') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="email" name="username" value="{{ g.user.email }}" autocomplete="username" hidden>
        <div class="form-stack">
            {{ f.text("current_password", "Current password", {}, errors if request.endpoint == "main.two_step_new_codes" else {},
                      labels, type="password", required=True, maxlength=128, autocomplete="current-password") }}
        </div>
        <div class="form-actions">
            <button class="button button--secondary" type="submit">Create new recovery codes</button>
        </div>
    </form>
</section>

<section class="card form-section" aria-labelledby="disable-title">
    <h2 class="form-section__title" id="disable-title">Turn off two-step verification</h2>
    <p>Signing in will then need only your password.</p>
    {{ f.error_summary(errors if request.endpoint == "main.two_step_disable" else {}, labels) }}
    <form method="post" action="{{ url_for('main.two_step_disable') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="email" name="username" value="{{ g.user.email }}" autocomplete="username" hidden>
        <div class="form-stack">
            {{ f.text("current_password", "Current password", {}, errors if request.endpoint == "main.two_step_disable" else {},
                      labels, type="password", required=True, maxlength=128, autocomplete="current-password") }}
            {{ f.text("code", "Code", {}, errors if request.endpoint == "main.two_step_disable" else {}, labels, required=True,
                      maxlength=20, autocomplete="one-time-code", hint="From your app, or an unused recovery code.") }}
        </div>
        <div class="form-actions">
            <button class="button button--danger" type="submit">Turn off</button>
        </div>
    </form>
</section>
{% else %}
<section class="card form-section" aria-labelledby="setup-title">
    <h2 class="form-section__title" id="setup-title">Set up two-step verification</h2>
    <ol class="steps">
        <li>Install an authenticator app on your phone, for example Google Authenticator, Microsoft Authenticator, 1Password or Aegis.</li>
        <li>In the app, add an account by entering this setup key (time-based):
            <p><code class="setup-key" aria-label="Setup key">{{ secret_grouped }}</code></p>
            <details>
                <summary>Using a password manager on this device instead?</summary>
                <p>Some apps accept this link: <code class="setup-uri">{{ uri }}</code></p>
            </details>
        </li>
        <li>Enter your current password and the 6-digit code the app now shows.</li>
    </ol>
    <p class="muted">The setup key is a secret: anyone who has it can create your codes. It is shown only on this page.</p>
    {{ f.error_summary(errors, labels) }}
    <form method="post" action="{{ url_for('main.two_step_enable') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="email" name="username" value="{{ g.user.email }}" autocomplete="username" hidden>
        <div class="form-stack">
            {{ f.text("current_password", "Current password", {}, errors, labels, type="password", required=True,
                      maxlength=128, autocomplete="current-password") }}
            {{ f.text("code", "Code", {}, errors, labels, required=True, maxlength=10, autocomplete="one-time-code",
                      hint="6 digits from your app.") }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Checking…">Turn on two-step verification</button>
        </div>
    </form>
</section>
{% endif %}
{% endblock %}
```

### `tradeledger/templates/auth/reset_password.html` (NEW)

**Why:** Choose a new password from a reset link (or the single 'invalid' message).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Choose a new password{% endblock %}
{% block heading %}Choose a new password{% endblock %}
{% block description %}For a one-time link from your administrator.{% endblock %}

{% block content %}
<section class="card form-section auth-card" aria-labelledby="reset-title">
    <h2 class="form-section__title" id="reset-title">{{ "Choose a new password" if valid else "Reset link" }}</h2>
    {% if valid %}
    <p>Choosing a new password signs you out everywhere and revokes your API tokens. You will then sign in again.</p>
    {{ f.error_summary(errors, labels) }}
    <form method="post" action="{{ url_for('main.reset_password') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="email" name="username" value="{{ email }}" autocomplete="username" hidden>
        <div class="form-stack">
            {{ f.text("new_password", "New password", {}, errors, labels, type="password", required=True, maxlength=128,
                      autocomplete="new-password", hint="At least 12 characters. A few unrelated words make a strong, memorable password.") }}
            {{ f.text("confirm_password", "Confirm new password", {}, errors, labels, type="password", required=True,
                      maxlength=128, autocomplete="new-password") }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Saving…">Save new password</button>
        </div>
    </form>
    {% else %}
    <div class="notice notice--warning" role="alert">
        <p>{{ message }}</p>
    </div>
    <p class="auth-card__note"><a href="{{ url_for('main.login') }}">Back to sign in</a></p>
    {% endif %}
</section>
{% endblock %}
```

### `tradeledger/templates/auth/two_step.html` (NEW)

**Why:** Second sign-in step: code or recovery code.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}

{% block title %}Two-step verification{% endblock %}
{% block heading %}Sign in{% endblock %}
{% block description %}Your password was right. One more step.{% endblock %}

{% block content %}
<section class="card form-section auth-card" aria-labelledby="two-step-title">
    <h2 class="form-section__title" id="two-step-title">Enter your code</h2>
    {% if error %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="two-step-error">
        <h2 class="error-summary__title" id="two-step-error">Not signed in yet</h2>
        <p>{{ error }}</p>
    </div>
    {% endif %}
    <p>Open your authenticator app and enter the 6-digit code for TradeLedger.
       Lost your phone? Enter one of your recovery codes instead.</p>
    <form method="post" action="{{ url_for('main.login_two_step') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <div class="form-stack">
            <div class="field{% if error %} field--error{% endif %}">
                <label class="field__label" for="code">Code</label>
                <p class="field__hint" id="code-hint">6 digits, or a recovery code such as abcd-efgh-jkmn.</p>
                <input class="input" id="code" name="code" type="text" inputmode="text" autocomplete="one-time-code"
                       autocapitalize="none" spellcheck="false" required maxlength="20" aria-describedby="code-hint"
                       {% if error %}aria-invalid="true"{% endif %} autofocus>
            </div>
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Checking…">Verify and sign in</button>
        </div>
    </form>
    <p class="muted auth-card__note">This step expires after 5 minutes. <a href="{{ url_for('main.login') }}">Start again</a>.</p>
</section>
{% endblock %}
```

### `tradeledger/totp.py` (NEW)

**Why:** TOTP/HOTP (RFC 6238/4226) with the standard library, AES-GCM secret storage bound to the user, recovery codes.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Two-step verification codes (TOTP, RFC 6238) and their storage (Phase 8.5).

Plain English
-------------
Two-step verification means signing in needs your password AND a six-digit
code from an authenticator app on your phone (Google Authenticator,
Microsoft Authenticator, 1Password, Aegis, ...). The app and TradeLedger
share a secret; both turn "secret + current time" into the same code,
which changes every 30 seconds. Someone who learns your password still
cannot sign in without your phone.

Technical summary
-----------------
Codes      TOTP as in RFC 6238 over HOTP (RFC 4226): HMAC-SHA1, 30-second
           steps, 6 digits, the defaults every authenticator app supports.
           Implemented with the standard library (hmac, hashlib, base64);
           the RFC 6238 test vectors are checked in tests/test_two_step.py.
Window     A code is accepted for the current step and one step either side
           (about +/- 30 s of clock difference).
Replay     The last accepted step is stored; a code for that step or an
           earlier one is refused, so an intercepted code cannot be reused.
Secret     20 random bytes (160 bits, as RFC 4226 recommends), shown to the
           user once in base32. In the database it is ENCRYPTED with AES-GCM
           (the `cryptography` package, already a dependency). The key is
           derived from SECRET_KEY with HKDF-SHA256, and the user id is bound
           in as associated data, so a secret copied onto another user's row
           does not decrypt. Rotating SECRET_KEY makes stored secrets
           unreadable: those users sign in with a recovery code and set
           two-step up again (or an operator runs disable-two-step).
Recovery   Ten single-use codes (60 random bits each, xxxx-xxxx-xxxx),
           stored as SHA-256 only. Generating new codes cancels the old set.

Why TOTP and not SMS or email codes: no telephone or email provider is
needed, SMS can be intercepted (SIM swapping), and TOTP works offline.
WebAuthn/passkeys would be stronger still, but need a JavaScript flow and
a library for attestation; they are listed as future work.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
SECRET_BYTES = 20
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
_STORAGE_VERSION = "v1"


# ---------- Codes ----------


def new_secret():
    """A new shared secret, base32 without padding (what apps accept)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key_bytes(secret_b32):
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded)


def hotp(key, counter, digits=DIGITS, digest=hashlib.sha1):
    """RFC 4226 HOTP value for `counter` (an int) as a zero-padded string."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def time_step(now):
    return int(now) // STEP_SECONDS


def code_at(secret_b32, now):
    return hotp(_key_bytes(secret_b32), time_step(now))


def normalise_code(text):
    return "".join(ch for ch in (text or "") if not ch.isspace() and ch != "-")


def verify_code(secret_b32, code, *, now, last_step=None):
    """The time step the code belongs to, or None if it is wrong, outside
    the window, or not newer than `last_step` (a replay)."""
    code = normalise_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    key = _key_bytes(secret_b32)
    current = time_step(now)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def provisioning_uri(secret_b32, *, account, issuer):
    """The otpauth:// URI authenticator apps import (key URI format)."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def grouped(secret_b32):
    """ "ABCD EFGH ..." for reading the secret aloud or typing it."""
    return " ".join(secret_b32[i : i + 4] for i in range(0, len(secret_b32), 4))


# ---------- Storage of the secret ----------


def _aead(secret_key):
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"tradeledger-totp-v1", info=b"totp secret").derive(
        secret_key.encode("utf-8")
    )
    return AESGCM(key)


def encrypt_secret(secret_b32, *, secret_key, user_id):
    nonce = secrets.token_bytes(12)
    sealed = _aead(secret_key).encrypt(nonce, secret_b32.encode("ascii"), str(user_id).encode("ascii"))
    return f"{_STORAGE_VERSION}:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt_secret(stored, *, secret_key, user_id):
    """The base32 secret, or None if it cannot be read (wrong key, wrong
    user, damaged value)."""
    if not isinstance(stored, str) or not stored.startswith(f"{_STORAGE_VERSION}:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(stored.split(":", 1)[1].encode("ascii"))
        plain = _aead(secret_key).decrypt(raw[:12], raw[12:], str(user_id).encode("ascii"))
    except (InvalidTag, ValueError):
        return None
    return plain.decode("ascii")


# ---------- Recovery codes ----------


def new_recovery_codes(count=RECOVERY_CODE_COUNT):
    codes = []
    for _ in range(count):
        chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
        codes.append(f"{chars[:4]}-{chars[4:8]}-{chars[8:]}")
    return codes


def recovery_hash(code):
    """SHA-256 of the code as typed, ignoring case, spaces and dashes."""
    return hashlib.sha256(normalise_code(code).lower().encode("utf-8")).hexdigest()


def looks_like_recovery_code(text):
    code = normalise_code(text).lower()
    return len(code) == 12 and all(ch in _RECOVERY_ALPHABET for ch in code)
```

### `tradeledger/web/reset.py` (NEW)

**Why:** /reset-password: token moved out of the URL, one transaction for password + revocations, throttled, no Referer.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Choosing a new password from a one-time reset link (Phase 8.5).

Flow
----
1. The operator runs:  flask --app tradeledger create-reset-link --email you@example.com
   and hands you the printed link (repositories/password_resets.py).
2. GET /reset-password?token=tlr_...  checks the link WITHOUT using it, keeps
   the token in this browser's signed session cookie and immediately
   redirects to /reset-password, so the token leaves the address bar, the
   browser history and any Referer header.
3. GET /reset-password  shows the form (or "invalid or expired").
4. POST /reset-password  (CSRF-protected): new password twice. On success
   the link is used up, every session ends, every API token is revoked
   and you are sent to the sign-in page. You are NOT signed in
   automatically, so two-step verification (if switched on) still applies.

Every failure (unknown, used, expired, malformed link) shows the same
message. Failed attempts are counted per network address; past the limit
the page answers 429, although a 256-bit token cannot be guessed anyway.
"""

import logging

from flask import current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, parameters_from_config, password_problem
from ..repositories import api_tokens, password_resets, throttle
from ..repositories.users import get_user, set_password_hash
from . import main_bp
from .auth import client_ip, digest, now_seconds

log = logging.getLogger("tradeledger.auth")

INVALID = (
    "This reset link is invalid, has already been used or has expired. Ask your administrator for a new one."
)
LABELS = {"new_password": "New password", "confirm_password": "Confirm new password"}
SESSION_KEY = "reset_token"


def _blocked(conn):
    config = current_app.config
    return throttle.is_blocked(
        conn,
        "reset-ip",
        digest("reset-ip", client_ip()),
        limit=config["LOGIN_MAX_FAILURES_PER_IP"],
        window_seconds=config["LOGIN_WINDOW_SECONDS"],
    )


def _record_failure(conn):
    throttle.record_failure(
        conn,
        "reset-ip",
        digest("reset-ip", client_ip()),
        window_seconds=current_app.config["LOGIN_WINDOW_SECONDS"],
    )


def _page(status=200, **context):
    g.referrer_policy = "no-referrer"
    defaults = {"valid": False, "errors": {}, "labels": LABELS, "message": None}
    return render_template("auth/reset_password.html", **{**defaults, **context}), status


@main_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    conn = get_db()
    if _blocked(conn):
        log.warning("password reset refused: too many failures", extra={"event": "auth.reset.throttled"})
        return _page(429, message="Too many attempts. Wait a few minutes, then try again.")

    if request.method == "GET" and "token" in request.args:
        token = request.args.get("token", "")
        if password_resets.find_valid(conn, token, now=now_seconds()) is None:
            _record_failure(conn)
            session.pop(SESSION_KEY, None)
            log.info("password reset link refused", extra={"event": "auth.reset.invalid"})
            return _page(400, message=INVALID)
        session[SESSION_KEY] = token
        return redirect(url_for("main.reset_password"), code=303)

    token = session.get(SESSION_KEY)
    link = password_resets.find_valid(conn, token, now=now_seconds()) if token else None
    if link is None:
        session.pop(SESSION_KEY, None)
        return _page(400 if request.method == "POST" else 200, message=INVALID)
    user = get_user(conn, link["user_id"])
    if user is None or not user["is_active"]:
        session.pop(SESSION_KEY, None)
        return _page(400, message=INVALID)

    if request.method == "GET":
        return _page(valid=True, email=user["email"])

    new = request.form.get("new_password", "")
    errors = {}
    problem = password_problem(new, email=user["email"])
    if problem:
        errors["new_password"] = problem
    elif request.form.get("confirm_password", "") != new:
        errors["confirm_password"] = "does not match the new password."
    if errors:
        return _page(400, valid=True, email=user["email"], errors=errors)

    now = now_seconds()
    with conn:
        if not password_resets.consume(conn, link["id"], user_id=user["id"], now=now):
            session.pop(SESSION_KEY, None)
            return _page(400, message=INVALID)
        set_password_hash(
            conn,
            user["id"],
            hash_password(new, parameters_from_config(current_app.config)),
            end_other_sessions=True,
        )
        revoked = api_tokens.revoke_all(conn, user_id=user["id"])
        throttle.clear(conn, "login-email", digest("login-email", user["email"]))
    session.clear()
    log.info(
        "password reset with a link",
        extra={"event": "auth.reset.completed", "user_id": user["id"], "tokens_revoked": revoked},
    )
    flash(
        "Your password has been changed and every session has been signed out. Sign in with your new password.",
        "success",
    )
    return redirect(url_for("main.login"), code=303)
```

### `tradeledger/web/two_step.py` (NEW)

**Why:** Second sign-in step and the two-step account pages.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Two-step verification: setting it up, and the second sign-in step (Phase 8.5).

Sign-in with two-step switched on
---------------------------------
1. /login checks the email and password as before. If they are right and
   the account uses two-step, NO session is started: the cookie only holds
   a "pending" note (user id, session version, time, where to go next) and
   the browser is sent to /login/two-step.
2. /login/two-step asks for the 6-digit code (or a recovery code). The
   pending note is valid for 5 minutes. Wrong codes are counted per account
   (5 per 15 minutes) and per network address, like passwords.
3. A right code starts the real session (web/auth.start_session).

Setting it up (Account -> Two-step verification)
------------------------------------------------
GET shows a new secret (kept only in this browser's signed session until
confirmed) as text and as an otpauth:// link. POST /enable needs the current
password AND a code from the app, which proves the app was set up correctly
before the account depends on it. Ten recovery codes are then shown ONCE.
Turning it off needs the password and a current code or a recovery code.
"""

import logging

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import totp
from ..db import get_db
from ..passwords import verify_password
from ..repositories import throttle, two_step
from ..repositories.users import get_user, record_login
from . import main_bp
from .auth import THROTTLED, client_ip, current_user_id, digest, now_seconds, safe_next, start_session

log = logging.getLogger("tradeledger.auth")

PENDING_KEY = "pending_two_step"
SETUP_KEY = "two_step_setup"
PENDING_SECONDS = 300
WRONG_CODE = "That code is not right. Check the time on your phone, then try the newest code."
LABELS = {"code": "Code", "current_password": "Current password"}


# ---------- Shared checks ----------


def _secret_of(user):
    return totp.decrypt_secret(
        user["totp_secret"], secret_key=current_app.config["SECRET_KEY"], user_id=user["id"]
    )


def check_second_factor(conn, user, code, *, allow_recovery=True):
    """True if `code` is a valid, unused TOTP code or recovery code for
    `user`. A successful TOTP code is recorded so it cannot be replayed."""
    now = now_seconds()
    secret = _secret_of(user)
    if secret is not None:
        step = totp.verify_code(secret, code, now=now, last_step=user["totp_last_step"])
        if step is not None and two_step.accept_step(conn, user_id=user["id"], step=step):
            return "code"
    if (
        allow_recovery
        and totp.looks_like_recovery_code(code)
        and two_step.use_recovery_code(conn, user_id=user["id"], code=code, now=now)
    ):
        return "recovery"
    return None


def _throttle_keys(user_id):
    return digest("two-step", str(user_id)), digest("login-ip", client_ip())


def _blocked(conn, user_id):
    config = current_app.config
    user_key, ip_key = _throttle_keys(user_id)
    window = config["LOGIN_WINDOW_SECONDS"]
    return throttle.is_blocked(
        conn, "two-step", user_key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ) or throttle.is_blocked(
        conn, "login-ip", ip_key, limit=config["LOGIN_MAX_FAILURES_PER_IP"], window_seconds=window
    )


def _record_failure(conn, user_id):
    window = current_app.config["LOGIN_WINDOW_SECONDS"]
    user_key, ip_key = _throttle_keys(user_id)
    throttle.record_failure(conn, "two-step", user_key, window_seconds=window)
    throttle.record_failure(conn, "login-ip", ip_key, window_seconds=window)


# ---------- Second sign-in step ----------


def begin_pending(user, next_path):
    """Called by /login after a correct password for a two-step account."""
    session.clear()
    session[PENDING_KEY] = {
        "uid": user["id"],
        "sv": user["session_version"],
        "at": now_seconds(),
        "next": next_path,
    }
    log.info(
        "password accepted, code required",
        extra={"event": "auth.login.two_step_required", "user_id": user["id"]},
    )
    return redirect(url_for("main.login_two_step"), code=303)


def _pending_user(conn):
    pending = session.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return None, None
    if now_seconds() - int(pending.get("at", 0)) > PENDING_SECONDS:
        session.pop(PENDING_KEY, None)
        return None, None
    user = get_user(conn, pending.get("uid"))
    if (
        user is None
        or not user["is_active"]
        or not user["totp_enabled_at"]
        or user["session_version"] != pending.get("sv")
    ):
        session.pop(PENDING_KEY, None)
        return None, None
    return user, pending


@main_bp.route("/login/two-step", methods=["GET", "POST"])
def login_two_step():
    conn = get_db()
    user, pending = _pending_user(conn)
    if user is None:
        flash("Please sign in again.", "info")
        return redirect(url_for("main.login"), code=303)
    if request.method == "GET":
        return render_template("auth/two_step.html", error=None)
    if _blocked(conn, user["id"]):
        log.warning("two-step refused: too many failures", extra={"event": "auth.two_step.throttled"})
        return render_template("auth/two_step.html", error=THROTTLED), 429
    method = check_second_factor(conn, user, request.form.get("code", ""))
    if method is None:
        _record_failure(conn, user["id"])
        log.warning("two-step code wrong", extra={"event": "auth.two_step.failed", "user_id": user["id"]})
        return render_template("auth/two_step.html", error=WRONG_CODE), 400
    record_login(conn, user["id"])
    start_session(get_user(conn, user["id"]))
    log.info(
        "sign-in succeeded",
        extra={"event": "auth.login.succeeded", "user_id": user["id"], "second_factor": method},
    )
    if method == "recovery":
        left = two_step.unused_recovery_codes(conn, user_id=user["id"])
        flash(
            f"You used a recovery code. {left} unused code(s) left: create new ones on the Account page.",
            "info",
        )
    return redirect(safe_next(pending.get("next")) or url_for("main.index"), code=303)


# ---------- Account page ----------


def _render_setup(status=200, **extra):
    user = g.user
    context = {"enabled": bool(user["totp_enabled_at"]), "errors": {}, "labels": LABELS, "new_codes": None}
    if user["totp_enabled_at"]:
        context["unused_codes"] = two_step.unused_recovery_codes(get_db(), user_id=user["id"])
        context["enabled_at"] = user["totp_enabled_at"]
    else:
        secret = session.get(SETUP_KEY)
        if not secret:
            secret = session[SETUP_KEY] = totp.new_secret()
        context["secret_grouped"] = totp.grouped(secret)
        context["uri"] = totp.provisioning_uri(
            secret, account=user["email"], issuer=current_app.config["TOTP_ISSUER"]
        )
    context.update(extra)
    return render_template("account/two_step.html", **context), status


@main_bp.route("/account/two-step")
def two_step_settings():
    return _render_setup()


def _password_ok(conn, user, errors):
    """Check the current password, counting failures like sign-in does."""
    config = current_app.config
    key = digest("login-email", user["email"])
    window = config["LOGIN_WINDOW_SECONDS"]
    if throttle.is_blocked(
        conn, "login-email", key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ):
        errors["current_password"] = THROTTLED
        return False
    if not verify_password(request.form.get("current_password", ""), user["password_hash"]):
        throttle.record_failure(conn, "login-email", key, window_seconds=window)
        errors["current_password"] = "is incorrect."
        return False
    return True


@main_bp.route("/account/two-step/enable", methods=["POST"])
def two_step_enable():
    conn = get_db()
    user = g.user
    if user["totp_enabled_at"]:
        return redirect(url_for("main.two_step_settings"), code=303)
    secret = session.get(SETUP_KEY)
    if not secret:
        return redirect(url_for("main.two_step_settings"), code=303)
    errors = {}
    password_ok = _password_ok(conn, user, errors)
    step = totp.verify_code(secret, request.form.get("code", ""), now=now_seconds())
    if step is None:
        errors["code"] = "is not right. Enter the 6-digit code your app shows now."
    if errors or not password_ok:
        return _render_setup(400, errors=errors)
    codes = totp.new_recovery_codes()
    two_step.enable(
        conn,
        user_id=user["id"],
        encrypted_secret=totp.encrypt_secret(
            secret, secret_key=current_app.config["SECRET_KEY"], user_id=user["id"]
        ),
        accepted_step=step,
        recovery_codes=codes,
    )
    session.pop(SETUP_KEY, None)
    g.user = get_user(conn, user["id"])
    log.info("two-step enabled", extra={"event": "auth.two_step.enabled", "user_id": user["id"]})
    return _render_setup(201, new_codes=codes)


@main_bp.route("/account/two-step/disable", methods=["POST"])
def two_step_disable():
    conn = get_db()
    user = g.user
    if not user["totp_enabled_at"]:
        abort(404)
    errors = {}
    password_ok = _password_ok(conn, user, errors)
    if password_ok and check_second_factor(conn, user, request.form.get("code", "")) is None:
        errors["code"] = "is not right. Enter a current code from your app, or an unused recovery code."
    if errors:
        return _render_setup(400, errors=errors)
    two_step.disable(conn, user_id=user["id"])
    log.info("two-step disabled", extra={"event": "auth.two_step.disabled", "user_id": user["id"]})
    flash("Two-step verification is off. Signing in now needs only your password.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/two-step/recovery-codes", methods=["POST"])
def two_step_new_codes():
    conn = get_db()
    user = g.user
    if not user["totp_enabled_at"]:
        abort(404)
    errors = {}
    if not _password_ok(conn, user, errors):
        return _render_setup(400, errors=errors)
    codes = totp.new_recovery_codes()
    two_step.replace_recovery_codes(conn, user_id=current_user_id(), codes=codes)
    log.info(
        "recovery codes replaced", extra={"event": "auth.two_step.codes_replaced", "user_id": user["id"]}
    )
    return _render_setup(201, new_codes=codes)
```

### `tests/test_browser_account_security.py` (NEW)

**Why:** 20 Playwright tests: two-step set-up and sign-in, reset-link journey, signing out another browser, new pages at four widths and 200% zoom.  
**How to run or test:** `python -m pytest tests/test_browser_account_security.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: the new account-security journeys in a real browser (Chromium).

Each journey uses the real forms, exactly as a person would:
  * set up two-step verification by reading the setup key from the page,
    then sign out and sign in with a code;
  * use a one-time reset link from the command line;
  * see two signed-in browsers and sign one out from the other.
Every new page is also checked at 375, 768, 1024 and 1440 px for labels,
headings and horizontal scrolling.

Optional screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import os
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user
from tradeledger import totp
from tradeledger.cli import create_reset_link_command

pytestmark = pytest.mark.browser

THIRD_EMAIL = "reset.user@example.com"


def add_users(conn):
    ensure_user(conn, OTHER_EMAIL)
    ensure_user(conn, THIRD_EMAIL)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    url, stop = start_server(str(tmp_path_factory.mktemp("acctsec") / "acct.db"), prepare=add_users)
    yield url
    stop()


def sign_in(page, server, email, password=TEST_PASSWORD):
    page.goto(server + "/login")
    page.fill("#field-email", email)
    page.fill("#field-password", password)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()


def sign_out(page):
    with page.expect_navigation():
        page.get_by_role("button", name="Sign out", exact=True).first.click()


def shoot(page, name):
    folder = os.environ.get("TRADELEDGER_SCREENSHOT_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name), full_page=True)


def test_two_step_set_up_and_sign_in(server, page_factory):
    page = page_factory()
    sign_in(page, server, OTHER_EMAIL)
    page.goto(server + "/account")
    page.get_by_role("link", name="Set up two-step verification").click()
    assert page.locator("h1").inner_text().strip() == "Two-step verification"
    key = page.locator(".setup-key").inner_text().replace(" ", "")
    assert re.fullmatch(r"[A-Z2-7]{32}", key)
    shoot(page, "final_two_step_setup_1440.png")
    page.fill("#field-current_password", TEST_PASSWORD)
    page.fill("#field-code", totp.code_at(key, __import__("time").time()))
    with page.expect_navigation():
        page.get_by_role("button", name="Turn on two-step verification").click()
    codes = page.locator(".recovery-codes code").all_inner_texts()
    assert len(codes) == 10
    assert "Save these recovery codes now" in page.content()
    shoot(page, "final_two_step_codes_1440.png")

    sign_out(page)
    sign_in(page, server, OTHER_EMAIL)
    assert page.url.endswith("/login/two-step")
    assert page.locator("#code").get_attribute("autocomplete") == "one-time-code"
    shoot(page, "final_two_step_signin_1440.png")
    page.fill("#code", "000000")
    with page.expect_navigation():
        page.get_by_role("button", name="Verify and sign in").click()
    assert "That code is not right" in page.content()
    assert page.evaluate("document.activeElement.hasAttribute('data-error-summary')")
    page.fill("#code", codes[0])  # a recovery code also works, once
    with page.expect_navigation():
        page.get_by_role("button", name="Verify and sign in").click()
    assert page.locator("h1").inner_text().strip() == "Overview"
    assert "You used a recovery code. 9 unused code(s) left" in page.content()
    assert page.problems == []


def test_reset_link_journey(server, page_factory):
    app = server.app
    result = CliRunner().invoke(
        create_reset_link_command,
        ["--email", THIRD_EMAIL, "--base-url", "http://localhost"],
        obj=ScriptInfo(create_app=lambda: app),
    )
    link = result.output.strip().splitlines()[-1].replace("http://localhost", server)
    page = page_factory()
    page.goto(link)
    assert page.url == server + "/reset-password"  # the token has left the address bar
    shoot(page, "final_reset_password_1440.png")
    page.fill("#field-new_password", "lighthouse pebble orbit tangerine")
    page.fill("#field-confirm_password", "lighthouse pebble orbit tangerine")
    with page.expect_navigation():
        page.get_by_role("button", name="Save new password").click()
    assert page.url.endswith("/login")
    assert "Your password has been changed" in page.content()
    sign_in(page, server, THIRD_EMAIL, "lighthouse pebble orbit tangerine")
    assert page.locator("h1").inner_text().strip() == "Overview"
    page.goto(link)  # used up
    assert "invalid, has already been used or has expired" in page.content()
    assert page.problems == []


def test_sign_out_another_browser(server, page_factory):
    laptop = page_factory()
    phone = page_factory(375, 800)
    sign_in(laptop, server, TEST_EMAIL)
    sign_in(phone, server, TEST_EMAIL)
    phone.goto(server + "/account")
    rows = phone.locator("section[aria-labelledby=sessions-title] tbody tr")
    assert rows.count() >= 2
    assert (
        phone.locator("section[aria-labelledby=sessions-title] .badge", has_text="This browser").count() == 1
    )
    shoot(phone, "final_account_sessions_375.png")
    other = phone.locator("section[aria-labelledby=sessions-title] tbody tr:not(.is-current)").first
    with phone.expect_navigation():
        other.get_by_role("button").click()
    assert "That browser has been signed out" in phone.content()
    laptop.goto(server + "/trades")
    assert "/login" in laptop.url
    assert "Your session has ended" in laptop.content()
    assert phone.problems == [] and laptop.problems == []


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", ["/account", "/account/two-step", "/reset-password", "/login"])
def test_new_pages_are_accessible_and_fit_the_screen(server, page_factory, width, path):
    page = page_factory(width, 900, server=server if path.startswith("/account") else None)
    page.goto(server + path)
    assert page.locator("h1").count() == 1
    assert not horizontal_overflow(page), (path, width)
    unlabelled = page.evaluate(
        """() => [...document.querySelectorAll('input:not([type=hidden]):not([hidden]), select, textarea')]
                 .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label')).map(e => e.name)"""
    )
    assert unlabelled == [], (path, unlabelled)
    if path == "/account" and width >= 1024:
        # Regression (Phase 8.5 review): the "Sign out" button sat behind a
        # scroll bar at 1024px. The sessions table must fit its card.
        assert page.evaluate(
            "[...document.querySelectorAll('.table--sessions')].every(t => "
            "t.closest('.table-wrap').scrollWidth <= t.closest('.table-wrap').clientWidth + 1)"
        )
    assert page.problems == []


def test_200_percent_zoom_on_the_account_page(server, page_factory):
    page = page_factory(720, 450, server=server, device_scale_factor=2)
    page.goto(server + "/account")
    assert not horizontal_overflow(page)
```

### `tests/test_chartjs_bundle.py` (NEW)

**Why:** 8 tests: bundled file matches its SRI hash, licences and BUILD.md present, self-hosting is the default, CDN never inherits the hash.  
**How to run or test:** `python -m pytest tests/test_chartjs_bundle.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: the self-hosted Chart.js file and the default configuration.

Plain English: TradeLedger now ships its own copy of Chart.js. These tests
prove the copy is the one that was checked (its fingerprint matches the
configuration), that its licences travel with it, and that development
and production really use it by default, with no outside website.
They run without a browser; tests/test_chartjs_real.py draws the charts.
"""

import base64
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import ensure_user, sign_in
from tradeledger import config as config_module
from tradeledger import create_app
from tradeledger.db import get_db, init_schema

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "tradeledger/static/vendor/chartjs"
LIBRARY = VENDOR / "chart.umd.min.js"


def sri(path):
    return "sha384-" + base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def test_bundled_file_matches_the_configured_integrity_hash():
    assert sri(LIBRARY) == config_module.Config.CHART_JS_BUNDLED_INTEGRITY


def test_bundled_file_is_chart_js_4_5_1_with_both_licences():
    header = LIBRARY.read_text(encoding="utf-8")[:600]
    assert "Chart.js v4.5.1" in header and "Released under the MIT License" in header
    assert "@kurkle/color v0.3.2" in header
    assert "MIT License" in (VENDOR / "LICENSE.md").read_text(encoding="utf-8")
    assert "Jukka Kurkela" in (VENDOR / "LICENSE-kurkle-color.md").read_text(encoding="utf-8")


def test_build_notes_record_the_source_commits_and_the_hash():
    notes = (VENDOR / "BUILD.md").read_text(encoding="utf-8")
    assert "9c5cf9fac7ec04a71b516e2aff3f7d76876be369" in notes
    assert "eb0d1db26a35704553c93782bad8c3ac2d971597" in notes
    assert config_module.Config.CHART_JS_BUNDLED_INTEGRITY in notes
    assert "not byte-for-byte the npm file" in notes  # the honest caveat stays


def test_bundled_file_contains_no_source_map_or_remote_reference():
    text = LIBRARY.read_text(encoding="utf-8")
    assert "sourceMappingURL" not in text
    assert not re.search(r"""["'`]https?://(?!www\.chartjs\.org|github\.com/kurkle)""", text[:600])


def config_values(env):
    """Read the Chart.js settings in a fresh interpreter (config.py reads the
    environment once, at import), so the test does not disturb this one."""
    script = (
        "from tradeledger import config as c\n"
        "for n in ('DevelopmentConfig', 'ProductionConfig'):\n"
        "    k = getattr(c, n)\n"
        "    print(n, k.CHART_JS_URL, k.CHART_JS_INTEGRITY)\n"
    )
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TRADELEDGER_CHART_JS")}
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**clean, **env},
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        name: (url, integrity)
        for name, url, integrity in (line.split() for line in result.stdout.splitlines())
    }


def test_development_and_production_self_host_by_default():
    for url, integrity in config_values({}).values():
        assert url == "/static/vendor/chartjs/chart.umd.min.js"
        assert integrity == sri(LIBRARY)


def test_a_cdn_url_from_the_environment_does_not_inherit_the_bundled_hash():
    cdn = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    for url, integrity in config_values({"TRADELEDGER_CHART_JS_URL": cdn}).values():
        assert (url, integrity) == (cdn, "None")


def test_dashboard_with_the_default_setting_uses_only_this_site(db_path):
    defaults = config_module.Config
    app = create_app(
        "testing",
        overrides={
            "DATABASE": db_path,
            "CHART_JS_URL": defaults.CHART_JS_BUNDLED_URL,
            "CHART_JS_INTEGRITY": defaults.CHART_JS_BUNDLED_INTEGRITY,
        },
    )
    with app.app_context():
        from tradeledger.seed import seed_sample_data

        init_schema(get_db())
        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    client = app.test_client()
    sign_in(client)
    response = client.get("/analytics")
    html = response.get_data(as_text=True)
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "jsdelivr" not in csp
    assert f'src="{defaults.CHART_JS_BUNDLED_URL}"' in html
    assert f'integrity="{defaults.CHART_JS_BUNDLED_INTEGRITY}"' in html
    library = client.get(defaults.CHART_JS_BUNDLED_URL)
    assert library.status_code == 200 and library.data == LIBRARY.read_bytes()
    library.close()
    app.extensions["tradeledger_db"].close()


def test_docker_build_context_includes_the_vendor_files():
    ignore = (Path(__file__).resolve().parent.parent / ".dockerignore").read_text(encoding="utf-8")
    assert "!tradeledger/" in ignore
    excluded = [line for line in ignore.splitlines() if "vendor/chartjs" in line and not line.startswith("#")]
    assert excluded == ["tradeledger/static/vendor/chartjs/*.tgz"]
```

### `tests/test_database_safety.py` (NEW)

**Why:** 11 tests: test bridge refused in production, SQLite migration crash recovery, PostgreSQL migration killed half-way, rollback of a failed reset, races.  
**How to run or test:** `python -m pytest tests/test_database_safety.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: database safety under failure.

Plain English: what happens if the computer crashes in the middle of an
upgrade, if a step fails half-way, or if two people press the same button
at the same moment? These tests force each of those situations and check
the database is left either fully changed or not changed at all, never
half-way. They also prove the test-only PostgreSQL bridge can never run a
production server.
"""

import os
import re
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

from tests.conftest import TEST_PASSWORD
from tests.legacy_fixtures import build_phase7_database
from tradeledger import create_app, db
from tradeledger.db import connect, get_db, init_schema, list_tables
from tradeledger.migrations import current_version
from tradeledger.passwords import verify_password
from tradeledger.repositories import password_resets, sessions
from tradeledger.repositories.users import get_user

ROOT = Path(__file__).resolve().parent.parent
STRONG_KEY = "b16b00b5" * 8


# ---------- The test-only bridge can never serve production ----------


def test_only_a_module_marked_test_only_can_replace_psycopg():
    impostor = types.ModuleType("not_a_test_driver")
    with pytest.raises(RuntimeError, match="TEST_ONLY"):
        db.register_postgres_driver(impostor)


def test_production_refuses_to_start_while_a_test_driver_is_registered(monkeypatch):
    fake = types.ModuleType("fake_test_driver")
    fake.TEST_ONLY = True
    previous = db._pg_driver_override
    db.register_postgres_driver(fake)
    try:
        monkeypatch.setenv("SECRET_KEY", STRONG_KEY)
        with pytest.raises(RuntimeError, match="test-only PostgreSQL driver"):
            create_app(
                "production",
                overrides={
                    "SECRET_KEY": STRONG_KEY,
                    "DATABASE": None,
                    "DATABASE_URL": "postgresql://u@db.invalid/x",
                },
            )
    finally:
        db._pg_driver_override = previous


def test_the_bridge_does_not_exempt_itself_from_the_production_check():
    """Only tests/conftest.py may set the exemption, at run time."""
    source = (ROOT / "tests" / "pg_libpq_bridge.py").read_text(encoding="utf-8")
    assert "ALLOW_IN_PRODUCTION_CONFIG_TESTS" not in source


def test_the_application_package_never_imports_the_bridge():
    importing = re.compile(r"^\s*(from|import)\s+\S*pg_libpq_bridge", re.M)
    for path in (ROOT / "tradeledger").rglob("*.py"):
        assert not importing.search(path.read_text(encoding="utf-8")), path


def test_the_bridge_is_not_in_the_docker_build_context():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[ignore.index("*")] == "*"  # deny everything first...
    assert "!tests/" not in ignore and not any(
        line.startswith("!tests") for line in ignore
    )  # ...tests never allowed


# ---------- A crash in the middle of a SQLite migration ----------


@pytest.mark.sqlite_only
def test_a_crash_during_a_migration_leaves_the_previous_version_intact(tmp_path):
    """Kill the process (os._exit, no clean-up, no rollback code) while
    migration 4 is running. SQLite's journal must undo the partial work on
    the next open: version 3, no new tables, every row unchanged. Then a
    normal start-up finishes the upgrade."""
    path = tmp_path / "crash.db"
    build_phase7_database(path)  # version 2 with synthetic data
    conn = connect(str(path))
    from tradeledger import migrations

    for version in (3,):
        conn.raw.execute("PRAGMA foreign_keys = OFF")
        conn.raw.executescript(f"BEGIN;\n{migrations.MIGRATIONS[version]}")
        conn.raw.execute(f"PRAGMA user_version = {version}")
        conn.raw.execute("COMMIT")
        conn.raw.execute("PRAGMA foreign_keys = ON")
    before = [tuple(r) for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]
    conn.close()

    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from tradeledger.db import connect
        from tradeledger import migrations
        conn = connect({str(path)!r})
        steps = {{"n": 0}}
        def crash():
            steps["n"] += 1
            if steps["n"] > 3:
                os._exit(17)   # power cut: no rollback, no close
            return 0
        conn.raw.set_progress_handler(crash, 50)
        migrations.migrate(conn)
        print("migration finished without crashing")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 17, result.stdout + result.stderr

    after = connect(str(path))
    try:
        assert current_version(after) == 3
        assert "user_sessions" not in list_tables(after)
        assert [tuple(r) for r in after.execute("SELECT * FROM trades ORDER BY id").fetchall()] == before
        assert after.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert init_schema(after) == [4]  # the next start finishes the job
        assert current_version(after) == 4 and "user_sessions" in list_tables(after)
    finally:
        after.close()


# ---------- An interrupted PostgreSQL migration ----------


@pytest.mark.postgres_only
def test_a_postgres_migration_killed_half_way_changes_nothing(db_path):
    """Start migration 4 in another process, make it pause inside its
    transaction, terminate that server process (as a crash or a deploy
    timeout would), then check nothing of migration 4 remains and that a
    normal start-up applies it cleanly."""
    from tradeledger import migrations

    baseline = connect(db_path)  # build version 3 by hand: the baseline only
    with baseline:
        baseline.execute("SELECT pg_advisory_xact_lock(?)", (migrations._POSTGRES_LOCK_ID,))
        baseline.executescript(db.read_schema(db.POSTGRESQL))
        baseline.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (3, "PostgreSQL baseline")
        )
    assert current_version(baseline) == 3

    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from tests import pg_libpq_bridge
        from tradeledger import db, migrations
        if os.environ.get("TRADELEDGER_PG_DRIVER") == "libpq-bridge":
            db.register_postgres_driver(pg_libpq_bridge)
        # Some real work first, then a pause (pg_stat_activity shows only
        # the first 1 kB of a statement, so the marker comes early).
        migrations.POSTGRES_MIGRATIONS[4] = (
            "CREATE TABLE half_done_marker (x INTEGER); SELECT pg_sleep(30) /* tl-slow-migration */; "
            + migrations.POSTGRES_MIGRATIONS[4]
        )
        conn = db.connect({db_path!r})
        migrations.migrate_postgres(conn, "")
        """
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        killed = False
        for _ in range(100):
            rows = baseline.execute(
                "SELECT pid FROM pg_stat_activity WHERE query LIKE ? AND pid <> pg_backend_pid()",
                ("%tl-slow-migration%",),
            ).fetchall()
            if rows:
                baseline.execute("SELECT pg_terminate_backend(CAST(? AS integer))", (rows[0]["pid"],))
                killed = True
                break
            if worker.poll() is not None:
                break
            threading.Event().wait(0.1)
        assert killed, "the slow migration never started: " + (
            worker.communicate(timeout=5)[0] if worker.poll() is not None else ""
        )
        output = worker.communicate(timeout=30)[0]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate()
    assert worker.returncode != 0, output  # the worker died with its connection
    assert current_version(baseline) == 3
    assert "user_sessions" not in list_tables(baseline)
    assert "half_done_marker" not in list_tables(baseline)  # the work done before the kill is gone
    assert init_schema(baseline) == [4]
    assert current_version(baseline) == 4
    baseline.close()


# ---------- Failed transactions in the Phase 8.5 flows ----------


def test_a_failure_while_resetting_a_password_leaves_the_link_and_password_unchanged(app, monkeypatch):
    from click.testing import CliRunner
    from flask.cli import ScriptInfo

    from tests.web_helpers import csrf_token
    from tradeledger.cli import create_reset_link_command
    from tradeledger.web import reset

    output = (
        CliRunner()
        .invoke(
            create_reset_link_command,
            ["--email", "trader@example.com", "--base-url", "http://localhost"],
            obj=ScriptInfo(create_app=lambda: app),
        )
        .output
    )
    link = output.strip().splitlines()[-1].replace("http://localhost", "")
    client = app.test_client()
    client.get(link)

    def broken_revoke(*args, **kwargs):
        raise RuntimeError("simulated failure after the link was marked used")

    monkeypatch.setattr(reset.api_tokens, "revoke_all", broken_revoke)
    app.config["PROPAGATE_EXCEPTIONS"] = True
    with pytest.raises(RuntimeError, match="simulated"):
        client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_token(client, "/reset-password"),
                "new_password": "orchard granite velvet comet",
                "confirm_password": "orchard granite velvet comet",
            },
        )
    with app.app_context():
        conn = get_db()
        assert verify_password(TEST_PASSWORD, get_user(conn, 1)["password_hash"])
        assert conn.execute("SELECT used_at FROM password_resets").fetchone()["used_at"] is None
        assert (
            conn.execute("SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NOT NULL").fetchone()[0] == 0
        )
    monkeypatch.undo()
    retry = app.test_client()
    retry.get(link)
    assert (
        retry.post(
            "/reset-password",
            data={
                "csrf_token": csrf_token(retry, "/reset-password"),
                "new_password": "orchard granite velvet comet",
                "confirm_password": "orchard granite velvet comet",
            },
        ).status_code
        == 303
    )


# ---------- Two requests at the same moment ----------


def _race(db_path, work, count=2):
    """Run `work(conn)` in `count` threads with separate connections, all
    released together. Returns the results."""
    barrier = threading.Barrier(count)
    results, errors = [], []

    def run():
        conn = connect(db_path)
        try:
            barrier.wait()
            results.append(work(conn))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


def test_one_reset_link_used_twice_at_once_works_only_once(app, db_path):
    with app.app_context():
        token = password_resets.create_link(get_db(), user_id=1, now=1_900_000_000, lifetime_seconds=1800)
        link = password_resets.find_valid(get_db(), token, now=1_900_000_001)

    def use(conn):
        with conn:
            return password_resets.consume(conn, link["id"], user_id=1, now=1_900_000_002)

    results, errors = _race(db_path, use, count=4)
    assert sorted(results) == [False, False, False, True] or (errors and results.count(True) == 1)
    assert results.count(True) == 1


def test_one_session_revoked_from_two_places_at_once(app, db_path):
    with app.app_context():
        _, session_id = sessions.create_session(get_db(), user_id=1, now=1_900_000_000, max_age_seconds=3600)

    def revoke(conn):
        with conn:
            return sessions.revoke(conn, session_id, user_id=1, now=1_900_000_010)

    results, _ = _race(db_path, revoke, count=4)
    assert results.count(True) == 1


def test_one_two_step_code_submitted_twice_at_once_is_accepted_once(app, db_path):
    from tradeledger.repositories import two_step

    with app.app_context():
        two_step.enable(get_db(), user_id=1, encrypted_secret="v1:x", accepted_step=100, recovery_codes=[])

    def accept(conn):
        with conn:
            return two_step.accept_step(conn, user_id=1, step=101)

    results, _ = _race(db_path, accept, count=4)
    assert results.count(True) == 1


# ---------- Clean shutdown with the real connection pool ----------


@pytest.mark.postgres_only
def test_a_process_with_an_open_connection_pool_exits_promptly(db_path):
    """Final-audit regression: the psycopg_pool pool was never closed, so
    every process (each Gunicorn worker) waited about 20 seconds at exit for
    the pool's background threads. Needs the genuine psycopg_pool."""
    pytest.importorskip("psycopg_pool", reason="needs the genuine psycopg_pool (CI postgres job)")
    if db._pg_driver_override is not None:
        pytest.skip("a test-only driver is registered; this test is about the genuine pool")
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from tradeledger import create_app
        app = create_app("production", overrides={{
            "SECRET_KEY": {STRONG_KEY!r}, "DATABASE": None, "DATABASE_URL": {db_path!r}}})
        assert app.test_client().get("/health/ready", headers={{"Host": "localhost"}}).status_code == 200
        print("POOL", type(app.extensions["tradeledger_db"]._pool).__name__, flush=True)
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "TRADELEDGER_PG_DRIVER"}
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr[-2000:]
    assert "POOL ConnectionPool" in result.stdout
    assert "couldn't stop thread" not in result.stderr
    assert elapsed < 5, f"exit took {elapsed:.1f} s"
```

### `tests/test_device_sessions.py` (NEW)

**Why:** 27 tests: server-side sessions, cookie replay after sign-out, per-device sign-out, isolation, password change, each layer independently.  
**How to run or test:** `python -m pytest tests/test_device_sessions.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: one server-side record per signed-in browser.

Plain English: each sign-in now leaves a row in the database. These tests
prove that signing out really ends the session on the server (a copied
cookie stops working), that one browser can be signed out from another,
that nobody can sign out someone else's browser, and that password changes
and "sign out everywhere else" behave as the Account page promises.
Runs on SQLite and PostgreSQL.
"""

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import csrf_token
from tradeledger.db import get_db
from tradeledger.repositories import sessions
from tradeledger.repositories.sessions import describe_user_agent, key_hash
from tradeledger.repositories.users import set_active
from tradeledger.web import auth

FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)


def login(client, email=TEST_EMAIL, password=TEST_PASSWORD, user_agent=FIREFOX):
    data = {"csrf_token": csrf_token(client, "/login"), "email": email, "password": password}
    return client.post("/login", data=data, headers={"User-Agent": user_agent})


def cookie_value(client, app):
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    return None if cookie is None else cookie.value


def session_rows(app, user_id=1):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute("SELECT * FROM user_sessions WHERE user_id = ? ORDER BY id", (user_id,))
        ]


def post(client, path, form_page="/account"):
    return client.post(path, data={"csrf_token": csrf_token(client, form_page)})


# ---------- What is stored ----------


def test_signing_in_creates_one_row_holding_only_a_hash(app, anon_client):
    assert login(anon_client).status_code == 303
    rows = session_rows(app)
    assert len(rows) == 1
    row = rows[0]
    with anon_client.session_transaction() as cookie:
        key = cookie["sid"]
    assert row["key_hash"] == key_hash(key) and key not in str(row)
    assert row["user_agent"] == "Firefox on Windows"
    assert row["expires_at"] - row["created_at"] == app.config["SESSION_MAX_AGE_SECONDS"]
    assert row["revoked_at"] is None


def test_no_ip_address_or_full_user_agent_is_stored(app, anon_client):
    login(anon_client, user_agent=SAFARI_IPHONE)
    row = session_rows(app)[0]
    assert set(row) == {
        "id",
        "user_id",
        "key_hash",
        "created_at",
        "last_seen_at",
        "expires_at",
        "user_agent",
        "revoked_at",
    }
    assert row["user_agent"] == "Safari on iOS"


@pytest.mark.parametrize(
    "header, label",
    [
        (FIREFOX, "Firefox on Windows"),
        (SAFARI_IPHONE, "Safari on iOS"),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
            "Chrome on macOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
            "Chrome on Android",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0",
            "Edge on Windows",
        ),
        ("curl/8.5.0", "Script on unknown system"),
        ("", "Unknown browser on unknown system"),
        ("<script>alert(1)</script>", "Unknown browser on unknown system"),
    ],
)
def test_user_agent_is_reduced_to_a_short_label(header, label):
    assert describe_user_agent(header) == label


# ---------- Signing out really ends the session ----------


def test_a_cookie_copied_before_sign_out_is_refused_afterwards(app, anon_client):
    """The Phase 8 weakness this phase fixes: a signed cookie stayed valid
    after sign-out. Now the server's row is crossed out."""
    login(anon_client)
    stolen = cookie_value(anon_client, app)
    assert post(anon_client, "/logout", "/").status_code == 303
    thief = app.test_client()
    thief.set_cookie(app.config["SESSION_COOKIE_NAME"], stolen)
    response = thief.get("/trades")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert session_rows(app)[0]["revoked_at"] is not None


def test_a_cookie_without_a_server_session_is_refused(app, anon_client):
    """A correctly signed cookie (as Phase 8 issued) with no session key is
    no longer enough on its own."""
    now = auth.now_seconds()
    with anon_client.session_transaction() as session:
        session.update({"uid": 1, "sv": 1, "auth_at": now, "seen_at": now})
    assert anon_client.get("/").status_code == 302


def test_a_session_key_only_works_for_its_own_account(app, anon_client):
    with app.app_context():
        other = ensure_user(get_db(), OTHER_EMAIL)
    login(anon_client)  # a real session for user 1
    with anon_client.session_transaction() as session:
        session["uid"] = other  # a server bug or a forged cookie pairing user 1's key with user 2
        session["sv"] = 1
    assert anon_client.get("/").status_code == 302


def test_the_server_enforces_the_absolute_limit_even_if_the_cookie_is_edited(app, client, monkeypatch):
    later = auth.now_seconds() + app.config["SESSION_MAX_AGE_SECONDS"] + 5
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["auth_at"] = later - 10
        session["seen_at"] = later - 10
    assert client.get("/").status_code == 302


# ---------- Signing out one browser from another ----------


def test_one_browser_can_sign_out_another(app):
    laptop, phone = app.test_client(), app.test_client()
    login(laptop, user_agent=FIREFOX)
    login(phone, user_agent=SAFARI_IPHONE)
    rows = session_rows(app)
    assert len(rows) == 2
    page = phone.get("/account").get_data(as_text=True)
    assert "Firefox on Windows" in page and "Safari on iOS" in page and "This browser" in page
    laptop_id = next(r["id"] for r in rows if r["user_agent"] == "Firefox on Windows")
    response = post(phone, f"/account/sessions/{laptop_id}/revoke")
    assert response.status_code == 303 and response.headers["Location"] == "/account"
    assert laptop.get("/").status_code == 302  # the laptop is signed out
    assert phone.get("/").status_code == 200  # the phone is not


def test_signing_out_this_browser_from_the_list_signs_it_out(app, anon_client):
    login(anon_client)
    own = session_rows(app)[0]["id"]
    response = post(anon_client, f"/account/sessions/{own}/revoke")
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert anon_client.get("/").status_code == 302


def test_nobody_can_sign_out_another_users_browser(app):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    victim, attacker = app.test_client(), app.test_client()
    login(victim)
    login(attacker, email=OTHER_EMAIL)
    victim_session = session_rows(app, 1)[0]["id"]
    response = post(attacker, f"/account/sessions/{victim_session}/revoke")
    assert response.status_code == 404
    missing = post(attacker, "/account/sessions/999999/revoke")
    assert missing.status_code == 404 and missing.get_data() == response.get_data()
    assert victim.get("/").status_code == 200


def test_the_account_page_lists_only_your_own_browsers_and_never_a_key(app):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    mine, theirs = app.test_client(), app.test_client()
    login(mine, user_agent=FIREFOX)
    login(theirs, email=OTHER_EMAIL, user_agent=SAFARI_IPHONE)
    page = mine.get("/account").get_data(as_text=True)
    assert "Firefox on Windows" in page and "Safari on iOS" not in page
    with mine.session_transaction() as session:
        assert session["sid"] not in page
    for row in session_rows(app, 1):
        assert row["key_hash"] not in page


def test_revoking_needs_post_and_a_csrf_token(app, client):
    own = session_rows(app)[0]["id"]
    assert client.get(f"/account/sessions/{own}/revoke").status_code == 405
    assert client.post(f"/account/sessions/{own}/revoke").status_code == 400
    assert client.get("/").status_code == 200


# ---------- Password changes and "sign out everywhere else" ----------


def test_changing_the_password_signs_out_other_browsers_but_not_this_one(app):
    here, there = app.test_client(), app.test_client()
    login(here)
    login(there)
    response = here.post(
        "/account/password",
        data={
            "csrf_token": csrf_token(here, "/account"),
            "current_password": TEST_PASSWORD,
            "new_password": "a brand new passphrase",
            "confirm_password": "a brand new passphrase",
        },
    )
    assert response.status_code == 303
    assert here.get("/").status_code == 200
    assert there.get("/").status_code == 302
    active = [r for r in session_rows(app) if r["revoked_at"] is None]
    assert len(active) == 1  # only this browser's NEW session


def test_sign_out_everywhere_else_keeps_only_this_browser(app):
    here, there, elsewhere = app.test_client(), app.test_client(), app.test_client()
    for c in (here, there, elsewhere):
        login(c)
    assert post(here, "/account/sessions/end-others").status_code == 303
    assert here.get("/").status_code == 200
    assert there.get("/").status_code == 302 and elsewhere.get("/").status_code == 302
    assert len([r for r in session_rows(app) if r["revoked_at"] is None]) == 1


def test_deactivating_an_account_revokes_its_sessions(app, client):
    with app.app_context():
        set_active(get_db(), 1, False)
    assert all(r["revoked_at"] is not None for r in session_rows(app))
    assert client.get("/").status_code == 302


def test_old_sessions_are_cleaned_up_at_the_next_sign_in(app, anon_client):
    with app.app_context():
        conn = get_db()
        long_ago = auth.now_seconds() - 3 * 24 * 3600
        sessions.create_session(conn, user_id=1, now=long_ago, max_age_seconds=60)
    assert len(session_rows(app)) == 1
    login(anon_client)
    rows = session_rows(app)
    assert len(rows) == 1 and rows[0]["created_at"] > long_ago


def test_last_seen_is_written_at_most_once_a_minute(app, client, monkeypatch):
    start = auth.now_seconds()
    first = session_rows(app)[0]["last_seen_at"]
    monkeypatch.setattr(auth, "now_seconds", lambda: start + 30)
    client.get("/")
    assert session_rows(app)[0]["last_seen_at"] == first
    monkeypatch.setattr(auth, "now_seconds", lambda: start + 90)
    client.get("/")
    assert session_rows(app)[0]["last_seen_at"] == start + 90


def test_sign_in_helper_matches_the_real_form(app, anon_client):
    """Guard for the test helper itself: sign_in() must create exactly the
    kind of row the real form creates, or other tests would prove nothing."""
    sign_in(anon_client)
    assert anon_client.get("/").status_code == 200
    assert len(session_rows(app)) == 1


# ---------- Each layer holds on its own (defence in depth) ----------
# Phase 8.5 mutation testing showed that, with the new server-side rows,
# the older cookie checks were no longer tested independently: removing
# them changed no test result. These tests keep the server row VALID and
# break only the cookie-side condition, so each layer must work alone.


def test_the_session_version_check_works_even_if_the_row_is_still_valid(app, client):
    with app.app_context():
        conn = get_db()
        with conn:  # a version bump that (hypothetically) forgot to revoke rows
            conn.execute("UPDATE users SET session_version = session_version + 1 WHERE id = 1")
        assert conn.execute("SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NULL").fetchone()[0] >= 1
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")


def test_the_cookie_age_check_works_even_if_the_row_was_extended(app, client, monkeypatch):
    later = auth.now_seconds() + app.config["SESSION_MAX_AGE_SECONDS"] + 5
    with app.app_context():
        conn = get_db()
        with conn:  # a row whose expiry and activity were (wrongly) pushed forward
            conn.execute("UPDATE user_sessions SET expires_at = ?, last_seen_at = ?", (later + 3600, later))
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["seen_at"] = later
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
```

### `tests/test_password_reset.py` (NEW)

**Why:** 29 tests: one-time reset links (single use, expiry, throttling, logs, Referer, revocations, CSRF) and set-password revoking tokens.  
**How to run or test:** `python -m pytest tests/test_password_reset.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: one-time password-reset links issued by an operator.

Plain English: TradeLedger cannot email anyone, so the server's operator
prints a link with a command and hands it over. These tests prove the
link works exactly once, expires, cannot be guessed or reused, keeps the
secret out of logs and Referer headers, ends every session and API token,
and never signs the person in by itself.
"""

import logging

import pytest
from click.testing import CliRunner

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, issue_token
from tests.web_helpers import csrf_token
from tradeledger.cli import create_reset_link_command, set_password_command
from tradeledger.db import get_db
from tradeledger.passwords import verify_password
from tradeledger.repositories import password_resets
from tradeledger.repositories.users import get_user, get_user_by_email
from tradeledger.web import auth

NEW_PASSWORD = "harbour lantern copper meadow"


def make_link(app, email=TEST_EMAIL, base_url="http://localhost"):
    result = CliRunner().invoke(
        create_reset_link_command, ["--email", email, "--base-url", base_url], obj=app_info(app)
    )
    assert result.exit_code == 0, result.output
    return result.output.strip().splitlines()[-1]


def app_info(app):
    from flask.cli import ScriptInfo

    return ScriptInfo(create_app=lambda: app)


def token_of(link):
    return link.split("token=", 1)[1]


def open_link(client, link):
    return client.get(link.replace("http://localhost", ""))


def submit(client, password=NEW_PASSWORD, confirm=None):
    return client.post(
        "/reset-password",
        data={
            "csrf_token": csrf_token(client, "/reset-password"),
            "new_password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def stored_hash(app, email=TEST_EMAIL):
    with app.app_context():
        return get_user_by_email(get_db(), email)["password_hash"]


# ---------- The command ----------


def test_the_command_prints_a_link_and_stores_only_a_hash(app):
    link = make_link(app)
    assert link.startswith("http://localhost/reset-password?token=tlr_")
    token = token_of(link)
    with app.app_context():
        rows = get_db().execute("SELECT * FROM password_resets").fetchall()
    assert len(rows) == 1 and rows[0]["token_hash"] == password_resets.token_hash(token)
    assert token not in str(dict(rows[0]))
    assert rows[0]["expires_at"] - rows[0]["created_at"] == 30 * 60


def test_a_new_link_cancels_the_previous_one(app, anon_client):
    first = make_link(app)
    second = make_link(app)
    assert open_link(anon_client, first).status_code == 400
    assert open_link(app.test_client(), second).status_code == 303


@pytest.mark.parametrize("base_url", ["http://journal.example.com", "ftp://x", "javascript:alert(1)"])
def test_the_command_refuses_insecure_base_urls(app, base_url):
    result = CliRunner().invoke(
        create_reset_link_command, ["--email", TEST_EMAIL, "--base-url", base_url], obj=app_info(app)
    )
    assert result.exit_code != 0 and "https://" in result.output


def test_the_command_refuses_unknown_and_deactivated_accounts(app):
    from tradeledger.repositories.users import set_active

    unknown = CliRunner().invoke(
        create_reset_link_command, ["--email", "nobody@example.com"], obj=app_info(app)
    )
    assert unknown.exit_code != 0
    with app.app_context():
        set_active(get_db(), 1, False)
    inactive = CliRunner().invoke(create_reset_link_command, ["--email", TEST_EMAIL], obj=app_info(app))
    assert inactive.exit_code != 0 and "deactivated" in inactive.output


# ---------- Using the link ----------


def test_opening_the_link_moves_the_token_out_of_the_address_bar(app, anon_client):
    response = open_link(anon_client, make_link(app))
    assert response.status_code == 303 and response.headers["Location"] == "/reset-password"
    page = anon_client.get("/reset-password")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Choose a new password" in html and "tlr_" not in html
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert page.headers["Cache-Control"] == "no-store"


def test_a_good_link_changes_the_password_once_and_does_not_sign_in(app, anon_client):
    old_hash = stored_hash(app)
    link = make_link(app)
    open_link(anon_client, link)
    response = submit(anon_client)
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert stored_hash(app) != old_hash
    assert verify_password(NEW_PASSWORD, stored_hash(app))
    assert anon_client.get("/").status_code == 302  # not signed in automatically
    again = app.test_client()
    assert open_link(again, link).status_code == 400  # used up


def test_after_a_reset_the_new_password_works_and_the_old_one_does_not(app, anon_client):
    open_link(anon_client, make_link(app))
    submit(anon_client)
    from tests.test_device_sessions import login

    assert login(app.test_client(), password=TEST_PASSWORD).status_code == 200  # form shown again: refused
    assert login(app.test_client(), password=NEW_PASSWORD).status_code == 303


def test_a_reset_ends_every_session_and_revokes_every_token(app, client):
    token = issue_token(app)
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    visitor = app.test_client()
    open_link(visitor, make_link(app))
    submit(visitor)
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_password_rules_still_apply(app, anon_client):
    open_link(anon_client, make_link(app))
    short = submit(anon_client, password="too short")
    assert short.status_code == 400 and "at least 12 characters" in short.get_data(as_text=True)
    mismatch = submit(anon_client, confirm="something else entirely")
    assert mismatch.status_code == 400 and "does not match" in mismatch.get_data(as_text=True)
    own_email = submit(anon_client, password=TEST_EMAIL)
    assert own_email.status_code == 400
    assert submit(anon_client).status_code == 303  # the link survived the mistakes


def test_an_expired_link_is_refused(app, anon_client, monkeypatch):
    link = make_link(app)
    later = auth.now_seconds() + 30 * 60 + 1
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    monkeypatch.setattr("tradeledger.web.reset.now_seconds", lambda: later)
    response = open_link(anon_client, link)
    assert (
        response.status_code == 400
        and "invalid, has already been used or has expired" in response.get_data(as_text=True)
    )


def test_a_link_that_expires_while_the_form_is_open_is_refused(app, anon_client, monkeypatch):
    open_link(anon_client, make_link(app))
    token = csrf_token(anon_client, "/reset-password")
    later = auth.now_seconds() + 31 * 60
    monkeypatch.setattr("tradeledger.web.reset.now_seconds", lambda: later)
    response = anon_client.post(
        "/reset-password",
        data={"csrf_token": token, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert verify_password(TEST_PASSWORD, stored_hash(app))


@pytest.mark.parametrize(
    "token",
    ["", "tlr_short", "tl_" + "a" * 43, "tlr_" + "a" * 43, "../../etc/passwd", "tlr_" + "%00" * 15],
)
def test_wrong_tokens_all_get_the_same_answer(app, anon_client, token):
    make_link(app)
    response = anon_client.get("/reset-password", query_string={"token": token})
    assert response.status_code == 400
    assert password_resets.PREFIX + "a" not in response.get_data(as_text=True)
    assert "invalid, has already been used or has expired" in response.get_data(as_text=True)


def test_the_form_without_a_link_shows_the_same_message(anon_client):
    page = anon_client.get("/reset-password")
    assert "invalid, has already been used or has expired" in page.get_data(as_text=True)
    assert "new_password" not in page.get_data(as_text=True)


def test_a_link_for_one_account_cannot_change_another(app, anon_client):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    other_before = stored_hash(app, OTHER_EMAIL)
    open_link(anon_client, make_link(app, TEST_EMAIL))
    anon_client.post(
        "/reset-password",
        data={
            "csrf_token": csrf_token(anon_client, "/reset-password"),
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
            "email": OTHER_EMAIL,
            "user_id": "2",
        },
    )
    assert stored_hash(app, OTHER_EMAIL) == other_before
    assert verify_password(NEW_PASSWORD, stored_hash(app, TEST_EMAIL))


def test_the_form_needs_a_csrf_token(app, anon_client):
    open_link(anon_client, make_link(app))
    response = anon_client.post(
        "/reset-password", data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
    )
    assert response.status_code == 400
    assert verify_password(TEST_PASSWORD, stored_hash(app))


def test_repeated_bad_links_are_throttled(app, anon_client):
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_IP"]):
        anon_client.get("/reset-password", query_string={"token": "tlr_" + "x" * 43})
    blocked = anon_client.get("/reset-password", query_string={"token": "tlr_" + "y" * 43})
    assert blocked.status_code == 429


def test_the_token_never_reaches_the_logs(app, anon_client, caplog):
    caplog.set_level(logging.DEBUG)
    link = make_link(app)
    open_link(anon_client, link)
    submit(anon_client)
    open_link(app.test_client(), link)
    for record in caplog.records:
        assert token_of(link) not in record.getMessage()
        assert token_of(link) not in str(record.__dict__)


def test_reset_links_are_deleted_with_their_account(app):
    """ON DELETE CASCADE: a link never outlives its account."""
    make_link(app)
    with app.app_context():
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM users WHERE id = 1")  # the test account has no trades here
        assert get_user(conn, 1) is None
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


# ---------- set-password (operator chooses the password) ----------


def test_set_password_also_revokes_api_tokens(app):
    """Phase 8.5 fix: the Phase 8 guide said set-password ended sessions
    AND tokens, but the command only ended sessions. A forgotten or leaked
    password should cut off scripts as well."""
    token = issue_token(app)
    result = CliRunner().invoke(
        set_password_command,
        ["--email", TEST_EMAIL, "--password-stdin"],
        input=NEW_PASSWORD + "\n",
        obj=app_info(app),
    )
    assert result.exit_code == 0, result.output
    assert "1 API token(s) revoked" in result.output
    client = app.test_client()
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---------- Edge cases found by branch coverage (Phase 8.5) ----------


def test_a_link_for_an_account_deactivated_later_is_refused(app, anon_client):
    from tradeledger.repositories.users import set_active

    link = make_link(app)
    open_link(anon_client, link)
    with app.app_context():
        set_active(get_db(), 1, False)
    page = anon_client.get("/reset-password")
    assert page.status_code == 400 and "invalid, has already been used or has expired" in page.get_data(
        as_text=True
    )


def test_two_tabs_with_the_same_link_change_the_password_once(app):
    """Both tabs show the form; the first to submit wins, the second is
    refused and does not overwrite the first password."""
    link = make_link(app)
    first, second = app.test_client(), app.test_client()
    open_link(first, link)
    open_link(second, link)
    second_token = csrf_token(second, "/reset-password")
    assert submit(first).status_code == 303
    late = second.post(
        "/reset-password",
        data={
            "csrf_token": second_token,
            "new_password": "second tab password!",
            "confirm_password": "second tab password!",
        },
    )
    assert late.status_code == 400
    assert verify_password(NEW_PASSWORD, stored_hash(app))


def test_the_development_server_access_line_hides_the_reset_token(app, caplog):
    """Phase 8.5 regression, found by the production-settings HTTP check:
    `flask run` logs every request line with its query string through the
    "werkzeug" logger, which bypassed the redaction filter."""
    import logging as stdlib_logging

    token = "tlr_" + "Q" * 43
    caplog.set_level(stdlib_logging.INFO, logger="werkzeug")
    stdlib_logging.getLogger("werkzeug").info(
        '%s - - [16/Nov/2026 04:18:20] "%s" %s -',
        "127.0.0.1",
        f"GET /reset-password?token={token} HTTP/1.1",
        "303",
    )
    messages = [record.getMessage() for record in caplog.records if record.name == "werkzeug"]
    assert messages and all(token not in message for message in messages)
    assert "tlr_***" in messages[0] or "token=***" in messages[0]
```

### `tests/test_postgres_roles.py` (NEW)

**Why:** 17 PostgreSQL tests: the whole app runs as the restricted role; DDL refused; unsafe or administrative roles refused.  
**How to run or test:** `python -m pytest tests/test_postgres_roles.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: least privilege in PostgreSQL (the grant-app-role command).

Plain English: the web app should not be able to delete or reshape the
database's tables, only read and write rows. These tests create a real
PostgreSQL role, give it exactly what grant-app-role grants, run the
application AS that role (sign in, add an instrument and a trade, use the
dashboard, the API and the account page) and then check that every kind
of structural change is refused.
"""

import uuid
from urllib.parse import quote

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.conftest import PG_URL, ensure_user, issue_token, sign_in
from tests.web_helpers import closed_trade_form, post_form
from tradeledger import create_app
from tradeledger.cli import grant_app_role_command
from tradeledger.db import connect, get_db, init_schema

pytestmark = pytest.mark.postgres_only


@pytest.fixture
def app_role():
    """A fresh role with no rights at all; dropped afterwards."""
    name = "tlapp_" + uuid.uuid4().hex[:10]
    admin = connect(PG_URL)
    admin.execute(f"CREATE ROLE {name} NOLOGIN")
    yield name
    admin.execute(f"DROP OWNED BY {name}")
    admin.execute(f"DROP ROLE {name}")
    admin.close()


def as_role(url, role):
    """The same database and schema, but every statement runs as `role`."""
    base, _, options = url.partition("options=")
    extra = quote(" -crole=" + role)
    return f"{base}options={options}{extra}"


def owner_app(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


def grant(app, role):
    return CliRunner().invoke(
        grant_app_role_command, ["--role", role], obj=ScriptInfo(create_app=lambda: app)
    )


def test_the_app_works_with_only_the_granted_rights(db_path, app_role):
    owner = owner_app(db_path)
    result = grant(owner, app_role)
    assert result.exit_code == 0, result.output
    owner.extensions["tradeledger_db"].close()

    app = create_app("testing", overrides={"DATABASE": as_role(db_path, app_role)})
    with app.app_context():
        assert get_db().execute("SELECT current_user").fetchone()[0] == app_role
    client = app.test_client()
    sign_in(client)
    response = post_form(
        client,
        "/instruments",
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar",
            "asset_class": "FOREX",
            "price_increment": "0.0001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    assert response.status_code in (302, 303), response.get_data(as_text=True)[:500]
    response = post_form(client, "/trades/new", closed_trade_form())
    assert response.status_code in (302, 303), response.get_data(as_text=True)[:500]
    for path in ("/", "/trades", "/trades/1", "/analytics", "/account", "/instruments"):
        assert client.get(path).status_code == 200, path
    token = issue_token(app)
    api = client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"})
    assert api.status_code == 200 and api.get_json()["pagination"]["total"] == 1
    assert client.get("/health/ready").status_code == 200
    app.extensions["tradeledger_db"].close()


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE trades",
        "TRUNCATE trades",
        "ALTER TABLE trades ADD COLUMN extra TEXT",
        "CREATE TABLE stolen (x INTEGER)",
        "DELETE FROM schema_migrations",
        "INSERT INTO schema_migrations (version, name) VALUES (99, 'fake')",
        "CREATE INDEX idx_extra ON trades (notes)",
        "ALTER TABLE users DROP CONSTRAINT users_email_unique",
    ],
)
def test_structural_changes_are_refused(db_path, app_role, statement):
    owner = owner_app(db_path)
    assert grant(owner, app_role).exit_code == 0
    owner.extensions["tradeledger_db"].close()
    conn = connect(as_role(db_path, app_role))
    try:
        with pytest.raises(Exception) as caught:
            conn.execute(statement)
        assert "permission denied" in str(caught.value) or "must be owner" in str(caught.value)
    finally:
        conn.close()


def test_a_superuser_or_the_owner_is_refused_as_the_app_role(db_path):
    owner = owner_app(db_path)
    with owner.app_context():
        me = get_db().execute("SELECT current_user").fetchone()[0]
    result = grant(owner, me)
    assert result.exit_code != 0 and "separate role" in result.output
    owner.extensions["tradeledger_db"].close()


def test_an_administrative_role_is_refused(db_path):
    name = "tladmin_" + uuid.uuid4().hex[:10]
    admin = connect(PG_URL)
    admin.execute(f"CREATE ROLE {name} NOLOGIN CREATEROLE")
    try:
        owner = owner_app(db_path)
        result = grant(owner, name)
        assert result.exit_code != 0 and "administrative rights" in result.output
        owner.extensions["tradeledger_db"].close()
    finally:
        admin.execute(f"DROP ROLE {name}")
        admin.close()


@pytest.mark.parametrize("role", ["Robert'); DROP TABLE trades;--", "has space", "UPPER", "a" * 70, ""])
def test_unsafe_role_names_are_refused_before_any_sql(db_path, role):
    owner = owner_app(db_path)
    result = grant(owner, role)
    assert result.exit_code != 0
    with owner.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0  # table still there
    owner.extensions["tradeledger_db"].close()


def test_an_unknown_role_is_refused(db_path):
    owner = owner_app(db_path)
    result = grant(owner, "tlnosuchrole_" + uuid.uuid4().hex[:6])
    assert result.exit_code != 0 and "does not exist" in result.output
    owner.extensions["tradeledger_db"].close()
```

### `tests/test_two_step.py` (NEW)

**Why:** 48 tests: RFC 4226/6238 vectors, encryption binding, set-up, sign-in, replay, recovery codes, throttling, expiry, operator switch-off, key rotation fails closed.  
**How to run or test:** `python -m pytest tests/test_two_step.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: two-step verification (TOTP codes and recovery codes).

Plain English: with two-step switched on, signing in needs the password
AND a 6-digit code from an authenticator app. These tests check the code
maths against the official RFC test values, then the whole journey:
setting it up, signing in, wrong and replayed codes, recovery codes,
throttling, switching it off, and that the secret is stored encrypted.
"""

import hashlib
import re

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import csrf_token
from tradeledger import totp
from tradeledger.cli import disable_two_step_command
from tradeledger.db import get_db
from tradeledger.repositories.users import get_user, public_user
from tradeledger.web import auth, two_step

# ---------- The maths (RFC 6238 Appendix B, SHA-1 test vectors) ----------

RFC_KEY = b"12345678901234567890"


@pytest.mark.parametrize(
    "unix_time, expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_test_vectors(unix_time, expected):
    assert totp.hotp(RFC_KEY, unix_time // 30, digits=8, digest=hashlib.sha1) == expected


@pytest.mark.parametrize(
    "counter, expected",
    [(0, "755224"), (1, "287082"), (2, "359152"), (3, "969429"), (9, "520489")],
)
def test_rfc_4226_hotp_test_vectors(counter, expected):
    assert totp.hotp(RFC_KEY, counter) == expected


def test_codes_are_accepted_one_step_either_side_and_not_further():
    secret = totp.new_secret()
    now = 1_800_000_000
    for offset in (-30, 0, 30):
        assert totp.verify_code(secret, totp.code_at(secret, now + offset), now=now) is not None
    for offset in (-90, 90):
        code = totp.code_at(secret, now + offset)
        if all(code != totp.code_at(secret, now + d) for d in (-30, 0, 30)):
            assert totp.verify_code(secret, code, now=now) is None


def test_a_code_cannot_be_used_twice():
    secret = totp.new_secret()
    now = 1_800_000_000
    code = totp.code_at(secret, now)
    step = totp.verify_code(secret, code, now=now)
    assert step is not None
    assert totp.verify_code(secret, code, now=now, last_step=step) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5x", None])
def test_malformed_codes_are_refused(code):
    assert totp.verify_code(totp.new_secret(), code, now=1_800_000_000) is None


def test_spaces_in_a_typed_code_are_ignored():
    secret = totp.new_secret()
    code = totp.code_at(secret, 1_800_000_000)
    assert totp.verify_code(secret, f"{code[:3]} {code[3:]}", now=1_800_000_000) is not None


def test_secret_is_160_bits_of_base32():
    secret = totp.new_secret()
    assert len(secret) == 32 and set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert totp.new_secret() != secret


def test_encrypted_secret_is_bound_to_its_key_and_its_user():
    secret = totp.new_secret()
    sealed = totp.encrypt_secret(secret, secret_key="k" * 64, user_id=1)
    assert secret not in sealed and sealed.startswith("v1:")
    assert totp.decrypt_secret(sealed, secret_key="k" * 64, user_id=1) == secret
    assert totp.decrypt_secret(sealed, secret_key="j" * 64, user_id=1) is None  # rotated SECRET_KEY
    assert totp.decrypt_secret(sealed, secret_key="k" * 64, user_id=2) is None  # copied to another user
    assert totp.decrypt_secret("v1:garbage", secret_key="k" * 64, user_id=1) is None
    assert totp.decrypt_secret(None, secret_key="k" * 64, user_id=1) is None


def test_provisioning_uri_follows_the_key_uri_format():
    uri = totp.provisioning_uri("ABCDEFGH", account="trader@example.com", issuer="TradeLedger")
    assert uri == (
        "otpauth://totp/TradeLedger%3Atrader%40example.com?secret=ABCDEFGH&issuer=TradeLedger"
        "&algorithm=SHA1&digits=6&period=30"
    )


def test_recovery_codes_are_random_and_readable():
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        assert len(code) == 14 and code[4] == code[9] == "-"
        assert totp.looks_like_recovery_code(code) and totp.looks_like_recovery_code(
            code.upper().replace("-", " ")
        )
        assert not set(code) & set("0o1li")


# ---------- Helpers for the web journey ----------


def shown_codes(html):
    return re.findall(r"<li><code>([a-z0-9-]+)</code></li>", html)


def setup_secret(client):
    page = client.get("/account/two-step").get_data(as_text=True)
    assert "Set up two-step verification" in page
    with client.session_transaction() as session:
        return session[two_step.SETUP_KEY]


def enable(client, secret, password=TEST_PASSWORD, code=None):
    return client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account/two-step"),
            "current_password": password,
            "code": code if code is not None else totp.code_at(secret, auth.now_seconds()),
        },
    )


def enabled_account(app, client):
    """Switch two-step on for the default account; return (secret, codes)."""
    secret = setup_secret(client)
    response = enable(client, secret)
    assert response.status_code == 201
    html = response.get_data(as_text=True)
    codes = shown_codes(html)
    assert len(codes) == 10
    return secret, codes


def password_step(client, email=TEST_EMAIL, password=TEST_PASSWORD):
    return client.post(
        "/login", data={"csrf_token": csrf_token(client, "/login"), "email": email, "password": password}
    )


def code_step(client, code):
    return client.post(
        "/login/two-step", data={"csrf_token": csrf_token(client, "/login/two-step"), "code": code}
    )


@pytest.fixture
def clock(monkeypatch):
    """Move the clock in steps of 30 seconds so each test code is fresh."""
    state = {"now": auth.now_seconds()}

    def advance(seconds=30):
        state["now"] += seconds

    for target in ("tradeledger.web.auth.now_seconds", "tradeledger.web.two_step.now_seconds"):
        monkeypatch.setattr(target, lambda: state["now"])
    advance.now = lambda: state["now"]
    return advance


# ---------- Setting it up ----------


def test_setup_needs_the_password_and_a_working_code(app, client, clock):
    secret = setup_secret(client)
    wrong_password = enable(client, secret, password="not my password at all")
    assert wrong_password.status_code == 400 and "is incorrect" in wrong_password.get_data(as_text=True)
    wrong_code = enable(client, secret, code="000000")
    assert wrong_code.status_code == 400 and "Code is not right" in wrong_code.get_data(as_text=True)
    with app.app_context():
        assert get_user(get_db(), 1)["totp_enabled_at"] is None
    clock()
    assert enable(client, secret).status_code == 201
    with app.app_context():
        user = get_user(get_db(), 1)
    assert user["totp_enabled_at"] and secret not in user["totp_secret"]
    assert public_user(user)["two_step_enabled"] is True and "totp_secret" not in public_user(user)


def test_the_setup_page_shows_the_key_but_never_after_it_is_on(app, client, clock):
    secret = setup_secret(client)
    page = client.get("/account/two-step").get_data(as_text=True)
    assert totp.grouped(secret) in page and "otpauth://totp/" in page
    enable(client, secret)
    after = client.get("/account/two-step").get_data(as_text=True)
    assert "Two-step verification is on" in after and secret not in after and "otpauth://" not in after
    with client.session_transaction() as session:
        assert two_step.SETUP_KEY not in session


def test_recovery_codes_are_shown_once_and_stored_hashed(app, client, clock):
    _, codes = enabled_account(app, client)
    assert codes[0] not in client.get("/account/two-step").get_data(as_text=True)
    with app.app_context():
        rows = get_db().execute("SELECT code_hash FROM recovery_codes WHERE user_id = 1").fetchall()
    assert len(rows) == 10
    assert {r["code_hash"] for r in rows} == {totp.recovery_hash(c) for c in codes}
    assert not any(c in r["code_hash"] for c in codes for r in rows)


def test_account_page_shows_the_status(app, client, clock):
    assert "Set up two-step verification" in client.get("/account").get_data(as_text=True)
    enabled_account(app, client)
    assert "Manage two-step verification" in client.get("/account").get_data(as_text=True)


# ---------- Signing in ----------


def test_a_password_alone_no_longer_signs_in(app, client, clock):
    enabled_account(app, client)
    browser = app.test_client()
    response = password_step(browser)
    assert response.status_code == 303 and response.headers["Location"] == "/login/two-step"
    assert browser.get("/").status_code == 302  # not signed in yet
    with browser.session_transaction() as session:
        assert "sid" not in session and "uid" not in session


def test_the_right_code_signs_in_and_returns_to_the_page_asked_for(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    browser.post(
        "/login",
        data={
            "csrf_token": csrf_token(browser, "/login"),
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "next": "/trades?status=OPEN",
        },
    )
    clock()
    response = code_step(browser, totp.code_at(secret, clock.now()))
    assert response.status_code == 303 and response.headers["Location"] == "/trades?status=OPEN"
    assert browser.get("/").status_code == 200


def test_a_wrong_code_is_refused_and_counted(app, client, clock):
    enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    response = code_step(browser, "123456")
    assert response.status_code == 400 and "That code is not right" in response.get_data(as_text=True)
    assert browser.get("/").status_code == 302


def test_a_code_cannot_be_replayed_by_someone_else(app, client, clock):
    secret, _ = enabled_account(app, client)
    clock()
    code = totp.code_at(secret, clock.now())
    first, second = app.test_client(), app.test_client()
    password_step(first)
    password_step(second)
    assert code_step(first, code).status_code == 303
    assert code_step(second, code).status_code == 400  # the same code, seen over someone's shoulder


def test_the_code_used_to_switch_it_on_cannot_be_reused_to_sign_in(app, client, clock):
    secret = setup_secret(client)
    code = totp.code_at(secret, clock.now())
    enable(client, secret, code=code)
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, code).status_code == 400


def test_a_recovery_code_works_once(app, client, clock):
    _, codes = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    response = code_step(browser, codes[0].upper())
    assert response.status_code == 303
    assert browser.get("/").status_code == 200
    again = app.test_client()
    password_step(again)
    assert code_step(again, codes[0]).status_code == 400


def test_another_users_recovery_code_does_not_work(app, clock):
    with app.app_context():
        other = ensure_user(get_db(), OTHER_EMAIL)
    mine, theirs = app.test_client(), app.test_client()
    sign_in(mine, 1)
    sign_in(theirs, other)
    enabled_account(app, mine)
    secret = setup_secret(theirs)
    clock()
    response = enable(theirs, secret)
    their_codes = shown_codes(response.get_data(as_text=True))
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, their_codes[0]).status_code == 400


def test_the_second_step_expires_after_five_minutes(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    token = csrf_token(browser, "/login/two-step")  # the form was open...
    clock(301)  # ...for more than five minutes
    response = browser.post(
        "/login/two-step", data={"csrf_token": token, "code": totp.code_at(secret, clock.now())}
    )
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert browser.get("/").status_code == 302


def test_the_second_step_without_a_password_goes_back_to_sign_in(anon_client):
    response = anon_client.get("/login/two-step")
    assert response.status_code == 303 and response.headers["Location"] == "/login"


def test_a_password_change_elsewhere_cancels_a_pending_second_step(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    token = csrf_token(browser, "/login/two-step")
    from tradeledger.repositories.users import bump_session_version

    with app.app_context():
        bump_session_version(get_db(), 1)
    clock()
    response = browser.post(
        "/login/two-step", data={"csrf_token": token, "code": totp.code_at(secret, clock.now())}
    )
    assert response.headers["Location"] == "/login"


def test_wrong_codes_are_throttled(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_ACCOUNT"]):
        code_step(browser, "000000")
    clock()
    blocked = code_step(browser, totp.code_at(secret, clock.now()))
    assert blocked.status_code == 429  # even the right code waits


def test_the_second_step_needs_a_csrf_token(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    clock()
    response = browser.post("/login/two-step", data={"code": totp.code_at(secret, clock.now())})
    assert response.status_code == 400


# ---------- Switching it off ----------


def test_switching_off_needs_password_and_a_code(app, client, clock):
    secret, _ = enabled_account(app, client)

    def disable(password, code):
        return client.post(
            "/account/two-step/disable",
            data={
                "csrf_token": csrf_token(client, "/account/two-step"),
                "current_password": password,
                "code": code,
            },
        )

    assert disable("wrong password here", totp.code_at(secret, clock.now())).status_code == 400
    assert disable(TEST_PASSWORD, "000000").status_code == 400
    clock()
    assert disable(TEST_PASSWORD, totp.code_at(secret, clock.now())).status_code == 303
    with app.app_context():
        user = get_user(get_db(), 1)
        left = get_db().execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = 1").fetchone()[0]
    assert user["totp_enabled_at"] is None and user["totp_secret"] is None and left == 0
    browser = app.test_client()
    assert password_step(browser).headers["Location"] == "/"


def test_new_recovery_codes_cancel_the_old_ones(app, client, clock):
    _, old_codes = enabled_account(app, client)
    response = client.post(
        "/account/two-step/recovery-codes",
        data={"csrf_token": csrf_token(client, "/account/two-step"), "current_password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, old_codes[1]).status_code == 400


def test_the_operator_can_switch_it_off_for_a_lost_phone(app, client, clock):
    enabled_account(app, client)
    result = CliRunner().invoke(
        disable_two_step_command, ["--email", TEST_EMAIL], obj=ScriptInfo(create_app=lambda: app)
    )
    assert result.exit_code == 0 and "switched off" in result.output
    assert client.get("/").status_code == 302  # sessions ended
    assert password_step(app.test_client()).headers["Location"] == "/"


def test_a_rotated_secret_key_fails_closed(app, client, clock):
    """If SECRET_KEY changes, stored secrets cannot be read. Sign-in must
    then REFUSE codes (never skip the second step); recovery codes still work."""
    secret, codes = enabled_account(app, client)
    app.config["SECRET_KEY"] = "a-completely-different-secret-key-" + "x" * 30
    browser = app.test_client()
    password_step(browser)
    clock()
    assert code_step(browser, totp.code_at(secret, clock.now())).status_code == 400
    assert code_step(browser, codes[2]).status_code == 303


# ---------- Edge cases found by branch coverage (Phase 8.5) ----------


def test_enable_without_a_setup_key_or_twice_just_returns_to_the_page(app, client, clock):
    no_key = client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account"),
            "current_password": TEST_PASSWORD,
            "code": "123456",
        },
    )
    assert no_key.status_code == 303 and no_key.headers["Location"] == "/account/two-step"
    enabled_account(app, client)
    again = client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account"),
            "current_password": TEST_PASSWORD,
            "code": "123456",
        },
    )
    assert again.status_code == 303
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = 1").fetchone()[0] == 10


def test_disable_and_new_codes_are_404_when_two_step_is_off(client):
    token = csrf_token(client, "/account")
    for path in ("/account/two-step/disable", "/account/two-step/recovery-codes"):
        response = client.post(
            path, data={"csrf_token": token, "current_password": TEST_PASSWORD, "code": "1"}
        )
        assert response.status_code == 404, path


def test_new_recovery_codes_need_the_password(app, client, clock):
    enabled_account(app, client)
    response = client.post(
        "/account/two-step/recovery-codes",
        data={
            "csrf_token": csrf_token(client, "/account/two-step"),
            "current_password": "not the password here",
        },
    )
    assert response.status_code == 400 and "is incorrect" in response.get_data(as_text=True)


def test_password_guessing_on_the_two_step_page_is_throttled(app, client):
    setup_secret(client)
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_ACCOUNT"]):
        enable(client, "A" * 32, password="wrong password guess", code="000000")
    blocked = enable(client, "A" * 32, password=TEST_PASSWORD, code="000000")
    assert blocked.status_code == 400 and "Too many sign-in attempts" in blocked.get_data(as_text=True)
```

### `tests/test_users_repository.py` (NEW)

**Why:** 20 tests: email rules and look-ups with untrusted values (found by branch coverage).  
**How to run or test:** `python -m pytest tests/test_users_repository.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8.5: edge cases of the users repository found by branch coverage.

Plain English: small rules about email addresses and account look-ups
that the bigger journey tests never happened to reach. Each one matters
because sign-in and the two-step page pass untrusted values into these
functions.
"""

import pytest

from tradeledger.db import get_db
from tradeledger.repositories.users import (
    MAX_EMAIL_LENGTH,
    create_user,
    email_problem,
    get_user,
    get_user_by_email,
)
from tradeledger.validation import ValidationError


@pytest.mark.parametrize(
    "value, problem",
    [
        ("", "is required."),
        (None, "is required."),
        ("   ", "is required."),
        ("a" * MAX_EMAIL_LENGTH + "@example.com", f"must be {MAX_EMAIL_LENGTH} characters or fewer."),
        ("no-at-sign.example.com", "must look like name@example.com."),
        ("two@@example.com", "must look like name@example.com."),
        ("trader@example.com", None),
        ("  Trader@Example.COM ", None),
    ],
)
def test_email_rules(value, problem):
    assert email_problem(value) == problem


def test_create_user_refuses_an_invalid_email_before_touching_the_database(app):
    with app.app_context():
        conn = get_db()
        before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with pytest.raises(ValidationError) as caught:
            create_user(conn, "not an email", "$argon2id$v=19$m=1024,t=1,p=1$c2FsdA$aGFzaA")
        assert caught.value.errors == {"email": "must look like name@example.com."}
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before


@pytest.mark.parametrize("user_id", ["1", None, True, 1.0, [1], {"id": 1}])
def test_get_user_ignores_anything_that_is_not_an_integer_id(app, user_id):
    """A tampered cookie value (for example a string uid in the pending
    two-step note) must find nobody rather than raise or match row 1."""
    with app.app_context():
        assert get_user(get_db(), user_id) is None


@pytest.mark.parametrize("email", ["", None, "   "])
def test_get_user_by_email_with_no_email_finds_nobody(app, email):
    with app.app_context():
        assert get_user_by_email(get_db(), email) is None


def test_get_user_by_email_is_case_insensitive(app):
    with app.app_context():
        assert get_user_by_email(get_db(), "  TRADER@example.COM ")["id"] == 1


def test_a_tampered_pending_two_step_note_is_refused(anon_client):
    with anon_client.session_transaction() as session:
        session["pending_two_step"] = {"uid": "1", "sv": 1, "at": 2_000_000_000, "next": "/"}
    response = anon_client.get("/login/two-step")
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    with anon_client.session_transaction() as session:
        assert "pending_two_step" not in session
```

### `scripts/build_chartjs.mjs` (NEW)

**Why:** Reproducible build of Chart.js 4.5.1 from the official source tags (checks the expected commits).  
**How to run or test:** `bash scripts/build_chartjs.mjs`

```text
// Rebuild tradeledger/static/vendor/chartjs/chart.umd.min.js from the
// OFFICIAL Chart.js 4.5.1 and @kurkle/color 0.3.2 source tags (Phase 8.5).
//
// Why this exists: the published npm/CDN file could not be downloaded in the
// build environment, but the official Git repositories could be cloned. This
// script reproduces Chart.js's own UMD build (rollup.config.js in the
// Chart.js repository: input src/index.umd.ts, target ES2022, minified, MIT
// banner, global `Chart`) with esbuild instead of rollup + swc + terser.
// The result is genuine Chart.js 4.5.1 code, but it is NOT byte-identical to
// the npm file, so its SRI hash differs from the CDN's.
//
// Usage (needs Node 18+ and esbuild):
//   git clone --depth 1 --branch v4.5.1 https://github.com/chartjs/chart.js  /tmp/chart.js
//   git clone --depth 1 --branch v0.3.2 https://github.com/kurkle/color      /tmp/color
//   node scripts/build_chartjs.mjs /tmp/chart.js /tmp/color [path/to/esbuild/lib/main.js]
//   openssl dgst -sha384 -binary tradeledger/static/vendor/chartjs/chart.umd.min.js | base64
// Expected source commits: chart.js 9c5cf9fac7ec04a71b516e2aff3f7d76876be369,
// color eb0d1db26a35704553c93782bad8c3ac2d971597 (checked below).

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const EXPECTED = {
  chartjs: { version: '4.5.1', commit: '9c5cf9fac7ec04a71b516e2aff3f7d76876be369' },
  color: { version: '0.3.2', commit: 'eb0d1db26a35704553c93782bad8c3ac2d971597' },
};

const [chartDir, colorDir, esbuildPath = 'esbuild'] = process.argv.slice(2);
if (!chartDir || !colorDir) {
  console.error('usage: node scripts/build_chartjs.mjs <chart.js checkout> <color checkout> [esbuild module]');
  process.exit(2);
}

function check(dir, name) {
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const commit = execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  if (pkg.version !== EXPECTED[name].version || commit !== EXPECTED[name].commit) {
    console.error(`${name}: expected ${EXPECTED[name].version} @ ${EXPECTED[name].commit}, got ${pkg.version} @ ${commit}`);
    process.exit(1);
  }
}
check(chartDir, 'chartjs');
check(colorDir, 'color');

const esbuildModule = esbuildPath === 'esbuild' ? 'esbuild' : pathToFileURL(resolve(esbuildPath)).href;
const { build } = await import(esbuildModule);
const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const banner = `/*!
 * Chart.js v4.5.1
 * https://www.chartjs.org
 * (c) 2025 Chart.js Contributors
 * Released under the MIT License
 */
/*! @kurkle/color v0.3.2 | https://github.com/kurkle/color | (c) 2023 Jukka Kurkela | Released under the MIT License */`;

await build({
  entryPoints: [join(chartDir, 'src/index.umd.ts')],
  bundle: true,
  minify: true,
  format: 'iife',
  target: 'es2022',
  platform: 'browser',
  banner: { js: banner },
  footer: { js: 'if(typeof module!=="undefined"&&module.exports){module.exports=window.Chart;}' },
  alias: { '@kurkle/color': join(colorDir, 'src/index.esm.js') },
  legalComments: 'none',
  outfile: join(project, 'tradeledger/static/vendor/chartjs/chart.umd.min.js'),
  logLevel: 'warning',
});
console.log('built tradeledger/static/vendor/chartjs/chart.umd.min.js');
```

## 2. Modified files

### `tradeledger/__init__.py` (MODIFIED)

**Why:** Per-response Referrer-Policy override (no-referrer on the reset page).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import os
import secrets

from flask import Flask, g, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import (
    CONFIG_BY_NAME,
    MIN_PRODUCTION_SECRET_KEY_LENGTH,
    PLACEHOLDER_SECRET_KEYS,
    REMOVED_UNSAFE_SETTINGS,
)


def create_app(config_name=None, overrides=None):
    """Build and return a configured Flask application.

    config_name: "development", "testing" or "production". If omitted, it is
    read from the TRADELEDGER_CONFIG environment variable, defaulting to
    "development".

    overrides: optional dict of settings applied last. Tests use this to
    point the app at a temporary database, e.g. {"DATABASE": "/tmp/x.db"}.
    """
    if config_name is None:
        config_name = os.environ.get("TRADELEDGER_CONFIG", "development")

    if config_name not in CONFIG_BY_NAME:
        valid = ", ".join(sorted(CONFIG_BY_NAME))
        raise ValueError(f"Unknown config '{config_name}'. Choose one of: {valid}")

    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[config_name])
    if overrides:
        app.config.update(overrides)
    app.config["CONFIG_NAME"] = config_name

    _configure_logging(app)
    _ensure_secret_key(app)
    _refuse_unsafe_production_settings(app)
    _configure_proxy(app)
    _configure_database(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    from .db import database_label

    app.logger.info(
        "TradeLedger %s started",
        app.config["APP_VERSION"],
        extra={"event": "app.started", "config": config_name, "database": database_label(app)},
    )
    return app


def _configure_logging(app):
    """Structured, redacted logging (see observability.py)."""
    from .observability import configure_logging, register_request_hooks

    configure_logging(app)
    register_request_hooks(app)


def _ensure_secret_key(app):
    """Make sure a SECRET_KEY exists, or fail loudly where one is required.

    Where a key is required (production), it must also be strong: at least
    32 characters and not one of the published placeholder values. The key
    signs the session cookie that holds the signed-in user's id and the
    CSRF token, so a guessable key would let an attacker sign in as anyone.
    """
    key = app.config.get("SECRET_KEY")
    weak = key in PLACEHOLDER_SECRET_KEYS or len(key or "") < MIN_PRODUCTION_SECRET_KEY_LENGTH
    if key and app.config["REQUIRE_SECRET_KEY"] and weak:
        raise RuntimeError(
            "SECRET_KEY is too weak for this environment. Use at least "
            f"{MIN_PRODUCTION_SECRET_KEY_LENGTH} random characters, for example the output of: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if key:
        return

    if app.config["REQUIRE_SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is not set. Set the SECRET_KEY environment variable "
            "before running TradeLedger in this environment."
        )

    # Development only: a random key that changes on every restart.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY not set - using a temporary development key. "
        "Set SECRET_KEY in your .env file to silence this warning."
    )


def _refuse_unsafe_production_settings(app):
    """Phase 8: settings that must never reach a production server.

    * The Phase 7 switches that disabled authentication no longer exist.
      If one is still set, it is IGNORED and a warning is logged, so an old
      .env file cannot silently reopen the journal.
    * The Werkzeug debugger runs arbitrary Python from the browser; it must
      never be on in production, so the app refuses to start.
    """
    for name in REMOVED_UNSAFE_SETTINGS:
        if name in os.environ:
            app.logger.warning(
                "%s is no longer supported and is ignored: TradeLedger always requires sign-in.",
                name,
                extra={"event": "config.removed_setting"},
            )
    if app.config["CONFIG_NAME"] == "production" and (app.debug or os.environ.get("FLASK_DEBUG") == "1"):
        raise RuntimeError("Debug mode must not be enabled in production (it allows remote code execution).")


def _configure_proxy(app):
    """Trust X-Forwarded-For/-Proto from exactly PROXY_HOPS proxies.

    Behind an AWS Application Load Balancer set TRADELEDGER_PROXY_HOPS=1:
    request.remote_addr then becomes the real client address (used by the
    sign-in limits) and request.is_secure reflects the original HTTPS
    connection. With 0 (the default) these headers are ignored, because
    anyone can send them. X-Forwarded-Host is never trusted: the Host
    header is checked against TRUSTED_HOSTS instead.
    """
    hops = app.config["PROXY_HOPS"]
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=0, x_port=0, x_prefix=0)


def _configure_database(app):
    """Choose SQLite or PostgreSQL (see db.make_source) and register hooks."""
    uses_postgres = bool(app.config.get("DATABASE_URL") or app.config.get("DB_HOST"))
    if not uses_postgres and not app.config.get("DATABASE"):
        if app.config["TESTING"]:
            raise RuntimeError(
                "The testing configuration needs an explicit DATABASE path "
                "so tests never touch development data."
            )
        os.makedirs(app.instance_path, exist_ok=True)
        app.config["DATABASE"] = os.path.join(app.instance_path, "tradeledger.db")

    from . import cli, db

    db.init_app(app)
    cli.init_app(app)


def _register_blueprints(app):
    """Attach groups of routes to the app: the website and the JSON API."""
    from .api import api_bp
    from .web import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)


def _register_error_handlers(app):
    """Friendly errors instead of Flask's defaults or stack traces.

    Website URLs get HTML pages (Phase 1). URLs under /api/ get the JSON
    error format, including for errors raised before any API code runs:
    unknown URL (404), wrong method (405) and oversized body (413).
    """
    from .api.errors import HTTP_ERRORS, error_response, is_api_request

    @app.errorhandler(404)
    def not_found(error):
        if is_api_request():
            return error_response(404, *HTTP_ERRORS[404])
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        if is_api_request():
            response, status = error_response(405, *HTTP_ERRORS[405])
            response.headers["Allow"] = ", ".join(error.valid_methods or [])
            return response, status
        return error  # website: Flask's standard 405 response

    @app.errorhandler(413)
    def too_large(error):
        if is_api_request():
            return error_response(413, *HTTP_ERRORS[413])
        return error

    @app.errorhandler(500)
    def internal_error(error):
        # The full traceback goes to the server log only, never to the client.
        app.logger.exception("Unhandled server error")
        if is_api_request():
            return error_response(500, *HTTP_ERRORS[500])
        return render_template("errors/500.html"), 500

    @app.errorhandler(HTTPException)
    def other_http_error(error):
        if is_api_request():
            return error_response(error.code, "http_error", error.name)
        return error


# Content Security Policy: the browser may only load scripts, styles and
# images from this site, and forms may only post back to it. Templates
# therefore contain no inline <script> or style="" attributes, which means
# even an HTML-injection bug could not run injected JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)


def build_csp(extra_script_sources=()):
    """The policy above, optionally allowing extra script URLs.

    Phase 6: only the /analytics response passes an extra source, the
    exact Chart.js file URL (a full path, not the whole CDN host), so no
    other page can load anything from the CDN, and 'unsafe-inline' and
    'unsafe-eval' are never allowed anywhere.
    """
    if not extra_script_sources:
        return CONTENT_SECURITY_POLICY
    scripts = " ".join(("'self'", *extra_script_sources))
    return CONTENT_SECURITY_POLICY.replace("script-src 'self'", f"script-src {scripts}", 1)


def _register_security_headers(app):
    """Add protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Pages that handle secrets in their own URL (the reset link) send no
        # Referer at all (Phase 8.5).
        response.headers["Referrer-Policy"] = g.get("referrer_policy", "strict-origin-when-cross-origin")
        response.headers["Content-Security-Policy"] = build_csp(g.get("csp_script_sources", ()))
        # Pages show trading records, so browsers and proxies must not store
        # them (Phase 7). Static CSS/JS are not HTML and stay cacheable; the
        # JSON API already sets its own no-store header.
        if response.mimetype == "text/html" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        if app.config["HSTS_ENABLED"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
```

### `tradeledger/cli.py` (MODIFIED)

**Why:** create-reset-link, disable-two-step, grant-app-role; set-password also revokes API tokens.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Command-line commands, run through Flask:

flask --app tradeledger init-db      create tables (keeps existing data)
flask --app tradeledger reset-db     delete ALL data and recreate tables (refused in production)
flask --app tradeledger seed-db --email E      add synthetic sample data to an account
flask --app tradeledger db-info      show tables, row counts and schema version
flask --app tradeledger analytics --email E    run the analytical SQL queries for an account
flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
flask --app tradeledger report --email E       performance report (Phase 3 engine)
flask --app tradeledger migrate-db   back up, then apply pending schema migrations
flask --app tradeledger openapi      write the OpenAPI specification to a file

Accounts (Phase 8)
flask --app tradeledger create-user --email E          asks for the password twice
flask --app tradeledger set-password --email E         reset a forgotten password
flask --app tradeledger deactivate-user --email E      block sign-in and API tokens
flask --app tradeledger activate-user --email E
flask --app tradeledger list-users
flask --app tradeledger claim-legacy-data --email E [--apply]
                                     give pre-Phase 8 records to an account

Account security (Phase 8.5)
flask --app tradeledger create-reset-link --email E [--base-url URL]
                                     one-time 30-minute link to choose a new password
flask --app tradeledger disable-two-step --email E   lost phone and recovery codes
flask --app tradeledger grant-app-role --role NAME    PostgreSQL: row access only for the web app

Passwords are typed at a hidden prompt (or piped with --password-stdin for
automation). They are never accepted as a command-line argument, because
arguments are visible to other users of the machine and kept in shell history.
"""

import json
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics, migrations
from .api.openapi import build_spec
from .db import POSTGRESQL, database_label, drop_all, get_db, init_schema, list_tables, schema_version
from .legacy import claim_legacy_data, legacy_counts
from .passwords import hash_password, parameters_from_config, password_problem
from .performance import build_report
from .performance.formatting import (
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from .repositories import DuplicateError, api_tokens, password_resets, two_step
from .repositories.instruments import get_instrument_by_symbol
from .repositories.users import (
    bump_session_version,
    count_users,
    create_user,
    email_problem,
    get_user_by_email,
    normalise_email,
    set_active,
    set_password_hash,
)
from .seed import seed_sample_data
from .validation import ValidationError


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths, strict=True)))


def user_from_email(conn, email):
    user = get_user_by_email(conn, email)
    if user is None:
        raise click.ClickException(f"No account with email {normalise_email(email)}.")
    return user


def read_new_password(from_stdin, email):
    """A new password from a hidden prompt (typed twice) or from standard input."""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    problem = password_problem(password, email=email)
    if problem:
        raise click.ClickException(f"Password {problem}")
    return password


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create any missing tables. Existing data is kept."""
    init_schema(get_db())
    click.echo(f"Database ready: {database_label(current_app)}")


@click.command("reset-db")
@with_appcontext
@click.confirmation_option(prompt="This deletes ALL accounts, trades and instruments. Continue?")
def reset_db_command():
    """Drop every table and recreate the empty schema."""
    if current_app.config["CONFIG_NAME"] == "production":
        raise click.ClickException("reset-db is refused in production. Restore from a backup instead.")
    conn = get_db()
    drop_all(conn)
    init_schema(conn)
    click.echo("Database reset. All tables are empty.")


@click.command("seed-db")
@click.option("--email", required=True, help="The account that receives the sample data.")
@with_appcontext
def seed_db_command(email):
    """Insert synthetic sample data into one account. Running it twice adds nothing new."""
    conn = get_db()
    init_schema(conn)
    user = user_from_email(conn, email)
    inserted = seed_sample_data(conn, user_id=user["id"])
    click.echo(
        f"Sample data: {inserted['instruments']} instrument(s) and {inserted['trades']} trade(s) inserted "
        f"for {user['email']}."
    )


@click.command("db-info")
@with_appcontext
def db_info_command():
    """Show which database is used, its schema version and row counts."""
    conn = get_db()
    click.echo(f"Database:       {database_label(current_app)}")
    click.echo(f"Schema version: {schema_version(conn)}")
    tables = list_tables(conn)
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from the database catalogue, not from user input, so it
    # is safe to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}  # noqa: S608 - name from the catalogue
        for name in tables
    ]
    print_table(rows)
    if "users" in tables and "trades" in tables:
        legacy = legacy_counts(conn)
        if legacy["instruments"] or legacy["trades"]:
            click.echo(
                f"Unassigned legacy records: {legacy['instruments']} instrument(s), {legacy['trades']} trade(s). "
                "Assign them with: flask --app tradeledger claim-legacy-data --email you@example.com"
            )


@click.command("analytics")
@click.option("--email", required=True, help="Whose records to analyse.")
@click.argument("name", required=False, type=click.Choice(sorted(analytics.ALL_QUERIES)))
@with_appcontext
def analytics_command(email, name):
    """Run one analytical query by NAME, or all of them, for one account."""
    conn = get_db()
    user = user_from_email(conn, email)
    names = [name] if name else list(analytics.ALL_QUERIES)
    for query_name in names:
        click.echo(f"\n== {query_name} ==")
        print_table(analytics.run_query(conn, query_name, user_id=user["id"]))


@click.command("run-sql")
@with_appcontext
@click.argument("sql")
def run_sql_command(sql):
    """Run your own SQL against a READ-ONLY connection (it cannot change data).

    This reads EVERY account's data: it is an operator tool for whoever
    already has the database credentials, not a feature for users."""
    if current_app.extensions["tradeledger_db"].dialect == POSTGRESQL:
        conn = get_db()
        try:
            with conn:
                conn.execute("SET TRANSACTION READ ONLY")
                rows = [dict(row) for row in conn.execute(sql).fetchall()]
        except Exception as exc:
            raise click.ClickException(f"SQL error: {exc}") from exc
        print_table(rows)
        return
    path = Path(current_app.config["DATABASE"]).resolve()
    if not path.exists():
        raise click.ClickException("Database file not found. Run init-db first.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    finally:
        conn.close()
    print_table(rows)


def summary_row(label, summary):
    """One display row for a group summary (rounded for display only)."""
    return {
        "group": label,
        "trades": summary.trades,
        "W/L/BE": f"{summary.wins}/{summary.losses}/{summary.breakevens}",
        "win rate": format_rate(summary.win_rate),
        "total R": format_r(summary.total_r),
        "expectancy": format_r(summary.expectancy_r),
        "PF (R)": format_ratio(summary.profit_factor_r),
        "max DD": format_r_amount(summary.max_drawdown_r.amount),
    }


def print_report(report):
    o = report.overall
    click.echo("== Overall (closed trades) ==")
    lines = [
        ("Closed trades", o.trades),
        ("Open trades (not in results)", report.open_trades),
        ("Wins / losses / breakevens", f"{o.wins} / {o.losses} / {o.breakevens}"),
        ("Win rate / loss rate", f"{format_rate(o.win_rate)} / {format_rate(o.loss_rate)}"),
        ("Trades with realised R", o.r_trades),
        ("Total R (cumulative)", format_r(o.total_r)),
        ("Average R", format_r(o.average_r)),
        ("Average winning R", format_r(o.average_win_r)),
        ("Average losing R", format_r(o.average_loss_r)),
        ("Expectancy per trade", format_r(o.expectancy_r)),
        ("Profit factor (R)", format_ratio(o.profit_factor_r)),
        (
            "Average planned R:R",
            f"{format_ratio(o.average_planned_rr)} ({o.planned_rr_trades} trades with a target)",
        ),
        ("Longest win / loss streak", f"{o.longest_win_streak} / {o.longest_loss_streak}"),
        ("Max drawdown (R)", format_r_amount(o.max_drawdown_r.amount)),
    ]
    if o.best_trade_r:
        lines.append(
            (
                "Best trade (R)",
                f"#{o.best_trade_r.trade_id} {o.best_trade_r.symbol} {format_r(o.best_trade_r.value)}",
            )
        )
        lines.append(
            (
                "Worst trade (R)",
                f"#{o.worst_trade_r.trade_id} {o.worst_trade_r.symbol} {format_r(o.worst_trade_r.value)}",
            )
        )
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        click.echo(f"{label.ljust(width)}  {value}")

    click.echo("\n== Money, per quote currency (never added together) ==")
    print_table(
        [
            {
                "currency": m.currency,
                "trades": m.trades,
                "gross P&L": format_money(m.gross_pnl, m.currency),
                "fees": format_money(m.fees, m.currency),
                "net P&L": format_money(m.net_pnl, m.currency),
                "winners": format_money(m.winning_pnl, m.currency),
                "losers": format_money(m.losing_pnl, m.currency),
                "PF": format_ratio(m.profit_factor),
                "max DD": format_money(m.max_drawdown.amount, m.currency),
            }
            for m in o.money.values()
        ]
    )

    for title, groups in (
        ("By instrument", report.by_instrument),
        ("By strategy", report.by_strategy),
        ("By session", report.by_session),
        ("By day of week (UTC)", report.by_weekday),
        ("By month", report.by_month),
    ):
        click.echo(f"\n== {title} ==")
        print_table([summary_row(label, s) for label, s in groups.items()])

    click.echo("\n== Cumulative R (chart data; not account equity) ==")
    print_table(
        [
            {
                "trade": p.trade_id,
                "date": p.trade_date[:10],
                "symbol": p.symbol,
                "R": format_r(p.realised_r),
                "cumulative": format_r(p.cumulative_r),
            }
            for p in report.cumulative_r
        ]
    )

    for title, items in (
        ("Excluded trades", report.excluded),
        ("Closed trades without a realised R", report.r_unavailable),
    ):
        if items:
            click.echo(f"\n== {title} ==")
            for item in items:
                click.echo(f"#{item.trade_id}: {item.reason}")

    if report.unknown_close_time_trade_ids:
        ids = ", ".join(f"#{i}" for i in report.unknown_close_time_trade_ids)
        click.echo("\n== Closed trades with no recorded exit time (ordered by entry time) ==")
        click.echo(ids)


@click.command("report")
@click.option("--email", required=True, help="Whose trades to report on.")
@click.option("--instrument", help="Only this symbol, e.g. XAUUSD.")
@click.option("--start", help="First date to include, YYYY-MM-DD (UTC).")
@click.option("--end", help="Last date to include, YYYY-MM-DD (UTC).")
@click.option("--json", "as_json", is_flag=True, help="Exact JSON output instead of a rounded table.")
@with_appcontext
def report_command(email, instrument, start, end, as_json):
    """Performance report calculated by the Python analytics engine."""
    conn = get_db()
    user_id = user_from_email(conn, email)["id"]
    instrument_id = None
    if instrument:
        found = get_instrument_by_symbol(conn, instrument, user_id=user_id)
        if found is None:
            raise click.ClickException(f"No instrument with symbol '{instrument}'.")
        instrument_id = found["id"]
    try:
        report = build_report(conn, user_id=user_id, instrument_id=instrument_id, start=start, end=end)
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_jsonable(report), indent=2))
    else:
        print_report(report)


@click.command("migrate-db")
@with_appcontext
@click.option("--no-backup", is_flag=True, help="SQLite: skip the safety copy (not recommended).")
@click.option(
    "--backed-up", is_flag=True, help="PostgreSQL: confirm a backup (pg_dump or RDS snapshot) exists."
)
def migrate_db_command(no_backup, backed_up):
    """Back up the database, then apply any pending schema migrations."""
    conn = get_db()
    before = schema_version(conn)
    if conn.dialect == POSTGRESQL:
        if migrations.pending_versions(conn) and before >= 1 and not backed_up:
            raise click.ClickException(
                "Pending PostgreSQL migrations. Take a backup first (pg_dump or an RDS snapshot, see "
                "docs/PHASE_8_DATABASE_MIGRATION.md), then run again with --backed-up."
            )
        applied = init_schema(conn)
        click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
        return
    if not migrations.pending_versions(conn) and before >= 1:
        click.echo(f"Already at schema version {before}. Nothing to do.")
        return
    if not no_backup and before >= 1:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = Path(current_app.config["DATABASE"])
        backup_path = path.with_name(f"{path.name}.v{before}-{stamp}.bak")
        migrations.backup(conn, str(backup_path))
        click.echo(f"Backup written: {backup_path}")
    applied = init_schema(conn)
    click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
    legacy = legacy_counts(conn)
    if legacy["instruments"] or legacy["trades"]:
        click.echo(
            f"{legacy['instruments']} instrument(s) and {legacy['trades']} trade(s) are unassigned and hidden "
            "until you run: flask --app tradeledger claim-legacy-data --email you@example.com"
        )


# ---------- Accounts (Phase 8) ----------


@click.command("create-user")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def create_user_command(email, password_stdin):
    """Create an account. Works even when self-registration is disabled."""
    conn = get_db()
    problem = email_problem(email)
    if problem:
        raise click.ClickException(f"Email address {problem}")
    password = read_new_password(password_stdin, normalise_email(email))
    try:
        user_id = create_user(
            conn, email, hash_password(password, parameters_from_config(current_app.config))
        )
    except DuplicateError as exc:
        raise click.ClickException(str(exc)) from exc
    current_app.logger.info(
        "account created by operator", extra={"event": "auth.user.created", "user_id": user_id}
    )
    click.echo(f"Account created for {normalise_email(email)} (user id {user_id}).")


@click.command("set-password")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def set_password_command(email, password_stdin):
    """Set a new password (e.g. a forgotten one) and sign the account out everywhere."""
    conn = get_db()
    user = user_from_email(conn, email)
    password = read_new_password(password_stdin, user["email"])
    hashed = hash_password(password, parameters_from_config(current_app.config))
    set_password_hash(conn, user["id"], hashed, end_other_sessions=True)
    revoked = api_tokens.revoke_all(conn, user_id=user["id"])  # Phase 8.5: tokens too
    current_app.logger.info(
        "password set by operator",
        extra={"event": "auth.password.reset", "user_id": user["id"], "tokens_revoked": revoked},
    )
    click.echo(
        f"Password updated for {user['email']}. Existing sessions have been ended"
        f" and {revoked} API token(s) revoked."
    )


@click.command("create-reset-link")
@click.option("--email", required=True)
@click.option("--base-url", help="Address people use to reach TradeLedger (default: TRADELEDGER_PUBLIC_URL).")
@with_appcontext
def create_reset_link_command(email, base_url):
    """Print a one-time link (30 minutes) the account holder uses to choose a
    new password. Hand it over by a channel you trust; you never see their
    password. Creating a link cancels any earlier one."""
    conn = get_db()
    user = user_from_email(conn, email)
    if not user["is_active"]:
        raise click.ClickException(f"{user['email']} is deactivated. Run activate-user first.")
    base = (base_url or current_app.config["PUBLIC_URL"]).rstrip("/")
    if not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise click.ClickException("--base-url must start with https:// (or http://localhost for testing).")
    lifetime = current_app.config["PASSWORD_RESET_LIFETIME_SECONDS"]
    token = password_resets.create_link(
        conn, user_id=user["id"], now=int(time.time()), lifetime_seconds=lifetime
    )
    current_app.logger.info(
        "password reset link created", extra={"event": "auth.reset.created", "user_id": user["id"]}
    )
    click.echo(f"One-time link for {user['email']} (valid for {lifetime // 60} minutes, works once):")
    click.echo(f"{base}/reset-password?token={token}")


@click.command("disable-two-step")
@click.option("--email", required=True)
@with_appcontext
def disable_two_step_command(email):
    """Switch off two-step verification for someone who lost their phone AND
    their recovery codes. Check who is asking first. Also ends their sessions."""
    conn = get_db()
    user = user_from_email(conn, email)
    if not user["totp_enabled_at"]:
        click.echo(f"Two-step verification is already off for {user['email']}.")
        return
    two_step.disable(conn, user_id=user["id"])
    bump_session_version(conn, user["id"])
    current_app.logger.info(
        "two-step disabled by operator",
        extra={"event": "auth.two_step.disabled_by_operator", "user_id": user["id"]},
    )
    click.echo(f"Two-step verification switched off for {user['email']}; their sessions have been ended.")


APP_TABLES = (
    "users",
    "api_tokens",
    "login_attempts",
    "user_sessions",
    "password_resets",
    "recovery_codes",
    "instruments",
    "trades",
)


@click.command("grant-app-role")
@click.option("--role", required=True, help="An existing PostgreSQL role the web app will log in as.")
@with_appcontext
def grant_app_role_command(role):
    """PostgreSQL only: give an existing role exactly what the running app
    needs (read and write rows, use the id sequences) and nothing else (no
    CREATE, DROP, ALTER or TRUNCATE). Run it as the schema owner after every
    migration. See docs/TRADELEDGER_FINAL_SECURITY_REVIEW.md (least privilege)."""
    conn = get_db()
    if conn.dialect != POSTGRESQL:
        raise click.ClickException("grant-app-role is for PostgreSQL; SQLite has no database users.")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise click.ClickException("Role names may use lower-case letters, digits and underscores only.")
    found = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolname = current_user AS is_me FROM pg_roles WHERE rolname = ?",
        (role,),
    ).fetchone()
    if found is None:
        raise click.ClickException(f"Role {role} does not exist. Create it first (see the security review).")
    if found["is_me"]:
        raise click.ClickException("Use a separate role for the app, not the one that owns the tables.")
    if found["rolsuper"] or found["rolcreaterole"] or found["rolcreatedb"]:
        raise click.ClickException(f"Role {role} has administrative rights; the app role must not.")
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    quoted_role, quoted_schema = f'"{role}"', '"' + schema.replace('"', '""') + '"'
    tables = ", ".join(f"{quoted_schema}.{name}" for name in APP_TABLES)
    statements = [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
        f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM {quoted_role}",
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {quoted_role}",
        f"GRANT SELECT ON {quoted_schema}.schema_migrations TO {quoted_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}",
    ]
    with conn:
        for statement in statements:
            conn.execute(statement)  # identifiers validated above; no user text reaches SQL unquoted
    click.echo(
        f"Role {role}: SELECT/INSERT/UPDATE/DELETE on {len(APP_TABLES)} tables, read-only schema_migrations,"
    )
    click.echo("sequence use for new ids; no CREATE, DROP, ALTER or TRUNCATE. Re-run after each migration.")


@click.command("deactivate-user")
@click.option("--email", required=True)
@with_appcontext
def deactivate_user_command(email):
    """Block sign-in and API access for an account (its data is kept)."""
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], False)
    click.echo(f"{user['email']} deactivated: sessions ended, sign-in and API tokens refused.")


@click.command("activate-user")
@click.option("--email", required=True)
@with_appcontext
def activate_user_command(email):
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], True)
    click.echo(f"{user['email']} can sign in again.")


@click.command("list-users")
@with_appcontext
def list_users_command():
    """Accounts with their status (never password hashes)."""
    rows = (
        get_db()
        .execute("SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY id")
        .fetchall()
    )
    print_table([{**dict(row), "is_active": bool(row["is_active"])} for row in rows])
    click.echo(f"{count_users(get_db())} account(s).")


@click.command("claim-legacy-data")
@click.option("--email", required=True, help="The account that becomes the owner.")
@click.option("--apply", "apply_changes", is_flag=True, help="Make the change (default: dry run).")
@with_appcontext
def claim_legacy_data_command(email, apply_changes):
    """Give unassigned pre-Phase 8 records to one account (dry run unless --apply)."""
    conn = get_db()
    user = user_from_email(conn, email)
    summary = claim_legacy_data(conn, user_id=user["id"], apply=apply_changes)
    click.echo(f"Unassigned records: {summary['instruments']} instrument(s), {summary['trades']} trade(s).")
    for conflict in summary["conflicts"]:
        click.echo(f"CONFLICT: {conflict}")
    if summary["conflicts"]:
        raise click.ClickException("Nothing was changed. Resolve the conflicts above first.")
    if summary["applied"]:
        current_app.logger.info(
            "legacy records assigned",
            extra={"event": "legacy.claimed", "user_id": user["id"], "trades": summary["trades"]},
        )
        click.echo(f"Done: every unassigned record now belongs to {user['email']}.")
    elif apply_changes:
        click.echo("Nothing to assign.")
    else:
        click.echo(f"Dry run: nothing changed. Re-run with --apply to assign them to {user['email']}.")


@click.command("openapi")
@with_appcontext
@click.option(
    "--output", default="docs/openapi.json", show_default=True, help="File to write the specification to."
)
def openapi_command(output):
    """Write the OpenAPI 3.1 specification (the same one served by the API)."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(build_spec(), indent=2) + "\n", encoding="utf-8")
    click.echo(f"OpenAPI specification written to {output}")


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
        report_command,
        migrate_db_command,
        openapi_command,
        create_user_command,
        set_password_command,
        deactivate_user_command,
        activate_user_command,
        list_users_command,
        claim_legacy_data_command,
        create_reset_link_command,
        disable_two_step_command,
        grant_app_role_command,
    ):
        app.cli.add_command(command)
```

### `tradeledger/config.py` (MODIFIED)

**Why:** Version 0.8.5; self-hosted Chart.js with its SRI by default; TOTP issuer, reset lifetime, PUBLIC_URL.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in:

  development  your own machine (flask run); SQLite by default
  testing      pytest; every test passes its own temporary database
  production   Gunicorn in Docker or on AWS; PostgreSQL expected

Phase 8 removed the Phase 7 switches TRADELEDGER_ENABLE_UNAUTHENTICATED_API
and TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL. Every page with trading
data now requires a signed-in user and the API requires a token, in EVERY
environment, so there is no setting that turns authentication off. If one
of those old variables is still set, the app logs a warning and ignores it.
"""

import os
from datetime import timedelta


def env_flag(name, default=False):
    """True when the variable is "1", False when it is "0", else `default`."""
    value = os.environ.get(name)
    if value == "1":
        return True
    if value == "0":
        return False
    return default


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_secret(name):
    """A secret from NAME, or from the file named by NAME_FILE.

    Docker and Compose "secrets" are mounted as files (e.g.
    /run/secrets/secret_key), which keeps them out of `docker inspect` and
    process listings. The file wins when both are set. Surrounding
    whitespace (a trailing newline) is removed.
    """
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return os.environ.get(name)


def env_hosts(name, default):
    """A comma-separated list of host names from the environment.

    Not set -> `default`.  "*" -> None, meaning "accept any Host header"
    (Flask's own default). Ports are ignored when hosts are compared.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.strip() == "*":
        return None
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


# Values that must never be accepted as a production SECRET_KEY: the example
# in .env.example, the Compose example and the public testing key below.
PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "replace-me-with-a-long-random-string",
        "test-secret-key-not-for-real-use",
        "change-me-to-64-random-hex-characters-before-starting-the-stack",
    }
)
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32

# Phase 7 settings that turned authentication off. Ignored since Phase 8.
REMOVED_UNSAFE_SETTINGS = (
    "TRADELEDGER_ENABLE_UNAUTHENTICATED_API",
    "TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL",
)


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.8.5"

    DEBUG = False
    TESTING = False

    # Read from the environment. Never hard-code a real key in this file.
    # It signs the session cookie, so anyone who knows it could forge a login.
    SECRET_KEY = env_secret("SECRET_KEY")

    # When True, the app refuses to start without a strong SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # ---------- Database ----------
    # SQLite file path. If neither this nor PostgreSQL is configured,
    # create_app() uses instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")
    # PostgreSQL, as one URL (Compose, CI)...
    DATABASE_URL = os.environ.get("DATABASE_URL")
    # ...or as separate settings (AWS ECS: DB_PASSWORD comes from Secrets Manager).
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = os.environ.get("DB_PORT", "5432")
    DB_NAME = os.environ.get("DB_NAME", "tradeledger")
    DB_USER = os.environ.get("DB_USER", "tradeledger")
    DB_PASSWORD = env_secret("DB_PASSWORD")
    # "require" encrypts the connection; "verify-full" also checks the server
    # certificate and needs the RDS certificate bundle (docs/PHASE_8_AWS_DEPLOYMENT.md).
    DB_SSLMODE = os.environ.get("DB_SSLMODE", "prefer")
    # Connections per process. Gunicorn workers x containers x max must stay
    # under the database's max_connections (db.t4g.micro: about 80).
    DB_POOL_MIN_SIZE = env_int("TRADELEDGER_DB_POOL_MIN", 1)
    DB_POOL_MAX_SIZE = env_int("TRADELEDGER_DB_POOL_MAX", 5)
    DB_POOL_TIMEOUT = env_int("TRADELEDGER_DB_POOL_TIMEOUT", 10)

    # Largest request body accepted, in bytes. Flask answers 413 above this.
    # A trade is well under 2 KB, so 64 KB is generous.
    MAX_CONTENT_LENGTH = 64 * 1024

    # ---------- Authentication (Phase 8) ----------
    # Argon2id cost (see passwords.py): OWASP's recommended minimum.
    PASSWORD_HASH_MEMORY_KIB = 19456
    PASSWORD_HASH_ITERATIONS = 2
    PASSWORD_HASH_LANES = 1

    # Self-service registration. On for local use. Production turns it OFF
    # by default: without email verification anyone could create accounts.
    REGISTRATION_ENABLED = env_flag("TRADELEDGER_REGISTRATION_ENABLED", True)
    REGISTRATIONS_PER_IP_PER_HOUR = 5

    # A session ends 8 hours after sign-in, or after 2 hours without activity,
    # whichever comes first. Both are checked on the server for every request.
    SESSION_MAX_AGE_SECONDS = env_int("TRADELEDGER_SESSION_MAX_AGE", 8 * 3600)
    SESSION_IDLE_TIMEOUT_SECONDS = env_int("TRADELEDGER_SESSION_IDLE_TIMEOUT", 2 * 3600)
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=SESSION_MAX_AGE_SECONDS)

    # Failed sign-ins allowed per email address and per IP address in the window.
    LOGIN_MAX_FAILURES_PER_ACCOUNT = 5
    LOGIN_MAX_FAILURES_PER_IP = 20
    LOGIN_WINDOW_SECONDS = 15 * 60

    # Personal API tokens (Authorization: Bearer tl_...).
    API_ENABLED = env_flag("TRADELEDGER_API_ENABLED", True)
    API_MAX_PER_PAGE = 100
    # Phase 8.5: two-step verification and one-time password-reset links.
    TOTP_ISSUER = os.environ.get("TRADELEDGER_TOTP_ISSUER", "TradeLedger")
    PASSWORD_RESET_LIFETIME_SECONDS = 30 * 60
    # The address people use to reach TradeLedger, for links printed by the
    # create-reset-link command (e.g. https://journal.example.com).
    PUBLIC_URL = os.environ.get("TRADELEDGER_PUBLIC_URL", "http://127.0.0.1:5000")

    API_TOKEN_DEFAULT_LIFETIME_DAYS = 90
    API_TOKEN_MAX_ACTIVE = 10

    JOURNAL_PER_PAGE = 20

    # Session cookie: holds the signed-in user's id and the CSRF token, and
    # is signed with SECRET_KEY (see web/auth.py). HttpOnly stops JavaScript
    # reading it; SameSite=Lax stops browsers sending it with cross-site
    # form posts, a second layer of CSRF defence.
    SESSION_COOKIE_NAME = "tradeledger_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Host header allow-list (Phase 7). Requests addressed to any other host
    # name get 400 before any route runs, which defeats DNS rebinding.
    #   TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1,192.168.1.20
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", ["localhost", "127.0.0.1"])

    # Number of reverse proxies in front of the app that set X-Forwarded-*
    # headers (an AWS Application Load Balancer is 1). 0 means trust none:
    # a client could otherwise fake its IP address (defeating the login
    # limits) or claim the request used HTTPS.
    PROXY_HOPS = env_int("TRADELEDGER_PROXY_HOPS", 0)

    # Strict-Transport-Security (HSTS) tells browsers to use HTTPS only.
    HSTS_ENABLED = False

    # "text" for people, "json" for log collectors such as CloudWatch.
    LOG_FORMAT = os.environ.get("TRADELEDGER_LOG_FORMAT", "text")
    # One line per request (method, path without query string, status, time).
    ACCESS_LOG = env_flag("TRADELEDGER_ACCESS_LOG", False)

    # Chart.js for the /analytics dashboard (Phase 6). Since Phase 8.5 the
    # library is SELF-HOSTED by default: the file ships in
    # static/vendor/chartjs/ (built from the official v4.5.1 source tag; see
    # its BUILD.md) and the browser checks it against CHART_JS_BUNDLED_INTEGRITY.
    # No third-party host is contacted and the CSP stays "script-src 'self'".
    # TRADELEDGER_CHART_JS_URL can still point at a CDN (then set
    # TRADELEDGER_CHART_JS_INTEGRITY for that file too).
    CHART_JS_VERSION = "4.5.1"
    CHART_JS_BUNDLED_URL = "/static/vendor/chartjs/chart.umd.min.js"
    CHART_JS_BUNDLED_INTEGRITY = "sha384-rBeQHxx+fD4yTWwymMLsGUggrWAk1ssEcKZuxBdYt+43t//g2T2q49rj78sPe7D3"
    CHART_JS_CDN_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    CHART_JS_URL = os.environ.get("TRADELEDGER_CHART_JS_URL") or CHART_JS_BUNDLED_URL
    CHART_JS_INTEGRITY = os.environ.get("TRADELEDGER_CHART_JS_INTEGRITY") or (
        CHART_JS_BUNDLED_INTEGRITY if CHART_JS_URL == CHART_JS_BUNDLED_URL else None
    )


class DevelopmentConfig(Config):
    """Local development on your own machine."""

    # In development a missing key is tolerated: the app generates a
    # temporary one and logs a warning (see create_app).
    REQUIRE_SECRET_KEY = False


class TestingConfig(Config):
    """Used by pytest. Fixed values make tests repeatable."""

    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-real-use"  # noqa: S105 - fixed, public, tests only
    REQUIRE_SECRET_KEY = False

    # Deliberately no default: tests must pass their own temporary database,
    # so a test can never touch your development data.
    DATABASE = None
    DATABASE_URL = None
    DB_HOST = None

    # Cheap hashing keeps 1,000+ tests fast. tests/test_passwords.py checks
    # that the production settings are the OWASP values and hashes with them.
    PASSWORD_HASH_MEMORY_KIB = 1024
    PASSWORD_HASH_ITERATIONS = 1

    REGISTRATION_ENABLED = True

    # The Phase 6/7 page and browser tests exercise the CDN mode (and replace
    # the CDN file with a stub or block it to test the offline fallback).
    # The self-hosted default is tested separately: tests/test_chartjs_bundle.py
    # and the real-library tests in tests/test_chartjs_real.py.
    CHART_JS_URL = Config.CHART_JS_CDN_URL
    CHART_JS_INTEGRITY = None


class ProductionConfig(Config):
    """Deployed environments (Docker Compose, AWS ECS)."""

    REQUIRE_SECRET_KEY = True

    REGISTRATION_ENABLED = env_flag("TRADELEDGER_REGISTRATION_ENABLED", False)

    # Only send the session cookie over HTTPS. The "__Host-" prefix makes
    # browsers refuse the cookie unless it is Secure, has Path=/ and no
    # Domain, so a sibling sub-domain cannot overwrite it.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "__Host-tradeledger_session"
    PREFERRED_URL_SCHEME = "https"

    # Production must say which host names it serves (for example
    # "journal.example.com"). Unset means no Host check, which is only safe
    # behind a load balancer that already enforces it (see SECURITY.md).
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", None)

    HSTS_ENABLED = True
    LOG_FORMAT = os.environ.get("TRADELEDGER_LOG_FORMAT", "json")
    ACCESS_LOG = env_flag("TRADELEDGER_ACCESS_LOG", True)


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

### `tradeledger/db.py` (MODIFIED)

**Why:** Only TEST_ONLY modules may replace psycopg; production refuses them; drop_all knows the new tables.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Database connections for SQLite and PostgreSQL (Phase 8).

Plain English
-------------
TradeLedger can store its data in two kinds of database:

* SQLite: one file on your computer. Nothing to install; ideal for local
  development and the automated tests.
* PostgreSQL: a database server. Used by the Docker Compose stack and by
  the AWS design (Amazon RDS), where several application containers share
  one database.

The rest of the application should not care which one it is talking to.
This module gives both the same small interface, so the repositories write
their SQL once.

Technical summary
-----------------
connect(target)   open a connection: a file path means SQLite, a
                  "postgresql://..." URL means PostgreSQL. Scripts and
                  tests use this.
get_db()          inside a Flask request or CLI command: one connection per
                  request, stored on flask.g and released when the request
                  ends. PostgreSQL connections come from a pool.

A Connection offers:
    execute(sql, params)   SQL with "?" placeholders on BOTH databases
                           (converted to psycopg's "%s" for PostgreSQL)
    executescript(sql)     several statements, no parameters
    with conn: ...         a transaction: commit on success, roll back on
                           any exception; nested blocks use SAVEPOINTs
    numeric(text)          the value to bind for an exact decimal column
    dialect                "sqlite" or "postgresql"

Rows behave like sqlite3.Row: row["symbol"], row[0] and dict(row) all work.
Integrity failures from either database are raised as db.IntegrityError
with a `kind` ("unique", "foreign_key", "check", "not_null") and, where
the database reports one, the constraint name.

Why not SQLAlchemy? It is the usual, excellent choice for this job and is
the documented upgrade path (docs/PHASE_8_DATABASE_MIGRATION.md). It is not
installable in the environment this phase was built in, and TradeLedger's
existing repository layer already keeps every SQL statement in a handful of
functions, so a small adapter (this file) was enough. It deliberately does
NOT build SQL, map objects or manage schemas: it only smooths over the
differences between the two drivers.

Connections run in autocommit mode at the driver level, and transactions
are explicit (BEGIN ... COMMIT). That gives SQLite and PostgreSQL the same
behaviour: a statement outside `with conn:` is committed immediately, and
everything inside `with conn:` is all-or-nothing.
"""

import atexit
import functools
import logging
import sqlite3
import threading
from decimal import Decimal
from importlib import resources
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, g

from . import migrations

SQLITE = "sqlite"
POSTGRESQL = "postgresql"

logger = logging.getLogger("tradeledger.db")

# Optional PostgreSQL driver override. Production uses psycopg 3. The test
# suite may register a different module with the same small interface
# (see tests/pg_libpq_bridge.py); application code never does.
_pg_driver_override = None


def register_postgres_driver(module):
    """Use `module` instead of psycopg for PostgreSQL (tests only).

    Phase 8.5 guards, so the test bridge can never serve real users:
      * only a module that declares TEST_ONLY = True is accepted;
      * the production configuration refuses to start while one is
        registered (PostgresSource below), pool or no pool.
    """
    global _pg_driver_override
    if module is not None and getattr(module, "TEST_ONLY", False) is not True:
        raise RuntimeError("only a module marked TEST_ONLY can replace psycopg")
    _pg_driver_override = module


def postgres_test_driver_active():
    """True while a test-only PostgreSQL driver replaces psycopg."""
    return _pg_driver_override is not None


def _postgres_driver():
    if _pg_driver_override is not None:
        return _pg_driver_override
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the installed packages
        raise RuntimeError(
            "PostgreSQL support needs the psycopg package: pip install -r requirements.txt"
        ) from exc
    return psycopg


# ---------- Errors ----------


class DatabaseError(Exception):
    """Base class for errors raised by this module."""


class DataError(DatabaseError):
    """PostgreSQL rejected a value's format, e.g. "abc" for a NUMERIC column.
    (SQLite stores anything and relies on CHECK constraints instead.)"""


class IntegrityError(DatabaseError):
    """A constraint stopped a write. Nothing from that statement was kept.

    kind        "unique", "foreign_key", "check", "not_null" or None
    constraint  the constraint (or trigger rule) name when known
    """

    def __init__(self, message, *, kind=None, constraint=None):
        super().__init__(message)
        self.kind = kind
        self.constraint = constraint


_PG_SQLSTATE_KINDS = {"23505": "unique", "23503": "foreign_key", "23514": "check", "23502": "not_null"}


def _sqlite_integrity(exc):
    message = str(exc)
    if message.startswith("UNIQUE constraint failed"):
        return IntegrityError(message, kind="unique", constraint=message.partition(": ")[2] or None)
    if message.startswith("FOREIGN KEY constraint failed"):
        return IntegrityError(message, kind="foreign_key")
    if message.startswith("CHECK constraint failed"):
        return IntegrityError(message, kind="check", constraint=message.partition(": ")[2] or None)
    if message.startswith("NOT NULL constraint failed"):
        return IntegrityError(message, kind="not_null", constraint=message.partition(": ")[2] or None)
    # Trigger rules raise "rule_name: explanation" (see migrations.py).
    head, sep, _ = message.partition(":")
    if sep and head.replace("_", "").isalnum():
        return IntegrityError(message, kind="check", constraint=head)
    return IntegrityError(message)


def _postgres_integrity(exc):
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    kind = _PG_SQLSTATE_KINDS.get(getattr(exc, "sqlstate", None))
    return IntegrityError(str(exc).strip(), kind=kind, constraint=constraint)


# ---------- Rows and cursors ----------


class Row(dict):
    """A result row: row["name"], row[0] and dict(row) all work."""

    __slots__ = ("_values",)

    def __init__(self, names, values):
        super().__init__(zip(names, values, strict=True))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    """The result of Connection.execute()."""

    def __init__(self, raw):
        self._raw = raw
        description = raw.description
        self._names = [getattr(col, "name", None) or col[0] for col in description] if description else None

    @property
    def rowcount(self):
        return self._raw.rowcount

    def _row(self, values):
        return None if values is None else Row(self._names, values)

    def fetchone(self):
        if self._names is None:
            return None
        return self._row(self._raw.fetchone())

    def fetchall(self):
        if self._names is None:
            return []
        return [Row(self._names, values) for values in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


# ---------- Connections ----------


class Connection:
    """Common behaviour; see SQLiteConnection and PostgresConnection."""

    dialect = None

    def __init__(self, raw):
        self.raw = raw
        self._depth = 0

    # Subclasses provide _run(sql, params) -> driver cursor, _run_script(sql)
    # for several statements, _control(sql) for one transaction-control
    # statement, _integrity(exc) and _integrity_errors (the driver's class).

    _data_errors = ()

    def execute(self, sql, params=()):
        if isinstance(params, dict):
            raise TypeError("TradeLedger SQL uses positional '?' placeholders, not named ones")
        try:
            return Cursor(self._run(sql, tuple(params)))
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc
        except self._data_errors as exc:
            raise DataError(str(exc).strip()) from exc

    def executescript(self, sql):
        try:
            self._run_script(sql)
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc

    def numeric(self, text):
        """The value to bind for an exact decimal column (see subclasses)."""
        return text

    @property
    def in_transaction(self):
        return self._depth > 0

    # Transactions. `with conn:` mirrors sqlite3's behaviour (commit or roll
    # back), and works the same way on PostgreSQL. A nested `with conn:`
    # becomes a SAVEPOINT, so an inner failure that the caller handles does
    # not abort the outer transaction.
    def __enter__(self):
        if self._depth == 0:
            self._control("BEGIN")
        else:
            self._control(f"SAVEPOINT tl_sp_{self._depth}")
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth == 0:
            self._control("ROLLBACK" if exc_type else "COMMIT")
        else:
            name = f"tl_sp_{self._depth}"
            if exc_type:
                self._control(f"ROLLBACK TO SAVEPOINT {name}")
            self._control(f"RELEASE SAVEPOINT {name}")
        return False

    def commit(self):
        """Statements outside `with conn:` are already committed. Kept so
        older code that calls commit() keeps working."""
        return None

    def rollback(self):
        if self._depth:
            self._depth = 0
            self._control("ROLLBACK")

    def close(self):
        self.raw.close()


class SQLiteConnection(Connection):
    dialect = SQLITE
    _integrity_errors = sqlite3.IntegrityError

    def _run(self, sql, params):
        return self.raw.execute(sql, params)

    def _run_script(self, sql):
        if self._depth:
            # sqlite3's executescript() would silently COMMIT the open transaction.
            raise DatabaseError("executescript() cannot run inside a transaction on SQLite")
        self.raw.executescript(sql)

    def _control(self, sql):
        self.raw.execute(sql)

    def _integrity(self, exc):
        return _sqlite_integrity(exc)

    def rollback(self):
        self._depth = 0
        if self.raw.in_transaction:
            self.raw.execute("ROLLBACK")

    def backup(self, target):
        self.raw.backup(target)


class PostgresConnection(Connection):
    dialect = POSTGRESQL

    def __init__(self, raw, driver):
        super().__init__(raw)
        self._integrity_errors = driver.IntegrityError
        self._data_errors = driver.DataError

    def _run(self, sql, params):
        cursor = self.raw.cursor()
        if params:
            cursor.execute(to_pyformat(sql), params)
        else:
            cursor.execute(sql)
        return cursor

    def _run_script(self, sql):
        self.raw.cursor().execute(sql)

    def _control(self, sql):
        self.raw.cursor().execute(sql)

    def _integrity(self, exc):
        return _postgres_integrity(exc)

    def numeric(self, text):
        """PostgreSQL columns are NUMERIC: bind a Decimal, which the driver
        sends as an exact numeric (never a float)."""
        return None if text is None else Decimal(text)


@functools.lru_cache(maxsize=512)
def to_pyformat(sql):
    """Convert "?" placeholders to psycopg's "%s".

    "?" inside a quoted SQL literal is left alone, and every literal "%" is
    doubled, because psycopg treats "%" as the start of a placeholder.
    """
    out, in_quote = [], False
    for char in sql:
        if char == "'":
            in_quote = not in_quote
            out.append(char)
        elif char == "%":
            out.append("%%")
        elif char == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


def is_postgres_url(target):
    return isinstance(target, str) and target.startswith(("postgresql://", "postgres://"))


def connect(target):
    """Open a connection to a SQLite file path or a PostgreSQL URL."""
    if is_postgres_url(target):
        driver = _postgres_driver()
        return PostgresConnection(driver.connect(target, autocommit=True), driver)
    return connect_sqlite(target)


def connect_sqlite(path):
    # isolation_level=None: autocommit at the driver level; Connection adds
    # explicit transactions. check_same_thread=False is safe because one
    # connection is only ever used by one request at a time.
    raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    # SQLite does NOT enforce foreign keys unless you switch them on, per connection.
    raw.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5 s for another writer instead of failing at once.
    raw.execute("PRAGMA busy_timeout = 5000")
    return SQLiteConnection(raw)


def redact_url(url):
    """postgresql://app:secret@db:5432/x -> postgresql://app:***@db:5432/x"""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ---------- Per-application connection source ----------


class SQLiteSource:
    dialect = SQLITE

    def __init__(self, path):
        self.path = path

    def acquire(self):
        return connect_sqlite(self.path)

    def release(self, conn):
        conn.rollback()
        conn.close()

    def describe(self):
        return f"sqlite:{self.path}"

    def close(self):
        return None


class PostgresSource:
    """A connection pool shared by the requests of one process.

    Gunicorn runs several worker processes; each gets its own pool, opened
    on first use (after the fork), so sockets are never shared between
    processes. Connections are checked when borrowed, so a database restart
    does not leave broken connections in the pool.
    """

    dialect = POSTGRESQL

    def __init__(self, conninfo, *, min_size, max_size, timeout, require_pool, label=None):
        self._conninfo = conninfo
        self._label = label
        self._min_size, self._max_size, self._timeout = min_size, max_size, timeout
        self._require_pool = require_pool
        self._pool = None
        self._lock = threading.Lock()
        if (
            require_pool
            and _pg_driver_override is not None
            and not getattr(_pg_driver_override, "ALLOW_IN_PRODUCTION_CONFIG_TESTS", False)
        ):
            # require_pool is set by the production configuration. The only
            # exception is the test suite checking production SETTINGS on
            # PostgreSQL: tests/conftest.py sets the flag on the bridge it
            # registers. A deployment never imports tests/ (it is not in the
            # Docker image), so it cannot reach this exception.
            raise RuntimeError(
                "A test-only PostgreSQL driver is registered; production must use psycopg. Refusing to start."
            )
        self._driver = _postgres_driver()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                except ImportError:
                    # A registered test driver (tests/pg_libpq_bridge.py) has no pool
                    # package. Production reaches this point with a test driver
                    # only in the test suite's production-settings tests (see
                    # __init__); a real deployment without psycopg_pool stops here.
                    if self._require_pool and _pg_driver_override is None:
                        raise RuntimeError(
                            "psycopg_pool is required for PostgreSQL in this environment"
                        ) from None
                    logger.warning("psycopg_pool is not installed: one connection per request")
                    self._pool = _UnpooledConnections(self._driver, self._conninfo)
                else:
                    self._pool = ConnectionPool(
                        self._conninfo,
                        kwargs={"autocommit": True},
                        min_size=self._min_size,
                        max_size=self._max_size,
                        timeout=self._timeout,
                        check=ConnectionPool.check_connection,
                        open=True,
                        name="tradeledger",
                    )
                    # Phase 8.5 final audit: close the pool when the process
                    # exits. Without this, psycopg_pool's four background
                    # threads each wait 5 seconds at interpreter shutdown, so
                    # every Gunicorn worker took about 20 seconds to stop
                    # (found only once the genuine psycopg_pool was tested).
                    atexit.register(self._close_quietly)
            return self._pool

    def _close_quietly(self):
        try:
            self.close()
        except Exception:
            logger.debug("closing the connection pool at exit failed", exc_info=True)

    def acquire(self):
        return PostgresConnection(self._get_pool().getconn(), self._driver)

    def release(self, conn):
        try:
            conn.rollback()
        finally:
            self._get_pool().putconn(conn.raw)

    def describe(self):
        if self._label:
            return self._label
        return redact_url(self._conninfo) if is_postgres_url(self._conninfo) else "postgresql"

    def close(self):
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()


class _UnpooledConnections:
    """Fallback used only when psycopg_pool is missing outside production."""

    def __init__(self, driver, conninfo):
        self._driver, self._conninfo = driver, conninfo

    def getconn(self):
        return self._driver.connect(self._conninfo, autocommit=True)

    def putconn(self, raw):
        raw.close()

    def close(self):
        return None


def build_conninfo(host, port, dbname, user, password, sslmode):
    """A libpq keyword/value connection string with every value quoted."""

    def quote(value):
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    fields = {"host": host, "port": port, "dbname": dbname, "user": user, "password": password}
    fields["sslmode"] = sslmode or "prefer"
    fields["application_name"] = "tradeledger"
    return " ".join(f"{key}={quote(value)}" for key, value in fields.items() if value not in (None, ""))


def make_source(app):
    """Choose the database from the configuration.

    DATABASE_URL  postgresql://user:password@host:5432/name  (Compose, CI)
    DB_HOST etc.  separate settings (AWS: ECS injects DB_PASSWORD from
                  Secrets Manager, so no URL containing it is ever built
                  in a task definition or logged)
    otherwise     the SQLite file in DATABASE
    """
    config = app.config
    pool = {
        "min_size": config["DB_POOL_MIN_SIZE"],
        "max_size": config["DB_POOL_MAX_SIZE"],
        "timeout": config["DB_POOL_TIMEOUT"],
        "require_pool": config["CONFIG_NAME"] == "production",
    }
    url = config.get("DATABASE_URL")
    if not url and is_postgres_url(config.get("DATABASE")):
        url = config["DATABASE"]  # tests pass a PostgreSQL URL where a file path would go
    if url:
        if not is_postgres_url(url):
            raise RuntimeError("DATABASE_URL must start with postgresql://")
        return PostgresSource(url, **pool)
    if config.get("DB_HOST"):
        conninfo = build_conninfo(
            config["DB_HOST"],
            config.get("DB_PORT"),
            config.get("DB_NAME"),
            config.get("DB_USER"),
            config.get("DB_PASSWORD"),
            config.get("DB_SSLMODE"),
        )
        label = (
            f"postgresql://{config.get('DB_USER')}@{config['DB_HOST']}:"
            f"{config.get('DB_PORT') or 5432}/{config.get('DB_NAME')}"
        )
        return PostgresSource(conninfo, label=label, **pool)
    return SQLiteSource(config["DATABASE"])


def source():
    return current_app.extensions["tradeledger_db"]


def get_db():
    """Return this request's connection, opening (or borrowing) it on first use."""
    if "db" not in g:
        g.db = source().acquire()
    return g.db


def close_db(exception=None):
    """Release the request's connection, if one was opened."""
    conn = g.pop("db", None)
    if conn is not None:
        source().release(conn)


# ---------- Schema ----------


def read_schema(dialect=SQLITE):
    """Return the baseline schema file shipped inside the package."""
    name = "schema.sql" if dialect == SQLITE else "schema_postgres.sql"
    return resources.files("tradeledger").joinpath(name).read_text(encoding="utf-8")


def init_schema(conn):
    """Bring the database up to the latest schema. Never deletes data.

    SQLite: run the version 1 baseline (schema.sql, CREATE ... IF NOT
    EXISTS), then every pending numbered migration (migrations.py).
    PostgreSQL: create the current schema from schema_postgres.sql on an
    empty database, then apply any later PostgreSQL migrations.

    Returns the list of migration versions applied (often empty).
    """
    if conn.dialect == POSTGRESQL:
        return migrations.migrate_postgres(conn, read_schema(POSTGRESQL))
    conn.executescript(read_schema())
    if migrations.current_version(conn) == 0:
        conn.execute("PRAGMA user_version = 1")
    return migrations.migrate(conn)


def drop_all(conn):
    """Delete every table and reset the schema version to 0.
    Used only by the reset-db command, which production refuses."""
    if conn.dialect == POSTGRESQL:
        conn.executescript(
            "DROP TABLE IF EXISTS trades, instruments, api_tokens, login_attempts, user_sessions, "
            "password_resets, recovery_codes, users, schema_migrations CASCADE"
        )
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS instruments;
        DROP TABLE IF EXISTS api_tokens;
        DROP TABLE IF EXISTS login_attempts;
        DROP TABLE IF EXISTS user_sessions;
        DROP TABLE IF EXISTS password_resets;
        DROP TABLE IF EXISTS recovery_codes;
        DROP TABLE IF EXISTS users;
        PRAGMA foreign_keys = ON;
        PRAGMA user_version = 0;
        """
    )


def schema_version(conn):
    return migrations.current_version(conn)


def list_tables(conn):
    if conn.dialect == POSTGRESQL:
        sql = (
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    return [row["name"] for row in conn.execute(sql)]


def ping(conn):
    """True when the database answers a trivial query (readiness check)."""
    return conn.execute("SELECT 1").fetchone()[0] == 1


def init_app(app):
    """Create the connection source and register the release hook."""
    app.extensions["tradeledger_db"] = make_source(app)
    app.teardown_appcontext(close_db)


def database_label(app):
    """A description that is safe to log (never contains a password)."""
    return app.extensions["tradeledger_db"].describe()
```

### `tradeledger/migrations.py` (MODIFIED)

**Why:** Migration 4 for SQLite and PostgreSQL (sessions, reset links, recovery codes, TOTP columns).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Numbered, forward-only database migrations.

How it works (SQLite)
---------------------
SQLite stores a whole number in the database file header, read and written
with PRAGMA user_version. TradeLedger uses it as the schema version:

    0  empty file, nothing created yet
    1  baseline tables from schema.sql (Phases 2 and 3)
    2  trades.closed_at column + its index and integrity triggers (Phase 4)
    3  user accounts and record ownership (Phase 8)
    4  per-device sessions, password-reset links, two-step verification
       (Phase 8.5)

migrate(conn) applies every migration newer than the file's version, in
order, each inside its own transaction together with the version bump. If a
migration fails, it is rolled back and the version is unchanged, so the
database is never left half-migrated. Running migrate() again is harmless.
The migrate-db command copies the database file before migrating.

How it works (PostgreSQL)
-------------------------
PostgreSQL support starts in Phase 8, so a PostgreSQL database has no
history to replay: schema_postgres.sql creates the version 3 structure
directly and records it in the schema_migrations table. Later versions
(4, 5, ...) must be added to BOTH dictionaries below, and the tests check
that the two engines end on the same version. Migrations take an advisory
lock, so two containers starting at the same moment cannot both migrate.

Migration 2: closed_at
----------------------
Adds an optional exit timestamp. Existing rows get NULL: we do NOT invent
exit times for historical trades. The cross-column rules are triggers,
because SQLite's ALTER TABLE cannot add table CHECK constraints.

Migration 3: users and ownership (Phase 8)
------------------------------------------
* New tables: users, api_tokens, login_attempts.
* instruments and trades gain user_id. A symbol is now unique PER USER,
  not across the whole database, so two users can both record EURUSD.
* trades has a two-column foreign key (instrument_id, user_id) ->
  instruments (id, user_id): the database itself refuses a trade that
  points at another user's instrument.
* SQLite cannot change a UNIQUE rule or add a foreign key to an existing
  table, so both tables are REBUILT: create the new table, copy every row
  unchanged (same ids, text, timestamps), drop the old table, rename.
  Foreign-key enforcement is switched off for the rebuild and
  PRAGMA foreign_key_check must report nothing before the commit.
* Existing rows get user_id NULL: "unassigned legacy data". Nobody can
  see unassigned records. The owner of the data assigns them, deliberately,
  with:  flask --app tradeledger claim-legacy-data --email you@example.com
  (a dry run unless --apply is given). Ownership is never guessed, and the
  first person to register on a server does not receive old data.

Migration 4: account security (Phase 8.5)
-----------------------------------------
Only NEW tables and NEW nullable columns, so no table is rebuilt and no
existing row changes (the same structure on both engines):
* user_sessions    one row per signed-in browser. The cookie holds a random
                   session key; the table holds only its SHA-256. Signing
                   out, "sign out this device" and password changes revoke
                   rows, so a copied cookie stops working on the server.
                   Times are whole seconds since 1970 (UTC).
* password_resets  one-time links an operator issues with the CLI
                   (create-reset-link). SHA-256 only; valid for 30 minutes.
* recovery_codes   single-use codes for two-step verification, SHA-256 only.
* users.totp_*     the two-step secret (AES-GCM encrypted, key derived from
                   SECRET_KEY), when it was switched on, and the last time
                   step accepted (so a code cannot be replayed).
"""

import sqlite3

TIMESTAMP_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
NOW_SQLITE = "(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"


def _decimal_check(column, *, positive=True, nullable=False):
    """The Phase 2 rule for an exact decimal stored as TEXT."""
    rule = f"{column} NOT GLOB '*[^0-9.]*' AND {column} GLOB '*[0-9]*'"
    if positive:
        rule += f" AND CAST({column} AS REAL) > 0"
    if nullable:
        return f"CHECK ({column} IS NULL OR ({rule}))"
    return f"CHECK ({rule})"


_CLOSED_AT_TRIGGERS = """
        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_insert
        BEFORE INSERT ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;

        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_update
        BEFORE UPDATE ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;
"""

_TRADE_COLUMNS = (
    "id, instrument_id, trade_date, direction, entry_price, stop_loss, take_profit, exit_price, "
    "position_size, fees, status, strategy, session, notes, created_at, updated_at, closed_at"
)

MIGRATIONS = {
    2: f"""
        ALTER TABLE trades ADD COLUMN closed_at TEXT
            CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}');

        CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades (closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,
    3: f"""
        CREATE TABLE users (
            id              INTEGER PRIMARY KEY,
            email           TEXT    NOT NULL UNIQUE
                                    CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            password_hash   TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
            is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            last_login_at   TEXT
        );

        CREATE TABLE api_tokens (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name         TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            token_hash   TEXT    NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            token_prefix TEXT    NOT NULL,
            created_at   TEXT    NOT NULL,
            expires_at   TEXT    NOT NULL,
            last_used_at TEXT,
            revoked_at   TEXT
        );
        CREATE INDEX idx_api_tokens_user ON api_tokens (user_id);

        CREATE TABLE login_attempts (
            id           INTEGER PRIMARY KEY,
            scope        TEXT NOT NULL,
            key_digest   TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX idx_login_attempts_lookup ON login_attempts (scope, key_digest, attempted_at);

        CREATE TABLE instruments_v3 (
            id                  INTEGER PRIMARY KEY,
            user_id             INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            symbol              TEXT    NOT NULL
                                        CHECK (symbol = upper(symbol)
                                               AND length(symbol) BETWEEN 1 AND 20),
            name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            asset_class         TEXT    NOT NULL
                                        CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
            price_increment     TEXT    NOT NULL {_decimal_check("price_increment")},
            contract_multiplier TEXT    NOT NULL {_decimal_check("contract_multiplier")},
            quote_currency      TEXT    NOT NULL
                                        CHECK (length(quote_currency) = 3
                                               AND quote_currency = upper(quote_currency)),
            created_at          TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            CONSTRAINT instruments_user_symbol_unique UNIQUE (user_id, symbol),
            CONSTRAINT instruments_id_user_unique UNIQUE (id, user_id)
        );

        CREATE TABLE trades_v3 (
            id              INTEGER PRIMARY KEY,
            user_id         INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            instrument_id   INTEGER NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT,
            trade_date      TEXT    NOT NULL CHECK (trade_date GLOB '{TIMESTAMP_GLOB}'),
            direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
            entry_price     TEXT    NOT NULL {_decimal_check("entry_price")},
            stop_loss       TEXT    NOT NULL {_decimal_check("stop_loss")},
            take_profit     TEXT    {_decimal_check("take_profit", nullable=True)},
            exit_price      TEXT    {_decimal_check("exit_price", nullable=True)},
            position_size   TEXT    NOT NULL {_decimal_check("position_size")},
            fees            TEXT    NOT NULL DEFAULT '0' {_decimal_check("fees", positive=False)},
            status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
            strategy        TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
            session         TEXT    CHECK (session IS NULL
                                           OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
            notes           TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            closed_at       TEXT    CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}'),

            CONSTRAINT trades_instrument_owner_fk FOREIGN KEY (instrument_id, user_id)
                REFERENCES instruments (id, user_id) ON DELETE RESTRICT,
            CONSTRAINT status_matches_exit CHECK (
                (status = 'OPEN'   AND exit_price IS NULL) OR
                (status = 'CLOSED' AND exit_price IS NOT NULL)
            ),
            CONSTRAINT stop_on_correct_side CHECK (
                (direction = 'LONG'  AND CAST(stop_loss AS REAL) < CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(stop_loss AS REAL) > CAST(entry_price AS REAL))
            ),
            CONSTRAINT target_on_correct_side CHECK (
                take_profit IS NULL OR
                (direction = 'LONG'  AND CAST(take_profit AS REAL) > CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(take_profit AS REAL) < CAST(entry_price AS REAL))
            )
        );

        INSERT INTO instruments_v3
            (id, user_id, symbol, name, asset_class, price_increment, contract_multiplier,
             quote_currency, created_at)
        SELECT id, NULL, symbol, name, asset_class, price_increment, contract_multiplier,
               quote_currency, created_at
        FROM instruments;

        INSERT INTO trades_v3 (user_id, {_TRADE_COLUMNS})
        SELECT NULL, {_TRADE_COLUMNS} FROM trades;

        DROP TABLE trades;
        DROP TABLE instruments;
        ALTER TABLE instruments_v3 RENAME TO instruments;
        ALTER TABLE trades_v3 RENAME TO trades;

        CREATE INDEX idx_trades_instrument_id ON trades (instrument_id);
        CREATE INDEX idx_trades_trade_date    ON trades (trade_date);
        CREATE INDEX idx_trades_status        ON trades (status);
        CREATE INDEX idx_trades_closed_at     ON trades (closed_at);
        CREATE INDEX idx_trades_user_date     ON trades (user_id, trade_date, id);
        CREATE INDEX idx_trades_user_closed   ON trades (user_id, closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,  # noqa: S608 - constant DDL built from module constants; no user input
    4: """
        CREATE TABLE user_sessions (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            key_hash     TEXT    NOT NULL UNIQUE CHECK (length(key_hash) = 64),
            created_at   INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            expires_at   INTEGER NOT NULL,
            user_agent   TEXT    NOT NULL DEFAULT '' CHECK (length(user_agent) <= 100),
            revoked_at   INTEGER
        );
        CREATE INDEX idx_user_sessions_user ON user_sessions (user_id, revoked_at);

        CREATE TABLE password_resets (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            token_hash TEXT    NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at    INTEGER
        );
        CREATE INDEX idx_password_resets_user ON password_resets (user_id);

        CREATE TABLE recovery_codes (
            id        INTEGER PRIMARY KEY,
            user_id   INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            code_hash TEXT    NOT NULL UNIQUE CHECK (length(code_hash) = 64),
            used_at   INTEGER
        );
        CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id);

        ALTER TABLE users ADD COLUMN totp_secret TEXT;
        ALTER TABLE users ADD COLUMN totp_enabled_at TEXT;
        ALTER TABLE users ADD COLUMN totp_last_step INTEGER;
    """,
}

# Migrations that rebuild tables: foreign keys must be OFF while they run.
REBUILDS = {3}

# PostgreSQL migrations after the baseline in schema_postgres.sql.
POSTGRES_BASELINE_VERSION = 3
POSTGRES_MIGRATIONS = {
    4: """
        CREATE TABLE user_sessions (
            id           BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id      BIGINT  NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            key_hash     TEXT    NOT NULL CONSTRAINT user_sessions_key_unique UNIQUE CHECK (length(key_hash) = 64),
            created_at   BIGINT  NOT NULL,
            last_seen_at BIGINT  NOT NULL,
            expires_at   BIGINT  NOT NULL,
            user_agent   TEXT    NOT NULL DEFAULT '' CHECK (length(user_agent) <= 100),
            revoked_at   BIGINT
        );
        CREATE INDEX idx_user_sessions_user ON user_sessions (user_id, revoked_at);

        CREATE TABLE password_resets (
            id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id    BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            token_hash TEXT   NOT NULL CONSTRAINT password_resets_hash_unique UNIQUE CHECK (length(token_hash) = 64),
            created_at BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            used_at    BIGINT
        );
        CREATE INDEX idx_password_resets_user ON password_resets (user_id);

        CREATE TABLE recovery_codes (
            id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id   BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            code_hash TEXT   NOT NULL CONSTRAINT recovery_codes_hash_unique UNIQUE CHECK (length(code_hash) = 64),
            used_at   BIGINT
        );
        CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id);

        ALTER TABLE users ADD COLUMN totp_secret TEXT;
        ALTER TABLE users ADD COLUMN totp_enabled_at TEXT;
        ALTER TABLE users ADD COLUMN totp_last_step BIGINT;
    """,
}
POSTGRES_MIGRATION_NAMES = {4: "Phase 8.5: per-device sessions, password-reset links, two-step verification"}

LATEST_VERSION = max(MIGRATIONS)

# Any constant works; it only has to be the same for every TradeLedger process.
_POSTGRES_LOCK_ID = 7_340_658_812


def current_version(conn):
    if conn.dialect == "postgresql":
        exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
        if not exists:
            return 0
        return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    return conn.execute("PRAGMA user_version").fetchone()[0]


def pending_versions(conn):
    version = current_version(conn)
    registry = POSTGRES_MIGRATIONS if conn.dialect == "postgresql" else MIGRATIONS
    return [v for v in sorted(registry) if v > version]


def migrate(conn):
    """Apply all pending SQLite migrations. Returns the versions applied."""
    applied = []
    raw = conn.raw
    for version in pending_versions(conn):
        rebuild = version in REBUILDS
        if rebuild:
            raw.execute("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction
        try:
            raw.executescript(f"BEGIN;\n{MIGRATIONS[version]}")
            problems = raw.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise sqlite3.IntegrityError(f"migration {version}: foreign_key_check failed: {problems[:5]}")
            raw.execute(f"PRAGMA user_version = {int(version)}")
            raw.execute("COMMIT")
        except sqlite3.Error:
            if raw.in_transaction:
                raw.execute("ROLLBACK")
            raise
        finally:
            if rebuild:
                raw.execute("PRAGMA foreign_keys = ON")
        applied.append(version)
    return applied


def migrate_postgres(conn, baseline_sql):
    """Create the PostgreSQL schema on an empty database, then apply any
    later PostgreSQL migrations. Each step is one transaction."""
    applied = []
    with conn:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
        if current_version(conn) == 0:
            conn.executescript(baseline_sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (POSTGRES_BASELINE_VERSION, "PostgreSQL baseline (same structure as SQLite version 3)"),
            )
            applied.append(POSTGRES_BASELINE_VERSION)
    for version in pending_versions(conn):
        with conn:
            conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
            if current_version(conn) >= version:
                continue  # another process applied it while we waited for the lock
            conn.executescript(POSTGRES_MIGRATIONS[version])
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, POSTGRES_MIGRATION_NAMES.get(version, f"migration {version}")),
            )
        applied.append(version)
    return applied


def backup(conn, destination_path):
    """Copy a whole SQLite database to another file (SQLite's online backup).
    PostgreSQL backups use pg_dump or RDS snapshots (see docs)."""
    if conn.dialect != "sqlite":
        raise RuntimeError("backup() copies SQLite files only; use pg_dump for PostgreSQL")
    target = sqlite3.connect(destination_path)
    try:
        conn.backup(target)
    finally:
        target.close()
```

### `tradeledger/observability.py` (MODIFIED)

**Why:** Redacts tlr_ reset tokens and the development server's access lines.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Logging, request ids and health checks (Phase 8).

Plain English
-------------
When something goes wrong on a server, logs are how you find out what
happened. Good logs say what happened, to which request, and when, without
ever containing passwords, session cookies, API tokens or your private
trading notes.

Technical summary
-----------------
* configure_logging(app): one handler on the "tradeledger" logger family
  and Flask's app.logger. LOG_FORMAT=json writes one JSON object per line,
  which CloudWatch Logs Insights can query by field; "text" is for people.
* RedactingFilter runs on EVERY record and masks, wherever they appear:
  API tokens (tl_...), "Bearer ..." values, passwords in connection URLs
  (postgresql://user:***@host) and password=... / secret=... pairs. It is
  a safety net; the code is also written never to log such values.
* Request ids: each request gets an id (a client may send a safe-looking
  X-Request-ID, e.g. from the load balancer; otherwise a random one). It is
  returned in the X-Request-ID response header and added to every log line
  written during the request, so one failing request can be traced.
* Access log: method, path WITHOUT the query string (search text and
  filters can contain note contents), status, duration, user id.
"""

import json
import logging
import re
import time
import uuid

from flask import g, has_request_context, request

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

_REDACTIONS = (
    (re.compile(r"tl_[A-Za-z0-9_-]{8,}"), "tl_***"),
    (re.compile(r"tlr_[A-Za-z0-9_-]{8,}"), "tlr_***"),  # password-reset links (Phase 8.5)
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|secret_key)(\s*[=:]\s*)('[^']*'|\"[^\"]*\"|\S+)"),
        r"\1\2***",
    ),
)

STANDARD_ATTRIBUTES = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def redact(text):
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Masks secrets in the message, its arguments and any extra fields."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        for key, value in list(vars(record).items()):
            if key not in STANDARD_ATTRIBUTES and isinstance(value, str):
                setattr(record, key, redact(value))
        if has_request_context():
            record.request_id = g.get("request_id")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in STANDARD_ATTRIBUTES and not key.startswith("_") and value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record):
        text = super().format(record)
        extras = {
            k: v
            for k, v in vars(record).items()
            if k not in STANDARD_ATTRIBUTES and not k.startswith("_") and v
        }
        if extras:
            text += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return redact(text)


def configure_logging(app):
    """Attach one redacting handler to the "tradeledger" logger.

    Flask names app.logger after the package, so app.logger IS the
    "tradeledger" logger, and module loggers such as "tradeledger.auth"
    reach the same handler. Flask's own default handler is removed so
    lines are not printed twice. Records still propagate, so pytest's
    caplog sees them (already redacted by the logger-level filter).
    """
    from flask.logging import default_handler

    logger = logging.getLogger("tradeledger")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if app.config["LOG_FORMAT"] == "json" else TextFormatter())
    handler.set_name("tradeledger")
    handler.addFilter(RedactingFilter())  # covers records from child loggers too
    for existing in list(logger.handlers):
        if existing is default_handler or existing.get_name() == "tradeledger":
            logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    # Phase 8.5: the development server ("flask run") writes its own access
    # lines through the "werkzeug" logger, with the full URL, so a one-time
    # reset link (?token=tlr_...) would appear in the terminal. Production
    # runs Gunicorn with its access log switched off, but development and
    # tests get the same protection.
    werkzeug_logger = logging.getLogger("werkzeug")
    if not any(isinstance(f, RedactingFilter) for f in werkzeug_logger.filters):
        werkzeug_logger.addFilter(RedactingFilter())


def register_request_hooks(app):
    access = logging.getLogger("tradeledger.access")

    @app.before_request
    def assign_request_id():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()

    @app.after_request
    def add_request_id(response):
        if "request_id" in g:
            response.headers["X-Request-ID"] = g.request_id
        if app.config["ACCESS_LOG"] and request.endpoint != "static":
            user = g.get("user") or g.get("api_user")
            access.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(
                        (time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000, 1
                    ),
                    "user_id": user["id"] if user else None,
                },
            )
        return response
```

### `tradeledger/repositories/users.py` (MODIFIED)

**Why:** TOTP columns; public_user shows only whether two-step is on; session rows revoked with the session version.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""User accounts (Phase 8).

Return values
  create_user           -> new id (int)
  get_user              -> dict or None (includes password_hash: never
                           send this dict to a template or the API; use
                           public_user())
  get_user_by_email     -> dict or None
  set_password_hash     -> None (optionally ends every other session)
  bump_session_version  -> the new version (ends every existing session and
                           revokes every user_sessions row)

Errors
  ValidationError  invalid email
  DuplicateError   the email is already registered

Emails are stored lower-case and trimmed, so "Trader@Example.com " and
"trader@example.com" are the same account.
"""

import re
import time

from ..db import IntegrityError
from ..timestamps import utc_now_text
from ..validation import ValidationError
from . import DuplicateError

MAX_EMAIL_LENGTH = 254
# Deliberately simple: something@something.something, no spaces. Full RFC
# 5322 validation is not worth it; only a confirmation email proves that an
# address works, and that needs an email service this phase does not have.
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}")

_COLUMNS = (
    "id, email, password_hash, is_active, session_version, created_at, updated_at, last_login_at, "
    "totp_secret, totp_enabled_at, totp_last_step"
)


def normalise_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def email_problem(value):
    """Why `value` is not a usable email (worded to follow the field
    label, e.g. "Email address must look like ..."), or None."""
    email = normalise_email(value)
    if not email:
        return "is required."
    if len(email) > MAX_EMAIL_LENGTH:
        return f"must be {MAX_EMAIL_LENGTH} characters or fewer."
    if not _EMAIL.fullmatch(email):
        return "must look like name@example.com."
    return None


def _to_dict(row):
    if row is None:
        return None
    user = dict(row)
    user["is_active"] = bool(user["is_active"])
    return user


def public_user(user):
    """The fields that are safe to show: never the password hash or the
    two-step secret."""
    shown = {key: user[key] for key in ("id", "email", "created_at", "last_login_at")}
    shown["two_step_enabled"] = bool(user.get("totp_enabled_at"))
    return shown


def create_user(conn, email, password_hash):
    problem = email_problem(email)
    if problem:
        raise ValidationError({"email": problem})
    now = utc_now_text()
    try:
        with conn:
            row = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?) RETURNING id",
                (normalise_email(email), password_hash, now, now),
            ).fetchone()
    except IntegrityError as exc:
        if exc.kind == "unique":
            raise DuplicateError("An account with this email address already exists.") from exc
        raise
    return row["id"]


def get_user(conn, user_id):
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return None
    row = conn.execute(f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()  # noqa: S608 - constant columns
    return _to_dict(row)


def get_user_by_email(conn, email):
    email = normalise_email(email)
    if not email:
        return None
    row = conn.execute(f"SELECT {_COLUMNS} FROM users WHERE email = ?", (email,)).fetchone()  # noqa: S608 - constant columns
    return _to_dict(row)


def count_users(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _revoke_sessions(conn, user_id):
    """Cross out every signed-in browser of this user (Phase 8.5 rows)."""
    conn.execute(
        "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (int(time.time()), user_id),
    )


def set_password_hash(conn, user_id, password_hash, *, end_other_sessions):
    """Store a new hash. end_other_sessions=True also raises the session
    version and revokes every user_sessions row, which signs out every
    browser that was logged in (the caller may then start a new session for
    the current browser)."""
    bump = ", session_version = session_version + 1" if end_other_sessions else ""
    with conn:
        conn.execute(
            f"UPDATE users SET password_hash = ?, updated_at = ?{bump} WHERE id = ?",  # noqa: S608 - fixed fragment
            (password_hash, utc_now_text(), user_id),
        )
        if end_other_sessions:
            _revoke_sessions(conn, user_id)


def bump_session_version(conn, user_id):
    with conn:
        row = conn.execute(
            "UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ? "
            "RETURNING session_version",
            (utc_now_text(), user_id),
        ).fetchone()
        _revoke_sessions(conn, user_id)
    return None if row is None else row["session_version"]


def record_login(conn, user_id):
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now_text(), user_id))


def set_active(conn, user_id, active):
    """Disable or re-enable an account. Disabling also ends its sessions."""
    with conn:
        conn.execute(
            "UPDATE users SET is_active = ?, session_version = session_version + 1, updated_at = ? WHERE id = ?",
            (bool(active), utc_now_text(), user_id),
        )
        _revoke_sessions(conn, user_id)
```

### `tradeledger/static/css/analytics.css` (MODIFIED)

**Why:** Breakdown tables fit at 1440 px (wrapping headings, sample tag under the name).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```css
/* ==========================================================================
   Analytics dashboard (Phase 6)
   Uses only the design tokens from base.css, so the dashboard matches the
   rest of TradeLedger and changes with it.
   ========================================================================== */

/* ---------- Filters ---------- */
.dash-filters__title {
    font-size: var(--text-base);
}

.filter-chip {
    display: inline-block;
    margin: 0 var(--space-1) var(--space-1) 0;
    padding: 0.1rem var(--space-2);
    border: 1px solid var(--color-border-strong);
    border-radius: 999px;
    color: var(--color-text);
}

/* ---------- KPI cards ---------- */
/* A <dl> of cards: auto-fit makes as many columns as fit (two on a phone,
   four at 1024px), and wide screens get exactly six (below). */
.kpi-grid {
    display: grid;
    /* 9.5rem lets two cards sit side by side on a 375px phone. */
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr));
    gap: var(--space-3);
    margin: 0;
}

/* Wide screens: exactly six columns, so the twelve cards form two even rows. */
@media (min-width: 80rem) {
    .kpi-grid {
        grid-template-columns: repeat(6, minmax(0, 1fr));
    }
}

.kpi {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-top: 3px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.kpi--positive {
    border-top-color: var(--color-positive);
}

.kpi--negative {
    border-top-color: var(--color-negative);
}

.kpi__label {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
    font-weight: 600;
}

.kpi__value {
    margin: 0;
    font-size: var(--text-xl);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

.kpi--positive .kpi__value {
    color: var(--color-positive);
}

.kpi--negative .kpi__value {
    color: var(--color-negative);
}

.kpi__note {
    margin: 0;
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

/* ---------- Chart cards ---------- */
.dash-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
}

@media (min-width: 72rem) {
    .dash-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    /* The cumulative R chart spans the full width; drawdown sits below. */
    .dash-grid--time {
        grid-template-columns: minmax(0, 1fr);
    }
}

.chart-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
    margin: 0;
    padding: var(--space-5);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.chart-card__head {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.chart-card__title {
    font-size: var(--text-base);
}

.chart-card__explain {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

/* Chart.js sizes the canvas to its parent when maintainAspectRatio is off,
   so the parent needs a definite height. position: relative is required by
   Chart.js's responsive resizing. */
.chart-card__canvas {
    position: relative;
    height: 16rem;
    min-width: 0;
}

.chart-card__canvas--tall {
    height: 20rem;
}

@media (max-width: 40rem) {
    .chart-card {
        padding: var(--space-4);
    }

    .chart-card__canvas {
        height: 14rem;
    }

    .chart-card__canvas--tall {
        height: 16rem;
    }
}

/* Without JavaScript there is nothing to draw: hide the empty boxes. */
html:not(.js) .chart-card__canvas {
    display: none;
}

.chart-card__canvas.is-unavailable {
    height: auto;
}

.chart-card__fallback {
    padding: var(--space-3) var(--space-4);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.chart-card__summary {
    font-size: var(--text-sm);
}

.chart-card__table-link {
    font-size: var(--text-sm);
}

/* ---------- Tables ---------- */
.data-table {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-top: var(--space-5);
}

.data-table__title {
    font-size: var(--text-base);
}

.table tbody th {
    text-align: left;
    font-weight: 600;
}

/* components.css removes the last row's border from <td> only; row
   headers (<th scope="row">) need the same. */
.table tbody tr:last-child th {
    border-bottom: 0;
}

/* Card layout (below 64rem, see components.css): row headers become a
   labelled line like every other cell. */
@media (max-width: 63.99rem) {
    .table tbody th {
        display: flex;
        justify-content: space-between;
        gap: var(--space-4);
        width: 100%;
        padding: var(--space-2) 0;
        border-bottom: 1px solid var(--color-border);
        white-space: normal;
        text-align: right;
    }

    .table tbody th[data-label]::before {
        content: attr(data-label);
        color: var(--color-text-muted);
        font-weight: 600;
        text-align: left;
    }

    .table tbody tr:last-child th {
        border-bottom: 1px solid var(--color-border);
    }

    .table .empty-row {
        justify-content: flex-start;
    }
}

.empty-row {
    font-style: italic;
}

.sample-tag {
    display: inline-block;
    margin-left: var(--space-1);
    padding: 0 var(--space-2);
    border: 1px solid var(--color-border-strong);
    border-radius: 999px;
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 500;
    white-space: nowrap;
}

.table .is-max-drawdown td,
.table .is-max-drawdown th {
    background: var(--color-negative-soft);
}

/* ---------- Data quality ---------- */
.data-quality {
    font-size: var(--text-sm);
}

/* Phase 8.5 polish: on wide screens the breakdown tables (eleven columns)
   were a little wider than their card at 1440px, so the last column sat
   behind a scroll bar. Column headings may now wrap onto two lines and the
   "Small sample" tag sits under the group name, which lets the full table
   fit from about 1280px upwards. Narrower screens keep the card layout. */
@media (min-width: 64rem) {
    .data-table .table thead th {
        white-space: normal;
        vertical-align: bottom;
    }

    .data-table .table tbody th .sample-tag {
        display: block;
        width: fit-content;
        margin: var(--space-1) 0 0;
    }
}
```

### `tradeledger/static/css/components.css` (MODIFIED)

**Why:** Table links at least 24 px tall (WCAG 2.2 target size).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```css
/* ==========================================================================
   Reusable components
   ========================================================================== */

/* ---------- Buttons ---------- */
.button,
.icon-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    min-height: var(--control-height);
    padding: 0 var(--space-4);
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    font: inherit;
    font-size: var(--text-sm);
    font-weight: 600;
    line-height: 1.2;
    text-decoration: none;
    white-space: nowrap;
    cursor: pointer;
}

.button--primary {
    background: var(--color-accent);
    color: var(--color-on-accent);
}

.button--primary:hover {
    background: var(--color-accent-strong);
}

.button--secondary,
.icon-button {
    background: var(--color-surface-raised);
    border-color: var(--color-border-strong);
    color: var(--color-text);
}

.button--secondary:hover,
.icon-button:hover {
    background: var(--color-surface-hover);
}

.button--ghost {
    background: transparent;
    color: var(--color-text-muted);
}

.button--ghost:hover {
    color: var(--color-text);
    background: var(--color-surface-hover);
}

.button--danger {
    background: var(--color-negative);
    color: #2a0707;
}

.button--danger:hover {
    background: #ff7070;
}

.button--danger-outline {
    background: transparent;
    border-color: rgba(255, 138, 138, 0.45);
    color: var(--color-negative);
}

.button--danger-outline:hover {
    background: var(--color-negative-soft);
}

.button--small {
    min-height: 2rem;
    padding: 0 var(--space-2);
    font-size: var(--text-xs);
}

.button.is-disabled,
.button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.button[aria-busy="true"] {
    cursor: progress;
}

.icon-button {
    padding: 0 var(--space-3);
}

.nav-toggle__bars,
.nav-toggle__bars::before,
.nav-toggle__bars::after {
    display: block;
    width: 1rem;
    height: 2px;
    background: currentColor;
    border-radius: 2px;
}

.nav-toggle__bars {
    position: relative;
}

.nav-toggle__bars::before,
.nav-toggle__bars::after {
    content: "";
    position: absolute;
    left: 0;
}

.nav-toggle__bars::before {
    top: -5px;
}

.nav-toggle__bars::after {
    top: 5px;
}

.text-link {
    color: var(--color-accent);
    text-underline-offset: 0.2em;
}

/* ---------- Cards ---------- */
.card {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    padding: var(--space-5);
}

.card--flush {
    padding: 0;
    overflow: hidden;
}

.card__header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
}

.section-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-2) var(--space-4);
    margin-bottom: var(--space-3);
}

/* ---------- Badges ---------- */
.badge {
    display: inline-flex;
    align-items: center;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--color-border-strong);
    background: var(--color-neutral-soft);
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
}

.badge--win {
    background: var(--color-positive-soft);
    border-color: rgba(76, 215, 154, 0.4);
    color: var(--color-positive);
}

.badge--loss {
    background: var(--color-negative-soft);
    border-color: rgba(255, 138, 138, 0.4);
    color: var(--color-negative);
}

.badge--open {
    background: var(--color-info-soft);
    border-color: rgba(124, 192, 255, 0.4);
    color: var(--color-info);
}

.badge-row {
    display: flex;
    gap: var(--space-2);
}

.heading-tag {
    vertical-align: middle;
    margin-left: var(--space-2);
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--color-border-strong);
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--color-text-muted);
}

.direction {
    font-size: var(--text-sm);
    font-weight: 600;
}

.direction--long::before {
    content: "▲ ";
    color: var(--color-positive);
}

.direction--short::before {
    content: "▼ ";
    color: var(--color-negative);
}

/* ---------- Notices and flash messages ---------- */
.notice {
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    border: 1px solid var(--color-border-strong);
    border-left-width: 4px;
    font-size: var(--text-sm);
}

.notice--info {
    background: var(--color-info-soft);
    border-left-color: var(--color-info);
}

.notice--warning {
    background: var(--color-warning-soft);
    border-left-color: var(--color-warning);
}

.notice--error {
    background: var(--color-negative-soft);
    border-left-color: var(--color-negative);
}

.flash-stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-4) var(--space-5) 0;
    max-width: var(--content-max);
}

.flash {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    border: 1px solid var(--color-border-strong);
    border-left: 4px solid var(--color-info);
    background: var(--color-surface-raised);
}

.flash--success {
    border-left-color: var(--color-positive);
}

.flash--error {
    border-left-color: var(--color-negative);
}

.flash__close {
    display: none;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: 0;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text-muted);
    font-size: var(--text-lg);
    cursor: pointer;
}

.js .flash__close {
    display: inline-flex;
}

.flash__close:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
}

/* ---------- Empty states ---------- */
.empty-state {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-6);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
    color: var(--color-text-muted);
}

.empty-state__title {
    color: var(--color-text);
}

/* ---------- Forms ---------- */
.form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: var(--space-4) var(--space-5);
}

fieldset.form-grid {
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
}

.field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
}

.field--wide {
    grid-column: 1 / -1;
}

.field__label {
    padding: 0;
    font-size: var(--text-sm);
    font-weight: 600;
}

.field__optional {
    font-weight: 400;
    color: var(--color-text-subtle);
}

.field__hint {
    font-size: var(--text-xs);
    color: var(--color-text-muted);
}

.field__error {
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-negative);
}

.field--error .input {
    border-color: var(--color-negative);
}

.input {
    width: 100%;
    min-height: var(--control-height);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-bg);
    color: var(--color-text);
    font: inherit;
    font-variant-numeric: tabular-nums;
}

.input:hover {
    border-color: #4a608a;
}

.input:focus-visible {
    border-color: var(--color-accent);
}

.input::placeholder {
    color: var(--color-text-subtle);
}

.input[type="datetime-local"],
.input[type="date"] {
    color-scheme: dark;
}

.select {
    appearance: none;
    padding-right: var(--space-6);
    background-image: linear-gradient(45deg, transparent 50%, var(--color-text-muted) 50%),
        linear-gradient(135deg, var(--color-text-muted) 50%, transparent 50%);
    background-position: calc(100% - 1.1rem) 55%, calc(100% - 0.75rem) 55%;
    background-size: 0.35rem 0.35rem;
    background-repeat: no-repeat;
}

.textarea {
    min-height: 7rem;
    resize: vertical;
    line-height: var(--leading);
}

/* Segmented radio buttons */
.choice__options {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
}

.choice__option {
    position: relative;
    flex: 1 1 6rem;
}

.choice__option input {
    position: absolute;
    opacity: 0;
    inset: 0;
    margin: 0;
    cursor: pointer;
}

.choice__option span {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: var(--control-height);
    padding: 0 var(--space-3);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-bg);
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-text-muted);
}

.choice__option input:hover + span {
    border-color: #4a608a;
}

.choice__option input:checked + span {
    border-color: var(--color-accent);
    background: rgba(106, 168, 255, 0.14);
    color: var(--color-text);
}

.choice__option input:checked + span::before {
    /* The span is a flex container, which drops trailing spaces, so
       the gap comes from a margin rather than a space character. */
    content: "✓";
    margin-right: var(--space-1);
}

.choice__option input:focus-visible + span {
    box-shadow: var(--focus-ring);
}

.form-section {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.form-section__note {
    font-size: var(--text-sm);
    color: var(--color-text-muted);
}

.form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-3);
    margin-top: var(--space-4);
}

.error-summary {
    padding: var(--space-4) var(--space-5);
    border: 2px solid var(--color-negative);
    border-radius: var(--radius);
    background: var(--color-negative-soft);
}

.error-summary__title {
    font-size: var(--text-base);
    margin-bottom: var(--space-2);
}

.error-summary ul {
    margin: 0;
    padding-left: var(--space-5);
}

.error-summary a {
    color: var(--color-text);
    font-weight: 600;
}

/* ---------- Tables ---------- */
.table-wrap {
    /* position: relative makes this the containing block for the
       absolutely positioned .visually-hidden labels inside the table;
       without it they escape the scroll box and widen the whole page. */
    position: relative;
    overflow-x: auto;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
}

.table th,
.table td {
    padding: var(--space-3);
    text-align: left;
    vertical-align: middle;
    border-bottom: 1px solid var(--color-border);
    white-space: nowrap;
}

.table thead th {
    position: sticky;
    top: 0;
    background: var(--color-surface-raised);
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}

.table tbody tr:last-child td {
    border-bottom: 0;
}

.table tbody tr:hover {
    background: var(--color-surface-hover);
}

.table .num-col {
    text-align: right;
}

.cell-stack {
    display: flex;
    flex-direction: column;
    line-height: 1.3;
}

.cell-stack--end {
    align-items: flex-end;
}

.cell-stack__sub {
    color: var(--color-text-muted);
    font-size: var(--text-xs);
}

.row-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-1);
}

/* A scrollable table region is focusable (tabindex="0") so keyboard users
   can scroll it with the arrow keys. */
.table-wrap:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
}

/* The hint only matters where the full table can be wider than its box:
   between the card layout (below 64rem) and wide screens (80rem+). */
.table-scroll-hint {
    display: none;
    margin: 0 0 var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

@media (min-width: 64rem) and (max-width: 79.99rem) {
    .table-scroll-hint {
        display: block;
    }
}

.table-note {
    margin-top: var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

/* Below 64rem (1024px), where the sidebar goes off-canvas, each row
   becomes a card: label on the left, value on the right. Every column is
   kept, nothing financial is hidden. */
@media (max-width: 63.99rem) {
    .table-wrap {
        border: 0;
        background: transparent;
        overflow: visible;
    }

    .table,
    .table tbody,
    .table tr,
    .table td {
        display: block;
        width: 100%;
    }

    .table thead {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
    }

    .table tbody {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
        align-items: start;
        gap: var(--space-3);
    }

    .table tr {
        border: 1px solid var(--color-border);
        border-radius: var(--radius-lg);
        background: var(--color-surface);
        padding: var(--space-2) var(--space-4);
    }

    .table td {
        display: flex;
        justify-content: space-between;
        gap: var(--space-4);
        padding: var(--space-2) 0;
        white-space: normal;
        text-align: right;
    }

    .table td[data-label]::before {
        content: attr(data-label);
        color: var(--color-text-muted);
        font-weight: 600;
        text-align: left;
    }

    .table tbody tr:last-child td {
        border-bottom: 1px solid var(--color-border);
    }

    .table td:last-child {
        border-bottom: 0;
    }

    .table .actions-col {
        justify-content: flex-start;
        padding-top: var(--space-3);
    }

    .row-actions {
        justify-content: flex-start;
        flex-wrap: wrap;
    }
}

/* ---------- Definition lists (label: value pairs) ---------- */
.data-list {
    display: grid;
    gap: 0;
    margin: 0;
}

.data-list > div {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: var(--space-1) var(--space-4);
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--color-border);
}

.data-list > div:last-child {
    border-bottom: 0;
}

.data-list dt {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.data-list dd {
    margin: 0;
    font-weight: 600;
    text-align: right;
}

/* ---------- Pagination ---------- */
.pagination {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
}

.pagination__status {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.results-summary {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

/* Phase 8.5: links inside table cells (trade numbers, symbols) are at
   least 24px tall, the WCAG 2.2 minimum target size (2.5.8), so they are
   easy to tap on a phone. The row height does not change. */
.table td a,
.table th a {
    display: inline-flex;
    align-items: center;
    min-height: 1.5rem;
}
```

### `tradeledger/static/css/pages.css` (MODIFIED)

**Why:** Two-step, recovery codes, session list layout.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```css
/* ==========================================================================
   Page-specific layouts
   ========================================================================== */

/* ---------- Overview ---------- */
.welcome {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: center;
}

.welcome__title {
    font-size: var(--text-xl);
    margin-bottom: var(--space-2);
}

.welcome__text {
    max-width: 62ch;
    color: var(--color-text-muted);
}

.welcome__counts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.welcome__counts li {
    flex: 1 1 7rem;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-surface-raised);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.welcome__count {
    display: block;
    color: var(--color-text);
    font-size: var(--text-2xl);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

@media (min-width: 64rem) {
    .welcome {
        grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
    }
}

.stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: var(--space-4);
}

.stat {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.stat__label {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
    font-weight: 600;
}

.stat__value {
    font-size: var(--text-2xl);
    font-weight: 700;
}

.stat__note {
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

.money-strip {
    margin-top: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.money-strip__title {
    font-size: var(--text-sm);
    color: var(--color-text-muted);
}

.money-strip__list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-6);
    font-weight: 600;
}

.money-strip__note {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.recent-list__item + .recent-list__item {
    border-top: 1px solid var(--color-border);
}

.recent-list__link {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-1) var(--space-4);
    align-items: center;
    padding: var(--space-3) var(--space-5);
    color: inherit;
    text-decoration: none;
}

.recent-list__link:hover {
    background: var(--color-surface-hover);
}

.recent-list__main {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
}

.recent-list__symbol {
    font-weight: 700;
}

.recent-list__meta {
    grid-column: 1;
    color: var(--color-text-muted);
    font-size: var(--text-xs);
}

.recent-list__result {
    grid-column: 2;
    grid-row: 1 / span 2;
    font-weight: 700;
    text-align: right;
}

/* ---------- Journal ---------- */
.filters {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.filters__grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: var(--space-4);
}

.filters__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.trade-table .direction {
    font-size: var(--text-xs);
}

.trade-row__symbol {
    color: var(--color-text);
    font-weight: 700;
    text-decoration: none;
}

.trade-row__symbol:hover {
    color: var(--color-accent);
    text-decoration: underline;
}

/* A thin coloured edge per outcome; the badge text says the same in words. */
.trade-row--win td:first-child {
    box-shadow: inset 3px 0 0 var(--color-positive);
}

.trade-row--loss td:first-child {
    box-shadow: inset 3px 0 0 var(--color-negative);
}

.trade-row--open td:first-child {
    box-shadow: inset 3px 0 0 var(--color-info);
}

@media (max-width: 63.99rem) {
    .trade-row--win td:first-child,
    .trade-row--loss td:first-child,
    .trade-row--open td:first-child {
        box-shadow: none;
    }

    /* .table .x beats the generic ".table tr" border rule by specificity. */
    .table .trade-row--win {
        border-left: 3px solid var(--color-positive);
    }

    .table .trade-row--loss {
        border-left: 3px solid var(--color-negative);
    }

    .table .trade-row--open {
        border-left: 3px solid var(--color-info);
    }
}

/* ---------- Trade form ---------- */
.trade-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
}

.form-section__title {
    font-size: var(--text-base);
}

.exit-fields[disabled] {
    opacity: 0.55;
}

/* ---------- Trade details ---------- */
.back-link {
    font-size: var(--text-sm);
}

.detail-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 60rem) {
    .detail-grid {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .detail-grid__wide {
        grid-column: 1 / -1;
    }
}

.result-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    border-top: 4px solid var(--color-border-strong);
}

.result-card .card__header {
    margin-bottom: 0;
}

.result-card--win {
    border-top-color: var(--color-positive);
}

.result-card--loss {
    border-top-color: var(--color-negative);
}

.result-card--open {
    border-top-color: var(--color-info);
}

.result-card__headline {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--space-2) var(--space-4);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.result-card__r {
    font-size: var(--text-lg);
}

.result-card__note,
.meta {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.result-card .button {
    align-self: flex-start;
}

.notes {
    margin-top: var(--space-3);
    margin-bottom: var(--space-4);
    white-space: pre-wrap; /* keep the user's line breaks; text is still escaped */
    overflow-wrap: anywhere;
    max-width: 75ch;
}

/* ---------- Delete confirmation ---------- */
.confirm {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    max-width: 40rem;
}

.confirm__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-3);
}

.data-list--compact dd {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: var(--space-2);
}

/* ---------- Instruments ---------- */
.split {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 72rem) {
    .split {
        grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
    }
}

.explainer {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.explainer h2,
.explainer strong {
    color: var(--color-text);
}

.formula {
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    overflow-wrap: anywhere;
}

/* ---------- About ---------- */
.prose-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr));
    gap: var(--space-5);
    align-items: start;
}

.prose {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.prose h2,
.prose h3,
.prose strong {
    color: var(--color-text);
}

.prose ul {
    margin: 0;
    padding-left: var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.roadmap {
    display: grid;
    gap: var(--space-2);
}

.roadmap-item {
    display: grid;
    grid-template-columns: 5rem minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--color-surface-raised);
}

.phase-number {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.phase-title {
    color: var(--color-text);
    font-weight: 600;
}

.phase-status {
    font-size: var(--text-xs);
    font-weight: 700;
}

.status-done .phase-status {
    color: var(--color-positive);
}

.status-next .phase-status {
    color: var(--color-accent);
}

/* ---------- Error pages ---------- */
.error-page {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    max-width: 40rem;
}

.error-code {
    color: var(--color-text-subtle);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.error-page__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
    margin-top: var(--space-2);
}

/* ---------- Sign-in, registration and account (Phase 8) ---------- */
.auth-card {
    max-width: 30rem;
}

.auth-card__note {
    margin-top: var(--space-4);
}

.form-stack {
    display: grid;
    gap: var(--space-4);
}

.form-section__subtitle {
    margin-top: var(--space-5);
    font-size: var(--text-lg);
}

.sidebar__account {
    overflow-wrap: anywhere;
}

.detail-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: var(--space-3) var(--space-5);
    margin: 0;
}

.detail-list dt {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.detail-list dd {
    margin: 0;
    overflow-wrap: anywhere;
}

.token-reveal {
    display: grid;
    gap: var(--space-2);
}

.token-reveal__value {
    font-family: var(--font-mono, ui-monospace, monospace);
    width: 100%;
}

.badge--neutral {
    color: var(--color-text-muted);
}

/* ---------- Two-step verification (Phase 8.5) ---------- */
.steps {
    display: grid;
    gap: var(--space-3);
    padding-left: var(--space-5);
}

.setup-key {
    display: inline-block;
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    font-size: var(--text-lg);
    letter-spacing: 0.08em;
    word-break: break-all;
}

.setup-uri {
    word-break: break-all;
}

.recovery-codes {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: var(--space-2);
    margin: var(--space-3) 0 0;
    padding: 0;
    list-style: none;
}

.recovery-codes code {
    display: block;
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    font-size: var(--text-base);
    text-align: center;
}

/* Signed-in browsers table (Phase 8.5): the "This browser" badge stays on
   one line and moves under the browser name when space is short. */
.session-browser {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: var(--space-1) var(--space-2);
}

.session-browser .badge {
    white-space: nowrap;
}

@media (min-width: 64rem) {
    .session-browser {
        justify-content: flex-start;
    }
}

/* Four short columns: let the dates wrap rather than push the "Sign out"
   button out of view between 1024 and 1280px. */
.table--sessions td {
    white-space: normal;
}

.table--sessions .actions-col {
    white-space: nowrap;
    text-align: right;
}
```

### `tradeledger/static/js/analytics.js` (MODIFIED)

**Why:** formatAxisValue for R axes; fallback message no longer mentions a CDN.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```javascript
// TradeLedger analytics dashboard: draws the seven charts with Chart.js.
//
// This file is PRESENTATION ONLY. It performs no financial calculation:
//   * every figure arrives from Python in the page's #dashboard-data JSON,
//     as an exact decimal string (e.g. "7.688173...") plus display text
//     (e.g. "+7.69R");
//   * the only conversion is toNumber(), which turns an exact string into
//     a JavaScript number to POSITION a point or bar. Tooltips show the
//     Python-formatted text, never a number computed here.
//
// If Chart.js cannot be loaded (offline, CDN blocked) or JavaScript is off,
// the page still shows every figure in its KPI cards, text descriptions and
// tables; each chart area explains why it is empty.
//
// The pure functions (toNumber, buildConfigs, ...) are exported for the
// Node.js tests in tests/js/analytics.test.js.
(function (root, factory) {
    "use strict";
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    } else {
        root.TradeLedgerCharts = api;
    }
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", function () {
            api.instances = api.init(document, root.Chart);
        });
    }
}(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    // Phase 8.5: Chart.js is normally served by TradeLedger itself, so the
    // message no longer assumes a CDN or an internet connection.
    var UNAVAILABLE_MESSAGE =
        "Chart unavailable: the Chart.js library could not be loaded, so this chart is not drawn. " +
        "The table below has the same figures.";
    var FAILED_MESSAGE = "This chart could not be drawn. The table below has the same figures.";

    // ---------- Small pure helpers ----------

    // Exact decimal string -> chart number. Absent, non-numeric or
    // non-finite values become null (Chart.js leaves a gap), never NaN or
    // Infinity.
    function toNumber(text) {
        if (text === null || text === undefined || text === "") {
            return null;
        }
        var value = Number(text);
        return Number.isFinite(value) ? value : null;
    }

    // "#4cd79a" + 0.2 -> "rgba(76, 215, 154, 0.2)" for translucent fills.
    function hexToRgba(hex, alpha) {
        var match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex).trim());
        if (!match) {
            return hex;
        }
        return "rgba(" + parseInt(match[1], 16) + ", " + parseInt(match[2], 16) + ", " +
            parseInt(match[3], 16) + ", " + alpha + ")";
    }

    // Axis label for a UTC time in milliseconds: "2026-03-02".
    function formatAxisDate(ms) {
        var date = new Date(ms);
        return isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
    }

    // Axis label for an R value: "-0.2R", never "-0.20000000000000002R".
    // Chart.js picks tick positions by repeated addition, which leaves binary
    // floating-point noise; rounding to 4 decimals is far finer than any tick
    // step it chooses here. Labels only: every figure shown as text comes from
    // Python (Phase 8.5 fix, found by the real Chart.js screenshots).
    function formatAxisValue(value) {
        var rounded = Math.round(Number(value) * 10000) / 10000;
        if (!isFinite(rounded)) {
            return "";
        }
        return (rounded === 0 ? 0 : rounded) + "R";
    }

    function signColour(value, theme) {
        if (value === null || value === 0) {
            return theme.muted;
        }
        return value > 0 ? theme.positive : theme.negative;
    }

    // Grid lines, with a stronger line at zero so gains and losses are
    // separated by a clear reference.
    function valueAxis(theme, extra) {
        var axis = {
            grid: {
                color: function (context) {
                    return context.tick && context.tick.value === 0 ? theme.zero : theme.grid;
                },
                lineWidth: function (context) {
                    return context.tick && context.tick.value === 0 ? 2 : 1;
                }
            },
            ticks: {
                callback: function (value) {
                    return formatAxisValue(value);
                }
            },
            title: {display: true, text: "R (risk multiples)"}
        };
        return Object.assign(axis, extra || {});
    }

    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,                      // no decorative motion
            plugins: {
                legend: {display: false},          // one series per chart; the caption explains it
                tooltip: {}
            }
        };
    }

    // ---------- Line charts: cumulative R and drawdown ----------

    function curveTooltipLines(point) {
        if (point.start) {
            return ["Starting point: " + point.cumulative_text];
        }
        var lines = [
            "Trade #" + point.trade_id + " · " + point.symbol,
            "Trade R: " + point.realised_text,
            "Cumulative R: " + point.cumulative_text
        ];
        if (!point.close_time_known) {
            lines.push("Exit time not recorded: placed at entry time");
        }
        return lines;
    }

    function timeAxis() {
        return {
            type: "linear",
            ticks: {
                maxTicksLimit: 6,
                callback: function (value) {
                    return formatAxisDate(value);
                }
            },
            title: {display: true, text: "Realised date (UTC)"}
        };
    }

    function buildCurveConfig(points, theme) {
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {x: timeAxis(), y: valueAxis(theme)};
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                return curveTooltipLines(points[item.dataIndex]);
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Cumulative realised R",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.cumulative_r)};
                    }),
                    borderColor: theme.accent,
                    backgroundColor: theme.accent,
                    borderWidth: 2,
                    pointRadius: points.length > 60 ? 0 : 3,
                    pointHoverRadius: 5,
                    tension: 0
                }]
            },
            options: options
        };
    }

    function buildDrawdownConfig(points, maxDrawdown, theme) {
        var troughId = maxDrawdown ? maxDrawdown.trough_trade_id : null;
        var isTrough = function (p) {
            return !p.start && troughId !== null && p.trade_id === troughId;
        };
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {
            x: timeAxis(),
            y: valueAxis(theme, {suggestedMax: 0})
        };
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                var point = points[item.dataIndex];
                var lines = ["Drawdown: " + point.drawdown_text + " below the running peak"];
                if (!point.start) {
                    lines.push("Trade #" + point.trade_id + " · " + point.symbol);
                    lines.push("Cumulative R: " + point.cumulative_text);
                }
                if (isTrough(point)) {
                    lines.push("Maximum drawdown: " + maxDrawdown.text);
                }
                return lines;
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Drawdown (R below the running peak)",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.underwater_r)};
                    }),
                    borderColor: theme.negative,
                    backgroundColor: hexToRgba(theme.negative, 0.18),
                    fill: "origin",
                    borderWidth: 2,
                    tension: 0,
                    pointRadius: points.map(function (p) {
                        return isTrough(p) ? 6 : (points.length > 60 ? 0 : 2);
                    }),
                    pointStyle: points.map(function (p) {
                        return isTrough(p) ? "triangle" : "circle";
                    }),
                    pointBackgroundColor: points.map(function (p) {
                        return isTrough(p) ? theme.text : theme.negative;
                    })
                }]
            },
            options: options
        };
    }

    // ---------- Bar charts: months, weekdays, groups ----------

    function barTooltipLines(row) {
        if (row.total_r === null) {
            return ["No closed trades"];
        }
        var lines = [
            "Total R: " + row.total_r_text,
            "Closed trades: " + row.trades + " (won " + row.wins + ", lost " + row.losses + ")",
            "Win rate: " + row.win_rate_text,
            "Average R: " + row.average_r_text,
            "Expectancy: " + row.expectancy_text
        ];
        if (row.small_sample) {
            lines.push("Small sample: treat with caution");
        }
        return lines;
    }

    function buildBarConfig(rows, theme, settings) {
        var horizontal = Boolean(settings.horizontal);
        var values = rows.map(function (row) {
            return toNumber(row.total_r);
        });
        var options = baseOptions();
        if (horizontal) {
            options.indexAxis = "y";
            options.scales = {x: valueAxis(theme), y: {grid: {display: false}}};
        } else {
            options.scales = {x: {grid: {display: false}}, y: valueAxis(theme)};
        }
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                return rows[items[0].dataIndex].label;
            },
            label: function (item) {
                return barTooltipLines(rows[item.dataIndex]);
            }
        };
        return {
            type: "bar",
            data: {
                labels: rows.map(function (row) {
                    return row.small_sample ? row.label + " *" : row.label;
                }),
                datasets: [{
                    label: settings.label,
                    data: values,
                    backgroundColor: values.map(function (value) {
                        return signColour(value, theme);
                    }),
                    borderRadius: 3,
                    maxBarThickness: 36
                }]
            },
            options: options
        };
    }

    // Every chart's configuration from the payload. Pure: no DOM, no Chart.
    function buildConfigs(payload, theme) {
        return {
            curve: buildCurveConfig(payload.curve, theme),
            drawdown: buildDrawdownConfig(payload.curve, payload.max_drawdown, theme),
            monthly: buildBarConfig(payload.monthly, theme, {label: "Total R by month"}),
            weekday: buildBarConfig(payload.weekday, theme, {label: "Total R by weekday"}),
            instruments: buildBarConfig(payload.instruments, theme,
                {label: "Total R by instrument", horizontal: true}),
            strategies: buildBarConfig(payload.strategies, theme,
                {label: "Total R by strategy", horizontal: true}),
            sessions: buildBarConfig(payload.sessions, theme,
                {label: "Total R by session", horizontal: true})
        };
    }

    // ---------- Browser glue ----------

    // Colours come from the CSS design tokens in base.css, so the charts
    // follow the same theme as the rest of the application.
    function readTheme(doc) {
        var styles = doc.defaultView.getComputedStyle(doc.documentElement);
        var token = function (name) {
            return styles.getPropertyValue(name).trim();
        };
        return {
            text: token("--color-text"),
            muted: token("--color-text-muted"),
            grid: token("--color-border"),
            zero: token("--color-text-subtle"),
            accent: token("--color-accent"),
            positive: token("--color-positive"),
            negative: token("--color-negative"),
            font: token("--font-sans")
        };
    }

    function showFallback(canvas, message) {
        var box = canvas.closest("[data-chart-box]");
        var note = box ? box.querySelector("[data-chart-fallback]") : null;
        canvas.hidden = true;
        if (box) {
            box.classList.add("is-unavailable");
        }
        if (note) {
            note.textContent = message;          // textContent: never parsed as HTML
            note.hidden = false;
        }
    }

    function init(doc, ChartLib) {
        var dataElement = doc.getElementById("dashboard-data");
        var canvases = Array.prototype.slice.call(doc.querySelectorAll("canvas[data-chart]"));
        if (!dataElement || canvases.length === 0) {
            return [];
        }
        if (typeof ChartLib !== "function") {
            canvases.forEach(function (canvas) {
                showFallback(canvas, UNAVAILABLE_MESSAGE);
            });
            return [];
        }
        var theme = readTheme(doc);
        if (ChartLib.defaults) {
            ChartLib.defaults.color = theme.muted;
            ChartLib.defaults.borderColor = theme.grid;
            if (ChartLib.defaults.font) {
                ChartLib.defaults.font.family = theme.font;
            }
        }
        var configs = buildConfigs(JSON.parse(dataElement.textContent), theme);
        var charts = [];
        canvases.forEach(function (canvas) {
            var config = configs[canvas.getAttribute("data-chart")];
            if (!config) {
                return;
            }
            try {
                charts.push(new ChartLib(canvas, config));
            } catch (error) {
                showFallback(canvas, FAILED_MESSAGE);
                if (typeof console !== "undefined") {
                    console.error("TradeLedger chart failed:", canvas.id, error);
                }
            }
        });
        return charts;
    }

    return {
        toNumber: toNumber,
        hexToRgba: hexToRgba,
        formatAxisDate: formatAxisDate,
        formatAxisValue: formatAxisValue,
        curveTooltipLines: curveTooltipLines,
        barTooltipLines: barTooltipLines,
        buildConfigs: buildConfigs,
        init: init,
        instances: []
    };
}));
```

### `tradeledger/static/vendor/chartjs/README.md` (MODIFIED)

**Why:** Describes the bundled files.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```markdown
# Self-hosted Chart.js

Third-party code lives here, never mixed with TradeLedger's own files in `static/js/`.

| File | What it is |
|---|---|
| `chart.umd.min.js` | Chart.js 4.5.1, served by TradeLedger itself (the default since Phase 8.5) |
| `LICENSE.md` | Chart.js's MIT licence |
| `LICENSE-kurkle-color.md` | MIT licence of `@kurkle/color` 0.3.2, which is bundled inside the file |
| `BUILD.md` | Where the file comes from, its SHA-384 hash, and how to rebuild it |

The dashboard loads `/static/vendor/chartjs/chart.umd.min.js` with a Subresource Integrity (SRI) hash, `CHART_JS_BUNDLED_INTEGRITY` in `tradeledger/config.py`. The browser refuses the file if it has been changed.

To load it from the jsDelivr CDN instead, set both of these:
- `TRADELEDGER_CHART_JS_URL=https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js`
- `TRADELEDGER_CHART_JS_INTEGRITY` to the CDN file's hash.

Real-chart tests: `python -m pytest -m chartjs_real -ra`.
```

### `tradeledger/templates/account/settings.html` (MODIFIED)

**Why:** Two-step status, signed-in browsers table with per-browser sign-out.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Account{% endblock %}
{% block heading %}Account{% endblock %}
{% block description %}Your sign-in details, sessions and API tokens.{% endblock %}

{% block content %}
<section class="card prose" aria-labelledby="profile-title">
    <h2 id="profile-title">Your account</h2>
    <dl class="detail-list">
        <div><dt>Email address</dt><dd>{{ account.email }}</dd></div>
        <div><dt>Created</dt><dd>{{ account.created_at | when }}</dd></div>
        <div><dt>Last sign-in</dt><dd>{{ account.last_login_at | when if account.last_login_at else "None recorded yet" }}</dd></div>
    </dl>
</section>

<section class="card form-section" aria-labelledby="password-title">
        <h2 class="form-section__title" id="password-title">Change password</h2>
        <p class="muted">Changing your password signs out every other browser and revokes all your API tokens.</p>
        {{ f.error_summary(password_errors, password_labels) }}
        <form method="post" action="{{ url_for('main.change_password') }}" novalidate data-submit-once>
            {{ csrf_field() }}
            <input type="email" name="username" value="{{ account.email }}" autocomplete="username" hidden>
            <div class="form-stack">
                {{ f.text("current_password", "Current password", {}, password_errors, password_labels, type="password",
                          required=True, maxlength=128, autocomplete="current-password") }}
                {{ f.text("new_password", "New password", {}, password_errors, password_labels, type="password",
                          required=True, maxlength=128, autocomplete="new-password", hint="At least 12 characters.") }}
                {{ f.text("confirm_password", "Confirm new password", {}, password_errors, password_labels,
                          type="password", required=True, maxlength=128, autocomplete="new-password") }}
            </div>
            <div class="form-actions">
                <button class="button button--primary" type="submit" data-busy-label="Saving…">Change password</button>
            </div>
        </form>
</section>

<section class="card form-section" aria-labelledby="two-step-title">
    <h2 class="form-section__title" id="two-step-title">Two-step verification</h2>
    {% if account.two_step_enabled %}
    <p><span class="badge badge--win">On</span> Signing in needs your password and a code from your authenticator app.</p>
    <p><a href="{{ url_for('main.two_step_settings') }}">Manage two-step verification</a></p>
    {% else %}
    <p><span class="badge badge--neutral">Off</span> Add a code from an authenticator app to every sign-in, so a stolen password alone is not enough.</p>
    <p><a class="button button--secondary" href="{{ url_for('main.two_step_settings') }}">Set up two-step verification</a></p>
    {% endif %}
</section>

<section class="card form-section" aria-labelledby="sessions-title">
    <h2 class="form-section__title" id="sessions-title">Signed-in browsers</h2>
    <p>Every browser signed in to your account. Lost a device, or signed in on a shared computer? Sign that browser out here.
       Sessions also end on their own after 8 hours, or 2 hours without activity.</p>
    <div class="table-wrap" role="region" aria-label="Signed-in browsers table" tabindex="0">
        <table class="table table--sessions">
            <caption class="visually-hidden">Browsers signed in to your account, most recently active first</caption>
            <thead>
                <tr>
                    <th scope="col">Browser</th>
                    <th scope="col">Signed in</th>
                    <th scope="col">Last active</th>
                    <th scope="col"><span class="visually-hidden">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {% for s in sessions %}
                <tr{% if s.id == current_session_id %} class="is-current"{% endif %}>
                    <td data-label="Browser"><span class="session-browser"><span>{{ s.user_agent or "Unknown browser" }}</span>{% if s.id == current_session_id %}<span class="badge badge--open">This browser</span>{% endif %}</span></td>
                    <td data-label="Signed in">{{ s.created_at | when_epoch }}</td>
                    <td data-label="Last active">{{ s.last_seen_at | when_epoch }}</td>
                    <td class="actions-col">
                        <form method="post" action="{{ url_for('main.revoke_session', session_id=s.id) }}">
                            {{ csrf_field() }}
                            <button class="button button--small button--secondary" type="submit">Sign out<span class="visually-hidden"> {{ "this browser" if s.id == current_session_id else s.user_agent }}</span></button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% if sessions | length > 1 %}
    <form method="post" action="{{ url_for('main.end_other_sessions') }}">
        {{ csrf_field() }}
        <div class="form-actions">
            <button class="button button--secondary" type="submit">Sign out everywhere else</button>
        </div>
    </form>
    {% endif %}
</section>

{% if api_enabled %}
<section class="card form-section" aria-labelledby="tokens-title">
    <h2 class="form-section__title" id="tokens-title">API tokens</h2>
    <p>Programs use a token to call the JSON API (<code>/api/v1</code>) on your behalf, with the header
       <code>Authorization: Bearer &lt;token&gt;</code>. A token can read and change your trades, so treat it like a password.</p>

    {% if new_token %}
    <div class="notice notice--warning token-reveal" role="status">
        <p><strong>Copy your new token now. It will not be shown again.</strong></p>
        <label class="field__label" for="new-token-value">Token “{{ new_token.record.name }}”</label>
        <input class="input token-reveal__value" id="new-token-value" type="text" readonly
               value="{{ new_token.value }}" spellcheck="false" autocomplete="off">
        <p class="muted">Expires {{ new_token.record.expires_at | when }}.</p>
    </div>
    {% endif %}

    {% if tokens %}
    <div class="table-wrap" role="region" aria-label="API tokens table" tabindex="0">
        <table class="table">
            <caption class="visually-hidden">Your API tokens, newest first</caption>
            <thead>
                <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Starts with</th>
                    <th scope="col">Created</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Last used</th>
                    <th scope="col">Status</th>
                    <th scope="col"><span class="visually-hidden">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {% for t in tokens %}
                <tr>
                    <td data-label="Name">{{ t.name }}</td>
                    <td data-label="Starts with"><code>{{ t.token_prefix }}…</code></td>
                    <td data-label="Created">{{ t.created_at | when }}</td>
                    <td data-label="Expires">{{ t.expires_at | when }}</td>
                    <td data-label="Last used">{{ t.last_used_at | when }}</td>
                    <td data-label="Status"><span class="badge badge--{{ 'win' if t.status == 'active' else 'neutral' }}">{{ t.status | title }}</span></td>
                    <td class="actions-col">
                        {% if t.status == "active" %}
                        <form method="post" action="{{ url_for('main.revoke_api_token', token_id=t.id) }}">
                            {{ csrf_field() }}
                            <button class="button button--small button--danger" type="submit">Revoke<span class="visually-hidden"> token {{ t.name }}</span></button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <p class="muted">You have no API tokens.</p>
    {% endif %}

    <h3 class="form-section__subtitle">Create a token</h3>
    {{ f.error_summary(token_errors, token_labels) }}
    <form method="post" action="{{ url_for('main.create_api_token') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="hidden" name="form_nonce" value="{{ token_form_nonce }}">
        <div class="form-grid">
            {{ f.text("name", "Token name", token_values, token_errors, token_labels, required=True, maxlength=60,
                      hint="What will use it, e.g. 'Import script on my laptop'.") }}
            {{ f.select("lifetime_days", "Expires after", token_values, token_errors, token_labels,
                        lifetime_options, required=True) }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Creating…">Create token</button>
        </div>
    </form>
</section>
{% endif %}
{% endblock %}
```

### `tradeledger/templates/auth/login.html` (MODIFIED)

**Why:** Explains how to get a reset link.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Sign in{% endblock %}
{% block heading %}Sign in{% endblock %}
{% block description %}Your journal, instruments and analytics are private to your account.{% endblock %}

{% block content %}
<section class="card form-section auth-card" aria-labelledby="login-title">
    <h2 class="form-section__title" id="login-title">Sign in to TradeLedger</h2>
    {% if error %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="login-error">
        <h2 class="error-summary__title" id="login-error">Sign-in failed</h2>
        <p>{{ error }}</p>
    </div>
    {% endif %}
    <form method="post" action="{{ url_for('main.login') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        {% if next_path %}<input type="hidden" name="next" value="{{ next_path }}">{% endif %}
        <div class="form-stack">
            {{ f.text("email", "Email address", values, {}, {}, type="email", required=True, maxlength=254,
                      autocomplete="username") }}
            {{ f.text("password", "Password", {}, {}, {}, type="password", required=True, maxlength=128,
                      autocomplete="current-password") }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Signing in…">Sign in</button>
        </div>
    </form>
    <p class="muted auth-card__note">
        {% if config.REGISTRATION_ENABLED %}
        New here? <a href="{{ url_for('main.register') }}">Create an account</a>.
        {% else %}
        Accounts on this server are created by its administrator.
        {% endif %}
        Forgotten your password? Ask your administrator for a one-time reset link. TradeLedger does not send email.
    </p>
</section>
{% endblock %}
```

### `tradeledger/web/__init__.py` (MODIFIED)

**Why:** Registers the reset and two-step modules.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  auth.py         sign in, sign out, registration, session checks (Phase 8)
  account.py      account settings, password change, API tokens (Phase 8),
                  signed-in browsers (Phase 8.5)
  reset.py        one-time password-reset links (Phase 8.5)
  two_step.py     two-step verification: set-up and sign-in (Phase 8.5)
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, health checks
  analytics.py    the /analytics performance dashboard (Phase 6)
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two. Every read
and write passes the signed-in user's id (auth.current_user_id()).
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Importing these modules registers their routes and template filters.
from . import (  # noqa: E402,F401
    account,
    analytics,
    auth,
    csrf,
    filters,
    instruments,
    reset,
    trades,
    two_step,
    views,
)

# Request hooks are registered HERE, explicitly, because their order
# matters and must not depend on import order (Phase 7: sorting the import
# line above once swapped them silently; tests caught it).
#   1. load_logged_in_user: read and check the session (g.user).
#   2. require_login: every endpoint not in auth.PUBLIC_ENDPOINTS redirects
#      anonymous visitors to the sign-in page. Running it BEFORE the CSRF
#      check means an anonymous POST is redirected, not told "form expired".
#   3. csrf: check the token on every state-changing request, including the
#      sign-in form itself (which stops "login CSRF").
main_bp.before_request(auth.load_logged_in_user)
main_bp.before_request(auth.require_login)
main_bp.before_request(csrf.check_csrf_token)
```

### `tradeledger/web/account.py` (MODIFIED)

**Why:** Session list, per-session revoke, password change and 'everywhere else' re-issue this browser's session.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Account settings: password change, signed-in browsers, API tokens (Phase 8, 8.5).

Every route here needs a signed-in user (it is not in PUBLIC_ENDPOINTS) and
every POST carries a CSRF token (web/csrf.py).
"""

import logging
import secrets

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, parameters_from_config, password_problem, verify_password
from ..repositories import api_tokens, sessions, throttle
from ..repositories.users import bump_session_version, get_user, public_user, set_password_hash
from ..validation import ValidationError
from . import main_bp
from .auth import THROTTLED, current_session_id, current_user_id, digest, now_seconds, start_session

log = logging.getLogger("tradeledger.auth")

PASSWORD_LABELS = {
    "current_password": "Current password",
    "new_password": "New password",
    "confirm_password": "Confirm new password",
}
TOKEN_LABELS = {"name": "Token name", "lifetime_days": "Expires after"}
LIFETIME_OPTIONS = [(30, "30 days"), (90, "90 days"), (365, "365 days")]


def new_token_form_nonce():
    """A one-time value for the "Create token" form.

    The new token is shown in the POST response itself (it must never be
    stored anywhere, including the session cookie). Without a nonce, a
    browser refresh would re-send that POST and silently create a second
    token. The nonce makes each rendered form usable once."""
    session["token_form_nonce"] = secrets.token_urlsafe(16)
    return session["token_form_nonce"]


def render_account(status=200, **extra):
    config = current_app.config
    context = {
        "account": public_user(g.user),
        "sessions": sessions.list_active(
            get_db(),
            user_id=current_user_id(),
            now=now_seconds(),
            idle_timeout_seconds=config["SESSION_IDLE_TIMEOUT_SECONDS"],
        ),
        "current_session_id": current_session_id(),
        "tokens": api_tokens.list_tokens(get_db(), user_id=current_user_id()),
        "password_errors": {},
        "token_errors": {},
        "token_values": {"lifetime_days": str(current_app.config["API_TOKEN_DEFAULT_LIFETIME_DAYS"])},
        "new_token": None,
        "password_labels": PASSWORD_LABELS,
        "token_labels": TOKEN_LABELS,
        "lifetime_options": LIFETIME_OPTIONS,
        "api_enabled": current_app.config["API_ENABLED"],
        "token_form_nonce": new_token_form_nonce(),
    }
    context.update(extra)
    return render_template("account/settings.html", **context), status


@main_bp.route("/account")
def account():
    return render_account()


@main_bp.route("/account/password", methods=["POST"])
def change_password():
    config = current_app.config
    conn = get_db()
    user = g.user
    key = digest("login-email", user["email"])
    window = config["LOGIN_WINDOW_SECONDS"]
    if throttle.is_blocked(
        conn, "login-email", key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ):
        return render_account(429, password_errors={"current_password": THROTTLED})

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    errors = {}
    if not verify_password(current, user["password_hash"]):
        throttle.record_failure(conn, "login-email", key, window_seconds=window)
        errors["current_password"] = "is incorrect."
    problem = password_problem(new, email=user["email"])
    if problem:
        errors["new_password"] = problem
    elif request.form.get("confirm_password", "") != new:
        errors["confirm_password"] = "does not match the new password."
    elif new == current and not errors:
        errors["new_password"] = "must be different from your current password."
    if errors:
        return render_account(400, password_errors=errors)

    set_password_hash(
        conn, user["id"], hash_password(new, parameters_from_config(config)), end_other_sessions=True
    )
    revoked = api_tokens.revoke_all(conn, user_id=user["id"])
    # Every session (including this one) was revoked; this browser gets a
    # brand-new session, as after signing in with the new password.
    start_session(get_user(conn, user["id"]))
    log.info(
        "password changed",
        extra={"event": "auth.password.changed", "user_id": user["id"], "tokens_revoked": revoked},
    )
    flash(
        "Password changed. Every other browser has been signed out"
        + (f" and {revoked} API token(s) revoked." if revoked else "."),
        "success",
    )
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/sessions/end-others", methods=["POST"])
def end_other_sessions():
    user_id = current_user_id()
    conn = get_db()
    bump_session_version(conn, user_id)  # revokes every row, this one included
    start_session(get_user(conn, user_id))  # ... so this browser starts afresh
    log.info("other sessions ended", extra={"event": "auth.sessions.ended_elsewhere", "user_id": user_id})
    flash("Every other browser signed in to your account has been signed out.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/sessions/<int:session_id>/revoke", methods=["POST"])
def revoke_session(session_id):
    """Sign out ONE browser (Phase 8.5). Another user's session id, an
    unknown id and an already-ended session all get the same 404."""
    user_id = current_user_id()
    this_browser = session_id == current_session_id()
    if not sessions.revoke(get_db(), session_id, user_id=user_id, now=now_seconds()):
        abort(404)
    log.info(
        "session revoked",
        extra={"event": "auth.session.revoked", "user_id": user_id, "session_ref": session_id},
    )
    if this_browser:
        session.clear()
        flash("You have signed out.", "success")
        return redirect(url_for("main.login"), code=303)
    flash("That browser has been signed out.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/tokens", methods=["POST"])
def create_api_token():
    config = current_app.config
    if not config["API_ENABLED"]:
        abort(404)
    expected = session.pop("token_form_nonce", None)
    if not expected or request.form.get("form_nonce") != expected:
        flash("That form had already been submitted, so no new token was created.", "info")
        return redirect(url_for("main.account"), code=303)
    name = request.form.get("name", "")
    raw_days = request.form.get("lifetime_days", "")
    values = {"name": name, "lifetime_days": raw_days}
    lifetime = int(raw_days) if raw_days.isdigit() else None
    try:
        token, record = api_tokens.create_token(
            get_db(),
            user_id=current_user_id(),
            name=name,
            lifetime_days=lifetime,
            max_active=config["API_TOKEN_MAX_ACTIVE"],
        )
    except ValidationError as exc:
        return render_account(400, token_errors=exc.errors, token_values=values)
    log.info(
        "API token created",
        extra={"event": "auth.token.created", "user_id": current_user_id(), "token_id": record["id"]},
    )
    # The token is shown in THIS response only. HTML responses are already
    # Cache-Control: no-store, so the browser does not keep a copy.
    return render_account(201, new_token={"value": token, "record": record})


@main_bp.route("/account/tokens/<int:token_id>/revoke", methods=["POST"])
def revoke_api_token(token_id):
    if not api_tokens.revoke_token(get_db(), token_id, user_id=current_user_id()):
        abort(404)  # not yours, already revoked, or does not exist: same answer
    log.info(
        "API token revoked",
        extra={"event": "auth.token.revoked", "user_id": current_user_id(), "token_id": token_id},
    )
    flash("API token revoked. Programs using it will be refused from now on.", "success")
    return redirect(url_for("main.account"), code=303)
```

### `tradeledger/web/auth.py` (MODIFIED)

**Why:** Server-side session rows at sign-in, on every request and at sign-out; two-step accounts go to the second step.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Signing in, signing out and registering (Phase 8).

Plain English
-------------
* Sign in: you type your email and password. If they match, TradeLedger
  puts your user number in a cookie that it signs with a secret key, so
  the browser cannot change it without the signature breaking.
* Every page then checks that cookie, loads your account and refuses
  anything private if you are not signed in.
* Sign out: a button (a POST form with a CSRF token), never a plain link,
  so another website cannot sign you out by embedding an image.

Technical summary
-----------------
Session    Flask's signed-cookie session (itsdangerous HMAC with SECRET_KEY).
           It holds: sid (a random session key, Phase 8.5), uid (user id),
           sv (the user's session_version), auth_at (sign-in time), seen_at
           (last activity), the CSRF token. No password, hash or personal
           data is ever put in it. The database keeps one user_sessions row
           per signed-in browser, holding only SHA-256(sid)
           (repositories/sessions.py).
Checks     On every website request (load_logged_in_user):
             * the account still exists and is active;
             * sv equals users.session_version; raising that number
               ("sign out everywhere", password change, account disabled)
               ends every existing session at once;
             * the user_sessions row for sid exists, belongs to this user,
               is not revoked, not expired and not idle (Phase 8.5);
             * auth_at is younger than SESSION_MAX_AGE_SECONDS (absolute
               limit) and seen_at younger than SESSION_IDLE_TIMEOUT_SECONDS.
           These are checked on the SERVER: the cookie's own expiry date is
           only a hint to the browser, and a copied cookie would ignore it.
Fixation   start_session() clears the session before writing the new user,
           so any session state planted before sign-in (including the CSRF
           token) is discarded. Signed cookies have no server-side session id
           to fixate, but clearing them is still required.
Default    require_login() runs before EVERY route in the website blueprint
deny       and only lets the endpoints in PUBLIC_ENDPOINTS through.
           A new route is private unless someone deliberately adds it to
           that set (tests/test_auth.py checks every registered route).
Brute      Failed sign-ins are counted per email and per IP address
force      (repositories/throttle.py); past the limits the form answers 429.
           Unknown emails still run a full Argon2id verification against a
           dummy hash, so response time does not reveal which emails exist.
Messages   One message for every failure: "Email or password is incorrect."
           A disabled account gets the same message.

Signed cookie AND a server-side record (Phase 8.5): Phase 8 used only the
signed cookie plus session_version, so it could end ALL of a user's
sessions but not one device's, and a cookie copied before sign-out stayed
valid until it expired. The user_sessions row closes both gaps; the signed
cookie still carries the CSRF token and the other values, and the row
lives in the shared database, so it works across containers.
"""

import logging
import time

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, needs_rehash, parameters_from_config, password_problem, verify_password
from ..repositories import DuplicateError, sessions, throttle
from ..repositories.users import (
    create_user,
    email_problem,
    get_user,
    get_user_by_email,
    normalise_email,
    record_login,
    set_password_hash,
)
from . import forms, main_bp

log = logging.getLogger("tradeledger.auth")

PUBLIC_ENDPOINTS = frozenset(
    {
        "main.login",
        "main.login_two_step",  # Phase 8.5: second step, needs a pending sign-in
        "main.register",
        "main.reset_password",  # Phase 8.5: needs a one-time link
        "main.health",
        "main.health_live",
        "main.health_ready",
        "main.about",
    }
)

LOGIN_FAILED = "Email or password is incorrect."
THROTTLED = "Too many sign-in attempts. Wait a few minutes, then try again."

LOGIN_LABELS = {"email": "Email address", "password": "Password"}
REGISTER_LABELS = {"email": "Email address", "password": "Password", "confirm_password": "Confirm password"}


def now_seconds():
    """Current time in whole seconds. One function so tests can move the clock."""
    return int(time.time())


def client_ip():
    """The client's IP address. Behind a load balancer this is only right
    when PROXY_HOPS matches the number of proxies (see config.py)."""
    return request.remote_addr or "unknown"


def digest(scope, value):
    return throttle.key_digest(current_app.config["SECRET_KEY"], scope, value)


def safe_next(target):
    """Accept only a path on THIS site for the post-login redirect (or None).

    Uses the same rules as every other "next" link (forms.safe_next):
    absolute URLs (https://evil.example), scheme-relative URLs
    (//evil.example), backslash tricks and control characters are refused.
    Otherwise an attacker could send a link to
    /login?next=https://evil.example and the real sign-in page would
    forward the victim to a fake one.
    """
    return forms.safe_next(target, None)


def dummy_hash():
    """A real Argon2id hash of a random value, computed once per process
    with the current settings: verifying against it costs the same as a
    real verification."""
    cache = current_app.extensions.setdefault("tradeledger_auth", {})
    params = parameters_from_config(current_app.config)
    if cache.get("dummy_params") != params:
        cache["dummy_hash"] = hash_password("dummy password that matches no account", params)
        cache["dummy_params"] = params
    return cache["dummy_hash"]


def start_session(user):
    """Sign this browser in as `user`: a fresh cookie plus a new
    user_sessions row (Phase 8.5) that can be revoked on the server."""
    session.clear()
    session.permanent = True
    now = now_seconds()
    key, session_id = sessions.create_session(
        get_db(),
        user_id=user["id"],
        now=now,
        max_age_seconds=current_app.config["SESSION_MAX_AGE_SECONDS"],
        user_agent=request.headers.get("User-Agent", ""),
    )
    session["sid"] = key
    session["uid"] = user["id"]
    session["sv"] = user["session_version"]
    session["auth_at"] = now
    session["seen_at"] = now
    g.session_id = session_id
    return session_id


def current_session_id():
    """The user_sessions id of THIS browser (None if not signed in)."""
    return g.get("session_id")


def current_user_id():
    """The signed-in user's id. Raises if called where nobody is signed in,
    so a coding mistake fails closed instead of reading someone's data."""
    user = g.get("user")
    if user is None:
        raise RuntimeError("current_user_id() called without a signed-in user")
    return user["id"]


# ---------- Request hooks (registered in web/__init__.py) ----------


def load_logged_in_user():
    g.user = None
    user_id = session.get("uid")
    if user_id is None:
        return None
    config = current_app.config
    user = get_user(get_db(), user_id)
    now = now_seconds()
    reason = None
    if user is None or not user["is_active"]:
        reason = "account_unavailable"
    elif session.get("sv") != user["session_version"]:
        reason = "signed_out_elsewhere"
    elif now - int(session.get("auth_at", 0)) > config["SESSION_MAX_AGE_SECONDS"]:
        reason = "expired"
    elif now - int(session.get("seen_at", 0)) > config["SESSION_IDLE_TIMEOUT_SECONDS"]:
        reason = "idle"
    record = None
    if reason is None:
        # Phase 8.5: the server-side row decides as well. A cookie whose row
        # was revoked (signed out, signed out from another device, password
        # changed) or never existed is refused even if its signature is valid.
        record = sessions.find_session(get_db(), session.get("sid"))
        reason = sessions.problem_with(
            record, user_id=user_id, now=now, idle_timeout_seconds=config["SESSION_IDLE_TIMEOUT_SECONDS"]
        )
        if reason == "unknown":
            reason = "no_server_session"
    if reason:
        session.clear()
        g.session_ended = reason
        log.info("session ended", extra={"event": "auth.session.ended", "reason": reason, "user_id": user_id})
        return None
    g.user = user
    g.session_id = record["id"]
    sessions.touch(get_db(), record, now)
    if now - int(session.get("seen_at", 0)) >= 60:  # refresh at most once a minute
        session["seen_at"] = now
    return None


def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or g.get("user") is not None:
        return None
    if g.get("session_ended"):
        flash("Your session has ended. Please sign in again.", "info")
    next_path = None
    if request.method == "GET":
        next_path = request.full_path.rstrip("?")
    return redirect(url_for("main.login", next=next_path), code=303 if request.method != "GET" else 302)


# ---------- Routes ----------


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    next_path = safe_next(request.values.get("next"))
    if request.method == "GET":
        return render_template("auth/login.html", values={}, error=None, next_path=next_path)

    config = current_app.config
    conn = get_db()
    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    email_key = digest("login-email", email)
    ip_key = digest("login-ip", client_ip())
    window = config["LOGIN_WINDOW_SECONDS"]

    if throttle.is_blocked(
        conn, "login-email", email_key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ) or throttle.is_blocked(
        conn, "login-ip", ip_key, limit=config["LOGIN_MAX_FAILURES_PER_IP"], window_seconds=window
    ):
        log.warning("sign-in refused: too many failures", extra={"event": "auth.login.throttled"})
        response = render_template(
            "auth/login.html", values={"email": email}, error=THROTTLED, next_path=next_path
        )
        return response, 429, {"Retry-After": str(window)}

    user = get_user_by_email(conn, email) if email else None
    password_ok = verify_password(password, user["password_hash"] if user else dummy_hash())
    if user is None or not password_ok or not user["is_active"]:
        reason = "unknown_email" if user is None else ("bad_password" if not password_ok else "inactive")
        throttle.record_failure(conn, "login-email", email_key, window_seconds=window)
        throttle.record_failure(conn, "login-ip", ip_key, window_seconds=window)
        log.warning(
            "sign-in failed",
            extra={"event": "auth.login.failed", "reason": reason, "email_ref": email_key[:12]},
        )
        return render_template(
            "auth/login.html", values={"email": email}, error=LOGIN_FAILED, next_path=next_path
        )

    throttle.clear(conn, "login-email", email_key)
    params = parameters_from_config(config)
    if needs_rehash(user["password_hash"], params):
        set_password_hash(conn, user["id"], hash_password(password, params), end_other_sessions=False)
        log.info("password hash upgraded", extra={"event": "auth.password.rehashed", "user_id": user["id"]})
    if user["totp_enabled_at"]:
        # Phase 8.5: the password was right, but the session only starts
        # after the second step (web/two_step.py).
        from .two_step import begin_pending

        return begin_pending(user, next_path)
    record_login(conn, user["id"])
    start_session(user)
    log.info("sign-in succeeded", extra={"event": "auth.login.succeeded", "user_id": user["id"]})
    return redirect(next_path or url_for("main.index"), code=303)


@main_bp.route("/logout", methods=["POST"])
def logout():
    user_id = current_user_id()
    # Cross the session out on the server too, so a copy of this cookie
    # (for example from a shared computer) cannot be used afterwards.
    sessions.revoke(get_db(), current_session_id(), user_id=user_id, now=now_seconds())
    session.clear()
    log.info("signed out", extra={"event": "auth.logout", "user_id": user_id})
    flash("You have signed out.", "success")
    return redirect(url_for("main.login"), code=303)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    config = current_app.config
    if not config["REGISTRATION_ENABLED"]:
        abort(404)
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    if request.method == "GET":
        return render_template("auth/register.html", values={}, errors={}, labels=REGISTER_LABELS)

    conn = get_db()
    ip_key = digest("register-ip", client_ip())
    if throttle.is_blocked(
        conn, "register-ip", ip_key, limit=config["REGISTRATIONS_PER_IP_PER_HOUR"], window_seconds=3600
    ):
        log.warning("registration refused: too many attempts", extra={"event": "auth.register.throttled"})
        errors = {"email": "could not be registered: too many attempts from your network. Try again later."}
        response = render_template("auth/register.html", values={}, errors=errors, labels=REGISTER_LABELS)
        return response, 429, {"Retry-After": "3600"}
    throttle.record_failure(conn, "register-ip", ip_key, window_seconds=3600)  # every attempt counts

    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    errors = {}
    problem = email_problem(email)
    if problem:
        errors["email"] = problem
    problem = password_problem(password, email=email)
    if problem:
        errors["password"] = problem
    elif request.form.get("confirm_password", "") != password:
        errors["confirm_password"] = "does not match the password."
    if not errors:
        try:
            user_id = create_user(conn, email, hash_password(password, parameters_from_config(config)))
        except DuplicateError:
            # Registration cannot avoid revealing that an address is taken
            # without an email-verification step (docs/PHASE_8_SECURITY_REVIEW.md).
            errors["email"] = "is already registered. Sign in instead."
    if errors:
        return (
            render_template(
                "auth/register.html", values={"email": email}, errors=errors, labels=REGISTER_LABELS
            ),
            400,
        )
    user = get_user(conn, user_id)
    record_login(conn, user_id)
    start_session(user)
    log.info("account created", extra={"event": "auth.register.succeeded", "user_id": user_id})
    flash("Your account is ready. Start by adding the instruments you trade.", "success")
    return redirect(url_for("main.instruments"), code=303)
```

### `tradeledger/web/filters.py` (MODIFIED)

**Why:** when_epoch filter for session times.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Jinja filters: display formatting for exact values.

All rounding goes through performance/formatting.py (the Phase 3 rounding
policy), so a value looks the same in the CLI report, the website and any
future page. Templates never do arithmetic on money.

  {{ value | r }}                  +1.97R      (None -> n/a)
  {{ value | r_amount }}           1.05R       (a size, no sign)
  {{ value | money(currency) }}    393.00 USD
  {{ value | rate }}               50.0%
  {{ value | ratio }}              2.85
  {{ value | plain }}              1.085       (exact stored value)
  {{ ts | when }}                  02 Mar 2026, 08:15 UTC
  {{ ts | day }} / {{ ts | clock }} 02 Mar 2026 / 08:15 UTC (for compact tables)
  {{ ts | datetime_local }}        2026-03-02T08:15:00 (for <input type=datetime-local>)
"""

from datetime import UTC, datetime

from ..performance.formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from ..timestamps import parse_stored
from . import main_bp


def _parse(timestamp):
    try:
        return parse_stored(timestamp)
    except (TypeError, ValueError):
        return None


@main_bp.app_template_filter("r")
def r_filter(value):
    return format_r(value)


@main_bp.app_template_filter("r_amount")
def r_amount_filter(value):
    return format_r_amount(value)


@main_bp.app_template_filter("money")
def money_filter(value, currency):
    return format_money(value, currency)


@main_bp.app_template_filter("rate")
def rate_filter(value):
    return format_rate(value)


@main_bp.app_template_filter("ratio")
def ratio_filter(value):
    return format_ratio(value)


@main_bp.app_template_filter("plain")
def plain_filter(value):
    if value is None:
        return "—"
    return decimal_to_text(value)


@main_bp.app_template_filter("when")
def when_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return NOT_AVAILABLE if timestamp is None else timestamp
    text = moment.strftime("%d %b %Y, %H:%M")
    if moment.second:
        text += moment.strftime(":%S")
    return text + " UTC"


@main_bp.app_template_filter("when_epoch")
def when_epoch_filter(seconds):
    """Whole seconds since 1970 (UTC), as stored for sessions and reset
    links (Phase 8.5), shown like every other time: "16 Nov 2026, 09:30 UTC"."""
    if seconds is None:
        return NOT_AVAILABLE
    moment = datetime.fromtimestamp(int(seconds), UTC)
    return moment.strftime("%d %b %Y, %H:%M UTC")


@main_bp.app_template_filter("day")
def day_filter(timestamp):
    moment = _parse(timestamp)
    return moment.strftime("%d %b %Y") if moment else NOT_AVAILABLE


@main_bp.app_template_filter("clock")
def clock_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return ""
    return moment.strftime("%H:%M:%S UTC" if moment.second else "%H:%M UTC")


@main_bp.app_template_filter("datetime_local")
def datetime_local_filter(timestamp):
    """Stored '2026-03-02T08:15:00Z' -> '2026-03-02T08:15:00'. Seconds are
    kept so editing a trade never silently changes its time."""
    moment = _parse(timestamp)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if moment else (timestamp or "")
```

### `.env.example` (MODIFIED)

**Why:** Documents the self-hosted Chart.js default, TRADELEDGER_PUBLIC_URL and TRADELEDGER_TOTP_ISSUER.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```bash
# Copy this file to .env and fill in real values.
# .env is ignored by Git - never commit it.

# Which config class to use: development, testing or production
TRADELEDGER_CONFIG=development

# Generate your own with:  python -c "import secrets; print(secrets.token_hex(32))"
# (Production refuses this placeholder and anything under 32 characters.
#  In containers prefer SECRET_KEY_FILE=/run/secrets/secret_key.)
SECRET_KEY=replace-me-with-a-long-random-string

# Host names this server answers to (Phase 7; protects against DNS
# rebinding). Default in development: localhost,127.0.0.1. Add your LAN IP
# if you open TradeLedger from another device. "*" switches the check off.
# TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1

# Optional: where the SQLite file lives. Leave commented out to use
# instance/tradeledger.db inside the project folder.
# DATABASE_PATH=/full/path/to/tradeledger.db

# Optional: where the dashboard loads Chart.js from. Since Phase 8.5 the
# default is the copy shipped with TradeLedger (tradeledger/static/vendor/
# chartjs/, checked with its built-in SRI hash), so nothing is loaded from
# another website. To use the jsDelivr CDN instead, set BOTH:
# TRADELEDGER_CHART_JS_URL=https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js
# TRADELEDGER_CHART_JS_INTEGRITY=sha384-<hash of that file>

# ---------- Phase 8 ----------

# PostgreSQL instead of SQLite (Docker Compose and AWS set these for you).
# Either one URL...
# DATABASE_URL=postgresql://tradeledger:CHANGE-ME@localhost:5432/tradeledger
# ...or separate settings (the password may come from DB_PASSWORD_FILE):
# DB_HOST=localhost
# DB_PORT=5432
# DB_NAME=tradeledger
# DB_USER=tradeledger
# DB_PASSWORD=CHANGE-ME
# DB_SSLMODE=require
# TRADELEDGER_DB_POOL_MAX=5

# Self-registration: on by default in development, OFF in production.
# TRADELEDGER_REGISTRATION_ENABLED=0

# Set to 1 ONLY when exactly one reverse proxy / load balancer is in front.
# TRADELEDGER_PROXY_HOPS=0

# Logs: "text" (people) or "json" (CloudWatch; production default).
# TRADELEDGER_LOG_FORMAT=text

# Turn the JSON API off entirely (tokens are still required when it is on).
# TRADELEDGER_API_ENABLED=1

# Removed in Phase 8 (ignored with a warning): TRADELEDGER_ENABLE_UNAUTHENTICATED_API,
# TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL. Authentication cannot be switched off.

# ---------- Phase 8.5 ----------
# The address people use to reach TradeLedger. The create-reset-link command
# prints one-time password-reset links starting with this (https:// in
# production; http://localhost is accepted for local testing).
# TRADELEDGER_PUBLIC_URL=https://journal.example.com
# The name authenticator apps show for two-step verification codes.
# TRADELEDGER_TOTP_ISSUER=TradeLedger
```

### `.github/workflows/ci.yml` (MODIFIED)

**Why:** Browser job: real-chart tests on the bundled file, then again on the official npm file.  
**How to run or test:** Runs on GitHub (never run yet); static checks in `tests/test_deployment_files.py`

```yaml
# TradeLedger continuous integration (Phase 7, extended in Phase 8).
#
# Runs on every push, every pull request, and on demand (Actions tab ->
# CI -> "Run workflow"). It only TESTS the code: nothing is deployed, no
# cloud credentials exist here. Deployment is a separate, manual workflow
# (deploy.yml) behind an approval.
#
# Jobs (they run in parallel):
#   python      lint, formatting, the non-browser test suite (SQLite) and
#               coverage, on the oldest and newest supported Python versions
#   postgres    the same suite against a real PostgreSQL 16 service with the
#               real psycopg driver, plus the independent analytics audit on
#               both databases (Phase 8)
#   javascript  the Node.js unit tests for static/js/analytics.js
#   audit       known-vulnerability check of the pinned Python dependencies
#   docker      Dockerfile lint, image build, and the Compose end-to-end
#               smoke test (sign-in, trade, analytics, API, restart) (Phase 8)
#   terraform   formatting and static validation of infra/ WITHOUT any AWS
#               account (terraform init -backend=false) (Phase 8)
#   browser     Playwright + Chromium, in three clearly separated steps:
#                 1. page tests with Chart.js REPLACED by a stand-in (stub)
#                 2. the offline fallback (Chart.js blocked)
#                 3. REAL Chart.js 4.5.1 rendering (library fetched from npm
#                    into this CI workspace only; never committed)

name: CI

on:
  push:
  pull_request:
  workflow_dispatch:

# Least privilege: the workflow only needs to read the repository.
permissions:
  contents: read

# A newer push to the same branch cancels the older, now-irrelevant run.
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PIP_DISABLE_PIP_VERSION_CHECK: "1"
  PYTHONDONTWRITEBYTECODE: "1"
  FORCE_COLOR: "1"

jobs:
  python:
    name: Python ${{ matrix.python-version }} · lint, tests, coverage
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        # README promises Python 3.11 or newer; test the oldest and the newest used.
        python-version: ["3.11", "3.13"]
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      - name: Lint (ruff check)
        run: ruff check --output-format=github .

      - name: Formatting (ruff format --check)
        run: ruff format --check .

      - name: Tests and coverage (everything except real-browser tests)
        # Coverage settings, including the minimum (fail_under), are in pyproject.toml.
        run: >
          python -m pytest -m "not browser"
          --cov=tradeledger --cov-report=term-missing
          --cov-report=xml --cov-report=html

      - name: Upload the coverage report
        if: always() && matrix.python-version == '3.13'
        uses: actions/upload-artifact@v6
        with:
          name: coverage-report
          path: |
            htmlcov/
            coverage.xml
          retention-days: 14
          if-no-files-found: ignore

  postgres:
    name: PostgreSQL 16 · full suite + audit (psycopg)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres  # throwaway CI-only database, not a real secret
          POSTGRES_DB: tradeledger_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      TRADELEDGER_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/tradeledger_test
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies (includes psycopg)
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      - name: Confirm the REAL driver is used (not the test-only bridge)
        run: python -c "import psycopg, psycopg_pool; print('psycopg', psycopg.__version__)"

      - name: Test suite on PostgreSQL (every test uses its own schema)
        run: python -m pytest -m "not browser" -ra

      - name: Set up Node.js (the audit runs the real analytics.js)
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      - name: Independent analytics audit, SQLite
        run: python scripts/qa_dashboard_agreement.py

      - name: Independent analytics audit, PostgreSQL
        run: |
          psql "postgresql://postgres:postgres@localhost:5432/postgres" -c "CREATE DATABASE tradeledger_audit"
          python scripts/qa_dashboard_agreement.py \
            --database-url postgresql://postgres:postgres@localhost:5432/tradeledger_audit

  javascript:
    name: JavaScript unit tests (Node.js)
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Node.js 22
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      # Node's built-in test runner: no npm packages are installed.
      - name: Run the tests
        run: node --test tests/js/*.test.js

  audit:
    name: Dependency vulnerability audit
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      # Checks every installed package against the Python Packaging Advisory
      # Database and OSV. Fails the job if a known vulnerability is found.
      - name: pip-audit
        run: pip-audit --strict --progress-spinner off

  browser:
    name: Browser tests (Playwright + Chromium)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies (with Playwright)
        run: python -m pip install -r requirements-browser.txt -c constraints.txt

      - name: Install Chromium and its system libraries
        run: python -m playwright install --with-deps chromium

      - name: "1/4 Page tests, Chart.js replaced by a stand-in (stub: NOT Chart.js rendering)"
        run: python -m pytest -m "browser and not chartjs_offline and not chartjs_real" -ra

      - name: "2/4 Offline fallback (Chart.js blocked)"
        run: python -m pytest -m chartjs_offline -ra

      - name: Set up Node.js (for npm pack)
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      - name: "3/4 REAL Chart.js rendering (the bundled, self-hosted file)"
        env:
          # A missing library file fails the job instead of being skipped.
          TRADELEDGER_REQUIRE_REAL_CHARTJS: "1"
          TRADELEDGER_SCREENSHOT_DIR: artifacts/screenshots
        run: python -m pytest -m chartjs_real -ra

      # Extra check: the official npm file (npm verifies it against the
      # registry's published integrity hash) must pass the same tests. It is
      # copied over the bundled file in this temporary CI workspace only.
      - name: "4/4 REAL Chart.js rendering (official npm chart.js@4.5.1)"
        env:
          TRADELEDGER_REQUIRE_REAL_CHARTJS: "1"
        run: |
          npm pack chart.js@4.5.1 --silent
          tar -xzf chart.js-4.5.1.tgz
          cp package/dist/chart.umd.min.js tradeledger/static/vendor/chartjs/chart.umd.min.js
          sha384sum tradeledger/static/vendor/chartjs/chart.umd.min.js
          python -m pytest -m chartjs_real -ra
          git checkout -- tradeledger/static/vendor/chartjs/chart.umd.min.js

      - name: Upload real-chart screenshots
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: chartjs-real-screenshots
          path: artifacts/screenshots/
          retention-days: 14
          if-no-files-found: ignore

  docker:
    name: Docker image + Compose smoke test
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Lint the Dockerfile (hadolint)
        run: docker run --rm -i hadolint/hadolint < Dockerfile

      - name: Validate compose.yaml
        run: |
          mkdir -p secrets && echo placeholder > secrets/secret_key.txt && echo placeholder > secrets/db_password.txt
          docker compose config --quiet
          rm secrets/secret_key.txt secrets/db_password.txt

      - name: Build the image
        run: docker build -t tradeledger:ci .

      - name: The image runs as a non-root user and contains no secrets or tests
        run: |
          test "$(docker run --rm --entrypoint id tradeledger:ci -u)" = "10001"
          ! docker run --rm --entrypoint sh tradeledger:ci -c 'ls -a /app' | grep -E '\.env|tests|secrets|\.git'

      - name: Compose end-to-end smoke test
        run: bash scripts/compose_smoke_test.sh

  terraform:
    name: Terraform fmt + validate (no AWS account)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: infra
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "~1.13.0"  # any 1.13.x; needs >= 1.10 for S3 state locking

      - name: terraform fmt -check
        run: terraform fmt -check -recursive -diff

      - name: terraform init (providers only, no remote state)
        run: terraform init -backend=false -input=false

      - name: terraform validate
        run: terraform validate
```

### `pyproject.toml` (MODIFIED)

**Why:** Reviewed Ruff exceptions for the new password-handling views.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```toml
# Tool configuration for TradeLedger (Phase 7).
#
# TradeLedger is an application, not a library published to PyPI, so this
# file holds tool settings only: no build system and no package metadata.
# Dependencies stay in requirements*.txt (see README "Setup").

# ---------------------------------------------------------------- pytest
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra --strict-markers"
markers = [
    "browser: drives a real Chromium browser through Playwright (skipped if Playwright is missing)",
    "chartjs_stub: browser test that replaces Chart.js with a recording stand-in (tests OUR code, not Chart.js drawing)",
    "chartjs_offline: browser test that blocks Chart.js to check the text fallback",
    "chartjs_real: browser test that needs the REAL Chart.js file (skipped unless it is installed locally)",
    "sqlite_only: relies on SQLite specifics (files, PRAGMA); skipped when testing on PostgreSQL",
    "postgres_only: needs TRADELEDGER_TEST_DATABASE_URL; skipped on SQLite",
]
# Turn every warning into a failure, so problems such as unclosed files or
# deprecated calls cannot pile up unnoticed. Narrow exceptions go below,
# each with a reason.
filterwarnings = [
    "error",
]

# ---------------------------------------------------------------- coverage
[tool.coverage.run]
source = ["tradeledger"]
branch = true
# The browser tests run the app in a background thread.
concurrency = ["thread"]

[tool.coverage.report]
show_missing = true
skip_covered = false
precision = 1
# CI fails below this. Measured baseline: see docs/PHASE_7_QA_REPORT.md.
fail_under = 90
exclude_also = [
    # Code that only exists to help a type checker.
    "if TYPE_CHECKING:",
]

[tool.coverage.html]
directory = "htmlcov"

# ---------------------------------------------------------------- ruff
[tool.ruff]
target-version = "py311"
line-length = 110
# docs/ holds historical code listings (PHASE_*_CODE.md) that must stay exactly
# as delivered; Ruff would otherwise reformat Python blocks inside Markdown.
extend-exclude = ["htmlcov", "instance", "docs"]

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle errors and warnings
    "F",        # pyflakes: unused imports/variables, undefined names
    "I",        # import order
    "B",        # flake8-bugbear: likely bugs
    "UP",       # pyupgrade: modern syntax for Python 3.11+
    "S",        # flake8-bandit: security checks (Bandit itself is not a dependency)
    "BLE",      # blind `except Exception`
    "SIM",      # simplifications
    "C4",       # comprehensions
    "PIE",      # misc. unnecessary code
    "RUF",      # Ruff's own checks
]
ignore = [
    "E501",     # line length is the formatter's job; long URLs and SQL stay readable
    "RUF001", "RUF002", "RUF003",  # typographic characters (–, …, ×) are intended British UI text
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = [
    "S101",     # pytest uses plain assert
    "S105", "S106", "S107",  # fixed fake secrets in tests
    "S108",     # temporary paths in tests
    "S311",     # random test data, not cryptography
    "S603", "S607",  # tests run node with a fixed argument list
    "S608",     # test SQL interpolates fixed table names only (reviewed in Phase 7)
]
# Browser test modules import shared pytest fixtures from tests/browser_support.py
# by name; pytest then passes them in as arguments, which Ruff reports as F811.
"tests/test_browser*.py" = ["F811"]
"tests/test_chartjs_real.py" = ["F811"]
# Form-error messages keyed by password field names ("current_password":
# "is incorrect.") are not hard-coded passwords (S105 false positives).
"tradeledger/web/auth.py" = ["S105"]
"tradeledger/web/account.py" = ["S105"]
"tradeledger/web/reset.py" = ["S105"]
"tradeledger/web/two_step.py" = ["S105"]
"scripts/**" = [
    "S311",     # synthetic data, not cryptography
    "S603", "S607",  # the audit runs node with a fixed argument list
]

[tool.ruff.format]
quote-style = "double"
```

### `tests/browser_support.py` (MODIFIED)

**Why:** Live-server session cookies come with a server-side session row.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Shared helpers for the real-browser (Playwright) tests.

Imported by tests/test_browser*.py and tests/test_chartjs_real.py. Importing
this module skips the importing test file when Playwright is not installed,
so the rest of the suite is unaffected.

Chart.js and the three kinds of browser test (Phase 7)
------------------------------------------------------
A browser test is in exactly one of these groups (pytest markers):

  chartjs_stub     Chart.js is REPLACED by CHART_STUB below, a stand-in that
                   records `new Chart(canvas, config)` calls and draws
                   nothing. Proves OUR page, CSP and analytics.js work.
                   Proves nothing about how Chart.js draws.
  chartjs_offline  The Chart.js URL is blocked, as if the browser had no
                   internet: checks the text fallback.
  chartjs_real     Uses the REAL Chart.js file served from
                   tradeledger/static/vendor/chartjs/. Skipped (and reported
                   as skipped) unless that file is present.

Tests that never open the dashboard carry none of these markers; they
still get the stand-in, because a page might link to the dashboard.

Signing in (Phase 8)
--------------------
Every server has the default test account (conftest.TEST_EMAIL) holding the
sample data. page_factory(server=live_server) gives a page that is already
signed in: the server mints a real signed session cookie, exactly what the
sign-in form would set. The sign-in journey itself is tested through the
real form in tests/test_browser_auth.py.
"""

import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from tests.conftest import ensure_user, new_server_session  # noqa: E402
from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

WIDTHS = [375, 768, 1024, 1440]
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
LOCAL_CHART_JS = Path(__file__).resolve().parent.parent / "tradeledger/static/vendor/chartjs/chart.umd.min.js"

# Records chart configurations; draws nothing. Never shipped to users.
CHART_STUB = """
(function () {
    function Chart(canvas, config) {
        this.canvas = canvas;
        this.config = config;
        (window.__charts = window.__charts || []).push(this);
    }
    Chart.defaults = {font: {}};
    window.Chart = Chart;
})();
"""


class LiveServer(str):
    """The server's base URL (a str, so `server + "/trades"` works), plus
    what a browser needs to be signed in to it."""

    def __new__(cls, url, app):
        server = super().__new__(cls, url)
        server.app = app
        return server

    def session_cookie(self, user_id=1):
        """A valid signed session cookie for `user_id`, as /login would set."""
        now = int(time.time())
        key = new_server_session(self.app, user_id, now)  # Phase 8.5: server-side row too
        data = {"sid": key, "uid": user_id, "sv": 1, "auth_at": now, "seen_at": now, "_permanent": True}
        value = self.app.session_interface.get_signing_serializer(self.app).dumps(data)
        return {"name": self.app.config["SESSION_COOKIE_NAME"], "value": value, "url": str(self)}


def start_server(db_path, overrides=None, seed=True, prepare=None):
    """Run TradeLedger on a free local port in a background thread.

    The default test account (user 1) always exists; seed=True gives it
    the sample data. prepare: optional function called with the database
    connection afterwards, to add test-specific data.
    Returns (LiveServer, stop_function)."""
    app = create_app("testing", overrides={"DATABASE": db_path, **(overrides or {})})
    with app.app_context():
        init_schema(get_db())
        user_id = ensure_user(get_db())
        if seed:
            seed_sample_data(get_db(), user_id=user_id)
        if prepare:
            prepare(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        thread.join(timeout=5)
        app.extensions["tradeledger_db"].close()

    return LiveServer(f"http://127.0.0.1:{server.server_port}", app), stop


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A fresh server and database for each test MODULE."""
    url, stop = start_server(str(tmp_path_factory.mktemp("browser") / "browser.db"))
    yield url
    stop()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except playwright_api.Error as exc:  # browser binary not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page_factory(browser):
    """Create pages; every page records JavaScript errors and console errors
    (including Content-Security-Policy violations) in page.problems.

    chart_js: "stub" (default), "blocked" (offline) or "real" (no
    interception: the page loads whatever URL the server configured).
    server:   a LiveServer to be signed in to as the default test account;
              omit it for an anonymous visitor.
    """
    contexts = []

    def make(width=1440, height=900, chart_js="stub", javascript=True, server=None, **context_options):
        context = browser.new_context(
            viewport={"width": width, "height": height}, java_script_enabled=javascript, **context_options
        )
        contexts.append(context)
        if server is not None:
            context.add_cookies([server.session_cookie()])
        if chart_js == "stub":
            context.route(
                CHART_JS_URL,
                lambda route: route.fulfill(
                    status=200, content_type="application/javascript", body=CHART_STUB
                ),
            )
        elif chart_js == "blocked":
            context.route(CHART_JS_URL, lambda route: route.abort("internetdisconnected"))
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        page.on(
            "console",
            lambda msg: (
                page.problems.append(msg.text)
                if msg.type == "error" and "status of 4" not in msg.text
                else None
            ),
        )
        return page

    yield make
    for context in contexts:
        context.close()


def horizontal_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def submit_and_wait(page, selector="form[data-trade-form] button[type=submit]"):
    """Click a form's submit button and wait for the page that comes back."""
    with page.expect_navigation():
        page.locator(selector).click()
```

### `tests/conftest.py` (MODIFIED)

**Why:** sign_in() creates a session row; the bridge gets the production-settings test exemption at run time.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Shared pytest fixtures.

pytest loads this file automatically. Any test that names a fixture as a
parameter (app, client, conn, seeded_conn, ...) receives it.

Databases (Phase 8)
-------------------
By default every test gets a fresh SQLite file in pytest's tmp_path.
Set TRADELEDGER_TEST_DATABASE_URL to run the SAME tests against PostgreSQL:

    TRADELEDGER_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tradeledger_test

Each test then gets its own empty PostgreSQL SCHEMA (a separate namespace
inside that database), which is dropped afterwards. Tests never touch
instance/tradeledger.db or any real journal. Tests that only make sense for
one engine (SQLite file tricks, PostgreSQL pooling) carry the markers
sqlite_only / postgres_only and are skipped on the other.

TRADELEDGER_PG_DRIVER=libpq-bridge selects the test-only libpq bridge
(tests/pg_libpq_bridge.py) instead of psycopg; see that file for why.

Accounts (Phase 8)
------------------
Every app and connection fixture creates the default test account
(TEST_EMAIL, user id 1 in a fresh database). `client` is signed in as that
user AND sends that user's API token, so Phase 1-7 tests exercise the same
pages and endpoints as before, now through the real authentication checks.
`anon_client` is a browser that has not signed in.
"""

import os
import time
import uuid
from urllib.parse import quote

import pytest

from tradeledger import create_app
from tradeledger.db import connect, init_schema, register_postgres_driver
from tradeledger.passwords import HashParameters, hash_password
from tradeledger.repositories import api_tokens
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.users import create_user, get_user_by_email
from tradeledger.seed import seed_sample_data

TEST_EMAIL = "trader@example.com"
TEST_PASSWORD = "correct horse battery staple"
OTHER_EMAIL = "other.trader@example.com"
FAST_HASH = HashParameters(memory_kib=1024, iterations=1, lanes=1)  # same as TestingConfig

PG_URL = os.environ.get("TRADELEDGER_TEST_DATABASE_URL")
if PG_URL and os.environ.get("TRADELEDGER_PG_DRIVER") == "libpq-bridge":
    from tests import pg_libpq_bridge

    # Lets the production-settings tests run on PostgreSQL too; without it
    # the adapter refuses a test driver under the production configuration.
    pg_libpq_bridge.ALLOW_IN_PRODUCTION_CONFIG_TESTS = True
    register_postgres_driver(pg_libpq_bridge)

_HASH_CACHE = {}


def password_hash(password=TEST_PASSWORD):
    """Hashing is deliberately slow; reuse one hash per password in tests."""
    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = hash_password(password, FAST_HASH)
    return _HASH_CACHE[password]


def ensure_user(conn, email=TEST_EMAIL, password=TEST_PASSWORD):
    """Create the account if it does not exist yet; return its id."""
    existing = get_user_by_email(conn, email)
    if existing:
        return existing["id"]
    return create_user(conn, email, password_hash(password))


def pytest_collection_modifyitems(config, items):
    engine = "postgresql" if PG_URL else "sqlite"
    for item in items:
        if engine == "postgresql" and item.get_closest_marker("sqlite_only"):
            item.add_marker(pytest.mark.skip(reason="SQLite-specific test (running on PostgreSQL)"))
        if engine == "sqlite" and item.get_closest_marker("postgres_only"):
            item.add_marker(pytest.mark.skip(reason="needs TRADELEDGER_TEST_DATABASE_URL (PostgreSQL)"))


def _with_search_path(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(f'-csearch_path={schema}')}"


@pytest.fixture
def db_path(tmp_path):
    """Where this test's database lives: a SQLite file path, or (on
    PostgreSQL) a URL whose search_path points at a fresh, empty schema."""
    if not PG_URL:
        yield str(tmp_path / "test.db")
        return
    schema = "t_" + uuid.uuid4().hex[:16]
    admin = connect(PG_URL)
    admin.execute(f"CREATE SCHEMA {schema}")
    try:
        yield _with_search_path(PG_URL, schema)
    finally:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()


@pytest.fixture
def app(db_path):
    """A fresh app using the testing configuration and a temporary database,
    with the default test account already created."""
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        from tradeledger.db import get_db

        init_schema(get_db())
        ensure_user(get_db())
    yield app
    app.extensions["tradeledger_db"].close()


@pytest.fixture
def user_id():
    """The default test account's id (the first account in a fresh database)."""
    return 1


def new_server_session(app, user_id, now=None):
    """Create a user_sessions row (Phase 8.5) and return its cookie key.
    For an account that does not exist, return a key with no row (the
    server then refuses it, which is what such tests expect)."""
    from tradeledger.db import IntegrityError, get_db
    from tradeledger.repositories import sessions

    now = int(time.time()) if now is None else now
    with app.app_context():
        try:
            key, _ = sessions.create_session(
                get_db(),
                user_id=user_id,
                now=now,
                max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
                user_agent="pytest",
            )
        except IntegrityError:
            key = "no-such-session-" + uuid.uuid4().hex
    return key


def sign_in(client, user_id=1, session_version=1):
    """Mark a test client as signed in, exactly as a successful login does:
    a signed cookie plus a server-side session row (Phase 8.5)."""
    now = int(time.time())
    key = new_server_session(client.application, user_id, now)
    with client.session_transaction() as session:
        session.clear()
        session.permanent = True
        session.update({"sid": key, "uid": user_id, "sv": session_version, "auth_at": now, "seen_at": now})


def issue_token(app, user_id=1, name="test token"):
    from tradeledger.db import get_db

    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=user_id, name=name, lifetime_days=30, max_active=10
        )
    return token


@pytest.fixture
def client(app):
    """A browser signed in as the default test account, which also sends
    that account's API token on every request (for the JSON API tests)."""
    test_client = app.test_client()
    sign_in(test_client)
    test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {issue_token(app)}"
    return test_client


@pytest.fixture
def anon_client(app):
    """A browser that has not signed in and has no API token."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Runs Flask CLI commands (init-db, seed-db, ...) inside tests."""
    return app.test_cli_runner()


@pytest.fixture
def conn(db_path):
    """A plain connection to an empty, initialised temporary database that
    contains the default test account (and nothing else)."""
    connection = connect(db_path)
    init_schema(connection)
    ensure_user(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn, user_id):
    """The same temporary database, with the synthetic sample data in the
    default test account."""
    seed_sample_data(conn, user_id=user_id)
    return conn


@pytest.fixture
def eurusd_id(conn, user_id):
    """Creates one instrument for the default account and returns its id."""
    return create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=user_id,
    )


@pytest.fixture
def long_trade(eurusd_id):
    """Valid input for a closed LONG trade on EURUSD."""
    return {
        "instrument_id": eurusd_id,
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",  # Phase 4: closed trades record an exit time
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "status": "CLOSED",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Test trade",
    }


# ---------- Phase 4: API fixtures ----------


@pytest.fixture
def seeded_client(app, client, user_id):
    """A signed-in test client whose account holds the synthetic sample data."""
    from tradeledger.db import get_db

    with app.app_context():
        seed_sample_data(get_db(), user_id=user_id)
    return client


@pytest.fixture
def trade_body():
    """A valid POST /api/v1/trades body for sample instrument 1 (EURUSD).
    Same prices as sample trade 1: net +393 USD, realised R +1.965."""
    return {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:15:00Z",
        "closed_at": "2026-04-01T11:40:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "API test trade (synthetic)",
    }
```

### `tests/js/analytics.test.js` (MODIFIED)

**Why:** Test for formatAxisValue (no floating-point noise in axis labels).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```javascript
// Unit tests for tradeledger/static/js/analytics.js (Node's built-in test
// runner, no packages needed):   node --test tests/js/*.test.js
//
// They check the PURE parts: number conversion, chart configurations and
// tooltip text. The Playwright tests check the page wiring with a stand-in
// for Chart.js; Chart.js's own drawing is checked by hand (see the guide).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const charts = require(path.join(__dirname, "..", "..", "tradeledger", "static", "js", "analytics.js"));

const THEME = {
    text: "#e8eef8", muted: "#a3b1c7", grid: "#24334f", zero: "#8796af",
    accent: "#6aa8ff", positive: "#4cd79a", negative: "#ff8a8a", font: "system-ui"
};

// A payload shaped exactly like performance/dashboard.chart_payload().
const PAYLOAD = {
    curve: [
        {start: true, x: 1000, cumulative_r: "0", cumulative_text: "0.00R", underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 1, symbol: "TEST", x: 1000, when: "31 Dec 2025, 23:30 UTC", close_time_known: true,
            realised_r: "2", realised_text: "+2.00R", cumulative_r: "2", cumulative_text: "+2.00R",
            underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 3, symbol: "TEST", x: 2000, when: "02 Jan 2026, 09:00 UTC", close_time_known: false,
            realised_r: "-2", realised_text: "-2.00R", cumulative_r: "0", cumulative_text: "0.00R",
            underwater_r: "-2", drawdown_text: "2.00R"}
    ],
    max_drawdown: {amount: "2", text: "2.00R", peak_trade_id: 1, trough_trade_id: 3},
    monthly: [
        {key: "2025-12", label: "Dec 2025", trades: 1, wins: 1, losses: 0, total_r: "2", total_r_text: "+2.00R",
            win_rate_text: "100.0%", average_r_text: "+2.00R", expectancy_text: "+2.00R"},
        {key: "2026-01", label: "Jan 2026", trades: 0, wins: 0, losses: 0, total_r: null,
            total_r_text: "No closed trades", win_rate_text: "n/a", average_r_text: "n/a", expectancy_text: "n/a"}
    ],
    weekday: [],
    instruments: [
        {label: "TEST", small_sample: true, trades: 2, wins: 1, losses: 1, total_r: "0.333333333333333333333333",
            total_r_text: "+0.33R", win_rate_text: "50.0%", average_r_text: "+0.17R", expectancy_text: "+0.17R"},
        {label: "LOSS", small_sample: false, trades: 12, wins: 2, losses: 10, total_r: "-3.5",
            total_r_text: "-3.50R", win_rate_text: "16.7%", average_r_text: "-0.29R", expectancy_text: "-0.29R"}
    ],
    strategies: [],
    sessions: []
};

test("toNumber converts exact strings and refuses unsafe values", () => {
    assert.equal(charts.toNumber("7.68817307692307692307692"), 7.688173076923077);
    assert.equal(charts.toNumber("-1.5"), -1.5);
    assert.equal(charts.toNumber("0"), 0);
    for (const bad of [null, undefined, "", "abc", "Infinity", "-Infinity", "NaN", "1e400"]) {
        assert.equal(charts.toNumber(bad), null, String(bad));
    }
});

test("hexToRgba builds translucent colours and leaves other values alone", () => {
    assert.equal(charts.hexToRgba("#ff8a8a", 0.18), "rgba(255, 138, 138, 0.18)");
    assert.equal(charts.hexToRgba("red", 0.5), "red");
});

test("formatAxisValue removes floating-point noise from R tick labels", () => {
    assert.equal(charts.formatAxisValue(-0.20000000000000002), "-0.2R");
    assert.equal(charts.formatAxisValue(-0.6000000000000001), "-0.6R");
    assert.equal(charts.formatAxisValue(1.25), "1.25R");
    assert.equal(charts.formatAxisValue(-0.00000000000000004), "0R");
    assert.equal(charts.formatAxisValue(3), "3R");
    assert.equal(charts.formatAxisValue(NaN), "");
});

test("formatAxisDate shows the UTC date", () => {
    assert.equal(charts.formatAxisDate(Date.UTC(2026, 0, 2, 23, 59)), "2026-01-02");
    assert.equal(charts.formatAxisDate(NaN), "");
});

test("buildConfigs returns all seven charts", () => {
    const configs = charts.buildConfigs(PAYLOAD, THEME);
    assert.deepEqual(Object.keys(configs).sort(),
        ["curve", "drawdown", "instruments", "monthly", "sessions", "strategies", "weekday"]);
    for (const config of Object.values(configs)) {
        assert.equal(config.options.animation, false);
        assert.equal(config.options.responsive, true);
        assert.equal(config.options.maintainAspectRatio, false);
    }
});

test("cumulative R chart plots the exact values on a time axis", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).curve;
    assert.equal(config.type, "line");
    assert.equal(config.options.scales.x.type, "linear");
    assert.deepEqual(config.data.datasets[0].data, [{x: 1000, y: 0}, {x: 1000, y: 2}, {x: 2000, y: 0}]);
    assert.equal(config.options.scales.x.ticks.callback(Date.UTC(2026, 2, 7)), "2026-03-07");
});

test("cumulative R tooltip uses the server's text, including the legacy warning", () => {
    const callbacks = charts.buildConfigs(PAYLOAD, THEME).curve.options.plugins.tooltip.callbacks;
    assert.equal(callbacks.title([{dataIndex: 0}]), "Before the first trade");
    assert.equal(callbacks.title([{dataIndex: 2}]), "02 Jan 2026, 09:00 UTC");
    assert.deepEqual(callbacks.label({dataIndex: 1}),
        ["Trade #1 · TEST", "Trade R: +2.00R", "Cumulative R: +2.00R"]);
    assert.deepEqual(callbacks.label({dataIndex: 2}),
        ["Trade #3 · TEST", "Trade R: -2.00R", "Cumulative R: 0.00R",
            "Exit time not recorded: placed at entry time"]);
});

test("drawdown chart plots below zero and marks the maximum", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).drawdown;
    const dataset = config.data.datasets[0];
    assert.deepEqual(dataset.data.map((p) => p.y), [0, 0, -2]);
    assert.equal(dataset.fill, "origin");
    assert.deepEqual(dataset.pointStyle, ["circle", "circle", "triangle"]);
    assert.equal(dataset.pointRadius[2], 6);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 2});
    assert.ok(lines.includes("Maximum drawdown: 2.00R"));
    assert.equal(lines[0], "Drawdown: 2.00R below the running peak");
});

test("zero line is drawn stronger than other grid lines", () => {
    const grid = charts.buildConfigs(PAYLOAD, THEME).monthly.options.scales.y.grid;
    assert.equal(grid.color({tick: {value: 0}}), THEME.zero);
    assert.equal(grid.color({tick: {value: 1}}), THEME.grid);
    assert.equal(grid.lineWidth({tick: {value: 0}}), 2);
});

test("bar chart leaves empty periods blank instead of drawing zero", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).monthly;
    assert.deepEqual(config.data.labels, ["Dec 2025", "Jan 2026"]);
    assert.deepEqual(config.data.datasets[0].data, [2, null]);
    assert.deepEqual(config.options.plugins.tooltip.callbacks.label({dataIndex: 1}), ["No closed trades"]);
});

test("group charts are horizontal, coloured by sign and flag small samples", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).instruments;
    assert.equal(config.options.indexAxis, "y");
    assert.deepEqual(config.data.labels, ["TEST *", "LOSS"]);
    assert.deepEqual(config.data.datasets[0].backgroundColor, [THEME.positive, THEME.negative]);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 0});
    assert.deepEqual(lines, ["Total R: +0.33R", "Closed trades: 2 (won 1, lost 1)", "Win rate: 50.0%",
        "Average R: +0.17R", "Expectancy: +0.17R", "Small sample: treat with caution"]);
});

test("init without Chart.js shows the fallback message on every chart", () => {
    const made = [];
    const canvas = (name) => {
        const note = {hidden: true, textContent: ""};
        const box = {classList: {add: (c) => made.push(c)}, querySelector: () => note};
        return {name, note, hidden: false, getAttribute: () => name, closest: () => box};
    };
    const canvases = [canvas("curve"), canvas("monthly")];
    const doc = {
        getElementById: () => ({textContent: JSON.stringify(PAYLOAD)}),
        querySelectorAll: () => canvases
    };
    assert.deepEqual(charts.init(doc, undefined), []);
    for (const c of canvases) {
        assert.equal(c.hidden, true);
        assert.equal(c.note.hidden, false);
        assert.match(c.note.textContent, /could not be loaded/);
    }
    assert.deepEqual(made, ["is-unavailable", "is-unavailable"]);
});
```

### `tests/pg_libpq_bridge.py` (MODIFIED)

**Why:** Declares TEST_ONLY = True (required by the adapter).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""TEST-ONLY PostgreSQL driver: a thin ctypes bridge to libpq.

Why this file exists
--------------------
TradeLedger talks to PostgreSQL through psycopg 3 (see requirements.txt).
The environment Phase 8 was built in has a real PostgreSQL 16 server and
libpq (PostgreSQL's official C client library, the one psql uses), but no
way to install psycopg. Rather than leave the PostgreSQL code untested, the
test suite can register this bridge instead (TRADELEDGER_PG_DRIVER=libpq-bridge),
so every test runs SQL against a REAL PostgreSQL server.

What it is NOT
--------------
* It is not shipped or used by the application: tradeledger/ never imports it.
* It is not psycopg. It covers only the calls TradeLedger's adapter makes
  (connect, cursor().execute, fetchone/fetchall, description, rowcount,
  close) and converts results by type: integers -> int, NUMERIC ->
  Decimal, booleans -> bool, everything else -> str. Parameters are sent as
  text with explicit types for int, Decimal and bool, and "unknown" for
  strings (which is also psycopg's behaviour for str).
* Results obtained through it show the SQL is correct on PostgreSQL. They
  do not prove psycopg-specific behaviour or performance; the CI job with
  real psycopg is the authoritative PostgreSQL run.
"""

import ctypes
import ctypes.util
from decimal import Decimal

# Read by tradeledger.db.register_postgres_driver(): only modules that say
# they are test-only may replace psycopg, and production refuses them all.
TEST_ONLY = True

_lib = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")

_lib.PQconnectdb.restype = ctypes.c_void_p
_lib.PQconnectdb.argtypes = [ctypes.c_char_p]
_lib.PQstatus.argtypes = [ctypes.c_void_p]
_lib.PQerrorMessage.restype = ctypes.c_char_p
_lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQfinish.argtypes = [ctypes.c_void_p]
_lib.PQexec.restype = ctypes.c_void_p
_lib.PQexec.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.PQexecParams.restype = ctypes.c_void_p
_lib.PQexecParams.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
_lib.PQresultStatus.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorMessage.restype = ctypes.c_char_p
_lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorField.restype = ctypes.c_char_p
_lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQntuples.argtypes = [ctypes.c_void_p]
_lib.PQnfields.argtypes = [ctypes.c_void_p]
_lib.PQfname.restype = ctypes.c_char_p
_lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQftype.restype = ctypes.c_uint
_lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQgetvalue.restype = ctypes.c_char_p
_lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQcmdTuples.restype = ctypes.c_char_p
_lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
_lib.PQclear.argtypes = [ctypes.c_void_p]

_CONNECTION_OK = 0
_PGRES_COMMAND_OK, _PGRES_TUPLES_OK = 1, 2
_DIAG_SQLSTATE, _DIAG_CONSTRAINT = ord("C"), ord("n")

# PostgreSQL type OIDs
_BOOL, _INT8, _INT2, _INT4, _OID, _FLOAT4, _FLOAT8, _NUMERIC = 16, 20, 21, 23, 26, 700, 701, 1700


class Error(Exception):
    pass


class DatabaseError(Error):
    def __init__(self, message, sqlstate=None, constraint=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = type("Diag", (), {"constraint_name": constraint})()


class IntegrityError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class DataError(DatabaseError):
    pass


def _convert(text, oid):
    if oid in (_INT2, _INT4, _INT8, _OID):
        return int(text)
    if oid == _NUMERIC:
        return Decimal(text)
    if oid == _BOOL:
        return text == "t"
    if oid in (_FLOAT4, _FLOAT8):
        return float(text)
    return text


def _param(value):
    """(type oid, text) for one parameter; None means SQL NULL."""
    if value is None:
        return 0, None
    if isinstance(value, bool):
        return _BOOL, "t" if value else "f"
    if isinstance(value, int):
        return _INT8, str(value)
    if isinstance(value, Decimal):
        return _NUMERIC, str(value)
    if isinstance(value, float):
        return _FLOAT8, repr(value)
    if isinstance(value, str):
        return 0, value
    raise TypeError(f"the libpq test bridge cannot send {type(value).__name__}")


def _to_numbered(sql):
    """psycopg-style "%s" -> "$1", "$2", ...; "%%" -> "%"."""
    out, n, i = [], 0, 0
    while i < len(sql):
        char = sql[i]
        if char == "%" and i + 1 < len(sql):
            nxt = sql[i + 1]
            if nxt == "s":
                n += 1
                out.append(f"${n}")
                i += 2
                continue
            if nxt == "%":
                out.append("%")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


class Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql, params=None):
        handle = self._conn._handle
        if params is None:
            result = _lib.PQexec(handle, sql.encode())
        else:
            types, values = zip(*(_param(v) for v in params), strict=True) if params else ((), ())
            count = len(values)
            type_array = (ctypes.c_uint * count)(*types)
            value_array = (ctypes.c_char_p * count)(*(None if v is None else v.encode() for v in values))
            result = _lib.PQexecParams(
                handle, _to_numbered(sql).encode(), count, type_array, value_array, None, None, 0
            )
        try:
            status = _lib.PQresultStatus(result)
            if status not in (_PGRES_COMMAND_OK, _PGRES_TUPLES_OK):
                message = (_lib.PQresultErrorMessage(result) or b"").decode(errors="replace").strip()
                sqlstate = (_lib.PQresultErrorField(result, _DIAG_SQLSTATE) or b"").decode() or None
                constraint = (_lib.PQresultErrorField(result, _DIAG_CONSTRAINT) or b"").decode() or None
                state = sqlstate or ""
                cls = (
                    IntegrityError
                    if state.startswith("23")
                    else DataError
                    if state.startswith("22")
                    else DatabaseError
                )
                raise cls(message or "PostgreSQL error", sqlstate, constraint)
            if status == _PGRES_TUPLES_OK:
                fields = _lib.PQnfields(result)
                oids = [_lib.PQftype(result, c) for c in range(fields)]
                self.description = [(_lib.PQfname(result, c).decode(),) for c in range(fields)]
                self._rows = [
                    tuple(
                        None
                        if _lib.PQgetisnull(result, r, c)
                        else _convert(_lib.PQgetvalue(result, r, c).decode(), oids[c])
                        for c in range(fields)
                    )
                    for r in range(_lib.PQntuples(result))
                ]
                self.rowcount = len(self._rows)
            else:
                self.description = None
                self._rows = []
                affected = (_lib.PQcmdTuples(result) or b"").decode()
                self.rowcount = int(affected) if affected else -1
        finally:
            _lib.PQclear(result)
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class Connection:
    def __init__(self, conninfo):
        self._handle = _lib.PQconnectdb(conninfo.encode())
        if _lib.PQstatus(self._handle) != _CONNECTION_OK:
            message = (_lib.PQerrorMessage(self._handle) or b"").decode(errors="replace").strip()
            _lib.PQfinish(self._handle)
            self._handle = None
            raise OperationalError(message)

    def cursor(self):
        return Cursor(self)

    def close(self):
        if self._handle:
            _lib.PQfinish(self._handle)
            self._handle = None


def connect(conninfo, autocommit=True):
    """libpq runs each statement in its own transaction unless BEGIN is sent,
    which is exactly psycopg's autocommit=True behaviour."""
    if not autocommit:
        raise ValueError("the libpq test bridge only supports autocommit=True")
    return Connection(conninfo)
```

### `tests/test_auth.py` (MODIFIED)

**Why:** Route walk and public list include the two new public pages; absolute-lifetime test uses real requests; cookie-edit test.  
**How to run or test:** `python -m pytest tests/test_auth.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: signing in, signing out, registration, sessions and throttling.

These tests drive the real forms (GET the page for its CSRF token, POST
the form) with anonymous test clients, so every check runs exactly as it
would for a browser.
"""

import logging

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import count_rows, csrf_token
from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.passwords import OWASP_MINIMUM, stored_parameters
from tradeledger.repositories.users import bump_session_version, get_user, get_user_by_email, set_active
from tradeledger.web import auth

STRONG_KEY = "f00d" * 16


def login(client, email=TEST_EMAIL, password=TEST_PASSWORD, next_path=None, **extra):
    data = {"csrf_token": csrf_token(client, "/login"), "email": email, "password": password, **extra}
    if next_path is not None:
        data["next"] = next_path
    return client.post("/login", data=data)


def session_of(client):
    with client.session_transaction() as session:
        return dict(session)


# ---------- Every private page needs a signed-in user ----------


def test_every_route_is_private_unless_deliberately_public(app, anon_client):
    """Default deny: walk EVERY registered website URL. Anything not in
    PUBLIC_ENDPOINTS must redirect an anonymous visitor to sign-in, so a
    new route added later cannot be public by accident."""
    checked = 0
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("main."):
            continue
        url = rule.build(dict.fromkeys(rule.arguments, 1))[1]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = anon_client.open(url, method=method)
            if rule.endpoint == "main.login_two_step":
                # Phase 8.5: public, but useless without a pending sign-in
                # (a correct password first), so it sends visitors back to /login.
                assert response.status_code in (303, 400), (method, url)
                if response.status_code == 303:
                    assert response.headers["Location"] == "/login", (method, url)
            elif rule.endpoint in auth.PUBLIC_ENDPOINTS:
                # 200 page; 400 a POST without a CSRF token (or a reset form
                # without a valid link); 404 registration switched off
                assert response.status_code in (200, 400, 404), (method, url)
            else:
                assert response.status_code in (302, 303), (method, url, response.status_code)
                assert response.headers["Location"].startswith("/login"), (method, url)
            checked += 1
    assert checked >= 25


def test_public_endpoints_are_the_expected_small_set():
    # Phase 8.5 added the two-step sign-in page and the reset-link page.
    # Both are useless without a secret: a pending sign-in after a correct
    # password, or a one-time link (tests/test_two_step.py, test_password_reset.py).
    assert {
        "main.login",
        "main.login_two_step",
        "main.reset_password",
        "main.register",
        "main.health",
        "main.health_live",
        "main.health_ready",
        "main.about",
    } == auth.PUBLIC_ENDPOINTS


def test_redirect_to_sign_in_remembers_the_page(anon_client):
    response = anon_client.get("/trades?status=OPEN")
    assert response.headers["Location"] == "/login?next=/trades?status%3DOPEN"


# ---------- Sign in ----------


def test_successful_sign_in(app, anon_client):
    response = login(anon_client)
    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    session = session_of(anon_client)
    assert session["uid"] == 1 and session["sv"] == 1
    assert anon_client.get("/").status_code == 200
    with app.app_context():
        assert get_user(get_db(), 1)["last_login_at"] is not None


def test_email_is_case_and_space_insensitive(anon_client):
    assert login(anon_client, email="  Trader@EXAMPLE.com ").status_code == 303


@pytest.mark.parametrize(
    "email, password",
    [
        (TEST_EMAIL, "wrong password entirely"),  # right account, wrong password
        ("nobody@example.com", TEST_PASSWORD),  # unknown account
        ("", ""),  # empty form
        (TEST_EMAIL, ""),
    ],
)
def test_failed_sign_in_gives_one_generic_message(anon_client, email, password):
    response = login(anon_client, email=email, password=password)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Email or password is incorrect." in html
    for disclosure in ("not registered", "no account", "unknown", "wrong password"):
        assert disclosure not in html.lower()
    assert "uid" not in session_of(anon_client)


def test_disabled_account_gets_the_same_message(app, anon_client):
    with app.app_context():
        set_active(get_db(), 1, False)
    response = login(anon_client)
    assert "Email or password is incorrect." in response.get_data(as_text=True)
    assert "uid" not in session_of(anon_client)


def test_password_is_never_echoed_or_logged(anon_client, caplog):
    with caplog.at_level(logging.DEBUG):
        response = login(anon_client, password="Secret-Typed-Password-77")
    assert b"Secret-Typed-Password-77" not in response.data
    assert "Secret-Typed-Password-77" not in caplog.text
    assert TEST_EMAIL not in caplog.text  # failures log a keyed digest, not the address


def test_sign_in_without_csrf_token_is_refused(anon_client):
    response = anon_client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert response.status_code == 400
    assert "uid" not in session_of(anon_client)


def test_sign_in_with_another_browsers_token_is_refused(app, anon_client):
    other_token = csrf_token(app.test_client(), "/login")
    csrf_token(anon_client, "/login")  # this browser has its own token
    response = anon_client.post(
        "/login", data={"csrf_token": other_token, "email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 400


def test_session_is_replaced_at_sign_in(anon_client):
    """Session fixation: nothing planted before sign-in survives it,
    including the CSRF token."""
    with anon_client.session_transaction() as session:
        session["planted"] = "by an attacker"
    before = csrf_token(anon_client, "/login")
    login(anon_client)
    session = session_of(anon_client)
    assert "planted" not in session
    assert session.get("_csrf_token") != before


def test_signed_in_user_visiting_login_goes_home(client):
    response = client.get("/login")
    assert response.status_code == 302 and response.headers["Location"] == "/"


def test_old_password_hash_is_upgraded_after_sign_in(app, anon_client):
    """Hashes created with weaker settings are re-hashed with the current
    ones the next time the user signs in (here: test -> OWASP settings)."""
    app.config["PASSWORD_HASH_MEMORY_KIB"] = OWASP_MINIMUM.memory_kib
    app.config["PASSWORD_HASH_ITERATIONS"] = OWASP_MINIMUM.iterations
    login(anon_client)
    with app.app_context():
        stored = get_user(get_db(), 1)["password_hash"]
    assert stored_parameters(stored) == OWASP_MINIMUM


# ---------- Safe redirects after sign-in ----------


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/trades?status=OPEN", "/trades?status=OPEN"),
        ("/analytics", "/analytics"),
        ("https://evil.example/phish", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("/\tevil", "/"),
        ("javascript:alert(1)", "/"),
        ("", "/"),
    ],
)
def test_only_local_paths_are_followed_after_sign_in(anon_client, target, expected):
    response = login(anon_client, next_path=target)
    assert response.headers["Location"] == expected


# ---------- Throttling ----------


def test_account_is_throttled_after_five_failures(app, anon_client):
    for _ in range(5):
        login(anon_client, password="wrong password guess")
    blocked = login(anon_client)  # even the RIGHT password is refused now
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == str(app.config["LOGIN_WINDOW_SECONDS"])
    assert "Too many sign-in attempts" in blocked.get_data(as_text=True)
    assert "uid" not in session_of(anon_client)


def test_throttle_is_per_account(app, anon_client):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    for _ in range(5):
        login(anon_client, password="wrong password guess")
    assert login(app.test_client(), email=OTHER_EMAIL).status_code == 303


def test_ip_is_throttled_across_accounts(app):
    app.config["LOGIN_MAX_FAILURES_PER_IP"] = 3
    client = app.test_client()
    for n in range(3):
        login(client, email=f"guess{n}@example.com", password="wrong password guess")
    assert login(client).status_code == 429  # a different (real) account, same IP


def test_failures_expire_after_the_window(app, anon_client, monkeypatch):
    from datetime import timedelta

    from tradeledger.repositories import throttle

    for _ in range(5):
        login(anon_client, password="wrong password guess")
    assert login(anon_client).status_code == 429
    real_now = throttle.utc_now
    monkeypatch.setattr(
        throttle, "utc_now", lambda: real_now() + timedelta(seconds=app.config["LOGIN_WINDOW_SECONDS"] + 1)
    )
    assert login(anon_client).status_code == 303


def test_successful_sign_in_clears_the_account_counter(app, anon_client):
    for _ in range(4):
        login(anon_client, password="wrong password guess")
    assert login(anon_client).status_code == 303
    other = app.test_client()
    for _ in range(4):
        login(other, password="wrong password guess")
    assert login(other).status_code == 303  # 4 + 4 failures never reached 5 in a row


def test_throttle_table_stores_no_raw_emails_or_addresses(app, anon_client):
    login(anon_client, email="victim@example.com", password="wrong password guess")
    with app.app_context():
        rows = [dict(r) for r in get_db().execute("SELECT scope, key_digest FROM login_attempts")]
    assert {r["scope"] for r in rows} == {"login-email", "login-ip"}
    for row in rows:
        assert "victim" not in row["key_digest"] and "127.0.0.1" not in row["key_digest"]
        assert len(row["key_digest"]) == 64


# ---------- Sign out ----------


def test_sign_out_needs_post(client):
    assert client.get("/logout").status_code == 405
    assert client.get("/").status_code == 200  # still signed in


def test_sign_out_needs_a_csrf_token(client):
    assert client.post("/logout", data={}).status_code == 400
    assert client.get("/").status_code == 200


def test_sign_out_ends_the_session(client):
    token = csrf_token(client, "/")
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert "uid" not in session_of(client)
    assert client.get("/trades").status_code == 302


# ---------- Session lifetime and invalidation ----------


def test_absolute_session_lifetime(app, client, monkeypatch):
    # Phase 8 simulated "recently active" by editing seen_at inside the
    # cookie. Since Phase 8.5 activity is recorded on the SERVER (the
    # user_sessions row), so a forged cookie value no longer counts as
    # activity; the test now stays active through real requests instead.
    start = auth.now_seconds()
    max_age = app.config["SESSION_MAX_AGE_SECONDS"]
    step = app.config["SESSION_IDLE_TIMEOUT_SECONDS"] - 60
    moment = start
    while moment + step < max_age + start - 120:
        moment += step
        monkeypatch.setattr(auth, "now_seconds", lambda m=moment: m)
        assert client.get("/").status_code == 200
    monkeypatch.setattr(auth, "now_seconds", lambda: start + max_age - 120)
    assert client.get("/").status_code == 200  # active all along, still inside 8 hours
    monkeypatch.setattr(auth, "now_seconds", lambda: start + max_age + 1)
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    page = client.get(response.headers["Location"])
    assert "Your session has ended" in page.get_data(as_text=True)


def test_editing_activity_inside_the_cookie_does_not_extend_a_session(app, client, monkeypatch):
    """Phase 8.5: the idle limit is checked against the server's record, so
    a client that rewrites seen_at in its own cookie gains nothing."""
    start = auth.now_seconds()
    later = start + app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 1
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["seen_at"] = later - 5
    assert client.get("/").status_code == 302


def test_idle_timeout(app, client, monkeypatch):
    start = auth.now_seconds()
    monkeypatch.setattr(auth, "now_seconds", lambda: start + app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 1)
    assert client.get("/").status_code == 302


def test_activity_keeps_an_idle_session_alive(app, client, monkeypatch):
    start = auth.now_seconds()
    step = app.config["SESSION_IDLE_TIMEOUT_SECONDS"] - 60
    for n in (1, 2, 3):  # three visits, each just inside the idle limit
        monkeypatch.setattr(auth, "now_seconds", lambda n=n: start + n * step)
        assert client.get("/").status_code == 200


def test_raising_the_session_version_ends_every_session(app, client):
    with app.app_context():
        bump_session_version(get_db(), 1)
    assert client.get("/").status_code == 302


def test_a_tampered_cookie_is_ignored(app, anon_client):
    """The cookie is signed: changing uid without the key breaks it."""
    sign_in(anon_client, user_id=1)
    cookie = anon_client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    payload, _, signature = cookie.value.rpartition(".")
    anon_client.set_cookie(app.config["SESSION_COOKIE_NAME"], payload + "." + signature[::-1])
    assert anon_client.get("/").status_code == 302


def test_session_for_a_deleted_user_is_ended(app, anon_client):
    sign_in(anon_client, user_id=999)
    assert anon_client.get("/").status_code == 302
    assert "uid" not in session_of(anon_client)


# ---------- Cookie flags ----------


def production_app(db_path, **overrides):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path, **overrides})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


def test_production_session_cookie_flags(db_path):
    client = production_app(db_path).test_client()
    token = csrf_token_https(client)
    response = client.post(
        "/login",
        data={"csrf_token": token, "email": TEST_EMAIL, "password": TEST_PASSWORD},
        base_url="https://localhost",
    )
    assert response.status_code == 303
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("__Host-tradeledger_session=")
    for flag in ("Secure", "HttpOnly", "Path=/", "SameSite=Lax"):
        assert flag in cookie
    assert "Domain=" not in cookie  # required by the __Host- prefix
    assert "Expires=" in cookie  # a permanent session, ending with the 8-hour limit


def csrf_token_https(client):
    import re

    html = client.get("/login", base_url="https://localhost").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_production_registration_is_off_by_default(db_path):
    client = production_app(db_path).test_client()
    assert client.get("/register", base_url="https://localhost").status_code == 404
    page = client.get("/login", base_url="https://localhost").get_data(as_text=True)
    assert 'href="/register"' not in page


# ---------- Registration ----------


def register(client, email="new.trader@example.com", password="four unrelated words here", confirm=None):
    return client.post(
        "/register",
        data={
            "csrf_token": csrf_token(client, "/register"),
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def test_registration_creates_a_signed_in_account(app, anon_client):
    response = register(anon_client)
    assert response.status_code == 303 and response.headers["Location"] == "/instruments"
    with app.app_context():
        user = get_user_by_email(get_db(), "new.trader@example.com")
    assert user["password_hash"].startswith("$argon2id$")
    assert "four unrelated" not in user["password_hash"]
    assert session_of(anon_client)["uid"] == user["id"]
    page = anon_client.get("/instruments").get_data(as_text=True)
    assert "EURUSD" not in page  # a new account starts empty


@pytest.mark.parametrize(
    "email, password, confirm, field_message",
    [
        ("", "four unrelated words here", None, "Email address is required."),
        ("not-an-email", "four unrelated words here", None, "Email address must look like name@example.com."),
        ("a@b.co", "short", None, "Password must be at least 12 characters."),
        ("a@b.co", "password1234", None, "Password is too common"),
        ("a@b.co", "four unrelated words here", "different words here", "Confirm password does not match"),
        (TEST_EMAIL, "four unrelated words here", None, "Email address is already registered."),
    ],
)
def test_registration_validation(app, anon_client, email, password, confirm, field_message):
    before = count_rows(app, "users")
    response = register(anon_client, email, password, confirm)
    assert response.status_code == 400
    assert field_message in response.get_data(as_text=True)
    assert count_rows(app, "users") == before
    assert "uid" not in session_of(anon_client)


def test_registration_is_rate_limited_per_ip(app, anon_client):
    for n in range(app.config["REGISTRATIONS_PER_IP_PER_HOUR"]):
        register(anon_client, email=f"bot{n}@example.com")
        anon_client.post("/logout", data={"csrf_token": csrf_token(anon_client, "/")})
    response = register(anon_client, email="one.more@example.com")
    assert response.status_code == 429
    assert count_rows(app, "users") == 1 + app.config["REGISTRATIONS_PER_IP_PER_HOUR"]


def test_oversized_registration_is_rejected(app, anon_client):
    response = anon_client.post("/register", data={"email": "x" * (70 * 1024)})
    assert response.status_code == 413
    assert count_rows(app, "users") == 1


def test_registration_ignores_unexpected_fields(app, anon_client):
    """Extra form fields such as is_active=0 or session_version cannot be
    smuggled into the new account: only email and password are read."""
    anon_client.post(
        "/register",
        data={
            "csrf_token": csrf_token(anon_client, "/register"),
            "email": "smuggle@example.com",
            "password": "four unrelated words here",
            "confirm_password": "four unrelated words here",
            "is_active": "0",
            "session_version": "99",
            "id": "1",
        },
    )
    with app.app_context():
        user = get_user_by_email(get_db(), "smuggle@example.com")
    assert user["id"] == 2 and user["is_active"] and user["session_version"] == 1


def test_password_hash_never_appears_in_pages(app, client):
    with app.app_context():
        stored = get_user(get_db(), 1)["password_hash"]
    for url in ("/", "/account", "/trades", "/about"):
        assert stored.encode() not in client.get(url).data
```

### `tests/test_chartjs_real.py` (MODIFIED)

**Why:** Runs by default with the bundled file; new axis-label regression test.  
**How to run or test:** `python -m pytest tests/test_chartjs_real.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: REAL Chart.js rendering tests (marker: chartjs_real).

These are the only tests that can show the seven charts are actually drawn
by Chart.js. They need the genuine library file:

    tradeledger/static/vendor/chartjs/chart.umd.min.js   (Chart.js 4.5.1)
    tradeledger/static/vendor/chartjs/LICENSE.md          (its MIT licence)

Without those files every test here is SKIPPED, and pytest says so in its
summary ("skipped: real Chart.js file not installed"). A skip is never a
pass: see docs/PHASE_7_QA_REPORT.md for the current status. Setting
TRADELEDGER_REQUIRE_REAL_CHARTJS=1 (as CI does after downloading the file)
turns the skip into a FAILURE, so a missing file cannot pass silently.

Since Phase 8.5 the file ships with TradeLedger (built from the official
v4.5.1 source tag; see static/vendor/chartjs/BUILD.md), so these tests run
by default. CI also runs them against the official npm file.

What they check, with the library served from TradeLedger itself (CSP stays
"script-src 'self'") and its Subresource Integrity hash enabled:
  * the file really is Chart.js 4.5.1 and its licence is present;
  * the browser accepts the integrity hash and loads the library;
  * all seven charts exist as Chart.js instances and paint pixels;
  * chart data matches the server's JSON payload, point for point;
  * tooltips (Chart.js's own tooltip model) show the same text as the tables;
  * an empty month is a gap (null), weekdays run Monday to Sunday;
  * positive and negative bars get different colours; the maximum
    drawdown point is marked;
  * filters change the charts; resizing keeps charts inside their cards;
  * no console errors and no CSP violations.

Optional real screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    LOCAL_CHART_JS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.factories import USER_ID
from tradeledger.repositories.trades import create_trade

EXPECTED_VERSION = "4.5.1"
LICENCE = LOCAL_CHART_JS.parent / "LICENSE.md"
LOCAL_URL = "/static/vendor/chartjs/chart.umd.min.js"
CHART_NAMES = ["curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"]

REQUIRED = os.environ.get("TRADELEDGER_REQUIRE_REAL_CHARTJS") == "1"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.chartjs_real,
    pytest.mark.skipif(
        not LOCAL_CHART_JS.exists() and not REQUIRED, reason="real Chart.js file not installed"
    ),
]


def add_january_trade(conn):
    """One extra LOSING trade in January 2026, in its own strategy "Fade".
    February becomes an empty month between January and the March sample
    data (a gap, not a zero), and the strategy chart gets a negative bar
    (every sample strategy has a positive total)."""
    create_trade(
        conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-01-14T09:00:00Z",
            "closed_at": "2026-01-14T15:00:00Z",
            "entry_price": "1.1000",
            "stop_loss": "1.0950",
            "exit_price": "1.0975",
            "position_size": "1",
            "strategy": "Fade",
            "session": "ASIA",
        },
        user_id=USER_ID,
    )


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    digest = hashlib.sha384(LOCAL_CHART_JS.read_bytes()).digest()
    integrity = "sha384-" + base64.b64encode(digest).decode()
    url, stop = start_server(
        str(tmp_path_factory.mktemp("chartjs") / "real.db"),
        overrides={"CHART_JS_URL": LOCAL_URL, "CHART_JS_INTEGRITY": integrity},
        prepare=add_january_trade,
    )
    yield url
    stop()


def open_dashboard(page_factory, real_server, query="", width=1440):
    page = page_factory(width, 900, chart_js="real", server=real_server)
    response = page.goto(real_server + "/analytics" + (f"?{query}" if query else ""))
    page.wait_for_function(
        "window.Chart && document.querySelectorAll('canvas[data-chart]').length === 7 && "
        "[...document.querySelectorAll('canvas[data-chart]')].every(c => window.Chart.getChart(c))"
    )
    return page, response


def payload(page):
    return json.loads(page.locator("#dashboard-data").inner_text())


def chart_eval(page, name, script):
    """Run `script` (a JS function body using `chart`) on one chart."""
    return page.evaluate(
        f"""(name) => {{ const chart = Chart.getChart(document.querySelector(`canvas[data-chart="${{name}}"]`));
                        {script} }}""",
        name,
    )


# ---------- The file itself ----------


def test_library_file_is_chart_js_4_5_1_with_its_licence():
    header = LOCAL_CHART_JS.read_text(encoding="utf-8", errors="replace")[:400]
    assert f"Chart.js v{EXPECTED_VERSION}" in header, header[:120]
    assert LICENCE.exists(), "LICENSE.md must sit next to the library file"
    assert "MIT" in LICENCE.read_text(encoding="utf-8")


def test_dashboard_loads_the_self_hosted_file_under_a_strict_policy(real_server, page_factory):
    page, response = open_dashboard(page_factory, real_server)
    csp = response.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp
    tag = page.locator(f'script[src="{LOCAL_URL}"]')
    assert tag.get_attribute("integrity").startswith("sha384-")
    assert page.evaluate("Chart.version") == EXPECTED_VERSION
    assert page.problems == []  # no console errors, no CSP violations


# ---------- Drawing ----------


def test_all_seven_charts_draw(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    expected_points = {
        "curve": len(data["curve"]),
        "drawdown": len(data["curve"]),
        "monthly": len(data["monthly"]),
        "weekday": len(data["weekday"]),
        "instruments": len(data["instruments"]),
        "strategies": len(data["strategies"]),
        "sessions": len(data["sessions"]),
    }
    for name in CHART_NAMES:
        info = chart_eval(
            page,
            name,
            """const c = chart.canvas, ctx = c.getContext('2d');
               const pixels = ctx.getImageData(0, 0, c.width, c.height).data;
               let painted = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) painted++;
               return {points: chart.data.datasets[0].data.length, painted, width: chart.width};""",
        )
        assert info["points"] == expected_points[name], name
        assert info["painted"] > 500, f"{name} painted only {info['painted']} pixels"
        assert info["width"] > 100, name
    assert page.problems == []


def test_chart_values_match_the_server_payload(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    curve = chart_eval(page, "curve", "return chart.data.datasets[0].data.map(p => p.y);")
    assert curve == [float(p["cumulative_r"]) for p in data["curve"]]
    for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
        values = chart_eval(page, name, "return chart.data.datasets[0].data;")
        assert values == [None if r["total_r"] is None else float(r["total_r"]) for r in data[name]], name


def test_empty_month_is_a_gap_and_weekdays_run_monday_to_sunday(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    monthly = chart_eval(
        page, "monthly", "return {labels: chart.data.labels, data: chart.data.datasets[0].data};"
    )
    assert monthly["labels"] == ["Jan 2026", "Feb 2026", "Mar 2026"]
    assert monthly["data"][1] is None  # February: no closed trades, not 0
    weekday = chart_eval(page, "weekday", "return chart.data.labels;")
    assert weekday == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def test_signs_have_different_colours_and_the_maximum_drawdown_is_marked(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    bars = chart_eval(
        page,
        "strategies",
        "return {data: chart.data.datasets[0].data, colours: chart.data.datasets[0].backgroundColor};",
    )
    positive = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v > 0}
    negative = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v < 0}
    assert positive and negative and positive.isdisjoint(negative)
    drawdown = chart_eval(
        page,
        "drawdown",
        "const d = chart.data.datasets[0]; return {y: d.data.map(p => p.y), styles: d.pointStyle};",
    )
    assert all(y <= 0 for y in drawdown["y"])
    deepest = drawdown["y"].index(min(drawdown["y"]))
    assert drawdown["styles"][deepest] == "triangle"


# ---------- Tooltips agree with the tables ----------


def test_chart_js_tooltips_show_the_table_figures(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for name in ("instruments", "strategies", "sessions"):
        rows = payload(page)[name]
        for index, row in enumerate(rows):
            lines = chart_eval(
                page,
                name,
                f"""chart.tooltip.setActiveElements([{{datasetIndex: 0, index: {index}}}], {{x: 0, y: 0}});
                    chart.update();
                    return chart.tooltip.body.flatMap(b => b.lines);""",
            )
            table_row = page.locator(f"#table-{name} tbody tr", has_text=row["label"]).first
            table_total = table_row.locator("td").nth(5).inner_text().strip()
            assert lines[0] == f"Total R: {table_total}", (name, row["label"])
    assert page.problems == []


# ---------- Filters, resizing ----------


def test_filters_change_the_charts(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server, "strategy=Breakout&session=LONDON")
    points = chart_eval(page, "curve", "return chart.data.datasets[0].data.length;")
    assert points == 4  # the start point plus sample trades 1, 3 and 10


def test_resizing_keeps_charts_inside_their_cards(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for width in (1440, 768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(300)  # Chart.js resizes on its ResizeObserver callback
        assert horizontal_overflow(page) == 0, width
        for name in CHART_NAMES:
            fits = chart_eval(
                page,
                name,
                """const box = chart.canvas.closest('[data-chart-box]').getBoundingClientRect();
                   const c = chart.canvas.getBoundingClientRect();
                   return c.width > 0 && c.right <= box.right + 1 && c.left >= box.left - 1;""",
            )
            assert fits, (width, name)


def test_axis_labels_have_no_floating_point_noise(real_server, page_factory):
    """Phase 8.5 regression: the drawdown axis read "-0.20000000000000002R".
    Every R-axis label is now a short, rounded number."""
    page, _ = open_dashboard(page_factory, real_server)
    for name in CHART_NAMES:
        labels = chart_eval(
            page,
            name,
            "const axes = Object.values(chart.scales).filter(s => s.options.title && "
            "/risk multiples/.test(s.options.title.text)); "
            "return axes.flatMap(s => s.ticks.map(t => String(t.label)));",
        )
        assert labels, name
        for label in labels:
            assert label.endswith("R") and len(label) <= 8, (name, label)
            assert label != "-0R", (name, label)


# ---------- Optional real screenshots ----------


@pytest.mark.skipif(
    not os.environ.get("TRADELEDGER_SCREENSHOT_DIR"), reason="TRADELEDGER_SCREENSHOT_DIR not set"
)
def test_capture_real_chart_screenshots(real_server, page_factory):
    folder = Path(os.environ["TRADELEDGER_SCREENSHOT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    for width, name in ((1440, "chartjs_real_desktop_1440.png"), (375, "chartjs_real_mobile_375.png")):
        page, _ = open_dashboard(page_factory, real_server, width=width)
        page.screenshot(path=str(folder / name), full_page=True)
        assert (folder / name).stat().st_size > 10_000
```

### `tests/test_db.py` (MODIFIED)

**Why:** Schema version 4.  
**How to run or test:** `python -m pytest tests/test_db.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Database setup, Flask integration, constraints and transactions.

Phase 8: these tests run on SQLite by default and on PostgreSQL when
TRADELEDGER_TEST_DATABASE_URL is set (see conftest.py). Errors are checked
as tradeledger.db.IntegrityError, the adapter's engine-neutral class, which
replaced sqlite3.IntegrityError in the Phase 7 version of this file.
"""

import pytest
from flask import g

from tests.factories import USER_ID
from tradeledger import create_app
from tradeledger.db import (
    DataError,
    IntegrityError,
    connect,
    get_db,
    init_schema,
    list_tables,
    schema_version,
)
from tradeledger.migrations import LATEST_VERSION

# ---------- Initialisation ----------


def index_names(conn):
    if conn.dialect == "postgresql":
        sql = "SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()"
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'index'"
    return {row["name"] for row in conn.execute(sql)}


def test_every_table_is_created(conn):
    assert {"instruments", "trades", "users", "api_tokens", "login_attempts"} <= set(list_tables(conn))


def test_indexes_are_created(conn):
    assert {"idx_trades_instrument_id", "idx_trades_user_date", "idx_trades_user_closed"} <= index_names(conn)


def test_schema_version_is_recorded(conn):
    # Was "== 1" in Phase 2, "== 2" in Phase 4-7 and "== 3" in Phase 8.
    # Phase 8.5 added migration 4 (sessions, reset links, two-step).
    assert schema_version(conn) == LATEST_VERSION == 4


def test_init_schema_twice_keeps_existing_data(conn, eurusd_id):
    init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


@pytest.mark.sqlite_only
def test_foreign_keys_are_switched_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---------- Flask integration ----------


def test_testing_config_without_database_path_refuses_to_start():
    with pytest.raises(RuntimeError, match="DATABASE"):
        create_app("testing")


def test_development_database_defaults_to_instance_folder(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "DATABASE", None)
    monkeypatch.setattr(DevelopmentConfig, "DATABASE_URL", None)
    monkeypatch.setattr(DevelopmentConfig, "DB_HOST", None)
    app = create_app("development")
    assert app.config["DATABASE"].endswith("tradeledger.db")
    assert "instance" in app.config["DATABASE"]


def test_get_db_reuses_one_connection_per_app_context(app):
    with app.app_context():
        assert get_db() is get_db()


def test_connection_is_released_when_app_context_ends(app):
    with app.app_context():
        conn = get_db()
    # SQLite: the connection is closed. PostgreSQL: it went back to the pool
    # and is no longer this request's. Either way, using it again is a bug
    # that must fail loudly rather than share a connection between requests.
    if conn.dialect == "sqlite":
        with pytest.raises(Exception, match="closed"):
            conn.execute("SELECT 1")


def test_connection_is_removed_from_g_after_teardown(app):
    with app.app_context():
        get_db()
        assert "db" in g
    with app.app_context():
        assert "db" not in g


# ---------- Constraints enforced by SQL itself ----------
# These bypass Python validation on purpose (raw INSERTs) to prove the
# database is a safety net even if application code has a bug.

RAW_TRADE_SQL = """
INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss,
                    exit_price, position_size, fees, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

VALID_RAW = [USER_ID, None, "2026-03-02T08:15:00Z", "LONG", "1.085", "1.083", "1.089", "1", "7", "CLOSED"]
POSITIONS = {
    "user_id": 0,
    "trade_date": 2,
    "direction": 3,
    "entry_price": 4,
    "stop_loss": 5,
    "exit_price": 6,
    "position_size": 7,
    "fees": 8,
    "status": 9,
}


def raw_trade(instrument_id, **changes):
    values = list(VALID_RAW)
    values[1] = instrument_id
    for field, value in changes.items():
        values[POSITIONS[field]] = value
    return values


def test_sql_accepts_a_valid_raw_trade(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))


def test_sql_rejects_unknown_instrument(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(999))
    assert info.value.kind == "foreign_key"


def test_sql_rejects_another_users_instrument(conn, eurusd_id):
    """Phase 8: the two-column foreign key (instrument_id, user_id) makes the
    database refuse a trade on someone else's instrument, even if the
    application forgot to check."""
    from tests.conftest import OTHER_EMAIL, ensure_user

    other = ensure_user(conn, OTHER_EMAIL)
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, user_id=other))
    assert info.value.kind == "foreign_key"


def test_sql_refuses_to_delete_instrument_that_has_trades(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))
    with pytest.raises(IntegrityError) as info:
        conn.execute("DELETE FROM instruments WHERE id = ?", (eurusd_id,))
    assert info.value.kind == "foreign_key"


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "BUY"},  # invalid direction
        {"status": "PENDING"},  # invalid status
        {"status": "CLOSED", "exit_price": None},  # closed without exit
        {"status": "OPEN"},  # open WITH an exit price
        {"stop_loss": "1.090"},  # long stop above entry
        {"stop_loss": "1.085"},  # stop equal to entry
        {"position_size": "0"},  # zero size
        {"position_size": "-1"},  # negative size
        {"entry_price": "abc"},  # not a number
        {"fees": "-2"},  # negative fees
        {"trade_date": "02/03/2026 08:15"},  # wrong date format
        {"user_id": None},  # no owner (PostgreSQL: NOT NULL; SQLite: legacy-only, see below)
    ],
)
def test_sql_rejects_invalid_raw_trades(conn, eurusd_id, changes):
    if changes == {"user_id": None} and conn.dialect == "sqlite":
        pytest.skip("SQLite allows NULL owners for unassigned pre-Phase 8 rows (migrations.py)")
    # PostgreSQL rejects "abc" as invalid NUMERIC input (a data error)
    # before any CHECK runs; SQLite stores text, so its CHECK rejects it.
    with pytest.raises((IntegrityError, DataError)):
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, **changes))


def test_sql_rejects_short_with_stop_below_entry(conn, eurusd_id):
    with pytest.raises(IntegrityError, match="stop_on_correct_side"):
        conn.execute(
            RAW_TRADE_SQL,
            raw_trade(eurusd_id, direction="SHORT", stop_loss="1.080", exit_price="1.080"),
        )


def test_sql_rejects_lowercase_symbol(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "eurusd", "x", "FOREX", "0.00001", "100000", "USD"),
        )
    assert info.value.kind == "check"


# ---------- Transactions ----------


def test_transaction_commits_on_success(db_path, conn, eurusd_id):
    other = connect(db_path)  # a second connection only sees committed data
    try:
        assert other.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1
    finally:
        other.close()


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError), conn:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "GBPUSD", "test", "FOREX", "0.00001", "100000", "USD"),
        )
        raise RuntimeError("something failed half-way")
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 0


def test_inner_failure_rolls_back_only_its_savepoint(conn):
    """A nested `with conn:` is a SAVEPOINT: an inner failure that the code
    handles undoes only the inner work, on both databases."""
    insert = (
        "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
        "contract_multiplier, quote_currency) VALUES (?, ?, 'x', 'FOREX', '0.1', '1', 'USD')"
    )
    with conn:
        conn.execute(insert, (USER_ID, "KEEP"))
        with pytest.raises(IntegrityError), conn:
            conn.execute(insert, (USER_ID, "KEEP"))  # duplicate symbol: fails inside the savepoint
    symbols = [row["symbol"] for row in conn.execute("SELECT symbol FROM instruments")]
    assert symbols == ["KEEP"]


def test_placeholders_inside_literals_and_percent_signs_are_preserved(conn):
    row = conn.execute("SELECT '?' AS q, '50%' AS pct, ? AS bound", ("a?b",)).fetchone()
    assert (row["q"], row["pct"], row["bound"]) == ("?", "50%", "a?b")


def test_named_parameters_are_refused(conn):
    with pytest.raises(TypeError, match="positional"):
        conn.execute("SELECT :x", {"x": 1})
```

### `tests/test_legacy_and_cli_accounts.py` (MODIFIED)

**Why:** Upgrades now apply [3, 4].  
**How to run or test:** `python -m pytest tests/test_legacy_and_cli_accounts.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: upgrading a Phase 7 database, claiming legacy records, and the
account commands (create-user, set-password, deactivate-user, ...).

The Phase 7 database is built by tests/fixtures/phase7_schema_v2.sql plus
the sample data inserted with Phase 7-shaped SQL (no users, no owners),
so the upgrade path tested here is the one an existing user takes.
"""

import sqlite3

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data, legacy_counts
from tradeledger.repositories import trades as trade_repo
from tradeledger.repositories.users import get_user_by_email

pytestmark = pytest.mark.sqlite_only  # PostgreSQL databases never had unowned rows


def snapshot(path, table):
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    rows = [dict(r) for r in raw.execute(f"SELECT * FROM {table} ORDER BY id")]
    raw.close()
    return rows


@pytest.fixture
def phase7_db(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


@pytest.fixture
def open_db(phase7_db):
    """Open connections to the Phase 7 file; all are closed after the test."""
    opened = []

    def open_connection():
        conn = connect(phase7_db)
        opened.append(conn)
        return conn

    yield open_connection
    for conn in opened:
        conn.close()


# ---------- Upgrading keeps every value ----------


def test_upgrade_from_phase_7_preserves_every_row(phase7_db, open_db):
    trades_before, instruments_before = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    conn = open_db()
    assert schema_version(conn) == 2
    assert init_schema(conn) == [3, 4]  # Phase 8.5 added migration 4 (new tables only)
    conn.close()
    trades_after, instruments_after = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before  # same ids, same text, same timestamps
    for before, after in zip(instruments_before, instruments_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before


def test_upgrade_keeps_foreign_keys_and_rules(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    assert conn.raw.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    triggers = {r[0] for r in conn.raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert triggers == {"trades_closed_at_rules_insert", "trades_closed_at_rules_update"}


def test_unassigned_records_are_invisible_to_every_account(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    first = ensure_user(conn)  # even the FIRST account gets nothing automatically
    assert trade_repo.list_trades(conn, user_id=first) == []
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- Claiming ----------


def test_dry_run_changes_nothing(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    summary = claim_legacy_data(conn, user_id=ensure_user(conn), apply=False)
    assert summary == {"instruments": 5, "trades": 16, "conflicts": [], "applied": False}
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


def test_apply_assigns_everything_in_one_go(phase7_db, open_db):
    before = snapshot(phase7_db, "trades")
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    assert claim_legacy_data(conn, user_id=owner, apply=True)["applied"]
    assert legacy_counts(conn) == {"instruments": 0, "trades": 0}
    assert len(trade_repo.list_trades(conn, user_id=owner)) == 16
    conn.close()
    after = snapshot(phase7_db, "trades")
    assert [{**r, "user_id": None} for r in after] == [{**r, "user_id": None} for r in before]
    assert {r["user_id"] for r in after} == {owner}


def test_symbol_conflict_blocks_the_claim(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "already mine",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=owner,
    )
    summary = claim_legacy_data(conn, user_id=owner, apply=True)
    assert summary["applied"] is False
    assert summary["conflicts"] == ["the account already has an instrument with symbol EURUSD"]
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- The commands ----------


def app_for(path):
    app = create_app("testing", overrides={"DATABASE": path})
    return app, app.test_cli_runner()


def test_migrate_db_command_backs_up_and_reports_unassigned_records(phase7_db, tmp_path):
    _app, runner = app_for(phase7_db)
    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 2 -> 4" in result.output
    assert "16 trade(s) are unassigned" in result.output
    backups = list(tmp_path.glob("phase7.db.v2-*.bak"))
    assert len(backups) == 1
    assert (
        snapshot(str(backups[0]), "trades")[0].get("user_id", "absent") == "absent"
    )  # the untouched v2 copy


def test_claim_command_dry_run_then_apply(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    created = runner.invoke(
        args=["create-user", "--email", "Owner@Example.com", "--password-stdin"],
        input="my own long passphrase\n",
    )
    assert created.exit_code == 0, created.output
    dry = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com"])
    assert "Dry run: nothing changed" in dry.output
    applied = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "every unassigned record now belongs to owner@example.com" in applied.output
    again = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "Nothing to assign." in again.output


def test_claim_command_for_unknown_account_fails(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    result = runner.invoke(args=["claim-legacy-data", "--email", "ghost@example.com", "--apply"])
    assert result.exit_code != 0
    assert "No account with email ghost@example.com" in result.output


def test_create_user_rejects_weak_passwords_and_duplicates(app, runner):
    weak = runner.invoke(args=["create-user", "--email", "a@b.co", "--password-stdin"], input="short\n")
    assert weak.exit_code != 0 and "Password must be at least 12 characters" in weak.output
    dup = runner.invoke(
        args=["create-user", "--email", TEST_EMAIL, "--password-stdin"], input="a perfectly long one\n"
    )
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_create_user_prompt_asks_twice_and_hides_input(app, runner):
    mismatch = runner.invoke(
        args=["create-user", "--email", "p@example.com"], input="first long password\nsecond long password\n"
    )
    assert mismatch.exit_code != 0
    assert "first long password" not in mismatch.output  # hidden input is not echoed


def test_set_password_ends_existing_sessions(app, runner, client):
    result = runner.invoke(
        args=["set-password", "--email", TEST_EMAIL, "--password-stdin"], input="replacement passphrase\n"
    )
    assert result.exit_code == 0, result.output
    assert client.get("/").status_code == 302  # signed out


def test_deactivate_blocks_sign_in_and_tokens(app, runner, client):
    assert runner.invoke(args=["deactivate-user", "--email", TEST_EMAIL]).exit_code == 0
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/trades").status_code == 401
    assert runner.invoke(args=["activate-user", "--email", TEST_EMAIL]).exit_code == 0
    with app.app_context():
        assert get_user_by_email(get_db(), TEST_EMAIL)["is_active"]


def test_list_users_never_prints_hashes(app, runner):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    output = runner.invoke(args=["list-users"]).output
    assert TEST_EMAIL in output and OTHER_EMAIL in output
    assert "$argon2id$" not in output and "password" not in output.lower()


def test_reset_db_is_refused_in_production(db_path):
    app = create_app("production", overrides={"SECRET_KEY": "e" * 64, "DATABASE": db_path})
    result = app.test_cli_runner().invoke(args=["reset-db", "--yes"])
    assert result.exit_code != 0
    assert "reset-db is refused in production" in result.output
```

### `tests/test_migrations.py` (MODIFIED)

**Why:** Version 4; failing-migration test uses number 5.  
**How to run or test:** `python -m pytest tests/test_migrations.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Schema migration: a real Phase 3 (version 1) database upgraded to the latest version.

tests/fixtures/schema_v1.sql is an exact copy of the Phase 3 schema.sql,
so these tests start from the same structure an existing user has.

Phase 8 changes (the requirement changed, the checks did not weaken):
* The latest version is now 4 (Phase 8.5 added migration 4, which only
  creates new tables and columns), so a version 1 database receives
  migrations [2, 3, 4] and ends on version 4.
* After migration 3 the old records are UNASSIGNED (user_id NULL) and
  invisible to every account. Tests that read them through the
  repositories first create the test account and claim the records with
  the same function the claim-legacy-data command uses.
* Raw SQL errors are checked as tradeledger.db.IntegrityError.
SQLite-only: PostgreSQL has no Phase 3 history (see migrations.py); the
trigger tests at the end run on both engines.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import ensure_user
from tests.factories import USER_ID
from tradeledger import create_app, migrations
from tradeledger.db import IntegrityError, connect, drop_all, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data
from tradeledger.performance import build_report
from tradeledger.repositories.trades import create_trade, get_trade, update_trade
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES
from tradeledger.validation import ValidationError, validate_instrument, validate_trade

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8")

V1_TRADE_COLUMNS = (
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)


def build_v1_database(path):
    """Create a Phase 3 database holding the 16 sample trades WITHOUT exit
    times, exactly as an existing user's database looks."""
    conn = connect(path)
    conn.executescript(V1_SCHEMA)
    raw_conn = sqlite3.connect(path)  # the Phase 3 code used named parameters
    raw_conn.row_factory = sqlite3.Row
    with raw_conn:
        for raw in SAMPLE_INSTRUMENTS:
            item = validate_instrument(raw)
            raw_conn.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
                "contract_multiplier, quote_currency) VALUES (:symbol, :name, :asset_class, "
                ":price_increment, :contract_multiplier, :quote_currency)",
                item,
            )
        ids = {r["symbol"]: r["id"] for r in raw_conn.execute("SELECT id, symbol FROM instruments")}
        for raw in SAMPLE_TRADES:
            data = {k: v for k, v in raw.items() if k not in ("symbol", "closed_at")}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data, require_closed_at=False)
            raw_conn.execute(
                f"INSERT INTO trades ({', '.join(V1_TRADE_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in V1_TRADE_COLUMNS)})",
                trade,
            )
    raw_conn.close()
    return conn


def migrate_and_claim(conn):
    """Upgrade, create the test account and give it the legacy records."""
    applied = init_schema(conn)
    user_id = ensure_user(conn)
    assert user_id == USER_ID
    assert claim_legacy_data(conn, user_id=user_id, apply=True)["applied"]
    return applied


def columns(conn, table):
    if conn.dialect == "postgresql":
        sql = "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()"
        return [row["name"] for row in conn.execute(sql, (table,))]
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture
def v1_conn(tmp_path, request):
    if request.node.get_closest_marker("sqlite_only") is None:
        pytest.fail("v1_conn tests must be marked sqlite_only")
    conn = build_v1_database(str(tmp_path / "phase3.db"))
    yield conn
    conn.close()


# ---------- The starting point really is Phase 3 ----------


@pytest.mark.sqlite_only
def test_fixture_is_a_version_1_database(v1_conn):
    assert schema_version(v1_conn) == 1
    assert "closed_at" not in columns(v1_conn, "trades")
    assert v1_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16


# ---------- Migrating ----------


@pytest.mark.sqlite_only
def test_migration_adds_closed_at_and_bumps_version(v1_conn):
    applied = init_schema(v1_conn)
    assert applied == [2, 3, 4]
    assert schema_version(v1_conn) == 4
    assert "closed_at" in columns(v1_conn, "trades")
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_trades_closed_at", "trades_closed_at_rules_insert", "trades_closed_at_rules_update"} <= names


@pytest.mark.sqlite_only
def test_migration_preserves_every_existing_value(v1_conn):
    trades_before = rows(v1_conn, "trades")
    instruments_before = rows(v1_conn, "instruments")
    init_schema(v1_conn)
    trades_after = rows(v1_conn, "trades")
    assert [
        {k: v for k, v in r.items() if k != "user_id"} for r in rows(v1_conn, "instruments")
    ] == instruments_before
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert {k: v for k, v in after.items() if k not in ("closed_at", "user_id")} == before
        assert after["closed_at"] is None  # never fabricated
        assert after["user_id"] is None  # never guessed (Phase 8)


@pytest.mark.sqlite_only
def test_migration_is_idempotent(v1_conn):
    assert init_schema(v1_conn) == [2, 3, 4]
    snapshot = rows(v1_conn, "trades")
    assert init_schema(v1_conn) == []
    assert migrations.migrate(v1_conn) == []
    assert schema_version(v1_conn) == 4
    assert rows(v1_conn, "trades") == snapshot


def test_new_database_goes_straight_to_latest(conn):
    assert schema_version(conn) == migrations.LATEST_VERSION
    assert "closed_at" in columns(conn, "trades")


@pytest.mark.sqlite_only
def test_failed_migration_rolls_back_completely(v1_conn, monkeypatch):
    init_schema(v1_conn)
    broken = {
        **migrations.MIGRATIONS,
        5: "CREATE TABLE half_done (x INTEGER); SELECT * FROM table_that_does_not_exist;",
    }
    monkeypatch.setattr(migrations, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrations.migrate(v1_conn)
    assert schema_version(v1_conn) == 4
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in names


def test_reset_then_init_gives_a_fully_migrated_schema(conn):
    """Regression: drop_all must reset the version, or init would skip migration 2."""
    drop_all(conn)
    assert schema_version(conn) == 0
    init_schema(conn)
    assert schema_version(conn) == 4
    assert "closed_at" in columns(conn, "trades")


# ---------- Legacy trades after migration ----------


@pytest.mark.sqlite_only
def test_legacy_trades_are_reported_and_ordered_by_entry_time(v1_conn):
    migrate_and_claim(v1_conn)
    report = build_report(v1_conn, user_id=USER_ID)
    closed_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert report.unknown_close_time_trade_ids == closed_ids
    assert [p.trade_id for p in report.cumulative_r] == closed_ids
    assert all(not p.close_time_known for p in report.cumulative_r)
    assert all(p.realised_at == p.trade_date for p in report.cumulative_r)
    # The numbers are the same as Phase 3 produced for this data.
    assert report.overall.trades == 14 and report.overall.wins == 7


@pytest.mark.sqlite_only
def test_new_closed_trade_requires_closed_at_after_migration(v1_conn):
    migrate_and_claim(v1_conn)
    trade = {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:00:00Z",
        "direction": "LONG",
        "entry_price": "1.1",
        "stop_loss": "1.09",
        "exit_price": "1.12",
        "position_size": "1",
    }
    with pytest.raises(ValidationError) as info:
        create_trade(v1_conn, trade, user_id=USER_ID)
    assert info.value.errors == {"closed_at": "is required for a CLOSED trade"}
    new_id = create_trade(v1_conn, dict(trade, closed_at="2026-04-01T09:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, new_id, user_id=USER_ID)["closed_at"] == "2026-04-01T09:00:00Z"


@pytest.mark.sqlite_only
def test_mixed_legacy_and_new_trades_are_in_realised_order(v1_conn):
    migrate_and_claim(v1_conn)
    # Entered BEFORE every sample trade, closed AFTER all of them.
    new_id = create_trade(
        v1_conn,
        {
            "instrument_id": 1,
            "trade_date": "2026-02-27T08:00:00Z",
            "closed_at": "2026-03-25T08:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    curve = build_report(v1_conn, user_id=USER_ID).cumulative_r
    assert curve[-1].trade_id == new_id and curve[-1].close_time_known


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_edited_without_inventing_a_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    edited = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "closed_at",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
        )
    }
    edited["notes"] = "Edited after the migration"
    assert update_trade(v1_conn, 1, edited, user_id=USER_ID) is True
    after = get_trade(v1_conn, 1, user_id=USER_ID)
    assert after["notes"] == "Edited after the migration"
    assert after["closed_at"] is None


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_given_its_real_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    data = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
            "notes",
        )
    }
    update_trade(v1_conn, 1, dict(data, closed_at="2026-03-02T12:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, 1, user_id=USER_ID)["closed_at"] == "2026-03-02T12:00:00Z"
    # Once known, it cannot be removed again: the trade is no longer legacy.
    with pytest.raises(ValidationError):
        update_trade(v1_conn, 1, dict(data, closed_at=None), user_id=USER_ID)


# ---------- Triggers enforce the rules in SQL ----------

RAW = (
    "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price, "
    "stop_loss, exit_price, position_size, fees, status) VALUES (1, 1, ?, ?, 'LONG', '1.1', "
    "'1.09', ?, '1', '0', ?)"
)


def test_trigger_rejects_open_trade_with_closed_at(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T09:00:00Z", None, "OPEN"))


def test_trigger_rejects_close_before_entry(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T07:00:00Z", "1.12", "CLOSED"))


def test_trigger_rejects_bad_update(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute("UPDATE trades SET closed_at = '2000-01-01T00:00:00Z' WHERE id = 1")


def test_check_constraint_rejects_badly_formatted_closed_at(seeded_conn):
    with pytest.raises(IntegrityError):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "01/04/2026 09:00", "1.12", "CLOSED"))


def test_sql_still_allows_legacy_style_closed_trade_without_closed_at(seeded_conn):
    seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", None, "1.12", "CLOSED"))


# ---------- migrate-db command ----------


@pytest.mark.sqlite_only
def test_migrate_db_command_backs_up_then_migrates(tmp_path):
    path = tmp_path / "user.db"
    build_v1_database(str(path)).close()
    runner = create_app("testing", overrides={"DATABASE": str(path)}).test_cli_runner()

    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 1 -> 4" in result.output
    backups = list(tmp_path.glob("user.db.v1-*.bak"))
    assert len(backups) == 1
    backup = connect(str(backups[0]))
    assert schema_version(backup) == 1
    assert backup.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16
    backup.close()

    again = runner.invoke(args=["migrate-db"])
    assert "Already at schema version 4" in again.output
    assert len(list(tmp_path.glob("user.db.v1-*.bak"))) == 1
```

### `tests/test_operations.py` (MODIFIED)

**Why:** Redaction cases for reset-link tokens.  
**How to run or test:** `python -m pytest tests/test_operations.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: production configuration, proxies, logging and health checks."""

import io
import json
import logging

import pytest

from tests.conftest import ensure_user
from tradeledger import create_app
from tradeledger.db import build_conninfo, get_db, init_schema, redact_url
from tradeledger.observability import JsonFormatter, RedactingFilter, redact

STRONG_KEY = "ab" * 32


def production_app(db_path, **overrides):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path, **overrides})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


# ---------- Production settings ----------


def test_production_refuses_debug_mode(db_path, monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with pytest.raises(RuntimeError, match="Debug mode must not be enabled in production"):
        create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})


def test_production_defaults(db_path):
    config = production_app(db_path).config
    assert config["DEBUG"] is False
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SESSION_COOKIE_HTTPONLY"] is True
    assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert config["SESSION_COOKIE_NAME"].startswith("__Host-")
    assert config["REGISTRATION_ENABLED"] is False
    assert config["HSTS_ENABLED"] is True
    assert config["PROXY_HOPS"] == 0  # forwarded headers are NOT trusted unless configured
    assert config["LOG_FORMAT"] == "json"


def test_compose_example_secret_is_refused(db_path):
    with pytest.raises(RuntimeError, match="SECRET_KEY is too weak"):
        create_app(
            "production",
            overrides={
                "SECRET_KEY": "change-me-to-64-random-hex-characters-before-starting-the-stack",
                "DATABASE": db_path,
            },
        )


def test_errors_never_show_stack_traces_or_settings(db_path):
    app = production_app(db_path, PROPAGATE_EXCEPTIONS=False)

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal detail " + app.config["SECRET_KEY"])

    response = app.test_client().get("/boom", base_url="https://localhost")
    assert response.status_code == 500
    body = response.get_data(as_text=True)
    for leak in ("Traceback", "internal detail", STRONG_KEY, "DATABASE"):
        assert leak not in body


# ---------- Reverse proxy headers ----------


def remote_addr_and_scheme(app, headers):
    @app.get("/whoami-test")
    def whoami():
        from flask import request

        return {"ip": request.remote_addr, "secure": request.is_secure, "host": request.host}

    return (
        app.test_client()
        .get("/whoami-test", headers=headers, environ_base={"REMOTE_ADDR": "10.0.0.5"})
        .get_json()
    )


SPOOFED = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"}


def test_forwarded_headers_are_ignored_by_default(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    seen = remote_addr_and_scheme(app, SPOOFED)
    assert seen == {"ip": "10.0.0.5", "secure": False, "host": "localhost"}


def test_one_trusted_proxy_hop(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "PROXY_HOPS": 1})
    seen = remote_addr_and_scheme(app, SPOOFED)
    assert seen["ip"] == "203.0.113.9" and seen["secure"] is True
    assert seen["host"] == "localhost"  # X-Forwarded-Host is never trusted


def test_only_the_last_hop_is_trusted(db_path):
    """A client can prepend fake addresses; with 1 hop only the address the
    load balancer appended (the last one) is used."""
    app = create_app("testing", overrides={"DATABASE": db_path, "PROXY_HOPS": 1})
    seen = remote_addr_and_scheme(app, {"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
    assert seen["ip"] == "198.51.100.7"


# ---------- Health checks ----------


def test_liveness_and_readiness(anon_client):
    assert anon_client.get("/health/live").get_json() == {"status": "ok"}
    assert anon_client.get("/health/ready").get_json() == {"status": "ready"}


def test_readiness_fails_without_the_database_and_reveals_nothing(tmp_path, caplog):
    app = create_app("testing", overrides={"DATABASE": str(tmp_path / "missing-dir" / "db.sqlite")})
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}
    assert "missing-dir" not in response.get_data(as_text=True)
    assert "readiness check failed" in caplog.text  # the detail is in the server log
    assert app.test_client().get("/health/live").status_code == 200  # liveness unaffected


# ---------- Logging ----------


def test_request_id_is_returned_and_unsafe_ids_are_replaced(anon_client):
    good = anon_client.get("/health/live", headers={"X-Request-ID": "alb-trace-1234"})
    assert good.headers["X-Request-ID"] == "alb-trace-1234"
    bad = anon_client.get("/health/live", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert bad.headers["X-Request-ID"] != "<script>alert(1)</script>"
    assert len(bad.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize(
    "text, secret",
    [
        ("token tl_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "AbCdEfGhIjKl"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("connect postgresql://app:Sup3rS3cret@db:5432/x", "Sup3rS3cret"),
        ("password=hunter2hunter2 next", "hunter2hunter2"),
        ("SECRET_KEY: 'deadbeefdeadbeef'", "deadbeefdeadbeef"),
        # Phase 8.5: password-reset links, wherever they appear in a message
        ("link https://x.example/reset-password/tlr_QwErTyUiOpAsDfGhJkLzXcVbNm012345", "QwErTyUiOpAsDf"),
        ("pasted tlr_QwErTyUiOpAsDfGhJkLzXcVbNm012345 into chat", "QwErTyUiOpAsDf"),
    ],
)
def test_redaction(text, secret):
    assert secret not in redact(text)


def test_json_log_line_is_redacted_and_structured():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("tradeledger.test-json")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning(
            "used tl_0123456789abcdefghijKLMNOP", extra={"event": "x", "url": "postgres://u:pw1234@h/db"}
        )
    finally:
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["level"] == "WARNING" and entry["event"] == "x"
    assert "0123456789abcdef" not in stream.getvalue() and "pw1234" not in stream.getvalue()


def test_access_log_has_no_query_string_or_notes(db_path, caplog):
    app = create_app("testing", overrides={"DATABASE": db_path, "ACCESS_LOG": True})
    with app.app_context():
        init_schema(get_db())
    with caplog.at_level(logging.INFO, logger="tradeledger.access"):
        app.test_client().get("/login?next=/trades?q=MY-PRIVATE-NOTE")
    records = [r for r in caplog.records if r.name == "tradeledger.access"]
    assert records and records[-1].path == "/login" and records[-1].status == 200
    assert "MY-PRIVATE-NOTE" not in caplog.text


def test_database_url_is_logged_without_password(tmp_path, caplog):
    assert redact_url("postgresql://app:pw@db:5432/tl") == "postgresql://app:***@db:5432/tl"
    assert redact_url("postgresql://app@db/tl") == "postgresql://app@db/tl"


def test_conninfo_quotes_every_value():
    info = build_conninfo("db.internal", "5432", "tradeledger", "app", "p'w d\\x", "require")
    assert "password='p\\'w d\\\\x'" in info
    assert "sslmode='require'" in info


# ---------- Security headers still apply to the new pages ----------


@pytest.mark.parametrize("url", ["/login", "/register", "/health/ready"])
def test_new_pages_keep_the_security_headers(anon_client, url):
    response = anon_client.get(url)
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_secrets_can_come_from_mounted_files(tmp_path, monkeypatch):
    """Docker/Compose secrets are files; NAME_FILE wins over NAME."""
    from tradeledger.config import env_secret

    secret_file = tmp_path / "secret_key.txt"
    secret_file.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("TL_TEST_SECRET", "from-the-environment")
    assert env_secret("TL_TEST_SECRET") == "from-the-environment"
    monkeypatch.setenv("TL_TEST_SECRET_FILE", str(secret_file))
    assert env_secret("TL_TEST_SECRET") == "from-the-file"  # trailing newline removed
    monkeypatch.delenv("TL_TEST_SECRET")
    monkeypatch.delenv("TL_TEST_SECRET_FILE")
    assert env_secret("TL_TEST_SECRET") is None
```

### `tests/test_postgres.py` (MODIFIED)

**Why:** Baseline 3 plus migration 4 on PostgreSQL; new tables.  
**How to run or test:** `python -m pytest tests/test_postgres.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: PostgreSQL-specific behaviour and the SQLite -> PostgreSQL import.

Marked postgres_only: these run when TRADELEDGER_TEST_DATABASE_URL points at
a PostgreSQL server (CI's postgres service, or a local server) and are
reported as skipped otherwise. Every test works in its own empty schema.
"""

import json
import threading
from decimal import Decimal

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger.db import IntegrityError, connect, init_schema, list_tables
from tradeledger.migrations import LATEST_VERSION, POSTGRES_BASELINE_VERSION, current_version
from tradeledger.repositories import instruments as instrument_repo
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

pytestmark = pytest.mark.postgres_only

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "migrate_sqlite_to_postgres",
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py",
)
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)


# ---------- Schema ----------


def test_postgres_and_sqlite_end_on_the_same_version(conn):
    assert conn.dialect == "postgresql"
    # Phase 8: the baseline WAS the latest version (3). Phase 8.5 added
    # migration 4, which PostgreSQL applies on top of the baseline, so this
    # now also exercises the PostgreSQL migration loop for real.
    assert POSTGRES_BASELINE_VERSION == 3
    assert current_version(conn) == LATEST_VERSION == 4
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r["version"] for r in rows] == [3, 4]


def test_postgres_has_the_same_tables_and_columns_as_sqlite(conn, tmp_path):
    sqlite = connect(str(tmp_path / "compare.db"))
    init_schema(sqlite)
    try:
        assert set(list_tables(conn)) - {"schema_migrations"} == set(list_tables(sqlite))
        for table in (
            "instruments",
            "trades",
            "users",
            "api_tokens",
            "login_attempts",
            "user_sessions",
            "password_resets",
            "recovery_codes",
        ):
            pg_columns = {
                r["name"]
                for r in conn.execute(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = ?",
                    (table,),
                )
            }
            lite_columns = {r["name"] for r in sqlite.execute(f"PRAGMA table_info({table})")}
            assert pg_columns == lite_columns, table
    finally:
        sqlite.close()


def test_money_columns_are_exact_numeric(conn):
    types = {
        r["column_name"]: r["data_type"]
        for r in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'trades'"
        )
    }
    for column in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        assert types[column] == "numeric", column  # never real / double precision


def test_init_schema_is_idempotent_and_never_drops_data(seeded_conn, user_id):
    assert init_schema(seeded_conn) == []
    assert len(trade_repo.list_trades(seeded_conn, user_id=user_id)) == 16


def test_two_processes_migrating_at_once_are_safe(db_path):
    """The advisory lock makes concurrent migrations wait, not collide."""
    errors = []

    def migrate():
        try:
            c = connect(db_path)
            init_schema(c)
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=migrate) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = connect(db_path)
    assert current_version(check) == LATEST_VERSION
    # One row per version (baseline 3 plus migration 4): nothing applied twice.
    assert check.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    check.close()


# ---------- Decimal precision ----------


@pytest.mark.parametrize(
    "price",
    ["999999999999999.999999999999999", "0.000000000000001", "1.10000", "123456.123456789"],
)
def test_decimals_round_trip_exactly(conn, eurusd_id, user_id, price):
    entry = Decimal(price)
    trade_id = (
        trade_repo.create_trade(
            conn,
            {
                "instrument_id": eurusd_id,
                "trade_date": "2026-03-02T08:15:00Z",
                "direction": "SHORT",
                "entry_price": price,
                "stop_loss": str(entry * 2) if entry < Decimal("1e14") else "999999999999999.999999999999999",
                "position_size": "1",
                "status": "OPEN",
            },
            user_id=user_id,
        )
        if entry < Decimal("1e14")
        else None
    )
    if trade_id is None:
        # The maximum value: store it directly in a NUMERIC column and read it back.
        row = conn.execute("SELECT CAST(? AS NUMERIC) AS v", (conn.numeric(price),)).fetchone()
        assert row["v"] == entry and str(row["v"]) == price
        return
    stored = trade_repo.get_trade(conn, trade_id, user_id=user_id)["entry_price"]
    assert stored == entry  # exactly equal: no binary floating point anywhere
    assert isinstance(stored, Decimal)


def test_database_limits_repeat_the_validation_limits(conn, eurusd_id, user_id):
    """Defence in depth: even raw SQL cannot store 16 integer digits."""
    with pytest.raises(IntegrityError):
        conn.execute(
            "INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss, "
            "position_size, status) VALUES (?, ?, '2026-03-02T08:15:00Z', 'LONG', ?, '1', '1', 'OPEN')",
            (user_id, eurusd_id, conn.numeric("1" + "0" * 15)),
        )


def test_closed_at_rule_is_a_named_check_constraint(seeded_conn, user_id):
    with pytest.raises(IntegrityError) as info:
        seeded_conn.execute(
            "UPDATE trades SET closed_at = '2020-01-01T00:00:00Z' WHERE user_id = ? AND status = 'CLOSED'",
            (user_id,),
        )
    assert info.value.constraint == "closed_at_rules"


# ---------- Concurrency ----------


def test_concurrent_edits_are_serialised(db_path, seeded_conn, user_id):
    """update_trade locks the row (SELECT ... FOR UPDATE): a second
    connection's edit waits for the first transaction instead of
    interleaving with it."""
    trade = trade_repo.list_trades(seeded_conn, user_id=user_id, status="CLOSED")[0]
    data = {k: trade[k] for k in trade_repo._WRITE_FIELDS}
    for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        data[key] = None if data[key] is None else str(data[key])
    first, second = connect(db_path), connect(db_path)
    order = []
    try:
        with first:
            first.execute("SELECT id FROM trades WHERE id = ? FOR UPDATE", (trade["id"],))

            def other_edit():
                trade_repo.update_trade(second, trade["id"], {**data, "notes": "second"}, user_id=user_id)
                order.append("second")

            worker = threading.Thread(target=other_edit)
            worker.start()
            worker.join(timeout=0.5)
            assert worker.is_alive()  # blocked by the first transaction's lock
            first.execute("UPDATE trades SET notes = 'first' WHERE id = ?", (trade["id"],))
            order.append("first")
        worker.join(timeout=5)
        assert order == ["first", "second"]
        assert trade_repo.get_trade(seeded_conn, trade["id"], user_id=user_id)["notes"] == "second"
    finally:
        first.close()
        second.close()


def test_failed_write_rolls_back_the_whole_transaction(conn, user_id):
    with pytest.raises(IntegrityError), conn:
        instrument_repo.create_instrument(
            conn,
            {
                "symbol": "AAA",
                "name": "a",
                "asset_class": "OTHER",
                "price_increment": "0.1",
                "contract_multiplier": "1",
                "quote_currency": "USD",
            },
            user_id=user_id,
        )
        conn.execute("INSERT INTO trades (user_id) VALUES (?)", (user_id,))  # violates NOT NULL
    assert instrument_repo.list_instruments(conn, user_id=user_id) == []


# ---------- SQLite -> PostgreSQL import ----------


@pytest.fixture
def phase7_file(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


def run_import(phase7_file, db_path, *extra):
    lines = []
    args = ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", TEST_EMAIL, *extra]
    from tests.conftest import PG_URL  # noqa: F401 - the bridge is registered by conftest when used

    code, summary = importer.run(args, out=lines.append)
    return code, summary, "\n".join(lines)


def test_import_dry_run_writes_nothing(phase7_file, db_path, conn, user_id):
    code, summary, output = run_import(phase7_file, db_path)
    assert code == 0, output
    assert summary["status"] == "dry run: nothing written"
    assert (summary["instruments"], summary["trades"]) == (5, 16)
    assert trade_repo.list_trades(conn, user_id=user_id) == []


def test_import_copies_everything_exactly(phase7_file, db_path, conn, user_id, tmp_path):
    import sqlite3

    report = tmp_path / "audit.json"
    code, summary, output = run_import(phase7_file, db_path, "--apply", "--report", str(report))
    assert code == 0, output
    assert summary["status"] == "imported and verified"
    audit = json.loads(report.read_text())
    assert audit["trades"] == 16 and len(audit["trade_id_map"]) == 16
    assert "@" not in audit["target"] or ":***@" in audit["target"] or "postgres@" in audit["target"]

    source = sqlite3.connect(phase7_file)
    source.row_factory = sqlite3.Row
    for old in source.execute("SELECT * FROM trades ORDER BY id"):
        new = trade_repo.get_trade(conn, audit["trade_id_map"][str(old["id"])], user_id=user_id)
        for field in ("entry_price", "stop_loss", "exit_price", "position_size", "fees"):
            assert (new[field] is None and old[field] is None) or new[field] == Decimal(old[field])
        assert new["closed_at"] == old["closed_at"]
        assert new["created_at"] == old["created_at"]
    source.close()


def test_import_report_matches_the_engine_on_both_databases(phase7_file, db_path, conn, user_id, tmp_path):
    from tradeledger.legacy import claim_legacy_data
    from tradeledger.performance import build_report

    run_import(phase7_file, db_path, "--apply")
    lite = connect(phase7_file)
    init_schema(lite)
    claim_legacy_data(lite, user_id=ensure_user(lite), apply=True)
    a, b = build_report(lite, user_id=1).overall, build_report(conn, user_id=user_id).overall
    lite.close()
    assert (a.trades, a.wins, a.total_r, a.expectancy_r) == (b.trades, b.wins, b.total_r, b.expectancy_r)


def test_import_refuses_a_non_empty_target(phase7_file, db_path, seeded_conn):
    code, _summary, output = run_import(phase7_file, db_path, "--apply")
    assert code == 1
    assert "only writes into an empty journal" in output
    assert len(trade_repo.list_trades(seeded_conn, user_id=1)) == 16  # untouched


def test_import_refuses_an_unknown_owner(phase7_file, db_path, conn):
    lines = []
    code, _ = importer.run(
        ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", "ghost@example.com", "--apply"],
        out=lines.append,
    )
    assert code == 1 and "create it first" in "\n".join(lines)


def test_import_into_one_account_leaves_others_alone(phase7_file, db_path, conn):
    other = ensure_user(conn, OTHER_EMAIL)
    seed_sample_data(conn, user_id=other)
    code, _, output = run_import(phase7_file, db_path, "--apply")
    assert code == 0, output
    assert len(trade_repo.list_trades(conn, user_id=other)) == 16
    assert len(trade_repo.list_trades(conn, user_id=1)) == 16


def test_import_never_prints_a_password(phase7_file, db_path, conn):
    url_with_password = db_path.replace(
        "postgresql://postgres@", "postgresql://postgres:NotARealPassword@", 1
    )
    _, summary, output = run_import(phase7_file, url_with_password)
    assert "NotARealPassword" not in output and "NotARealPassword" not in json.dumps(summary)
```

### `tests/test_seed_and_analytics.py` (MODIFIED)

**Why:** db-info reports schema version 4.  
**How to run or test:** `python -m pytest tests/test_seed_and_analytics.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Sample data, analytical SQL and the CLI commands.

Expected values were worked out by hand from tradeledger/seed.py
(see the comments beside each sample trade).
"""

from decimal import Decimal

import pytest

from tests.conftest import TEST_EMAIL
from tests.factories import USER_ID
from tradeledger import analytics, seed
from tradeledger.seed import SAMPLE_TRADES, seed_sample_data
from tradeledger.validation import ValidationError


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Seeding ----------


def test_seed_inserts_everything_once(conn):
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 5, "trades": 16}


def test_seed_is_idempotent(conn):
    seed_sample_data(conn, user_id=USER_ID)
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 0, "trades": 0}
    assert count(conn, "instruments") == 5
    assert count(conn, "trades") == 16


def test_seed_does_not_touch_existing_user_trades(conn, long_trade):
    from tradeledger.repositories.trades import create_trade

    create_trade(conn, dict(long_trade, trade_date="2026-01-05T10:00:00Z"), user_id=USER_ID)
    seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "trades") == 17


def test_seed_covers_required_scenarios(seeded_conn):
    rows = seeded_conn.execute("SELECT direction, status, strategy, session FROM trades").fetchall()
    assert {r["direction"] for r in rows} == {"LONG", "SHORT"}
    assert {r["status"] for r in rows} == {"OPEN", "CLOSED"}
    assert None in {r["strategy"] for r in rows}
    assert len({r["session"] for r in rows}) >= 3


def test_seed_is_all_or_nothing(conn, monkeypatch):
    """If one sample record is invalid, the whole seed run is rolled back."""
    broken = dict(SAMPLE_TRADES[0], trade_date="2026-04-01T09:00:00Z", position_size="-1")
    monkeypatch.setattr(seed, "SAMPLE_TRADES", [*SAMPLE_TRADES, broken])
    with pytest.raises(ValidationError):
        seed.seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "instruments") == 0
    assert count(conn, "trades") == 0


# ---------- Analytics on an empty database ----------


def test_analytics_on_empty_database(conn):
    assert analytics.closed_trade_count(conn, user_id=USER_ID) == 0
    assert analytics.closed_count_by_instrument(conn, user_id=USER_ID) == []
    assert analytics.closed_by_weekday(conn, user_id=USER_ID) == []
    assert analytics.trades_by_strategy(conn, user_id=USER_ID) == []
    assert analytics.trades_by_session(conn, user_id=USER_ID) == []
    assert analytics.win_rate_by_instrument(conn, user_id=USER_ID) == []


# ---------- Analytics on the sample data ----------


def test_closed_trade_count(seeded_conn):
    assert analytics.closed_trade_count(seeded_conn, user_id=USER_ID) == 14


def test_closed_count_by_instrument(seeded_conn):
    assert analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID) == [
        {"symbol": "XAUUSD", "closed_trades": 4},
        {"symbol": "EURUSD", "closed_trades": 3},
        {"symbol": "GBPUSD", "closed_trades": 3},
        {"symbol": "NAS100", "closed_trades": 2},
        {"symbol": "UK100", "closed_trades": 2},
    ]


def test_instrument_with_no_closed_trades_shows_zero(seeded_conn):
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        seeded_conn,
        {
            "symbol": "US30",
            "name": "Wall Street 30 (test)",
            "asset_class": "INDEX",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    rows = analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID)
    assert {"symbol": "US30", "closed_trades": 0} in rows


def test_closed_by_weekday(seeded_conn):
    assert analytics.closed_by_weekday(seeded_conn, user_id=USER_ID) == [
        {"weekday_number": 1, "weekday": "Monday", "closed_trades": 3},
        {"weekday_number": 2, "weekday": "Tuesday", "closed_trades": 3},
        {"weekday_number": 3, "weekday": "Wednesday", "closed_trades": 3},
        {"weekday_number": 4, "weekday": "Thursday", "closed_trades": 2},
        {"weekday_number": 5, "weekday": "Friday", "closed_trades": 3},
    ]


def test_trades_by_strategy(seeded_conn):
    assert analytics.trades_by_strategy(seeded_conn, user_id=USER_ID) == [
        {"strategy": "Breakout", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"strategy": "Pullback", "total_trades": 5, "open_trades": 0, "closed_trades": 5},
        {"strategy": "Reversal", "total_trades": 3, "open_trades": 1, "closed_trades": 2},
        {"strategy": "Unspecified", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
    ]


def test_trades_by_session(seeded_conn):
    assert analytics.trades_by_session(seeded_conn, user_id=USER_ID) == [
        {"session": "LONDON", "total_trades": 7, "open_trades": 1, "closed_trades": 6},
        {"session": "NEW_YORK", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"session": "ASIA", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
        {"session": "Unspecified", "total_trades": 1, "open_trades": 0, "closed_trades": 1},
    ]


def test_win_rate_by_instrument(seeded_conn):
    assert analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) == [
        {
            "symbol": "XAUUSD",
            "quote_currency": "USD",
            "closed_trades": 4,
            "wins": 3,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 75.0,
        },
        {
            "symbol": "NAS100",
            "quote_currency": "USD",
            "closed_trades": 2,
            "wins": 1,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "UK100",
            "quote_currency": "GBP",
            "closed_trades": 2,
            "wins": 1,
            "losses": 0,
            "breakevens": 1,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "EURUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 2,
            "breakevens": 0,
            "win_rate_pct": 33.3,
        },
        {
            "symbol": "GBPUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 1,
            "breakevens": 1,
            "win_rate_pct": 33.3,
        },
    ]


def test_fees_can_turn_a_price_gain_into_a_loss(seeded_conn):
    """Sample trade 9: long EURUSD, exit 2 points (0.00002) above entry,
    gross +2.00 USD, fees 7.00 -> net -5.00 USD -> counted as a LOSS."""
    eurusd = next(
        r for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) if r["symbol"] == "EURUSD"
    )
    assert eurusd["losses"] == 2


def test_sql_win_rate_agrees_with_exact_decimal_calculation(seeded_conn):
    """Cross-check the float-based SQL against exact Decimal arithmetic."""
    rows = seeded_conn.execute(
        """
        SELECT i.symbol, t.direction, t.entry_price, t.exit_price,
               t.position_size, t.fees, i.contract_multiplier
        FROM trades t JOIN instruments i ON i.id = t.instrument_id
        WHERE t.status = 'CLOSED'
        """
    ).fetchall()

    expected = {}
    for r in rows:
        move = Decimal(r["exit_price"]) - Decimal(r["entry_price"])
        if r["direction"] == "SHORT":
            move = -move
        net = move * Decimal(r["position_size"]) * Decimal(r["contract_multiplier"]) - Decimal(r["fees"])
        tally = expected.setdefault(r["symbol"], {"wins": 0, "losses": 0, "breakevens": 0})
        tally["wins" if net > 0 else "losses" if net < 0 else "breakevens"] += 1

    actual = {
        r["symbol"]: {"wins": r["wins"], "losses": r["losses"], "breakevens": r["breakevens"]}
        for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID)
    }
    assert actual == expected


# ---------- CLI commands ----------


def test_cli_init_seed_and_info(runner):
    assert "Database ready" in runner.invoke(args=["init-db"]).output
    assert "5 instrument(s) and 16 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    assert "0 instrument(s) and 0 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    info = runner.invoke(args=["db-info"]).output
    assert "Schema version: 4" in info  # 1 before Phase 4, 2 in Phases 4-7, 3 in Phase 8
    assert "trades" in info and "16" in info


def test_cli_analytics_runs_every_query(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    output = runner.invoke(args=["analytics", "--email", TEST_EMAIL]).output
    for name in analytics.ALL_QUERIES:
        assert f"== {name} ==" in output


def test_cli_run_sql_is_read_only(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    ok = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in ok.output
    blocked = runner.invoke(args=["run-sql", "DELETE FROM trades"])
    assert blocked.exit_code != 0
    assert "readonly" in blocked.output or "read-only" in blocked.output  # SQLite / PostgreSQL wording
    still_there = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in still_there.output


def test_cli_reset_db_empties_tables(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    result = runner.invoke(args=["reset-db", "--yes"])
    assert "Database reset" in result.output
    for table in ("trades", "instruments"):
        output = runner.invoke(args=["run-sql", f"SELECT COUNT(*) AS n FROM {table}"]).output
        assert output.strip().splitlines()[-1].strip() == "0"
```

### `scripts/benchmark_dashboard.py` (MODIFIED)

**Why:** Signed-in requests create a session row; measures user + session lookup.  
**How to run or test:** `python scripts/benchmark_dashboard.py --help`

```python
"""Time the dashboard, the API and authentication on large synthetic journals.

    python scripts/benchmark_dashboard.py              # 100, 1,000 and 10,000 trades (SQLite)
    python scripts/benchmark_dashboard.py 5000         # a custom size
    python scripts/benchmark_dashboard.py --profile    # also show where the time goes
    python scripts/benchmark_dashboard.py --database-url postgresql://user:pw@host/postgres
        # PostgreSQL: a throwaway database is created (and dropped) per size
        # (add --pg-bridge where psycopg is not installed: test-only libpq bridge,
        #  whose timings include its own ctypes overhead and are NOT psycopg's)

Creates a throwaway database (never your real one) holding TWO accounts:
the measured one with N synthetic closed trades spread over several
instruments, strategies, sessions and two years, and a second account with
another N trades, so every query really has to filter by owner (Phase 8).
Then it measures, as the median of 5 runs:

  sql        the database query alone (list_trades with realised dates)
  report     build_report(): SQL query + Decimal calculations
  dashboard  build_dashboard() + chart_payload(): the above plus chart prep
  page       a full GET /analytics through Flask, including the template
  filtered   GET /analytics with strategy + session + date filters
  memory     peak Python memory allocated while rendering one page (tracemalloc)
  size       the HTML page, and the chart data (JSON) inside it
  api_list   GET /api/v1/trades?per_page=100 with a Bearer token
  api_sum    GET /api/v1/analytics/summary with a Bearer token
and once per run, independent of N:
  argon2     hashing and verifying one password with the production settings
  token      looking up and checking one API token
  session    loading the signed-in user for one request

Numbers depend on the machine; the script prints the Python version and
platform so results can be compared honestly. They are not guarantees.
"""

import cProfile
import json
import os
import platform
import pstats
import re
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, register_postgres_driver
from tradeledger.passwords import OWASP_MINIMUM, hash_password, verify_password
from tradeledger.performance import build_report
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories import api_tokens, sessions
from tradeledger.repositories.trades import list_trades
from tradeledger.repositories.users import create_user, get_user

RUNS = 5
STRATEGIES = ("Breakout", "Pullback", "Reversal", None)
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER", None)
EXITS = ("102", "99", "100.5", "99", "101.25", "100")  # +2, -1, +0.5, -1, +1.25, 0 R
FAST_HASH = "$argon2id$"  # accounts are created with a placeholder-format hash; nobody signs in


def populate(conn, count, user_id):
    with conn:
        instruments = []
        for n, currency in enumerate(("USD", "USD", "GBP", "USD", "EUR")):
            row = conn.execute(
                "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment,"
                " contract_multiplier, quote_currency) VALUES (?, ?, ?, 'OTHER', ?, ?, ?) RETURNING id",
                (user_id, f"BENCH{n}", f"Benchmark {n}", conn.numeric("0.01"), conn.numeric("1"), currency),
            ).fetchone()
            instruments.append(row["id"])
        hundred, ninety_nine, one, zero = (conn.numeric(v) for v in ("100", "99", "1", "0"))
        for n in range(count):
            day = n * 730 // count  # spread over two years
            month, dom = 1 + (day // 28) % 12, 1 + day % 28
            year = 2025 + day // 336
            conn.execute(
                "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price,"
                " stop_loss, exit_price, position_size, fees, status, strategy, session)"
                " VALUES (?, ?, ?, ?, 'LONG', ?, ?, ?, ?, ?, 'CLOSED', ?, ?)",
                (
                    user_id,
                    instruments[n % len(instruments)],
                    f"{year}-{month:02d}-{dom:02d}T08:{n % 60:02d}:00Z",
                    f"{year}-{month:02d}-{dom:02d}T{9 + n % 12:02d}:{n % 60:02d}:00Z",
                    hundred,
                    ninety_nine,
                    conn.numeric(EXITS[n % len(EXITS)]),
                    one,
                    zero,
                    STRATEGIES[n % len(STRATEGIES)],
                    SESSIONS[n % len(SESSIONS)],
                ),
            )


def median_ms(action):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def peak_memory_mb(action):
    tracemalloc.start()
    action()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / (1024 * 1024)


def chart_data_kb(html):
    match = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return len(match.group(1).encode()) / 1024 if match else 0.0


def profile(action, top=12):
    """Print where `action` spends its time, sorted two ways.

    cumulative: time in a function INCLUDING everything it calls; shows
                which high-level step is slow (e.g. build_dashboard).
    tottime:    time spent INSIDE the function itself; shows the actual
                hot spot. In Phase 7 this view revealed datetime.strptime
                being called ~50,000 times at 10,000 trades.
    """
    profiler = cProfile.Profile()
    profiler.runcall(action)
    for order in ("cumulative", "tottime"):
        print(f"\n--- sorted by {order} ---")
        pstats.Stats(profiler).sort_stats(order).print_stats(top)


class Target:
    """A throwaway database: a SQLite file, or a new PostgreSQL database."""

    def __init__(self, server_url, label):
        self.server_url, self.label = server_url, label
        self.folder = tempfile.TemporaryDirectory()

    def __enter__(self):
        if not self.server_url:
            return os.path.join(self.folder.name, "bench.db")
        from urllib.parse import urlsplit, urlunsplit

        self.name = f"tl_bench_{self.label}_{os.getpid()}"
        self.admin = connect(self.server_url)
        self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
        self.admin.execute(f"CREATE DATABASE {self.name}")
        parts = urlsplit(self.server_url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.name}", parts.query, ""))

    def __exit__(self, *exc):
        if self.server_url:
            self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
            self.admin.close()
        self.folder.cleanup()


def benchmark(count, server_url=None, show_profile=False):
    with Target(server_url, count) as target:
        app = create_app("testing", overrides={"DATABASE": target})
        with app.app_context():
            conn = get_db()
            init_schema(conn)
            me = create_user(conn, "bench@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            other = create_user(conn, "other@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            populate(conn, count, me)
            populate(conn, count, other)  # the second account: queries must filter by owner
            if build_report(conn, user_id=me).overall.trades != count:
                raise SystemExit(f"setup failed: expected {count} closed trades")
            token, _ = api_tokens.create_token(conn, user_id=me, name="bench", lifetime_days=1, max_active=10)
            sql_ms = median_ms(lambda: list_trades(conn, user_id=me, date_field="realised"))
            report_ms = median_ms(lambda: build_report(conn, user_id=me))
            dashboard_ms = median_ms(
                lambda: chart_payload(build_dashboard(conn, ReportFilters(), user_id=me))
            )
            token_ms = median_ms(lambda: api_tokens.authenticate(conn, token))
            now = int(time.time())
            # Phase 8.5: a signed-in request reads the user AND its session row.
            key, _ = sessions.create_session(
                conn,
                user_id=me,
                now=now,
                max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
                user_agent="bench",
            )
            session_ms = median_ms(lambda: (get_user(conn, me), sessions.find_session(conn, key)))
        client = app.test_client()
        with client.session_transaction() as session:
            session.update({"sid": key, "uid": me, "sv": 1, "auth_at": now, "seen_at": now})
        html = client.get("/analytics").get_data(as_text=True)
        if "dashboard-data" not in html:
            raise SystemExit("the dashboard did not render")
        page_ms = median_ms(lambda: client.get("/analytics"))
        filtered_ms = median_ms(
            lambda: client.get("/analytics?strategy=Breakout&session=LONDON&start=2025-06-01&end=2026-06-30")
        )
        memory = peak_memory_mb(lambda: client.get("/analytics"))
        bearer = {"Authorization": f"Bearer {token}"}
        api_list_ms = median_ms(lambda: client.get("/api/v1/trades?per_page=100", headers=bearer))
        api_summary_ms = median_ms(lambda: client.get("/api/v1/analytics/summary", headers=bearer))
        if show_profile:
            print(f"\n--- where GET /analytics spends its time ({count:,} trades) ---")
            profile(lambda: client.get("/analytics"))
        app.extensions["tradeledger_db"].close()
    result = {
        "trades": count,
        "sql_ms": sql_ms,
        "report_ms": report_ms,
        "dashboard_ms": dashboard_ms,
        "page_ms": page_ms,
        "filtered_ms": filtered_ms,
        "api_list_ms": api_list_ms,
        "api_summary_ms": api_summary_ms,
        "token_check_ms": token_ms,
        "session_load_ms": session_ms,
        "peak_mb": memory,
        "page_kb": len(html.encode()) / 1024,
        "chart_kb": chart_data_kb(html),
    }
    print(
        f"{count:>7,} trades | sql {sql_ms:7.1f} | report {report_ms:7.1f} | dashboard {dashboard_ms:7.1f} | "
        f"page {page_ms:7.1f} | filtered {filtered_ms:6.1f} | api list {api_list_ms:6.1f} | "
        f"api summary {api_summary_ms:7.1f} ms | peak {memory:5.1f} MB | page {result['page_kb']:6,.0f} KB"
    )
    return result


def argon2_ms():
    stored = hash_password("benchmark password", OWASP_MINIMUM)
    return (
        median_ms(lambda: hash_password("benchmark password", OWASP_MINIMUM)),
        median_ms(lambda: verify_password("benchmark password", stored)),
    )


if __name__ == "__main__":
    argv = sys.argv[1:]
    server = argv[argv.index("--database-url") + 1] if "--database-url" in argv else None
    if "--pg-bridge" in argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    sizes = [int(a) for a in argv if a.isdigit()] or [100, 1000, 10000]
    engine = (
        "PostgreSQL" + (" (test-only libpq bridge)" if "--pg-bridge" in argv else "") if server else "SQLite"
    )
    print(f"Python {platform.python_version()} on {platform.platform()}; {engine}; median of {RUNS} runs")
    hash_ms, verify_ms = argon2_ms()
    print(f"Argon2id (m=19456 KiB, t=2, p=1): hash {hash_ms:.1f} ms, verify {verify_ms:.1f} ms")
    results = [benchmark(size, server, "--profile" in argv) for size in sizes]
    print(
        json.dumps(
            {"engine": engine, "argon2_hash_ms": hash_ms, "argon2_verify_ms": verify_ms, "runs": results}
        )
    )
```

### `scripts/qa_dashboard_agreement.py` (MODIFIED)

**Why:** The audit's signed-in client creates a session row like the real form.  
**How to run or test:** `python scripts/qa_dashboard_agreement.py --help`

```python
"""Phase 6 QA: does everything the dashboard shows agree with an
INDEPENDENT recalculation from the raw database rows?

An audit, not part of pytest (it takes ~15 s and needs Node.js). It builds
a throwaway database (the sample data plus 600 random trades in three
currencies, 5% open, 3% with no exit time), recomputes every figure from
the raw SQL rows with its OWN code (it never calls the engine), then
compares four outputs against that for every filter combination:
  1. the HTML page (KPI cards, money strip, all detailed tables)
  2. the chart payload embedded in the page
  3. the JSON API (/api/v1/analytics/*)
  4. the tooltip text produced by the REAL analytics.js (run in Node)

Phase 8: the audited account (user 1) shares the database with a SECOND
account holding 120 decoy trades on instruments with the same symbols. The
independent recalculation reads only user 1's rows, so any leak of the
other account's data into the dashboard or the API is a disagreement.
The page and the API are requested as user 1 (signed-in session; API token).

Usage (from the project folder):
    python scripts/qa_dashboard_agreement.py            # finds node on PATH
    python scripts/qa_dashboard_agreement.py --node /path/to/node
    python scripts/qa_dashboard_agreement.py --database-url postgresql://...  # audit on PostgreSQL
      (add --pg-bridge to use the test-only libpq bridge where psycopg is not installed)

Exit code 0 = no disagreements. Precision trap: every Decimal sum here runs
inside localcontext(prec=80); Python's default context keeps only 28 digits.
"""

import itertools
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import unescape
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema, register_postgres_driver  # noqa: E402
from tradeledger.passwords import HashParameters, hash_password  # noqa: E402
from tradeledger.repositories import api_tokens, sessions  # noqa: E402
from tradeledger.repositories.instruments import create_instrument  # noqa: E402
from tradeledger.repositories.trades import create_trade  # noqa: E402
from tradeledger.repositories.users import create_user  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

AUDITED = 1  # the audited account's id (created first)
FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)

TOL = Decimal("1e-30")
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    None: "Unspecified",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
problems = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        problems.append(message)


# ---------- My own formatting (written from the documented rules) ----------


def q(value, places):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_r(v):
    if v is None:
        return "n/a"
    r = q(v, "0.01")
    return "0.00R" if r == 0 else f"{r:+}R"


def fmt_rate(wins, total):
    return "n/a" if total == 0 else f"{q(Decimal(wins) * 100 / Decimal(total), '0.1')}%"


def fmt_ratio(v):
    return "n/a" if v is None else str(q(v, "0.01"))


# ---------- Build a database: sample data + 600 random trades ----------


def random_trade(rnd, instrument_ids, strategies, sessions):
    direction = rnd.choice(["LONG", "SHORT"])
    entry = Decimal(rnd.randint(5000, 20000)) / 100
    risk = Decimal(rnd.randint(10, 300)) / 100
    stop = entry - risk if direction == "LONG" else entry + risk
    move = Decimal(rnd.randint(-400, 400)) / 100
    exit_price = max(Decimal("0.01"), entry + move)
    start = datetime(2025, 11, 1, tzinfo=UTC)
    entered = start + (datetime(2026, 4, 30, tzinfo=UTC) - start) * rnd.random()
    entered = entered.replace(microsecond=0)
    closed = entered.timestamp() + rnd.randint(60, 5 * 86400)
    closed = datetime.fromtimestamp(closed, UTC)
    is_open = rnd.random() < 0.05
    return {
        "instrument_id": rnd.choice(instrument_ids),
        "direction": direction,
        "status": "OPEN" if is_open else "CLOSED",
        "trade_date": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed_at": None if is_open else closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entry_price": str(entry),
        "stop_loss": str(stop),
        "exit_price": None if is_open else str(exit_price),
        "position_size": str(rnd.choice(["1", "2", "0.5", "3"])),
        "fees": str(Decimal(rnd.randint(0, 300)) / 100),
        "strategy": rnd.choice(strategies),
        "session": rnd.choice(sessions),
    }


def build_database(target):
    app = create_app("testing", overrides={"DATABASE": target})
    rnd = random.Random(20261115)
    with app.app_context():
        conn = get_db()
        init_schema(conn)
        audited = create_user(conn, "audited@example.com", hash_password("audit password 1", FAST))
        decoy = create_user(conn, "decoy@example.com", hash_password("audit password 2", FAST))
        if audited != AUDITED:
            raise SystemExit("the audit needs an empty database (audited account must be id 1)")
        seed_sample_data(conn, user_id=audited)
        # The decoy account: same symbols, very different trades.
        seed_sample_data(conn, user_id=decoy)
        decoy_instruments = [
            r[0] for r in conn.execute("SELECT id FROM instruments WHERE user_id = ?", (decoy,))
        ]
        for _ in range(120):
            create_trade(
                conn,
                random_trade(rnd, decoy_instruments, ["Breakout", "Decoy"], ["ASIA", None]),
                user_id=decoy,
            )
        extra = [
            create_instrument(
                conn,
                {
                    "symbol": sym,
                    "name": sym,
                    "asset_class": "OTHER",
                    "price_increment": "0.01",
                    "contract_multiplier": mult,
                    "quote_currency": cur,
                },
                user_id=audited,
            )
            for sym, mult, cur in (("QAUSD", "10", "USD"), ("QAGBP", "1", "GBP"), ("QAEUR", "0.5", "EUR"))
        ]
        strategies = ["Breakout", "Pullback", "Scalp", "Fade", None]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]
        for _ in range(600):
            create_trade(conn, random_trade(rnd, extra, strategies, sessions), user_id=audited)
        # 3% legacy trades: exit time never recorded.
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM trades WHERE status='CLOSED' AND user_id = ? AND id > 16 ORDER BY id",
                (audited,),
            )
        ]
        for trade_id in rnd.sample(ids, len(ids) * 3 // 100):
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        conn.commit()
    return app


# ---------- Independent recalculation from raw rows ----------


def raw_rows(app):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute(
                "SELECT t.*, i.symbol, i.contract_multiplier, i.quote_currency "
                "FROM trades t JOIN instruments i ON i.id = t.instrument_id WHERE t.user_id = ?",
                (AUDITED,),
            )
        ]


def independent(rows, f):
    with localcontext() as ctx:
        ctx.prec = 80
        out = []
        open_count = 0
        for t in rows:
            if f["instrument_id"] and t["instrument_id"] != f["instrument_id"]:
                continue
            if f["strategy"] and t["strategy"] != f["strategy"]:
                continue
            if f["session"] and t["session"] != f["session"]:
                continue
            if t["status"] == "OPEN":
                open_count += 1 if not (f["start"] or f["end"]) else 0
                continue
            realised = t["closed_at"] or t["trade_date"]
            day = realised[:10]
            if (f["start"] and day < f["start"]) or (f["end"] and day > f["end"]):
                continue
            sign = 1 if t["direction"] == "LONG" else -1
            entry, stop, exit_ = (Decimal(t[k]) for k in ("entry_price", "stop_loss", "exit_price"))
            size, mult = Decimal(t["position_size"]), Decimal(t["contract_multiplier"])
            net = (exit_ - entry) * sign * size * mult - Decimal(t["fees"] or "0")
            risk = abs(entry - stop) * size * mult
            out.append(
                {
                    "id": t["id"],
                    "when": realised,
                    "symbol": t["symbol"],
                    "net": net,
                    "r": net / risk,
                    "cur": t["quote_currency"],
                    "strategy": t["strategy"],
                    "session": t["session"],
                }
            )
        out.sort(key=lambda x: (x["when"], x["id"]))
        return out, open_count


def summary(trades):
    with localcontext() as ctx:
        ctx.prec = 80
        n = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        losses = sum(1 for t in trades if t["net"] < 0)
        total = sum((t["r"] for t in trades), Decimal(0))
        gains = sum((t["r"] for t in trades if t["r"] > 0), Decimal(0))
        pains = -sum((t["r"] for t in trades if t["r"] < 0), Decimal(0))
        cum, peak, worst = Decimal(0), Decimal(0), Decimal(0)
        for t in trades:
            cum += t["r"]
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        streaks = {"WIN": 0, "LOSS": 0}
        run_kind, run = None, 0
        for t in trades:
            kind = "WIN" if t["net"] > 0 else "LOSS" if t["net"] < 0 else None
            run = run + 1 if kind and kind == run_kind else (1 if kind else 0)
            run_kind = kind
            if kind:
                streaks[kind] = max(streaks[kind], run)
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "breakevens": n - wins - losses,
            "total_r": total,
            "mean_r": total / n if n else None,
            "pf": gains / pains if pains else None,
            "max_dd": worst,
            "win_streak": streaks["WIN"],
            "loss_streak": streaks["LOSS"],
        }


def grouped(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in groups.items()}


# ---------- Reading the page ----------


def kpis(html):
    return dict(re.findall(r'data-kpi="([a-z_]+)">.*?class="kpi__value">([^<]*)<', html, re.S))


def strip(fragment):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def table(html, table_id):
    match = re.search(rf'id="{table_id}".*?<tbody>(.*?)</tbody>', html, re.S)
    if not match:
        return None
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S):
        head = strip(re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))
        head = head.replace("Small sample", "").strip()
        rows[head] = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    return rows


def payload_of(html):
    m = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


# ---------- Compare one filter combination ----------


def compare(client, rows, f, collected):
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    tag = query or "(no filters)"
    response = client.get("/analytics?" + query)
    check(response.status_code == 200, f"{tag}: page status {response.status_code}")
    html = response.get_data(as_text=True)
    trades, _ = independent(rows, f)
    s = summary(trades)
    k = kpis(html)

    expected = {"trades": str(s["trades"])}
    if s["trades"]:
        expected.update(
            {
                "wins": str(s["wins"]),
                "losses": str(s["losses"]),
                "breakevens": str(s["breakevens"]),
                "win_rate": fmt_rate(s["wins"], s["trades"]),
                "average_r": fmt_r(s["mean_r"]),
                "expectancy_r": fmt_r(s["mean_r"]),
                "profit_factor_r": fmt_ratio(s["pf"]),
                "total_r": fmt_r(s["total_r"]),
                "max_drawdown_r": f"{q(s['max_dd'], '0.01')}R",
                "longest_win_streak": f"{s['win_streak']} trade" + ("" if s["win_streak"] == 1 else "s"),
                "longest_loss_streak": f"{s['loss_streak']} trade" + ("" if s["loss_streak"] == 1 else "s"),
            }
        )
    else:
        expected.update(
            dict.fromkeys(
                (
                    "wins",
                    "win_rate",
                    "average_r",
                    "expectancy_r",
                    "profit_factor_r",
                    "total_r",
                    "max_drawdown_r",
                ),
                "n/a",
            )
        )
    for key, value in expected.items():
        check(k.get(key) == value, f"{tag}: KPI {key} page={k.get(key)!r} independent={value!r}")

    # Money per currency, never combined.
    money = defaultdict(Decimal)
    for t in trades:
        money[t["cur"]] += t["net"]
    for cur, net in money.items():
        check(f"{q(net, '0.01')} {cur}" in html, f"{tag}: money {q(net, '0.01')} {cur} missing")

    api = {
        name: client.get(f"/api/v1/analytics/{name}?{query}").get_json()
        for name in ("summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve")
    }
    o = api["summary"]["data"]["overall"]
    check(o["trades"] == s["trades"], f"{tag}: API trades")
    check(abs(Decimal(o["total_r"]) - s["total_r"]) < TOL, f"{tag}: API total_r")

    if not s["trades"]:
        check(payload_of(html) is None, f"{tag}: payload present with no results")
        return

    payload = payload_of(html)
    collected.append({"tag": tag, "payload": payload})

    # Curve: same trades, same order, same exact cumulative values.
    cum = Decimal(0)
    points = payload["curve"][1:]
    check([p["trade_id"] for p in points] == [t["id"] for t in trades], f"{tag}: curve order")
    check(
        [p["trade_id"] for p in api["curve"]["data"]] == [t["id"] for t in trades], f"{tag}: API curve order"
    )
    # strict=False: a length mismatch is already reported by the order check above.
    for p, t in zip(points, trades, strict=False):
        with localcontext() as ctx:  # same precision as independent()
            ctx.prec = 80
            cum += t["r"]
        check(abs(Decimal(p["cumulative_r"]) - cum) < TOL, f"{tag}: curve #{t['id']} cumulative")
        check(p["cumulative_text"] == fmt_r(cum), f"{tag}: curve #{t['id']} text")
    check(abs(Decimal(payload["max_drawdown"]["amount"]) - s["max_dd"]) < TOL, f"{tag}: payload max drawdown")

    # Groups: page table, payload and API all equal the independent figures.
    specs = [
        ("instruments", "table-instruments", lambda t: t["symbol"], lambda key: key, "instruments"),
        (
            "strategies",
            "table-strategies",
            lambda t: t["strategy"] or "Unspecified",
            lambda key: key,
            "strategies",
        ),
        ("sessions", "table-sessions", lambda t: t["session"], lambda key: SESSION_LABELS[key], "sessions"),
    ]
    for name, table_id, key, label, api_name in specs:
        truth = {label(g): v for g, v in grouped(trades, key).items()}
        page_rows = table(html, table_id)
        check(
            set(page_rows) == set(truth), f"{tag}: {name} table rows {sorted(page_rows)} vs {sorted(truth)}"
        )
        for group, v in truth.items():
            cells = page_rows.get(group)
            if not cells:
                continue
            check(cells[0] == str(v["trades"]), f"{tag}: {name}/{group} trades")
            check(
                cells[1] == f"{v['wins']} / {v['losses']} / {v['breakevens']}", f"{tag}: {name}/{group} w/l/b"
            )
            check(cells[2] == fmt_rate(v["wins"], v["trades"]), f"{tag}: {name}/{group} win rate")
            check(
                cells[5] == fmt_r(v["total_r"]),
                f"{tag}: {name}/{group} total {cells[5]} vs {fmt_r(v['total_r'])}",
            )
        pay = {p["label"]: p for p in payload[name]}
        check(set(pay) == set(truth), f"{tag}: {name} payload labels")
        for group, v in truth.items():
            if group in pay:
                check(
                    abs(Decimal(pay[group]["total_r"]) - v["total_r"]) < TOL, f"{tag}: {name}/{group} payload"
                )
        api_rows = {
            (
                SESSION_LABELS[g["group"]]
                if name == "sessions" and g["group"] in SESSION_LABELS
                else g["group"]
            ): g
            for g in api[api_name]["data"]
        }
        check(set(api_rows) == set(truth), f"{tag}: {name} API groups {sorted(api_rows)}")
        # Order: best total R first (instruments/strategies).
        if name != "sessions":
            order = [p["label"] for p in payload[name]]
            check(order == sorted(order, key=lambda g: (-truth[g]["total_r"], g)), f"{tag}: {name} order")

    # Months: every month from first to last, gaps kept.
    month_truth = grouped(trades, lambda t: t["when"][:7])
    first, last = min(month_truth), max(month_truth)
    expected_keys, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        expected_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    check([p["key"] for p in payload["monthly"]] == expected_keys, f"{tag}: month keys")
    for p in payload["monthly"]:
        v = month_truth.get(p["key"])
        if v is None:
            check(p["total_r"] is None, f"{tag}: empty month {p['key']} not null")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: month {p['key']}")
    month_table = table(html, "table-monthly")
    check(len(month_table) == len(expected_keys), f"{tag}: month table rows")

    # Weekdays, UTC.
    def weekday(t):
        return WEEKDAYS[datetime.strptime(t["when"], "%Y-%m-%dT%H:%M:%SZ").weekday()]

    day_truth = grouped(trades, weekday)
    check([p["key"] for p in payload["weekday"]] == WEEKDAYS, f"{tag}: weekday order")
    day_table = table(html, "table-weekday")
    for p in payload["weekday"]:
        v = day_truth.get(p["key"])
        cells = day_table[p["key"]]
        if v is None:
            check(p["total_r"] is None and cells == ["No closed trades"], f"{tag}: {p['key']} empty")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: weekday {p['key']}")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: weekday table {p['key']}")


NODE_SCRIPT = r"""
const fs = require("fs");
const charts = require(process.argv[2]);
const items = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const theme = {text: "#fff", muted: "#aaa", grid: "#333", zero: "#999", accent: "#00f",
               positive: "#0f0", negative: "#f00", font: "sans-serif"};
const out = items.map(({tag, payload}) => {
  const c = charts.buildConfigs(payload, theme);
  const res = {tag};
  for (const name of Object.keys(c)) {
    const cb = c[name].options.plugins.tooltip.callbacks;
    const n = c[name].data.datasets[0].data.length;
    res[name] = {labels: c[name].data.labels || null, data: c[name].data.datasets[0].data,
                 tooltips: Array.from({length: n}, (_, i) => cb.label({dataIndex: i})),
                 titles: Array.from({length: n}, (_, i) => cb.title ? cb.title([{dataIndex: i}]) : null)};
  }
  return res;
});
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def check_tooltips(collected, node):
    work = Path(tempfile.mkdtemp())
    (work / "run.js").write_text(NODE_SCRIPT)
    (work / "in.json").write_text(json.dumps(collected))
    subprocess.run(
        [
            node,
            str(work / "run.js"),
            str(PROJECT / "tradeledger/static/js/analytics.js"),
            str(work / "in.json"),
            str(work / "out.json"),
        ],
        check=True,
    )
    results = json.loads((work / "out.json").read_text())
    for item, res in zip(collected, results, strict=True):
        tag, payload = item["tag"], item["payload"]
        # Curve: every point's tooltip quotes the server's exact display text,
        # and the plotted y equals the exact value converted once.
        for i, p in enumerate(payload["curve"]):
            lines = res["curve"]["tooltips"][i]
            check(res["curve"]["data"][i]["y"] == float(Decimal(p["cumulative_r"])), f"{tag}: curve y {i}")
            if not p.get("start"):
                check(f"Cumulative R: {p['cumulative_text']}" in lines, f"{tag}: curve tooltip {i}")
                check(f"Trade R: {p['realised_text']}" in lines, f"{tag}: trade R tooltip {i}")
                check(
                    ("Exit time not recorded: placed at entry time" in lines) == (not p["close_time_known"]),
                    f"{tag}: legacy note {i}",
                )
            dd = res["drawdown"]["tooltips"][i][0]
            check(
                dd
                == (
                    f"Drawdown: {p['drawdown_text']} below the running peak"
                    if Decimal(p["underwater_r"]) != 0
                    else dd
                ),
                f"{tag}: drawdown tooltip {i}",
            )
        # Bars: tooltip text equals payload text; null periods stay null.
        for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
            for i, row in enumerate(payload[name]):
                lines = res[name]["tooltips"][i]
                if row["total_r"] is None:
                    check(
                        res[name]["data"][i] is None and lines == ["No closed trades"],
                        f"{tag}: {name} gap {i}",
                    )
                else:
                    check(lines[0] == f"Total R: {row['total_r_text']}", f"{tag}: {name} tooltip {i}")
                    check(
                        f"Closed trades: {row['trades']} (won {row['wins']}, lost {row['losses']})" in lines,
                        f"{tag}: {name} count tooltip {i}",
                    )
                    check(res[name]["data"][i] == float(Decimal(row["total_r"])), f"{tag}: {name} value {i}")
    return len(results)


def sign_in_as_audited(app, client):
    """The same session a real sign-in creates, plus an API token."""
    import time

    now = int(time.time())
    with app.app_context():  # Phase 8.5: a real sign-in also writes a session row
        key, _ = sessions.create_session(
            get_db(),
            user_id=AUDITED,
            now=now,
            max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
            user_agent="audit",
        )
    with client.session_transaction() as session:
        session.update({"sid": key, "uid": AUDITED, "sv": 1, "auth_at": now, "seen_at": now})
    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=AUDITED, name="audit", lifetime_days=1, max_active=10
        )
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"


def main():
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else shutil.which("node")
    if not node:
        sys.exit("Node.js is needed to run analytics.js; pass --node /path/to/node")
    if "--pg-bridge" in sys.argv:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    target = sys.argv[sys.argv.index("--database-url") + 1] if "--database-url" in sys.argv else None
    target = target or str(Path(tempfile.mkdtemp()) / "qa.db")
    app = build_database(target)
    rows = raw_rows(app)
    client = app.test_client()
    sign_in_as_audited(app, client)
    with app.app_context():
        instrument_ids = [
            r[0]
            for r in get_db().execute("SELECT id FROM instruments WHERE user_id = ? ORDER BY id", (AUDITED,))
        ]
        decoy_ids = [
            r[0] for r in get_db().execute("SELECT id FROM instruments WHERE user_id <> ?", (AUDITED,))
        ]
    # A decoy instrument id is someone else's: the page and the API must
    # treat it exactly like an id that does not exist.
    for decoy_id in decoy_ids[:2]:
        page = client.get(f"/analytics?instrument_id={decoy_id}")
        check(page.status_code == 400, f"decoy instrument {decoy_id}: page status {page.status_code}")
        api = client.get(f"/api/v1/analytics/summary?instrument_id={decoy_id}")
        check(api.status_code == 400, f"decoy instrument {decoy_id}: API status {api.status_code}")
    strategies = [None, "Breakout", "Pullback", "Scalp", "Reversal"]
    sessions = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
    dates = [(None, None), ("2026-01-01", "2026-02-28"), ("2025-12-15", None), ("2026-03-02", "2026-03-02")]
    collected = []
    combos = 0
    for inst, strat, sess, (start, end) in itertools.product(
        [None, *instrument_ids], strategies, sessions, dates
    ):
        f = {"instrument_id": inst, "strategy": strat, "session": sess, "start": start, "end": end}
        compare(client, rows, f, collected)
        combos += 1
    charted = check_tooltips(collected, node)
    closed = sum(1 for r in rows if r["status"] == "CLOSED")
    legacy = sum(1 for r in rows if r["status"] == "CLOSED" and not r["closed_at"])
    print(f"trades in database: {len(rows)} ({closed} closed, {legacy} with no exit time)")
    print(f"filter combinations: {combos}; with results (charts checked in Node): {charted}")
    print(f"individual comparisons: {checks}; disagreements: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```


# TradeLedger final security review (Phase 8.5, version 0.8.5)

**Scope:**
- authentication: passwords, sessions, two-step verification, password recovery;
- authorisation and user isolation;
- the API and its tokens;
- PostgreSQL privileges;
- migrations;
- logging and errors;
- the self-hosted Chart.js.

This review builds on [`PHASE_8_SECURITY_REVIEW.md`](PHASE_8_SECURITY_REVIEW.md) (findings P8-1 to P8-20) and records what Phase 8.5 changed.

**Method:**
- a code review against each threat (repeated in the final audit on 16 November);
- the genuine psycopg, psycopg_pool and Gunicorn in the final audit;
- adversarial automated tests on SQLite and PostgreSQL;
- **security mutation testing:** 40 planted security bugs, all caught (QA report §5);
- a production-configuration HTTP check over raw HTTP.

**What this is not:** an independent penetration test or a certification.

## 1. What Phase 8.5 changed

| # | Weakness in Phase 8 | Status in 8.5 | Evidence |
|---|---|---|---|
| S1 | **A cookie copied before sign-out stayed valid** until it expired, because signed cookies cannot be "un-signed" | **Fixed.** Every sign-in creates a `user_sessions` row. The cookie holds a random 256-bit key; the database stores only its SHA-256. Sign-out revokes the row, so the server refuses the copied cookie | `test_a_cookie_copied_before_sign_out_is_refused_afterwards`; raw-HTTP replay check (QA §9) |
| S2 | **No way to sign out one device** (P8-5 residual) | **Fixed.** The Account page lists each signed-in browser (a short label such as "Firefox on Windows", the sign-in time, the last activity) with its own Sign out button. Another user's session id gets 404 | `test_device_sessions.py` (27 tests), `test_browser_account_security.py` |
| S3 | **Idle and absolute limits were checked against values inside the cookie** | **Strengthened.** They are now also checked against the server's row. Rewriting `seen_at` in a cookie gains nothing | `test_editing_activity_inside_the_cookie_does_not_extend_a_session`, `test_the_server_enforces_the_absolute_limit_even_if_the_cookie_is_edited` |
| S4 | **No password recovery** (P8-15) | **Fixed, without email.** An operator runs `create-reset-link` and passes on a one-time, 30-minute link; the user chooses the new password, so the operator never sees it (unlike `set-password`). Details in §3 | `test_password_reset.py` (29 tests) |
| S5 | **No multi-factor authentication** | **Fixed (optional per user):** TOTP two-step verification with recovery codes (§4) | `test_two_step.py` (48 tests), including the RFC 4226 and RFC 6238 test vectors |
| S6 | **The app connected to PostgreSQL as the table owner** (P8-11) | **Tooling fixed; deployment step open.** `grant-app-role` gives an existing role only SELECT, INSERT, UPDATE and DELETE on the eight application tables, read access to `schema_migrations`, and sequence use. It refuses superusers, roles that can create roles or databases, and the owner itself. The whole application was run **as such a role** on PostgreSQL 16, and every kind of DDL was refused. **Open:** the Terraform and the deploy workflow still connect as the RDS master user (§6) | `test_postgres_roles.py` (17 tests) |
| S7 | **`set-password` did not revoke API tokens**, although the Phase 8 guide said it did | **Fixed:** sessions and tokens both end | `test_set_password_also_revokes_api_tokens` |
| S8 | **The development server's access log printed full URLs.** A reset link's token would appear in the terminal | **Fixed:** the redaction filter now also covers the `werkzeug` logger. Production was not affected: Gunicorn runs with its access log off, and TradeLedger's own access log drops query strings | `test_the_development_server_access_line_hides_the_reset_token` (fails without the fix) |
| S9 | **The test-only PostgreSQL bridge could in principle be registered in a real deployment** | **Fixed.** Only a module marked `TEST_ONLY` can replace psycopg, and the production configuration refuses to start while one is registered. The single exemption is a flag set at run time by `tests/conftest.py`; `tests/` is not in the Docker image | `test_database_safety.py` (4 tests); mutant caught |
| S11 | **Final audit:** the PostgreSQL connection pool was never closed, so each process took about 20 s to exit and Gunicorn workers could be killed mid-shutdown (`graceful_timeout` is 20 s) | **Fixed:** the pool is closed at exit. Found only when the genuine psycopg_pool was tested | `test_a_process_with_an_open_connection_pool_exits_promptly` (fails without the fix); Gunicorn stops in 0.4 s |
| S10 | **Chart.js came from a third-party CDN** (supply chain, availability) | **Fixed.** The file is served by TradeLedger itself with an SRI hash, and the CSP stays `script-src 'self'`. It was built from the official source tags, and its provenance and licences are recorded in `static/vendor/chartjs/` | `test_chartjs_bundle.py`, `test_chartjs_real.py` |

## 2. Sessions in detail

```
sign-in ──► row (user_id, SHA-256(key), created, last_seen, expires, short UA label)
cookie  ──► { sid: key, uid, sv, auth_at, seen_at, csrf }  (signed with SECRET_KEY)
request ──► cookie signature ✓ → user active ✓ → sv == users.session_version ✓
            → row exists, same user, not revoked, now < expires, now − last_seen ≤ idle ✓
```

- **Two layers, each tested on its own.** Mutation testing showed that the older cookie checks had become untested once the rows existed. Two tests now break only the cookie-side condition.
- **A password change, "sign out everywhere else", deactivation and `set-password`** all bump `session_version` **and** revoke every row. The browser that made the change gets a new session.
- **Privacy:**
  - no IP address is stored;
  - the user agent is reduced to a label such as "Safari on iOS", so it never holds the raw header;
  - expired and revoked rows older than a day are deleted at the user's next sign-in.
- **Cost:** one indexed lookup per request, measured at 0.018 ms on SQLite together with the user lookup. "Last seen" is written at most once a minute.

## 3. Password recovery in detail

| Property | How |
|---|---|
| Unguessable | `tlr_` + 256 random bits; only SHA-256 stored |
| Short-lived | 30 minutes; a newer link cancels older ones |
| Single use | `UPDATE … WHERE used_at IS NULL`. Four simultaneous uses: exactly one succeeds (`test_one_reset_link_used_twice_at_once_works_only_once`) |
| Kept out of URLs and logs | Opening the link stores the token in the signed session and redirects to `/reset-password`, so it leaves the address bar, history and Referer (`Referrer-Policy: no-referrer` on that page). The query string is redacted in logs; production has no URL access log |
| Ends access | All sessions revoked and all API tokens revoked |
| Does not bypass two-step | The user is **not** signed in automatically; two-step still applies at the next sign-in |
| Same answer for every failure | Unknown, used, expired or malformed links all get one message; failures are throttled per address (429) |
| Atomic | The link use, the new hash and the revocations are one transaction. A forced failure half-way leaves the password and the link untouched (`test_a_failure_while_resetting_a_password_leaves_the_link_and_password_unchanged`) |
| Operator safety | The command refuses deactivated accounts, and base URLs that are not HTTPS (localhost is allowed for testing) |

**Why not email:** there is no email service, and a form that pretends to send email would be a fake feature.

**Residual risk:** the channel used to hand over the link must be trusted. The operator should confirm the person's identity first.

## 4. Two-step verification in detail

- **Codes:** TOTP as defined in RFC 6238 (HMAC-SHA1, 30-second steps, 6 digits), written with the standard library. It is checked against the official RFC test vectors.
- **Window and replay:** codes are accepted one step either side of the current one. The last accepted step is stored with an atomic `UPDATE … WHERE totp_last_step < ?`, so a code cannot be replayed, even by two simultaneous requests.
- **Secret storage:**
  - encrypted with AES-GCM, with a key derived from `SECRET_KEY` by HKDF-SHA256;
  - the user id is bound as associated data, so a secret copied onto another user's row does not decrypt;
  - after a `SECRET_KEY` rotation, codes are **refused, never skipped** (fails closed), and recovery codes still work.
- **Setting it up** needs the current password **and** a working code. The new secret lives only in this browser's signed session until it is confirmed.
- **Recovery codes:** 10 codes of 60 random bits each, shown once and stored as SHA-256. Each works once, and generating new ones cancels the old set.
- **Sign-in:** after a correct password, only a "pending" note is kept, valid for 5 minutes and tied to the session version. No session starts until the code is right. Wrong codes are throttled per account (5 in 15 minutes) and per address.
- **Turning it off** needs the password and a code (or a recovery code). An operator command, `disable-two-step`, handles a lost phone and ends all sessions.
- **Not implemented:** WebAuthn/passkeys (stronger, but needs a JavaScript ceremony and an attestation library); making two-step mandatory for all users.

## 5. The rest of the checklist

| Area | Status |
|---|---|
| **CSRF** | Every form, including sign-in, two-step, reset, per-session sign-out and two-step management. The API ignores cookies. New tests post without a token for each new form |
| **XSS** | Jinja auto-escaping and a strict CSP (`script-src 'self'`, no inline scripts). User-agent labels are fixed strings, never echoed headers (`test_user_agent_is_reduced_to_a_short_label` includes a script tag) |
| **SQL injection** | Parameterised everywhere. The role name in `grant-app-role` is checked against a strict pattern before it is quoted into DDL, and injection attempts are refused before any SQL runs |
| **Rate limiting** | Database-backed throttles for sign-in (account and address), registration, the two-step code, password checks on account pages, and reset links. Throttles do not stop a large botnet: that needs WAF rate rules (open) |
| **User isolation** | Unchanged and still proven (36 tests, plus the decoy account in the audit, on both engines). New: another user's session id and recovery code are refused |
| **Token revocation** | API tokens are revoked on password change, reset and `set-password`. Sessions are revoked as in §2 |
| **Safe migrations** | Migration 4 only adds tables and nullable columns. A process killed during the SQLite migration leaves version 3 intact and the next start finishes it. A PostgreSQL migration whose server process was terminated half-way left nothing behind (both tested) |
| **Leakage through logs and errors** | Redaction covers `tl_` tokens, `tlr_` links, Bearer values, password fields and database URLs, now including the `werkzeug` logger. Error pages show no stack traces. The reset and two-step pages give one message for every failure |
| **Security headers** | CSP, HSTS (production), X-Frame-Options DENY, nosniff, `no-store`, and Referrer-Policy (`no-referrer` on the reset page) |

## 6. Residual risks and open items

1. **The deployment still uses the RDS master user.** Using the least-privilege role in AWS needs these steps, none done (nothing may be deployed):
   - a second secret;
   - a migration task that runs as the owner;
   - `grant-app-role` after each migration;
   - the service connecting as the app role.

   They are described in the guide.
2. **Password recovery depends on the operator and a trusted channel.** There is no self-service email reset.
3. **Two-step is optional per user,** and SMS or email codes are deliberately not offered. Passkeys are not offered either.
4. **The TOTP secrets depend on `SECRET_KEY`.** Rotating it disables codes until users re-enrol or use recovery codes.
5. **No WAF:** distributed password guessing is limited only by per-account throttles and Argon2id's cost.
6. **Supply chain:**
   - The Chart.js file is a source build, not the npm file (CI compares the two; not yet run).
   - The Docker base image is pinned by tag, not digest.
   - pip-audit has not run. Instead, an offline check against the PyPA Advisory Database (final audit) found 0 known vulnerabilities in the 32 pinned packages.
7. **Unverified here:** the Docker image, `terraform validate`, GitHub Actions, any AWS resource, psycopg's C implementation, Firefox, Safari and screen readers. In the final audit, the genuine psycopg 3.3.8 (pure-Python implementation), psycopg_pool 3.3.0 and Gunicorn 26.0.0 **were** tested.
8. **Registration in production** stays off, because it would reveal which emails are registered.
9. **API tokens are not bound to two-step.** A token created by a two-step user works without codes until it expires (at most 365 days) or is revoked. By design, but a reviewer should confirm it is acceptable.
10. **`SECRET_KEY` has two jobs:** it signs cookies and derives the TOTP encryption key. A separate key would make rotation independent.

## 7. Security definition of done

| Requirement | Status |
|---|---|
| Authentication cannot be bypassed | ✅ Default-deny route walk; sign-out really ends sessions; 40/40 mutants caught |
| Password recovery cannot be abused | ✅ Single use, expiry, no auto sign-in, throttled, atomic, logs clean |
| Two-step cannot be bypassed | ✅ No session before the code; replay and reuse refused; fails closed on key rotation; reset and set-password do not turn it off |
| Users cannot access other users' data or sessions | ✅ Both engines |
| Tokens and sessions revocable | ✅ |
| CSRF, XSS, SQLi protections | ✅ |
| Least-privilege database access | ⚠️ Tooling and tests ✅; AWS wiring open |
| No secrets in logs, errors or the ZIP | ✅ Tests, plus the clean-extraction scan |
| Independent penetration test | ❌ Not done |

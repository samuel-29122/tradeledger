# Security policy

## What TradeLedger is (and is not)

Since Phase 8 (version 0.8.0), TradeLedger has user accounts:
- every page with trading data needs a signed-in user;
- the JSON API needs a personal API token;
- each account can see and change only its own records.

It is designed to run locally, in Docker Compose, or on AWS behind HTTPS.

Since Phase 8.5 (version 0.8.5) it also has:
- optional two-step verification (TOTP);
- a server-side record of every signed-in browser, each one revocable;
- one-time password-reset links issued by the administrator;
- a least-privilege PostgreSQL role command.

It has **not** had an independent security assessment. The security tests show that specific attacks are blocked. That is not the same as being production-secure. Things that are still missing:
- email-based password recovery and email verification (there is no email service);
- passkeys;
- protection against large distributed attacks (that needs an edge service such as AWS WAF).

The full review is in [`docs/TRADELEDGER_FINAL_SECURITY_REVIEW.md`](docs/TRADELEDGER_FINAL_SECURITY_REVIEW.md).

## Supported versions

Only the latest version (currently 0.8.0) receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private vulnerability reporting on this repository (Security tab → "Report a vulnerability") if the maintainer has enabled it, or contact the maintainer privately through their GitHub profile.

Include:

1. what you found and where (file, route or setting);
2. how to reproduce it (a minimal request or steps);
3. the impact you expect;
4. the version (`/health` shows it).

You will get an acknowledgement and, if the report is confirmed, a fix with a test that proves it, noted in `CHANGELOG.md`.

## Running TradeLedger on a server

1. Set `TRADELEDGER_CONFIG=production`. The app then refuses debug mode and weak secret keys, marks cookies Secure, and sends HSTS.
2. Set a random `SECRET_KEY` of at least 32 characters, or `SECRET_KEY_FILE` pointing at a mounted secret.
3. Serve it **only over HTTPS**.
4. Restrict host names: either set `TRADELEDGER_TRUSTED_HOSTS` to the names you serve, or let a load balancer rule do it. The AWS design does the latter.
5. Behind exactly one reverse proxy or load balancer, set `TRADELEDGER_PROXY_HOPS=1`. Never set it without a proxy: clients could fake their address and get around the sign-in limits.
6. Keep self-registration off (the production default) and create accounts with `flask --app tradeledger create-user`.
7. Keep the database private. Use PostgreSQL with TLS (`DB_SSLMODE=require`) and back it up (see [`docs/PHASE_8_DATABASE_MIGRATION.md`](docs/PHASE_8_DATABASE_MIGRATION.md)).
8. Run the web app as a PostgreSQL role that cannot change the tables: create it, run `flask --app tradeledger grant-app-role --role <name>` as the table owner after every migration, and give the app that role's credentials (see the final guide).
9. Set `TRADELEDGER_PUBLIC_URL` to your HTTPS address, so `create-reset-link` prints correct links. Confirm a person's identity before giving them a link.
10. Treat `SECRET_KEY` rotation as an event: it also invalidates stored two-step secrets, so users then sign in with a recovery code and set two-step up again.

The settings `TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL` and `TRADELEDGER_ENABLE_UNAUTHENTICATED_API` no longer exist. There is no way to switch authentication off.

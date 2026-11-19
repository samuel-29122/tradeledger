# Phase 8 release checklist

Tick each item before tagging `v0.8.0` on GitHub. Items marked **(open)** could not be done in the build environment and must be done by a person with the right access.

## Code and tests

- [x] `ruff check .` and `ruff format --check .` clean
- [x] `python -m pytest` on SQLite: 1,259 passed, 32 skipped, 0 failed
- [x] Full suite on PostgreSQL 16: 1,248 passed, 43 skipped, 0 failed (test-only libpq bridge)
- [x] `node --test tests/js/*.test.js`: 11/11
- [x] Independent audit on SQLite and PostgreSQL: 0 disagreements
- [x] Security mutants: 20/20 caught
- [x] Backup → restore round trip identical (synthetic data)
- [ ] **(open)** CI green on GitHub: python (3.11 and 3.13), postgres (psycopg), javascript, audit (pip-audit), browser (including **real Chart.js**), docker, terraform
- [ ] **(open)** Real Chart.js screenshots uploaded by the CI `browser` job, then committed to `docs/screenshots/`

## Security

- [x] Every website route private unless listed (route-walking test)
- [x] Two-user isolation tests pass on both engines
- [x] No secrets in the repository (scan test); `.gitignore` covers `.env`, `secrets/`, Terraform state and `*.tfvars`
- [x] Production refuses debug mode, weak keys and the removed "no authentication" settings
- [ ] **(open)** Decide on the RDS application role (security review P8-11) before storing real data
- [ ] **(open)** Pin `python:3.13-slim` by digest after the first successful CI build

## Packaging

- [x] `tradeledger_phase8.zip` built from the tested tree; no `.env`, databases, secrets, caches, virtual environments or Terraform state
- [x] Clean extraction: tests, lint, audit and smoke checks re-run in a fresh folder
- [x] `CHANGELOG.md` has 0.8.0; `APP_VERSION` is "0.8.0"

## Before any AWS deployment

- [ ] **(open)** AWS Budget with an email alert
- [ ] **(open)** State bucket: private, versioned, encrypted
- [ ] **(open)** ACM certificate issued; domain ready
- [ ] **(open)** GitHub environment `production` with required reviewers and the variables in `docs/PHASE_8_AWS_DEPLOYMENT.md`
- [ ] **(open)** `terraform plan` reviewed line by line by a second person
- [ ] **(open)** After the first deployment: sign in, change the first password, run a restore drill to a new RDS instance, then delete the drill instance

## Manual checks still worth doing

- [ ] **(open)** Sign in and use the account page with a screen reader (NVDA or VoiceOver)
- [ ] **(open)** Firefox and Safari: the `__Host-` cookie on `http://localhost` (Compose) and on HTTPS
- [ ] **(open)** Password manager save and fill on the sign-in and change-password forms

# Contributing to TradeLedger

TradeLedger is a personal portfolio project, but it is run like a team project: every change goes through a branch, a pull request and CI.

## Set up

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env                                    # then set SECRET_KEY
flask --app tradeledger init-db
flask --app tradeledger create-user --email you@example.com
flask --app tradeledger seed-db --email you@example.com
```

PostgreSQL tests (optional; CI always runs them): start a PostgreSQL 16 server, then

```bash
TRADELEDGER_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tradeledger_test python -m pytest
```

Optional, for browser tests: `pip install -r requirements-browser.txt -c constraints.txt` then `python -m playwright install chromium`.

## Workflow

1. **Branch** from `main`: `git switch -c fix/short-description` (prefixes: `feat/`, `fix/`, `test/`, `docs/`, `chore/`).
2. **Write a failing test first** for a bug or a new rule, then make it pass.
3. **Keep commits small and single-purpose.** A formatting-only change goes in its own commit, never mixed with behaviour changes.
4. **Run the checks before pushing:**

   ```bash
   ruff check .
   ruff format --check .
   python -m pytest
   node --test tests/js/*.test.js
   ```

   Touching SQL? Also run the suite with `TRADELEDGER_TEST_DATABASE_URL` set (PostgreSQL). Touching `infra/`? Run `terraform fmt -recursive` and `terraform validate` in `infra/`.

5. **Open a pull request.** The template asks what changed, why, and how it was tested. CI must pass before merging.
6. **Update `CHANGELOG.md`** under "Unreleased" for anything a user would notice.

## Rules that keep the project trustworthy

- **Money and R are `Decimal`, never `float`.** Round only for display.
- **Expected values in tests are worked out by hand** (show the arithmetic in the docstring), never by calling the function under test.
- **Never weaken or delete a test to make the suite pass.** If a requirement deliberately changes, change the test's expectation and say why in the test and the pull request.
- **SQL is always parameterised.** Values go in `?` placeholders; only fixed column names from the code may be placed into SQL text.
- **Every repository call names its owner** (`user_id=...`). A new query on user data must filter by `user_id`, and the change needs a two-user test in `tests/test_isolation.py`.
- **New routes are private by default.** Adding one to `PUBLIC_ENDPOINTS` (web) or `PUBLIC_API_ENDPOINTS` (API) needs a reason in the pull request.
- **Write SQL that runs on both SQLite and PostgreSQL**, or branch explicitly on `conn.dialect` and test both.
- **Never log secrets or personal data:** no passwords, tokens, cookies, full database URLs or trade notes. The log redaction is a safety net, not a licence.
- **Schema changes are migrations:** add version N to `MIGRATIONS` (SQLite) **and** `POSTGRES_MIGRATIONS`, with an upgrade test.
- **No inline `<script>` or `style=""`** in templates: the Content-Security-Policy forbids them.
- **Label Chart.js browser tests honestly** with `chartjs_stub`, `chartjs_offline` or `chartjs_real`.
- **No real trading data** in the repository, tests or screenshots: use the synthetic sample data.
- **Pytest treats warnings as errors** (see `pyproject.toml`). Fix the cause, for example close file responses, rather than silencing the warning.

## Updating dependencies

Change the pin in the right `requirements*.txt` file, update `constraints.txt` to the versions actually installed (`pip list --format=freeze`), run every check, and record the change in `CHANGELOG.md`. Details: `docs/PHASE_8_RELEASE_CHECKLIST.md`.

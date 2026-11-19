# Release checklist

Use this for every release (a version bump in `tradeledger/config.py`). Tick each line; if a line cannot be done, write down why in the release notes rather than skipping it silently.

## 1. Before the release branch

- [ ] Every change since the last release is merged through a pull request with green CI.
- [ ] `CHANGELOG.md` has a section for the new version (Security / Performance / Added / Changed / Fixed).
- [ ] `APP_VERSION` in `tradeledger/config.py` is updated; `/health` shows it.
- [ ] The About page roadmap is up to date (`tradeledger/web/views.py`).

## 2. Quality gates (all must pass)

```bash
ruff check .
ruff format --check .
python -m pytest                                    # warnings are errors
python -m pytest --cov=tradeledger --cov-report=term-missing
node --test tests/js/*.test.js
python scripts/qa_dashboard_agreement.py            # must print "disagreements: 0"
pip-audit
```

- [ ] Coverage did not drop below the previous release's figure without a written reason.
- [ ] Browser tests ran (`python -m pytest -m browser -ra`); the summary's skips are only the expected `chartjs_real` ones when the library file is absent.
- [ ] `python -m pytest -m chartjs_real -ra` passed with the real Chart.js file (locally or in CI). If not, the release notes say "real chart rendering not verified".
- [ ] OpenAPI document regenerated and committed if the API changed (`flask --app tradeledger openapi`; `test_docs_openapi_json_is_up_to_date` checks it).

## 3. Dependencies

- [ ] Every direct dependency is pinned with `==` in `requirements*.txt`.
- [ ] `constraints.txt` matches `pip list --format=freeze` for the indirect dependencies.
- [ ] `pip-audit` reports no known vulnerabilities (or each is assessed in the release notes).
- [ ] If Chart.js changed: version updated in `config.py`, `tests/test_chartjs_real.py` and the CI `npm pack` step; integrity hash recomputed; notices updated.

## 4. Database

- [ ] Any new migration is numbered, transactional, idempotent and tested from the previous version (`tests/test_migrations.py`, `tests/test_database_integrity.py`).
- [ ] `migrate-db` backs up the database before migrating, and the upgrade steps are in the README.

## 5. Package and verify

- [ ] Build the ZIP from a clean working tree: no `.env`, `instance/`, `*.db`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `.coverage`.
- [ ] Extract it into an empty folder and, there: create a fresh database (`init-db`, `seed-db`), run the full test suite, start the server and request every page.
- [ ] Compare the extracted files with the source tree (no missing or extra files).
- [ ] Screenshots in `docs/screenshots/` match the release, and any caption says honestly what was and was not rendered.

## 6. Publish

- [ ] Tag the release: `git tag -a v0.7.0 -m "TradeLedger 0.7.0"` then `git push --tags`.
- [ ] Release notes = the changelog section + the "not verified" list from the QA report.

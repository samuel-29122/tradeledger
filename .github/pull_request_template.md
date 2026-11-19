## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem this solves, or the requirement it meets. -->

## How it was tested

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `python -m pytest` passes (new behaviour has a test that failed before the change)
- [ ] `node --test tests/js/*.test.js` passes (if JavaScript changed)
- [ ] Browser tests run, or the reason they were not is stated here
- [ ] Any Chart.js browser test is labelled `chartjs_stub`, `chartjs_offline` or `chartjs_real`

## Checks for this kind of change

- [ ] Money and R stay `Decimal` (no floats); expected test values are worked out by hand
- [ ] SQL values are bound parameters
- [ ] No test was weakened or removed to make the suite pass (if an expectation changed, it is explained above)
- [ ] `CHANGELOG.md` updated under "Unreleased" (if a user would notice)
- [ ] No secrets, `.env`, databases or real trading data are included

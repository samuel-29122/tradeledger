# TradeLedger — Phase 3 Guide: Python Trading Analytics Engine

## Verification summary

Run before delivery on Linux, with Python 3.13.18, SQLite 3.53.1, Flask 3.1.3 and pytest 9.1.1:

- **Full suite: 261 passed.** That is the 145 existing tests plus 116 new ones.
- `flask report` was run against the synthetic database. Its output is in Part G.
- Every sample-data metric matches hand-worked exact fractions to **55 decimal places**.
- **Mutation checks:** removing fees from net P&L made 30 tests fail. Starting the drawdown peak below zero made 3 fail.
- **Phase 1 and 2 still work:** the home page, `/health` (now version 0.3.0), the 404 page, `init-db`, `seed-db` (idempotent), `db-info` and `analytics` all ran.
- The Phase 2 SQL win-rate query agrees with the Python engine on every instrument.

**Not verified:** Windows and macOS (only Linux was used), Python 3.11 and 3.12, and real broker contract values.

**Two bugs were found and fixed during development:**

1. **A precision leak in `profit_factor()`.** The negation in `gains / -losses` ran outside the 60-digit Decimal context. In Python's `decimal` module even a unary minus rounds to the current precision (28 digits by default). The result was wrong in the 29th significant digit, which is invisible on screen but still a leak. The test that compares against exact fractions caught it; a test that only checked the engine against its own arithmetic would not have.
2. **A wrong expected value in one of my tests.** I expected +3R for a trade but had forgotten that its fees reduce R, which made it +2.8R. The engine was right and the test was wrong. It's the same lesson as sample trade 9: fees count.

---

## Part A — Architecture and objectives

**Plain English.** Phase 2 stored trades. Phase 3 is the calculator: it reads closed trades from the database and works out how well they performed. That means win rate, average R, expectancy, drawdown, streaks and money per currency, broken down by instrument, strategy, session, day and month.

**Technical version.** `tradeledger/performance/` is a package of pure functions and frozen dataclasses in four layers:

```
   database ──► repositories.trades.list_trades()      (Phase 2, unchanged API)
                         │  list of trade dicts (Decimals)
                         ▼
 report.py      build_report(conn, filters) ──► analyse(trades)
                         │  splits OPEN / excluded / closed
                         ▼
 trade_math.py  evaluate_trade(trade) ──► TradeResult   (one trade: P&L, risk, R)
                         │  list of TradeResults
                         ▼
 metrics.py     summarise(results) ──► Summary          (all metrics for a group)
                         │
                         ▼
 report.py      PerformanceReport(overall, by_instrument, by_strategy,
                                  by_session, by_weekday, by_date, by_month,
                                  cumulative_r, open_trades, excluded, r_unavailable)
                         │
                         ▼
 formatting.py  to_jsonable(report)   exact JSON (Decimals as strings)
                format_r / format_money / format_rate   rounded display text
                         │
                         ▼
 cli.py         flask report   (today)      Flask routes and templates (Phase 4-6)
```

### Key decisions

| Decision | Problem it solves | Alternatives | Trade-off |
|---|---|---|---|
| **Pure functions** (no database, no Flask) | Calculations are testable with plain dicts in milliseconds | Methods on a `Trade` class, or SQL views | Data must be passed in explicitly. That is a feature: nothing is hidden. |
| **Frozen dataclasses** for results (`TradeResult`, `Summary`, `MoneySummary`, `Drawdown`, `TradeRef`, `CurvePoint`, `PerformanceReport`) | Named, typed fields instead of anonymous dicts; immutable, so nothing downstream can alter a result; `==` works, which tests rely on | Plain dicts, NamedTuple, Pydantic | Slightly more code than dicts. Pydantic would be an extra dependency for no gain at this stage. |
| **`None` means "cannot be calculated"** | Never invent a value (e.g. profit factor with no losses) | `0`, `float('inf')`, raising an error | Callers must handle `None`. The display layer shows `n/a`. |
| **One 60-digit Decimal context (`EXACT`)** | Products of 15-digit inputs stay exact; the global default of 28 digits is never relied on | Python's default context | Every arithmetic block must use `localcontext(EXACT)`. The bug above shows why the tests matter. |
| **Python calculates, SQL filters** | SQLite has no exact decimal type | All-SQL analytics | Every matching row is loaded into memory. That's fine for tens of thousands of trades; see Part K. |
| **Rounding only at display** | Rounding early compounds errors (tested) | Round when storing or calculating | JSON strings are long. That's correct, because the receiver decides the rounding. |

### Minimal change to Phase 2 code

`list_trades()` and `get_trade()` now also return `quote_currency` and `contract_multiplier` (joined from `instruments`). The engine needs both. The change only adds fields, so all Phase 2 tests still pass unchanged.

---

## Part B — Financial formulas and assumptions

All amounts are in the instrument's **quote currency**. `sign` is +1 for LONG and −1 for SHORT.

### Per trade (`trade_math.py`)

| Metric | Formula | Example (sample trade 1: EURUSD long, 1 lot, multiplier 100000, fees 7) |
|---|---|---|
| Price move | sign × (exit − entry) | 1.089 − 1.085 = 0.004 |
| **Gross P&L** | price move × size × multiplier | 0.004 × 1 × 100000 = 400 USD |
| **Net P&L** | gross − fees | 400 − 7 = 393 USD |
| Risk per unit | sign × (entry − stop); must be > 0 | 1.085 − 1.083 = 0.002 |
| **Initial risk** | risk per unit × size × multiplier | 0.002 × 1 × 100000 = 200 USD |
| **Realised R** | net P&L ÷ initial risk | 393 ÷ 200 = **+1.965R** |
| **Planned R:R** | sign × (target − entry) ÷ risk per unit | 0.004 ÷ 0.002 = **2.00** |
| Outcome | WIN if net > 0, LOSS if net < 0, BREAKEVEN if net = 0 exactly | WIN |

**Planned R:R vs realised R.** Planned R:R is the plan, made before the trade: price-based, ignoring fees. Realised R is what actually happened: after fees and in money terms. Trade 1 planned 2.00 and achieved 1.965R. The 0.035R gap is the 7 USD of fees divided by the 200 USD risked.

**Assumptions and conventions**

1. **Initial risk excludes fees.** It is the money lost if price reaches the stop. A stopped-out trade with fees is therefore slightly worse than −1R: sample trade 2 is −1.035R. This is deliberate, because it shows the true cost. The alternative is to include expected fees in risk; that makes stop-outs exactly −1R but hides the cost.
2. **Fees are in the quote currency** (carried over from Phase 2).
3. **Breakeven means net P&L exactly zero.** Exit at entry with fees is a **loss** (tested). There is no tolerance band. The alternative is a "scratch" zone such as ±0.1R. That's a reasonable product choice, but it changes win rates, so it would have to be explicit and documented.
4. **R is currency-neutral.** Converting both net P&L and risk to the same other currency multiplies both by the same rate, so R is unchanged. That's why R can be totalled across instruments while money cannot.
5. **R still depends on correct instrument data.** If `contract_multiplier` is wrong, P&L and risk are both wrong by the same factor, so R happens to stay correct. But if position size and multiplier use mismatched definitions for one trade, the money figures are wrong. R does not remove the need for correct contract specifications.
6. **Open trades are excluded** from every realised metric and only counted (`open_trades`).
7. **Invalid risk** (stop on the wrong side, or equal to entry) keeps the trade's money and outcome but gives `realised_r = None` and a reason. Such a trade is counted in win rate but not in R metrics, and `r_trades` shows how many trades the R metrics are based on.
8. **Incomplete data** (closed with no exit, a missing multiplier, a float, a bad date…) excludes the trade, and it is listed in `excluded` with the reason. No value is guessed.

### Across trades (`metrics.py`)

Using the 14 closed sample trades:

| Metric | Formula | Sample value | `None` when |
|---|---|---|---|
| Win / loss / breakeven rate | count ÷ closed trades (0–1) | 0.5 / 0.357 / 0.143 | no trades |
| Total R | Σ realised R | +7.688R | — (0 when empty) |
| Average R | Σ R ÷ trades with R | +0.549R | no trades with R |
| Average win R | mean R of wins | +1.692R | no wins |
| Average loss R | mean R of losses (negative) | −0.831R | no losses |
| **Expectancy (R)** | P(win) × avg win + P(loss) × avg loss + P(BE) × 0 | 0.5 × 1.692 + 0.357 × (−0.831) = **+0.549R** | no trades with R |
| **Profit factor (R)** | Σ positive R ÷ \|Σ negative R\| | 11.845 ÷ 4.157 = **2.85** | no losing trades, or no trades |
| Profit factor (money) | same, using net P&L, **per currency** | USD 1278.50 ÷ 480 = 2.66 | as above |
| Average planned R:R | mean over trades with a valid target | 2.07 (12 trades) | no targets |
| Longest win / loss streak | longest consecutive run in date order; a breakeven ends a run | 2 / 2 | — (0) |
| **Max drawdown (R)** | largest (running peak − current) of cumulative R, peak starting at 0 | **1.05R** (after trade 7, through trade 9) | — (0) |
| Max drawdown (money) | same on cumulative net P&L, **per currency** | USD 243.50 | — (0) |
| Best / worst trade | highest / lowest R (overall), net P&L (per currency); ties go to the earliest | #10 +2.00R / #12 −1.05R | no trades |

**Why expectancy equals average R.** Average R = (Σ win R + Σ loss R + Σ BE R) ÷ n. Split the sums: (n_win/n) × (Σ win R / n_win) + (n_loss/n) × (Σ loss R / n_loss), and the BE term is zero. That is exactly the expectancy formula. The engine calculates it from the parts (to match how it's taught), and a test plus 5 random property tests check that both give the same number.

**About the names "gross profit" and "gross loss".** Trading reports often call the sum of winning trades "gross profit", which is easily confused with gross P&L (before fees). TradeLedger avoids the clash: `gross_pnl` means before fees, and `winning_pnl` / `losing_pnl` mean the sums of net P&L of winners and losers.

### What "drawdown" means here (and what it doesn't)

- `max_drawdown_r` is measured **in R**, on the cumulative-R series.
- `money[cur].max_drawdown` is measured **in that one currency**, on that currency's cumulative net P&L.
- **No percentage drawdown** is calculated. A percentage needs an account balance over time, which TradeLedger doesn't record. Inventing a starting balance would make the figure meaningless.
- **Cumulative R is not an equity curve.** It shows performance in risk units, assuming every trade risked "1R". If you risked different amounts on different trades, your account's actual path differs. Every label in the code and output says so.

### Limitations to state in an interview

- **One date per trade (the entry time).** Ordering, streaks, drawdown and date groups use it. A trade opened Monday and closed Wednesday counts as Monday.
- **Day and month groups use UTC** (see the Phase 2 guide).
- **Money is never converted between currencies.** Combining them needs historical exchange rates at each trade's close, which is a separate feature.
- **Small samples.** Every summary includes its trade count. 2–4 trades per instrument tells you almost nothing statistically; the dashboard should show `n` next to every rate.
- **Display rounding uses 2 decimal places for every currency.** JPY would need 0.

### Rounding policy

| Stage | Rule |
|---|---|
| Storage | Exact canonical text (Phase 2) |
| Calculation | Exact for + − ×; division rounded at the 60th significant digit (`EXACT` context, ROUND_HALF_EVEN) |
| JSON / API | Never rounded; Decimal → string, no floats (`to_jsonable`) |
| Display | R and ratios 2 dp, money 2 dp, rates 1 dp, ROUND_HALF_UP (`format_*`) |

Why late? `test_rounding_late_is_more_accurate_than_rounding_early` shows three trades of +0.005R: rounding each first gives 0.03R, while the true 0.015R displays as 0.02R.

---

## Part C — Updated project structure

```
tradeledger/
├── tradeledger/
│   ├── performance/                   NEW package
│   │   ├── __init__.py                NEW  public interface: analyse, build_report
│   │   ├── trade_math.py              NEW  per-trade P&L, risk, R, planned R:R
│   │   ├── metrics.py                 NEW  Summary, MoneySummary, all aggregate metrics
│   │   ├── report.py                  NEW  grouping, cumulative R, PerformanceReport
│   │   └── formatting.py              NEW  JSON conversion and display rounding
│   ├── repositories/trades.py         MODIFIED  rows include quote_currency, contract_multiplier
│   ├── cli.py                         MODIFIED  new `report` command
│   ├── config.py                      MODIFIED  version 0.3.0
│   ├── routes.py                      MODIFIED  roadmap: Phase 3 Done, Phase 4 Next
│   └── (everything else unchanged)
├── tests/
│   ├── factories.py                   NEW  make_trade(), r_trade(), dated()
│   ├── test_trade_math.py             NEW  37 tests
│   ├── test_metrics.py                NEW  24 tests
│   ├── test_report.py                 NEW  30 tests
│   ├── test_properties.py             NEW  25 tests (5 rules x 5 seeds)
│   └── (Phase 1-2 tests unchanged)
├── docs/PHASE_3_GUIDE.md              NEW
├── docs/PHASE_3_CODE.md               NEW  every new or changed file in full
└── README.md                          MODIFIED
```

No new dependencies. `decimal`, `fractions`, `dataclasses` and `random` are all in Python's standard library.

---

## Parts D and E — Code and tests

Every file's complete contents are in **`docs/PHASE_3_CODE.md`**, generated from the tested files, and are also shown in the conversation.

---

## Part F — Running the analytics

With the venv active in the project folder (VS Code: **Terminal → New Terminal**):

| | Windows PowerShell | macOS / Linux |
|---|---|---|
| Activate | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

The commands below are the same on every OS:

```bash
flask --app tradeledger seed-db                                 # if not already seeded
flask --app tradeledger report                                  # full rounded report
flask --app tradeledger report --instrument XAUUSD              # one instrument
flask --app tradeledger report --start 2026-03-09 --end 2026-03-13
flask --app tradeledger report --json                           # exact values
pytest                                                          # all 261 tests
pytest tests/test_metrics.py -v                                 # one file, verbose
```

On Windows PowerShell, to save the JSON to a file: `flask --app tradeledger report --json > report.json`. The same works on macOS and Linux.

**From Python** (type `python` with the venv active):

```python
from tradeledger.db import connect
from tradeledger.performance import build_report
from tradeledger.performance.formatting import format_r

conn = connect("instance/tradeledger.db")
report = build_report(conn)
print(report.overall.expectancy_r)              # exact Decimal
print(format_r(report.overall.expectancy_r))    # +0.55R
print(report.by_strategy["Breakout"].win_rate)  # 0.6
conn.close()
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `No such command 'report'` | Make sure you have the Phase 3 files; run from the project root |
| Report shows 0 trades | `flask --app tradeledger seed-db` |
| `ModuleNotFoundError: tests` in pytest | Run `pytest` from the project root (where `pytest.ini` is) |
| `n/a` in a column | Correct: that metric can't be calculated for the group (e.g. profit factor with no losses) |

---

## Part G — Verified results (real output, synthetic data)

```
== Overall (closed trades) ==
Closed trades                 14
Open trades (not in results)  2
Wins / losses / breakevens    7 / 5 / 2
Win rate / loss rate          50.0% / 35.7%
Trades with realised R        14
Total R (cumulative)          +7.69R
Average R                     +0.55R
Average winning R             +1.69R
Average losing R              -0.83R
Expectancy per trade          +0.55R
Profit factor (R)             2.85
Average planned R:R           2.07 (12 trades with a target)
Longest win / loss streak     2 / 2
Max drawdown (R)              1.05R
Best trade (R)                #10 UK100 +2.00R
Worst trade (R)               #12 GBPUSD -1.05R

== Money, per quote currency (never added together) ==
currency  trades  gross P&L   fees       net P&L     winners       losers       PF    max DD
GBP       2       200.00 GBP  0.00 GBP   200.00 GBP  200.00 GBP    0.00 GBP     n/a   0.00 GBP
USD       12      842.00 USD  43.50 USD  798.50 USD  1,278.50 USD  -480.00 USD  2.66  243.50 USD

== By instrument ==
group   trades  W/L/BE  win rate  total R  expectancy  PF (R)  max DD
EURUSD  3       1/2/0   33.3%     +0.90R   +0.30R      1.84    1.07R
GBPUSD  3       1/1/1   33.3%     +0.42R   +0.14R      1.40    1.05R
NAS100  2       1/1/0   50.0%     +0.97R   +0.48R      1.95    1.02R
UK100   2       1/0/1   50.0%     +2.00R   +1.00R      n/a     0.00R
XAUUSD  4       3/1/0   75.0%     +3.41R   +0.85R      4.33    1.02R

== By day of week (UTC) ==
Monday     3       1/1/1   33.3%     +0.94R   +0.31R      1.92    1.02R
Tuesday    3       1/2/0   33.3%     -0.10R   -0.03R      0.95    1.05R
Wednesday  3       1/1/1   33.3%     +0.96R   +0.32R      1.94    1.02R
Thursday   2       1/1/0   50.0%     +1.43R   +0.72R      43.95   0.03R
Friday     3       3/0/0   100.0%    +4.46R   +1.49R      n/a     0.00R
```

(The full output also includes strategy, session, month and cumulative-R tables.)

### Hand-worked check

Realised R for each closed trade, worked out by hand as net ÷ risk:

| # | Net | Risk | R | Cumulative |
|---|---|---|---|---|
| 1 | +393 | 200 | +1.965 | 1.965 |
| 2 | −103.5 | 100 | −1.035 | 0.930 |
| 3 | 0 | 200 | 0 | 0.930 |
| 4 | +293 | 200 | +1.465 | 2.395 |
| 5 | +198 | 100 | +1.980 | 4.375 |
| 6 | −133 | 130 | −1.0231 | 3.352 |
| 7 | +198.5 | 100 | +1.985 | **5.337 (peak)** |
| 8 | −81.5 | 80 | −1.01875 | 4.318 |
| 9 | −5 | 150 | −0.0333 | **4.285 (trough)** |
| 10 | +200 | 100 | +2.000 | 6.285 |
| 11 | 0 | 100 | 0 | 6.285 |
| 12 | −157 | 150 | −1.0467 | 5.238 |
| 13 | +158 | 80 | +1.975 | 7.213 |
| 16 | +38 | 80 | +0.475 | **7.688** |

- **Max drawdown** = 5.337 − 4.285 = 1.01875 + 1/30 = **101/96 ≈ 1.0521R** ✓
- **Total** = 11.845 − 4.1568 = **239871/31200 ≈ 7.6882R** ✓
- **Expectancy** = 7.6882 ÷ 14 = **0.5492R** ✓
- **Profit factor** = 11.845 ÷ 4.1568 = **2.8495** ✓
- **USD drawdown:** cumulative USD net peaks at 846 (after trade 7) and falls to 602.5 (after trade 12), so 243.50 ✓

---

## Part H — Documentation and ZIP

`tradeledger_phase3.zip` is the complete runnable project. It excludes `.env`, the `instance/` database, virtual environments and caches. The README is updated with the new features and limitations.

---

## Part I — Teaching walkthrough

### 1. How Python functions calculate trading statistics

**Plain:** each function answers one small question. "What was this trade's P&L?" "What's the average of these numbers?" Bigger answers are built from smaller ones.
**Technical:** `summarise()` is a pipeline of small pure functions (`ratio`, `mean`, `expectancy`, `profit_factor`, `longest_streaks`, `max_drawdown`), each with its own unit tests. A pure function's output depends only on its inputs: no database, no global state, no hidden clock. Call it twice with the same input and you get the same answer, which is what makes it testable.

### 2. Why Decimal matters

```python
>>> 0.1 + 0.2
0.30000000000000004
>>> 1.0895 - 1.091
-0.0015000000000000568     # should be -0.0015
>>> from decimal import Decimal
>>> Decimal("1.0895") - Decimal("1.091")
Decimal('-0.0015')
```
Floats store numbers in binary, and most decimal fractions (0.1, 1.0895) have no exact binary form, just as 1/3 has no exact decimal form. In a single calculation the error is tiny. But it can flip a breakeven into a "loss" of −0.00000000001, and errors add up over thousands of trades. `Decimal` stores decimal digits exactly.

Two Decimal traps the code handles:
- **`Decimal(0.1)` copies the float's error.** Always build from strings: `Decimal("0.1")`. The engine refuses float inputs outright (tested).
- **Precision is set by a context.** The default rounds to 28 significant digits, and *every* operation rounds, even `-x`. TradeLedger uses one 60-digit context, `EXACT`, wrapped around all arithmetic. The bug in the verification summary is exactly this trap.

### 3. How initial risk is calculated

Initial risk is *how much money you lose if the stop is hit*:
```
risk per unit = sign × (entry − stop)     LONG: entry − stop     SHORT: stop − entry
initial risk  = risk per unit × position size × contract multiplier
```
Sample trade 6 (gold short, 0.2 lots, multiplier 100): (2172 − 2165.5) × 0.2 × 100 = 6.5 × 20 = **130 USD**. If the result isn't positive (stop on the wrong side or equal to entry) the risk is invalid and `initial_risk()` returns `None`. R is then unavailable, never guessed.

### 4. Realised R vs planned R:R

- **Planned R:R** = reward you aimed for ÷ risk you accepted, from prices only. Trade 6: (2165.5 − 2150) ÷ 6.5 = **2.38**. You planned to make 2.38 times your risk.
- **Realised R** = what you actually made ÷ risk, after fees. Trade 6 was stopped out: −133 ÷ 130 = **−1.023R**.

Comparing the two across many trades shows whether you let winners reach target (average realised win vs average planned R:R) and how much fees cost you.

### 5. How expectancy works

Expectancy answers: *on average, how many R does one trade make?*
```
E = P(win) × average win R + P(loss) × average loss R
  = 0.5 × 1.692 + 0.357 × (−0.831) = +0.549R
```
A trader risking £50 per trade with +0.55R expectancy makes, on average, about £27 per trade **if** the sample is representative. With 14 trades, it isn't yet. Expectancy can be positive with a win rate under 50% (large wins, small losses) or negative with a win rate over 70% (small wins, occasional big losses). That's why win rate alone is misleading.

### 6. How profit factor works

```
profit factor = total of all winning results ÷ total of all losing results (as a positive number)
```
Above 1 is profitable; 2 means £2 won for every £1 lost. With **no losses** the division is by zero. The engine returns `None` (shown as `n/a`) rather than infinity, because "infinitely good" after three trades is not a meaningful number. The R version (2.85) and the per-currency money version (USD 2.66) differ because trades have different sizes in money but the same weight in R.

### 7. How streaks are calculated

Walk the trades in date order, keeping the current kind and length:
```
W  L  BE W  W  L  W  L  L  W  BE L  W  W
1  1  -  1  2  1  1  1  2  1  -  1  1  2     → longest win 2, longest loss 2
```
When the outcome matches the previous one, the length grows; otherwise it resets to 1. A breakeven ends both kinds of run. That's a convention: the alternative is to skip breakevens. Sorting first matters. `test_summary_sorts_by_date_before_streaks_and_drawdown` feeds trades out of order and checks the result.

### 8. How maximum drawdown is calculated

Track the running total and the highest point reached so far. At every trade, the drawdown is `peak − current`; keep the largest.
```
cumulative: 1.97  0.93  0.93  2.40  4.38  3.35  5.34  4.32  4.28  6.28 …
peak:       1.97  1.97  1.97  2.40  4.38  4.38  5.34  5.34  5.34  6.28 …
drawdown:   0     1.04  1.04  0     0     1.02  0     1.02  1.05  0    …   → max 1.05R
```
The peak starts at **0, before the first trade**. That way a journal that starts with losses still records them as a drawdown (tested: two losses first give a drawdown of 3). One pass, O(n), no nested loops.

### 9. How Python processes database records

1. `list_trades()` runs one parameterised SELECT with a JOIN and returns a list of dicts with Decimal values.
2. `evaluate_trades()` loops once: OPEN trades are counted, bad records go to `excluded`, and good ones become `TradeResult` objects.
3. `group_summaries()` buckets results into a dict of lists by a key function (`lambda r: r.symbol`), then calls `summarise()` on each bucket.
4. Nothing goes back to the database, so the report is a snapshot.

### 10. How to test financial calculations

TradeLedger uses five layers, all present in the suite:

1. **Hand-worked examples** with round numbers (`make_trade`: risk 10, net 20, R 2).
2. **Independent arithmetic.** The sample-data tests use `fractions.Fraction`, which is exact rational arithmetic, rather than re-running the engine's Decimal code. A test that recomputes a result with the same code under test proves nothing. This approach caught a real bug.
3. **Edge cases:** empty input, all wins, all losses, breakevens, missing fields, wrong-side stops, floats, ties and unordered input.
4. **Properties** that must hold for any input: `R × risk = net`, `net = gross − fees`, a long equals its mirrored short, expectancy equals average R, rates sum to 1, and order doesn't matter. Each is checked over 1,000 random trades (5 seeds × 200).
5. **Mutation checks:** break the code on purpose and confirm the tests fail.

### 11. How to design a maintainable analytics engine

- **Layers that only depend downwards:** formatting → report → metrics → trade_math. `trade_math` imports nothing from the project.
- **One definition of each formula.** The CLI, future web pages and the JSON API all call the same `summarise()`. The dashboard can never disagree with the report.
- **Explicit unavailability** (`None` plus a reason) instead of zeros that look like real results.
- **Typed, immutable results** so the dashboard code is readable (`report.overall.expectancy_r`, not `stats["e"]`).
- **No premature optimisation.** Loading rows into Python is simple and fast enough; the point at which to optimise is noted in Part K.

---

## Part J — Practical exercise: recovery factor

**Recovery factor** = total R ÷ max drawdown R. It measures how much you made relative to the worst dip. Sample: 7.688 ÷ 1.052 ≈ **7.31**.

1. In `metrics.py`, add `recovery_factor: Optional[Decimal]` to `Summary`, after `max_drawdown_r`.
2. Calculate it in `summarise()` using the existing `ratio()` helper. Decide and document what happens when max drawdown is 0. Hint: what does `ratio()` already return?
3. In `tests/test_metrics.py`, add tests for: the worked example in `test_summary_mixed_hand_worked` (total 2, drawdown 1, so 2), all winners (no drawdown, so `None`), and all losers.
4. In `tests/test_report.py`, assert the sample value to 40 places using `Fraction`. You'll need to work it out from 239871/31200 and 101/96.
5. Add a "Recovery factor" line to `print_report()` in `cli.py` using `format_ratio`.
6. Run `pytest`. You're aiming for 261 + your new tests, all passing.

**Questions to answer in your own words:**
1. Sample trade 2 was stopped out exactly at its stop. Why is its R −1.035 and not −1.000?
2. Why does the engine return `None` for profit factor when there are no losses, instead of a very large number?
3. Why must trades be sorted by date before calculating drawdown, but not before calculating average R?
4. Your Breakout strategy shows a 60% win rate and +0.99R expectancy over 5 trades. What would you say to someone who wants to trade only Breakout from now on?

---

## Part K — Preparation for Phase 4: Flask backend and analytics integration

### How Flask will consume the engine

```python
# Phase 4 sketch, not yet in the project
from flask import jsonify, render_template, request
from .db import get_db
from .performance import build_report
from .performance.formatting import to_jsonable

@bp.get("/api/performance")
def performance_json():
    report = build_report(get_db(), start=request.args.get("start"), end=request.args.get("end"))
    return jsonify(to_jsonable(report))          # exact strings, no floats

@bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", report=build_report(get_db()))
    # template: {{ report.overall.expectancy_r | r }}, using format_r as a Jinja filter
```

- **HTML pages** get the `PerformanceReport` object directly; Jinja filters wrap `format_r`, `format_money` and `format_rate`, so rounding stays in one place.
- **Chart.js** (Phase 6) will fetch `/api/performance` and plot `cumulative_r`. JavaScript numbers are floats, so the strings are converted with `Number(...)` **only at the chart**. A sub-pixel error in a line's position is harmless; an error in a stored figure is not.
- **A `ValidationError`** from bad `start`/`end` query parameters will become a 400 response, not a 500.

### Phase 4 scope (for when you ask to continue)

Flask routes for trade CRUD (list, view, create, edit, delete), server-side validation reusing `validate_trade()`, CSRF protection for forms, the POST-redirect-GET pattern to prevent duplicate submissions, flash messages, 400/404 handling, a JSON performance endpoint, and route tests with the Flask test client.

### When to optimise

The report loads every matching trade and recalculates on each request. For one trader, 10,000 trades take well under a second. If a page ever feels slow, the next steps in order are: measure first, cache the report until a trade changes, then move grouping into SQL (likely with PostgreSQL's exact `NUMERIC`).

### Phase 3 completion checklist

All verified on Linux; tick them on your machine:

- [ ] `pytest` shows 261 passed
- [ ] `flask --app tradeledger report` matches Part G
- [ ] `--json` values are strings, not floats
- [ ] The home page shows Phase 3 Done; `/health` shows 0.3.0
- [ ] `seed-db`, `db-info`, `analytics` still work
- [ ] Commit: `git add . && git commit -m "Phase 3: Decimal analytics engine with R-based metrics, per-currency P&L, report command and tests"`

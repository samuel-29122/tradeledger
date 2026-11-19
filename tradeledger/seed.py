"""Synthetic sample data for development and tests.

EVERYTHING IN THIS FILE IS INVENTED. None of it is anyone's real trading.

About the instrument values
--------------------------
contract_multiplier means "money, in quote_currency, per 1.0 move in price
per 1.0 unit of position_size". Real values depend on the broker's contract
specification and on what one unit of position size means at that broker.
The figures below are ILLUSTRATIVE ASSUMPTIONS for synthetic data only:

  EURUSD, GBPUSD  1 unit = 1 standard lot of 100,000 base currency -> 100000
  XAUUSD          1 unit = 1 lot of 100 troy ounces                -> 100
  NAS100, UK100   1 unit = a CFD paying 1 quote-currency per point  -> 1

Before recording real trades, replace them with your broker's figures.
Fees are assumed to be in the instrument's quote currency.

About closed_at (added in Phase 4)
----------------------------------
Each synthetic closed trade has a synthetic exit time on the same day as its
entry, and the trades close in the same order they were opened. That keeps
every Phase 2 and Phase 3 expected result valid. This is invented sample
data, so giving it exit times fabricates nothing. Real trades recorded
before Phase 4 are never given invented exit times (see migrations.py).
"""

from .repositories import require_user_id
from .repositories.instruments import create_instrument, get_instrument_by_symbol
from .repositories.trades import create_trade
from .validation import validate_trade

SAMPLE_INSTRUMENTS = [
    {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "GBPUSD",
        "name": "British Pound / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "XAUUSD",
        "name": "Gold / US Dollar (sample)",
        "asset_class": "COMMODITY",
        "price_increment": "0.01",
        "contract_multiplier": "100",
        "quote_currency": "USD",
    },
    {
        "symbol": "NAS100",
        "name": "US Tech 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
    },
    {
        "symbol": "UK100",
        "name": "UK 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "GBP",
    },
]

# Net P&L in the comment = gross - fees, in the instrument's quote currency.
SAMPLE_TRADES = [
    # 1  WIN  +393.00 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: clean break of Asian range.",
    },
    # 2  LOSS -103.50 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-03T13:40:00Z",
        "closed_at": "2026-03-03T15:05:00Z",
        "direction": "SHORT",
        "entry_price": "1.08800",
        "stop_loss": "1.09000",
        "take_profit": "1.08400",
        "exit_price": "1.09000",
        "position_size": "0.5",
        "fees": "3.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": "Sample: stopped out on US data.",
    },
    # 3  BREAKEVEN 0.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-04T08:30:00Z",
        "closed_at": "2026-03-04T10:10:00Z",
        "direction": "LONG",
        "entry_price": "1.27000",
        "stop_loss": "1.26800",
        "take_profit": "1.27400",
        "exit_price": "1.27000",
        "position_size": "1",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: moved stop to entry.",
    },
    # 4  WIN  +293.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-05T14:05:00Z",
        "closed_at": "2026-03-05T17:30:00Z",
        "direction": "SHORT",
        "entry_price": "1.27500",
        "stop_loss": "1.27700",
        "take_profit": "1.27100",
        "exit_price": "1.27200",
        "position_size": "1",
        "fees": "7",
        "strategy": "Reversal",
        "session": "NEW_YORK",
        "notes": "Sample: closed early before target.",
    },
    # 5  WIN  +198.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-06T14:30:00Z",
        "closed_at": "2026-03-06T16:45:00Z",
        "direction": "LONG",
        "entry_price": "2150.00",
        "stop_loss": "2140.00",
        "take_profit": "2170.00",
        "exit_price": "2170.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 6  LOSS -133.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-09T02:10:00Z",
        "closed_at": "2026-03-09T05:20:00Z",
        "direction": "SHORT",
        "entry_price": "2165.50",
        "stop_loss": "2172.00",
        "take_profit": "2150.00",
        "exit_price": "2172.00",
        "position_size": "0.2",
        "fees": "3",
        "strategy": "Reversal",
        "session": "ASIA",
        "notes": None,
    },
    # 7  WIN  +198.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-10T14:45:00Z",
        "closed_at": "2026-03-10T16:00:00Z",
        "direction": "LONG",
        "entry_price": "18250.0",
        "stop_loss": "18200.0",
        "take_profit": "18350.0",
        "exit_price": "18350.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 8  LOSS -81.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-11T15:00:00Z",
        "closed_at": "2026-03-11T15:50:00Z",
        "direction": "SHORT",
        "entry_price": "18300.0",
        "stop_loss": "18340.0",
        "take_profit": "18200.0",
        "exit_price": "18340.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 9  LOSS -5.00 USD  (exit ABOVE entry on a long, but fees exceed the gain)
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-12T09:00:00Z",
        "closed_at": "2026-03-12T09:35:00Z",
        "direction": "LONG",
        "entry_price": "1.09100",
        "stop_loss": "1.08950",
        "take_profit": "1.09400",
        "exit_price": "1.09102",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": "Sample: scratched; fees made it a loss.",
    },
    # 10 WIN  +200.00 GBP
    {
        "symbol": "UK100",
        "trade_date": "2026-03-13T10:30:00Z",
        "closed_at": "2026-03-13T13:15:00Z",
        "direction": "LONG",
        "entry_price": "7700.0",
        "stop_loss": "7680.0",
        "take_profit": "7740.0",
        "exit_price": "7740.0",
        "position_size": "5",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": None,
    },
    # 11 BREAKEVEN 0.00 GBP (no strategy, no target)
    {
        "symbol": "UK100",
        "trade_date": "2026-03-16T11:00:00Z",
        "closed_at": "2026-03-16T12:30:00Z",
        "direction": "SHORT",
        "entry_price": "7750.0",
        "stop_loss": "7770.0",
        "take_profit": None,
        "exit_price": "7750.0",
        "position_size": "5",
        "fees": "0",
        "strategy": None,
        "session": "LONDON",
        "notes": None,
    },
    # 12 LOSS -157.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-17T08:05:00Z",
        "closed_at": "2026-03-17T08:50:00Z",
        "direction": "LONG",
        "entry_price": "1.28000",
        "stop_loss": "1.27850",
        "take_profit": "1.28300",
        "exit_price": "1.27850",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": None,
    },
    # 13 WIN  +158.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-18T03:20:00Z",
        "closed_at": "2026-03-18T07:00:00Z",
        "direction": "LONG",
        "entry_price": "2180.00",
        "stop_loss": "2172.00",
        "take_profit": "2196.00",
        "exit_price": "2196.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Pullback",
        "session": "ASIA",
        "notes": None,
    },
    # 14 OPEN
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-19T14:35:00Z",
        "closed_at": None,
        "direction": "LONG",
        "entry_price": "18500.0",
        "stop_loss": "18440.0",
        "take_profit": "18620.0",
        "exit_price": None,
        "position_size": "1",
        "fees": "0.75",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": "Sample: still running.",
    },
    # 15 OPEN
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-20T08:45:00Z",
        "closed_at": None,
        "direction": "SHORT",
        "entry_price": "1.09300",
        "stop_loss": "1.09450",
        "take_profit": None,
        "exit_price": None,
        "position_size": "1",
        "fees": "0",
        "strategy": "Reversal",
        "session": "LONDON",
        "notes": None,
    },
    # 16 WIN  +38.00 USD (no strategy, no session)
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-20T15:10:00Z",
        "closed_at": "2026-03-20T18:20:00Z",
        "direction": "SHORT",
        "entry_price": "2190.00",
        "stop_loss": "2198.00",
        "take_profit": None,
        "exit_price": "2186.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": None,
        "session": None,
        "notes": None,
    },
]


def seed_sample_data(conn, *, user_id):
    """Insert the sample instruments and trades for ONE user. Safe to run
    repeatedly.

    * An instrument is skipped if this user already has its symbol.
    * A trade is skipped if this user already has a trade with the same
      instrument, trade_date, direction and entry_price.
    * Everything happens in ONE transaction: if any record fails, nothing
      from this run is kept.

    Returns {"instruments": n_inserted, "trades": n_inserted}.
    """
    require_user_id(user_id)
    inserted = {"instruments": 0, "trades": 0}

    with conn:
        ids = {}
        for raw in SAMPLE_INSTRUMENTS:
            existing = get_instrument_by_symbol(conn, raw["symbol"], user_id=user_id)
            if existing:
                ids[raw["symbol"]] = existing["id"]
                continue
            ids[raw["symbol"]] = create_instrument(conn, raw, user_id=user_id)
            inserted["instruments"] += 1

        for raw in SAMPLE_TRADES:
            data = {key: value for key, value in raw.items() if key != "symbol"}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data)
            already_there = conn.execute(
                """
                SELECT 1 FROM trades
                WHERE user_id = ? AND instrument_id = ? AND trade_date = ?
                  AND direction = ? AND entry_price = ?
                """,
                (
                    user_id,
                    trade["instrument_id"],
                    trade["trade_date"],
                    trade["direction"],
                    conn.numeric(trade["entry_price"]),
                ),
            ).fetchone()
            if already_there:
                continue
            create_trade(conn, data, user_id=user_id)
            inserted["trades"] += 1

    return inserted

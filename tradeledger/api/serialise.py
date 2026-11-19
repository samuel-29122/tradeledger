"""Turning repository rows and analytics results into API JSON.

Rules
  * Every Decimal becomes a string with its exact value ("1.085"), never a
    float. formatting.to_jsonable() does the conversion.
  * Unavailable values are null.
  * The field list for each resource is explicit, so adding a database
    column never leaks into the API by accident.
"""

from ..performance.formatting import to_jsonable
from ..performance.trade_math import IncompleteTradeError, evaluate_trade

TRADE_FIELDS = (
    "id",
    "instrument_id",
    "symbol",
    "quote_currency",
    "trade_date",
    "closed_at",
    "direction",
    "status",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "strategy",
    "session",
    "notes",
    "created_at",
    "updated_at",
)

INSTRUMENT_FIELDS = (
    "id",
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
    "created_at",
)

RESULT_FIELDS = (
    "outcome",
    "gross_pnl",
    "fees",
    "net_pnl",
    "initial_risk",
    "realised_r",
    "planned_rr",
    "r_unavailable_reason",
)


def trade_result(trade):
    """Calculated result for a CLOSED trade, using the Phase 3 engine.
    None for OPEN trades: they have no realised result yet."""
    if trade["status"] != "CLOSED":
        return None
    try:
        result = evaluate_trade(trade)
    except IncompleteTradeError:
        return None
    return {name: to_jsonable(getattr(result, name)) for name in RESULT_FIELDS}


def trade_json(trade):
    body = {name: to_jsonable(trade[name]) for name in TRADE_FIELDS}
    body["result"] = trade_result(trade)
    return body


def instrument_json(instrument):
    return {name: to_jsonable(instrument[name]) for name in INSTRUMENT_FIELDS}


def summary_json(summary):
    return to_jsonable(summary)


def groups_json(groups):
    """Dict of label -> Summary, as an ordered list (JSON objects have no
    guaranteed order; lists do)."""
    return [{"group": label, **to_jsonable(summary)} for label, summary in groups.items()]

"""Per-trade calculations.

Formulas (all amounts in the instrument's quote currency)
---------------------------------------------------------
  direction sign   LONG = +1, SHORT = -1
  price move       sign x (exit - entry)
  gross P&L        price move x position_size x contract_multiplier
  net P&L          gross P&L - fees
  risk per unit    sign x (entry - stop_loss)        must be > 0
  initial risk     risk per unit x position_size x contract_multiplier
  realised R       net P&L / initial risk
  planned R:R      sign x (take_profit - entry) / risk per unit
  outcome          WIN if net P&L > 0, LOSS if < 0, BREAKEVEN if exactly 0

Conventions
  * Initial risk EXCLUDES fees: it is the money lost if price reaches the
    stop. Fees then reduce net P&L, so a stopped-out trade with fees is
    slightly worse than -1R (e.g. -1.035R). This is intentional: it shows
    the real cost of trading.
  * Planned R:R is price-based and ignores fees. It describes the plan.
  * No rounding happens here. Rounding is for display only (formatting.py).

Timing (Phase 4)
  * trade_date is the entry time; closed_at is the exit time.
  * realised_at is when the result is treated as realised: closed_at when
    it was recorded, otherwise trade_date. Only legacy trades recorded
    before Phase 4 lack closed_at; they are flagged close_time_known=False
    so reports can say so. No exit time is ever invented or stored.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from ..timestamps import parse_stored

# Products of three 15-digit inputs need up to 45 digits; 60 keeps them
# exact. Only divisions (R, ratios) are rounded, at the 60th digit.
EXACT = Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow])

ZERO = Decimal("0")
SIGN = {"LONG": 1, "SHORT": -1}


class IncompleteTradeError(Exception):
    """The trade cannot be evaluated (open, missing or invalid data)."""


@dataclass(frozen=True)
class TradeResult:
    """Everything calculated for one closed trade."""

    trade_id: int
    symbol: str
    quote_currency: str
    trade_date: str  # entry time
    closed_at: str | None  # exit time; None = not recorded (legacy)
    realised_at: str  # closed_at, or trade_date if unknown
    close_time_known: bool
    direction: str
    strategy: str | None
    session: str | None
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    outcome: str
    initial_risk: Decimal | None  # None when the risk is invalid
    realised_r: Decimal | None  # None when the risk is invalid
    planned_rr: Decimal | None  # None without a valid target
    r_unavailable_reason: str | None  # why realised_r is None


# ---------- Small pure functions (each tested on its own) ----------


def price_move(direction, entry, exit_price):
    with localcontext(EXACT):
        return SIGN[direction] * (exit_price - entry)


def gross_pnl(direction, entry, exit_price, size, multiplier):
    with localcontext(EXACT):
        return price_move(direction, entry, exit_price) * size * multiplier


def net_pnl(gross, fees):
    with localcontext(EXACT):
        return gross - fees


def risk_per_unit(direction, entry, stop):
    """Positive when the stop is on the losing side of entry."""
    with localcontext(EXACT):
        return SIGN[direction] * (entry - stop)


def initial_risk(direction, entry, stop, size, multiplier):
    """Money at risk if the stop is hit, or None if the stop is invalid."""
    per_unit = risk_per_unit(direction, entry, stop)
    if per_unit <= 0:
        return None
    with localcontext(EXACT):
        return per_unit * size * multiplier


def realised_r(net, risk):
    """Net P&L expressed in units of initial risk, or None if risk is unusable."""
    if risk is None or risk <= 0:
        return None
    with localcontext(EXACT):
        return net / risk


def planned_rr(direction, entry, stop, target):
    """Planned reward divided by planned risk, or None if not calculable."""
    if target is None:
        return None
    risk = risk_per_unit(direction, entry, stop)
    with localcontext(EXACT):
        reward = SIGN[direction] * (target - entry)
    if risk <= 0 or reward <= 0:
        return None
    with localcontext(EXACT):
        return reward / risk


def classify(net):
    if net > 0:
        return "WIN"
    if net < 0:
        return "LOSS"
    return "BREAKEVEN"


# ---------- Input checking ----------


def _decimal(trade, field, *, required=True):
    """Read a field as Decimal. Floats are refused: they may already be inexact."""
    value = trade.get(field)
    if value is None:
        if required:
            raise IncompleteTradeError(f"missing {field}")
        return None
    if isinstance(value, (float, bool)):
        raise IncompleteTradeError(f"{field} must be Decimal, int or str, not {type(value).__name__}")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise IncompleteTradeError(f"{field} is not a number") from None
    if not number.is_finite():
        raise IncompleteTradeError(f"{field} is not a finite number")
    return number


# ---------- One trade ----------


def evaluate_trade(trade: Mapping) -> TradeResult:
    """Calculate a TradeResult for one CLOSED trade.

    `trade` is a mapping such as a row from repositories.trades.list_trades():
    it needs id, symbol, quote_currency, trade_date, direction, status,
    entry_price, stop_loss, exit_price, position_size, fees,
    contract_multiplier, and optionally closed_at, take_profit, strategy,
    session.

    Raises IncompleteTradeError for open trades and for data that cannot
    produce a trustworthy P&L. A trade whose P&L is known but whose risk is
    invalid is NOT an error: it gets realised_r = None plus a reason.
    """
    if trade.get("status") != "CLOSED":
        raise IncompleteTradeError("trade is not closed, so it has no realised result")
    direction = trade.get("direction")
    if direction not in SIGN:
        raise IncompleteTradeError("direction must be LONG or SHORT")
    for field in ("symbol", "quote_currency", "trade_date"):
        if not trade.get(field):
            raise IncompleteTradeError(f"missing {field}")
    try:
        parse_stored(trade["trade_date"])
    except (TypeError, ValueError):
        raise IncompleteTradeError("trade_date must look like 2026-03-02T08:15:00Z") from None
    closed_at = trade.get("closed_at")
    if closed_at is not None:
        try:
            parse_stored(closed_at)
        except (TypeError, ValueError):
            raise IncompleteTradeError("closed_at must look like 2026-03-02T08:15:00Z") from None
        if closed_at < trade["trade_date"]:
            raise IncompleteTradeError("closed_at is earlier than trade_date")

    with localcontext(EXACT):
        entry = _decimal(trade, "entry_price")
        stop = _decimal(trade, "stop_loss")
        exit_price = _decimal(trade, "exit_price")
        size = _decimal(trade, "position_size")
        multiplier = _decimal(trade, "contract_multiplier")
        target = _decimal(trade, "take_profit", required=False)
        fees = _decimal(trade, "fees", required=False) or ZERO

        if size <= 0:
            raise IncompleteTradeError("position_size must be greater than zero")
        if multiplier <= 0:
            raise IncompleteTradeError("contract_multiplier must be greater than zero")
        if fees < 0:
            raise IncompleteTradeError("fees must not be negative")

        # Validation keeps stored values small enough for exact arithmetic
        # (Phase 7). This guard is defence in depth: a row that is out of
        # range anyway (older data, a manual SQL edit) is reported as an
        # excluded trade instead of breaking every page that shows it.
        try:
            gross = gross_pnl(direction, entry, exit_price, size, multiplier)
            net = net_pnl(gross, fees)
            risk = initial_risk(direction, entry, stop, size, multiplier)
            r_value = realised_r(net, risk)
        except DecimalException:
            raise IncompleteTradeError("values are too large to calculate exactly") from None

    reason = None
    if r_value is None:
        reason = "stop-loss is not on the losing side of entry, so initial risk is not positive"

    return TradeResult(
        trade_id=trade.get("id"),
        symbol=trade["symbol"],
        quote_currency=trade["quote_currency"],
        trade_date=trade["trade_date"],
        closed_at=closed_at,
        realised_at=closed_at or trade["trade_date"],
        close_time_known=closed_at is not None,
        direction=direction,
        strategy=trade.get("strategy"),
        session=trade.get("session"),
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        outcome=classify(net),
        initial_risk=risk,
        realised_r=r_value,
        planned_rr=planned_rr(direction, entry, stop, target),
        r_unavailable_reason=reason,
    )

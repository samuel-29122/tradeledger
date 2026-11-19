"""Turning reports into JSON and human-readable text.

Rounding policy
---------------
* Calculations never round (except division at the 60th significant
  digit, see trade_math.EXACT).
* Rounding happens only here, at the very end, for display:
      R values and R:R   2 decimal places    e.g. +1.97R, 2.38
      money              2 decimal places    e.g. 798.50 USD
      rates              1 decimal place     e.g. 50.0%
  using ROUND_HALF_UP, the "school" rule people expect on screen.
  (Currencies without pence/cents, such as JPY, would need 0 places;
  that is a known simplification.)
* JSON output (to_jsonable) does NOT round. Decimals become strings so the
  receiver gets the exact value, and no float is ever created.

Why round late? If you round each trade's R to 2 places and then add 50 of
them, the rounding errors add up. Rounding once, after adding, does not.
"""

from dataclasses import asdict, is_dataclass
from decimal import ROUND_HALF_UP, Decimal

from .trade_math import EXACT

TWO_PLACES = Decimal("0.01")
ONE_PLACE = Decimal("0.1")
NOT_AVAILABLE = "n/a"


def decimal_to_text(value: Decimal) -> str:
    """Exact, plain notation: Decimal('4.0E+2') -> '400', no exponents."""
    # normalize() must use the 60-digit context, or it would round to 28.
    text = format(value.normalize(EXACT), "f")
    return "0" if text in ("-0", "") else text


def to_jsonable(obj):
    """Recursively convert dataclasses, dicts and lists for json.dumps().
    Decimal -> str (exact). Everything else is left as is."""
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return decimal_to_text(obj)
    return obj


def round_display(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def format_r(value):
    if value is None:
        return NOT_AVAILABLE
    rounded = round_display(value, TWO_PLACES)
    if rounded == 0:
        return "0.00R"  # never "-0.00R" or "+0.00R"
    return f"{rounded:+}R"


def format_r_amount(value):
    """A size in R with no sign, e.g. a drawdown: '1.05R'."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(abs(value), TWO_PLACES)}R"


def format_ratio(value):
    """Plain number to 2 places, e.g. planned R:R or profit factor."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(value, TWO_PLACES)}"


def format_money(value, currency):
    if value is None:
        return NOT_AVAILABLE
    rounded = round_display(value, TWO_PLACES)
    if rounded == 0:
        rounded = abs(rounded)
    return f"{rounded:,} {currency}"


def format_rate(value):
    """A 0-1 fraction as a percentage."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(value * 100, ONE_PLACE)}%"

"""Input validation for instruments and trades.

Each validate_* function takes raw input (a dict of strings or numbers,
e.g. from a web form) and returns a *clean* dict ready to store, or raises
ValidationError listing every problem found.

Python validation is the first line of defence and gives friendly messages.
The CHECK constraints in schema.sql are the last line of defence: they stop
bad data even if some future code forgets to validate.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from .timestamps import STORED_FORMAT

ASSET_CLASSES = ("FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER")
DIRECTIONS = ("LONG", "SHORT")
STATUSES = ("OPEN", "CLOSED")
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER")

# More significant digits than this cannot be compared exactly by SQLite's
# CAST(... AS REAL) inside the CHECK constraints, so we refuse them.
MAX_SIGNIFICANT_DIGITS = 15

# Magnitude limits (Phase 7 security fix). Counting significant digits is not
# enough on its own: "1e999999" has ONE significant digit, yet it is stored as
# a one-million-digit string and overflows Decimal arithmetic. Real prices,
# sizes, fees and multipliers sit far inside these limits.
MAX_INTEGER_DIGITS = 15
MAX_DECIMAL_PLACES = 15

TIMESTAMP_FORMAT = STORED_FORMAT  # the one stored shape, defined in timestamps.py


class ValidationError(Exception):
    """Raised when input is invalid. .errors maps field name -> message."""

    def __init__(self, errors):
        self.errors = dict(errors)
        summary = "; ".join(f"{field}: {msg}" for field, msg in self.errors.items())
        super().__init__(summary)


# ---------- Single-value helpers ----------


def parse_decimal(value, *, allow_zero=False):
    """Convert input to a Decimal, or raise ValueError with a reason.

    Floats are rejected on purpose: by the time a value is a float, it may
    already be inexact (0.1 is really 0.1000000000000000055...).
    """
    if isinstance(value, (bool, float)):
        raise ValueError("must be given as text or Decimal, not a float")
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            raise ValueError("is required")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("must be a number") from None

    if not number.is_finite():
        raise ValueError("must be a finite number")
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError("must be zero or more" if allow_zero else "must be greater than zero")
    if number != 0:
        # adjusted() is the power of ten of the leading digit: 123.4 -> 2.
        if number.adjusted() >= MAX_INTEGER_DIGITS:
            raise ValueError(f"must have at most {MAX_INTEGER_DIGITS} digits before the decimal point")
        if -number.normalize().as_tuple().exponent > MAX_DECIMAL_PLACES:
            raise ValueError(f"must have at most {MAX_DECIMAL_PLACES} decimal places")
    if len(number.normalize().as_tuple().digits) > MAX_SIGNIFICANT_DIGITS:
        raise ValueError(f"must have at most {MAX_SIGNIFICANT_DIGITS} significant digits")
    return number


def to_db_decimal(number):
    """Canonical text form for storage: no exponent, no trailing zeros.

    Decimal("1.08500") -> "1.085"    Decimal("100") -> "100"
    """
    text = format(number.normalize(), "f")
    return text


def parse_timestamp(value):
    """Convert a date-time input to a canonical UTC string.

    Accepted: any ISO 8601 date-time Python understands, e.g.
      "2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00".
    A value without a time-zone offset is treated as UTC.
    Returned: "2026-03-02T08:15:00Z" (seconds precision, always UTC).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("is required")
    text = value.strip()
    # A date alone ("2026-03-02") is ambiguous for a trade time; require a time.
    if len(text) <= 10:
        raise ValueError("must include a time, e.g. 2026-03-02T08:15:00Z")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("must be an ISO 8601 date-time, e.g. 2026-03-02T08:15:00Z") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    if moment.year < 1970:
        raise ValueError("must be 1970 or later")
    return moment.strftime(TIMESTAMP_FORMAT)


def parse_date(value):
    """Convert 'YYYY-MM-DD' to a date, or raise ValueError."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError("must be a date in YYYY-MM-DD form") from None


def date_range_bounds(start=None, end=None):
    """Turn inclusive dates into UTC timestamp bounds for SQL.

    Returns (lower, upper) where the SQL filter is
        trade_date >= lower AND trade_date < upper
    Either may be None. 'end' is inclusive: the whole of that day counts.
    """
    errors = {}
    start_day = end_day = None
    if start is not None:
        try:
            start_day = parse_date(start)
        except ValueError as exc:
            errors["start"] = str(exc)
    if end is not None:
        try:
            end_day = parse_date(end)
        except ValueError as exc:
            errors["end"] = str(exc)
    if not errors and start_day and end_day and start_day > end_day:
        errors["end"] = "must be on or after the start date"
    if errors:
        raise ValidationError(errors)

    def at_midnight(day):
        return datetime.combine(day, time(0, 0), tzinfo=UTC).strftime(TIMESTAMP_FORMAT)

    lower = at_midnight(start_day) if start_day else None
    upper = at_midnight(end_day + timedelta(days=1)) if end_day else None
    return lower, upper


def _clean_choice(value, choices):
    if not isinstance(value, str) or value.strip().upper() not in choices:
        raise ValueError("must be one of: " + ", ".join(choices))
    return value.strip().upper()


def _clean_optional_text(value, max_length):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    value = value.strip()
    if value == "":
        return None
    if len(value) > max_length:
        raise ValueError(f"must be {max_length} characters or fewer")
    return value


def _is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---------- Record validators ----------


def validate_instrument(data):
    """Validate instrument input. Returns a clean dict or raises ValidationError."""
    errors, clean = {}, {}

    symbol = data.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        errors["symbol"] = "is required"
    elif len(symbol.strip()) > 20 or not symbol.strip().replace("_", "").isalnum():
        errors["symbol"] = "must be 1-20 letters, digits or underscores"
    else:
        clean["symbol"] = symbol.strip().upper()

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors["name"] = "is required"
    elif len(name.strip()) > 100:
        errors["name"] = "must be 100 characters or fewer"
    else:
        clean["name"] = name.strip()

    try:
        clean["asset_class"] = _clean_choice(data.get("asset_class"), ASSET_CLASSES)
    except ValueError as exc:
        errors["asset_class"] = str(exc)

    for field in ("price_increment", "contract_multiplier"):
        try:
            clean[field] = to_db_decimal(parse_decimal(data.get(field)))
        except ValueError as exc:
            errors[field] = str(exc)

    currency = data.get("quote_currency")
    if not isinstance(currency, str) or len(currency.strip()) != 3 or not currency.strip().isalpha():
        errors["quote_currency"] = "must be a 3-letter currency code, e.g. USD"
    else:
        clean["quote_currency"] = currency.strip().upper()

    if errors:
        raise ValidationError(errors)
    return clean


def validate_trade(data, *, require_closed_at=True):
    """Validate trade input. Returns a clean dict or raises ValidationError.

    Rules (Python mirrors and extends the SQL constraints):
      * instrument_id is a positive whole number
      * trade_date (entry time) is a valid date-time, stored as UTC
      * closed_at (exit time) is optional input, stored as UTC, and:
          - must be empty for an OPEN trade
          - must not be earlier than trade_date
          - is REQUIRED for a CLOSED trade when require_closed_at is True
            (the default). The only caller that passes False is
            update_trade() for a legacy trade whose close time was never
            recorded, so editing it does not force anyone to invent one.
      * direction is LONG or SHORT; status is OPEN or CLOSED
      * prices and position size are > 0; fees are >= 0 (default 0)
      * LONG: stop_loss < entry_price; SHORT: stop_loss > entry_price
      * LONG: take_profit > entry_price; SHORT: take_profit < entry_price
      * status omitted -> CLOSED if an exit price is given, otherwise OPEN
      * CLOSED requires exit_price; OPEN must not have one
    """
    errors, clean = {}, {}

    instrument_id = data.get("instrument_id")
    try:
        if isinstance(instrument_id, (bool, float)):
            raise ValueError
        instrument_id = int(str(instrument_id).strip())
        if instrument_id <= 0:
            raise ValueError
        clean["instrument_id"] = instrument_id
    except (TypeError, ValueError):
        errors["instrument_id"] = "must be a positive whole number"

    try:
        clean["trade_date"] = parse_timestamp(data.get("trade_date"))
    except ValueError as exc:
        errors["trade_date"] = str(exc)

    try:
        clean["direction"] = _clean_choice(data.get("direction"), DIRECTIONS)
    except ValueError as exc:
        errors["direction"] = str(exc)

    numbers = {}
    for field in ("entry_price", "stop_loss", "position_size"):
        try:
            numbers[field] = parse_decimal(data.get(field))
        except ValueError as exc:
            errors[field] = str(exc)

    for field in ("take_profit", "exit_price"):
        if _is_blank(data.get(field)):
            numbers[field] = None
        else:
            try:
                numbers[field] = parse_decimal(data.get(field))
            except ValueError as exc:
                errors[field] = str(exc)

    fees = data.get("fees")
    try:
        numbers["fees"] = Decimal("0") if _is_blank(fees) else parse_decimal(fees, allow_zero=True)
    except ValueError as exc:
        errors["fees"] = str(exc)

    # Cross-field rules only make sense when the individual fields are valid.
    direction = clean.get("direction")
    entry = numbers.get("entry_price")
    stop = numbers.get("stop_loss")
    target = numbers.get("take_profit")
    if direction and entry is not None and stop is not None:
        if direction == "LONG" and not stop < entry:
            errors["stop_loss"] = "must be below the entry price for a LONG trade"
        if direction == "SHORT" and not stop > entry:
            errors["stop_loss"] = "must be above the entry price for a SHORT trade"
    if direction and entry is not None and target is not None:
        if direction == "LONG" and not target > entry:
            errors["take_profit"] = "must be above the entry price for a LONG trade"
        if direction == "SHORT" and not target < entry:
            errors["take_profit"] = "must be below the entry price for a SHORT trade"

    status = data.get("status")
    has_exit = numbers.get("exit_price") is not None
    if _is_blank(status):
        if "exit_price" not in errors:
            clean["status"] = "CLOSED" if has_exit else "OPEN"
    else:
        try:
            clean["status"] = _clean_choice(status, STATUSES)
        except ValueError as exc:
            errors["status"] = str(exc)
    if clean.get("status") == "CLOSED" and not has_exit and "exit_price" not in errors:
        errors["exit_price"] = "is required for a CLOSED trade"
    if clean.get("status") == "OPEN" and has_exit:
        errors["exit_price"] = "must be empty for an OPEN trade"

    closed_at = data.get("closed_at")
    if _is_blank(closed_at):
        clean["closed_at"] = None
    else:
        try:
            clean["closed_at"] = parse_timestamp(closed_at)
        except ValueError as exc:
            errors["closed_at"] = str(exc)
    if "closed_at" in clean:
        if clean.get("status") == "OPEN" and clean["closed_at"] is not None:
            errors["closed_at"] = "must be empty for an OPEN trade"
        elif clean.get("status") == "CLOSED" and clean["closed_at"] is None and require_closed_at:
            errors["closed_at"] = "is required for a CLOSED trade"
        elif (
            clean["closed_at"] is not None
            and "trade_date" in clean
            and clean["closed_at"] < clean["trade_date"]
        ):
            errors["closed_at"] = "must not be earlier than trade_date"

    for field, max_length in (("strategy", 50), ("notes", 2000)):
        try:
            clean[field] = _clean_optional_text(data.get(field), max_length)
        except ValueError as exc:
            errors[field] = str(exc)

    session = data.get("session")
    if _is_blank(session):
        clean["session"] = None
    else:
        try:
            clean["session"] = _clean_choice(session, SESSIONS)
        except ValueError as exc:
            errors["session"] = str(exc)

    if errors:
        raise ValidationError(errors)

    for field, number in numbers.items():
        clean[field] = None if number is None else to_db_decimal(number)
    return clean

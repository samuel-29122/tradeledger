"""Helpers that sit between HTML forms and validation.py.

HTML forms send every value as text, and a missing field is simply absent.
These helpers:
  * keep ONLY the fields a form may set (no mass assignment: a crafted
    "id" or "created_at" field is ignored);
  * keep what the user typed, so a failed submission can redisplay it;
  * turn database records back into form values for editing.

They do NOT decide whether a trade is valid. That is validation.py's job,
shared with the JSON API.
"""

from decimal import Decimal
from urllib.parse import urlsplit

from ..performance.formatting import decimal_to_text
from .filters import datetime_local_filter

TRADE_FORM_FIELDS = (
    "instrument_id",
    "direction",
    "status",
    "trade_date",
    "entry_price",
    "stop_loss",
    "take_profit",
    "position_size",
    "fees",
    "exit_price",
    "closed_at",
    "strategy",
    "session",
    "notes",
)

INSTRUMENT_FORM_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)

# Fields that have no meaning for an OPEN trade. The browser disables them
# (so they are not sent); the server also blanks them if they arrive anyway.
EXIT_FIELDS = ("exit_price", "closed_at")

BLANK_TRADE_FORM = {
    "instrument_id": "",
    "direction": "LONG",
    "status": "CLOSED",
    "trade_date": "",
    "entry_price": "",
    "stop_loss": "",
    "take_profit": "",
    "position_size": "",
    "fees": "0",
    "exit_price": "",
    "closed_at": "",
    "strategy": "",
    "session": "",
    "notes": "",
}


def read_form(form, fields):
    """Text values for the allowed fields only. Absent fields become ""."""
    return {name: form.get(name, "").strip() for name in fields}


def form_errors_for_status(values):
    """The website asks the user to choose a status explicitly (the API
    may infer it). Returns extra errors that validation.py would not raise."""
    if values.get("status") not in ("OPEN", "CLOSED"):
        return {"status": "must be OPEN or CLOSED"}
    return {}


def trade_input(values):
    """Form values -> the dict validate_trade() expects.
    Exit fields of an OPEN trade are ignored rather than rejected: the
    user switched the status to OPEN, so leftover exit values are stale."""
    data = {name: (value if value != "" else None) for name, value in values.items()}
    if values.get("status") == "OPEN":
        for name in EXIT_FIELDS:
            data[name] = None
    return data


def trade_to_form(trade):
    """A stored trade (Decimals, UTC strings) -> text values for the form."""

    def text(value):
        if value is None:
            return ""
        return decimal_to_text(value) if isinstance(value, Decimal) else str(value)

    values = {name: text(trade.get(name)) for name in TRADE_FORM_FIELDS}
    values["trade_date"] = datetime_local_filter(trade["trade_date"])
    values["closed_at"] = datetime_local_filter(trade["closed_at"]) if trade["closed_at"] else ""
    return values


def safe_next(target, fallback):
    """Only allow redirects to a path on this site.

    A `next` value comes from the URL, so an attacker could craft
    ?next=https://evil.example. We accept only relative paths that start
    with a single "/" (not "//", which browsers treat as another host) and
    contain no backslashes or scheme.
    """
    if not target or not isinstance(target, str):
        return fallback
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    if any(ord(char) < 32 or ord(char) == 127 for char in target):  # Phase 8: e.g. "/\t/evil.example"
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return fallback
    return target

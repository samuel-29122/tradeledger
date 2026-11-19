"""Reading and checking HTTP input: JSON bodies and query strings.

These functions only check the SHAPE of a request (is it JSON? are the
field names allowed? is `page` a whole number?). The business rules for a
trade or instrument stay in validation.py, so the web forms in Phase 5 and
this API apply exactly the same rules.
"""

import json
from decimal import Decimal

from flask import current_app, request

from ..validation import ValidationError, parse_date
from .errors import ApiError

# Fields a client may send when creating or replacing a trade. Anything
# else (id, created_at, updated_at, symbol, ...) is rejected, so a client
# can never write a column it should not control ("mass assignment").
TRADE_INPUT_FIELDS = (
    "instrument_id",
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
)

INSTRUMENT_INPUT_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)

DEFAULT_PER_PAGE = 20


def _reject_constant(name):
    # json.loads accepts NaN and Infinity by default; money must never be those.
    raise ValueError(f"{name} is not allowed")


def json_body():
    """Return the request body as a dict, or raise ApiError.

    * 415 if Content-Type is not application/json
    * 400 if the body is not valid JSON or is not a JSON object
    JSON numbers with a decimal point become Decimal (never float), so
    {"entry_price": 1.085} is read exactly. Strings such as "1.085" are the
    recommended form and are documented in the OpenAPI specification.
    """
    if request.mimetype != "application/json":
        raise ApiError(
            415,
            "unsupported_media_type",
            "Send the request body as JSON with Content-Type: application/json.",
        )
    try:
        text = request.get_data(as_text=False).decode("utf-8")
        body = json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, "malformed_json", "The request body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(400, "malformed_json", "The request body must be a JSON object.")
    return body


def check_fields(body, allowed, *, require_all=False):
    """Reject unknown field names; with require_all, every field must be present.

    require_all is used by PUT: a replacement must state every field
    (null is allowed for optional ones), so nothing is changed by omission.
    """
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise ApiError(
            400,
            "unknown_fields",
            "The request contains fields that cannot be set.",
            dict.fromkeys(unknown, "is not an accepted field"),
        )
    if require_all:
        missing = [name for name in allowed if name not in body]
        if missing:
            raise ApiError(
                400,
                "missing_fields",
                "PUT replaces the whole record, so every field must be supplied "
                "(use null for empty optional fields).",
                dict.fromkeys(missing, "is required for a full replacement"),
            )


def query_args(allowed):
    """Return the query string as a dict, rejecting unknown parameter names.
    Catching typos ('instrument' instead of 'instrument_id') is kinder than
    silently ignoring them and returning unfiltered data."""
    unknown = sorted(set(request.args) - set(allowed))
    if unknown:
        raise ApiError(
            400,
            "unknown_parameters",
            "The query string contains unknown parameters.",
            dict.fromkeys(unknown, "is not a supported parameter"),
        )
    return {name: request.args.get(name) for name in allowed if name in request.args}


def positive_int(args, name, *, default=None, maximum=None):
    raw = args.get(name)
    if raw is None:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ValidationError({name: "must be a whole number of 1 or more"})
    value = int(raw)
    if maximum is not None and value > maximum:
        raise ValidationError({name: f"must be {maximum} or less"})
    return value


def date_arg(args, name):
    raw = args.get(name)
    if raw is None:
        return None
    try:
        return parse_date(raw).isoformat()
    except ValueError as exc:
        raise ValidationError({name: str(exc)}) from None


def status_arg(args):
    raw = args.get("status")
    if raw is None:
        return None
    value = raw.strip().upper()
    if value not in ("OPEN", "CLOSED"):
        raise ValidationError({"status": "must be OPEN or CLOSED"})
    return value


def pagination_args(args):
    page = positive_int(args, "page", default=1)
    per_page = positive_int(
        args, "per_page", default=DEFAULT_PER_PAGE, maximum=current_app.config["API_MAX_PER_PAGE"]
    )
    return page, per_page

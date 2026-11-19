"""OpenAPI 3.1 specification for the TradeLedger API.

Built as a Python dict (no extra library) and served at
GET /api/v1/openapi.json. `flask --app tradeledger openapi` writes the same
document to docs/openapi.json.

The tests check that:
  * every API route and method exists in this document, and vice versa;
  * real responses validate against the schemas here (with
    additionalProperties: false, so an undocumented field fails the test).
"""

from flask import current_app, jsonify

from . import api_bp

# ---------- Reusable schema fragments ----------

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
TIMESTAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def decimal(description, nullable=False, example=None):
    schema = {
        "type": ["string", "null"] if nullable else "string",
        "pattern": DECIMAL_PATTERN,
        "description": description + " Exact decimal as a string (never a float).",
    }
    if example is not None:
        schema["examples"] = [example]
    return schema


def timestamp(description, nullable=False):
    return {
        "type": ["string", "null"] if nullable else "string",
        "pattern": TIMESTAMP_PATTERN,
        "description": description + " UTC, ISO 8601, e.g. 2026-03-02T08:15:00Z.",
    }


def nullable_string(description, enum=None):
    schema = {"type": ["string", "null"], "description": description}
    if enum:
        schema["enum"] = [*enum, None]
    return schema


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


DECIMAL_INPUT = {
    "description": 'Decimal value. Send a string (recommended, e.g. "1.085") or a JSON number; '
    "numbers are read exactly, never as binary floats. NaN and Infinity are rejected. "
    "At most 15 significant digits, 15 digits before the decimal point and 15 decimal places.",
    "anyOf": [{"type": "string"}, {"type": "number"}],
}
TIMESTAMP_INPUT = {
    "type": "string",
    "description": "ISO 8601 date-time. A value without an offset is treated as UTC; "
    "offsets are converted to UTC. A bare date is rejected.",
    "examples": ["2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00"],
}

TRADE_PROPERTIES = {
    "id": {"type": "integer"},
    "instrument_id": {"type": "integer"},
    "symbol": {"type": "string"},
    "quote_currency": {"type": "string", "description": "Currency of all money values for this trade."},
    "trade_date": timestamp("Entry time."),
    "closed_at": timestamp(
        "Exit time. null for OPEN trades, and for legacy CLOSED trades recorded "
        "before the exit time was captured (never invented).",
        nullable=True,
    ),
    "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
    "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
    "entry_price": decimal("Entry price.", example="1.085"),
    "stop_loss": decimal("Initial stop-loss price."),
    "take_profit": decimal("Target price, if planned.", nullable=True),
    "exit_price": decimal("Exit price. null while OPEN.", nullable=True),
    "position_size": decimal("Position size in the units the instrument's contract_multiplier assumes."),
    "fees": decimal("Total fees, in the quote currency."),
    "strategy": nullable_string("Free-text strategy label (max 50 characters)."),
    "session": nullable_string("Trading session.", ["ASIA", "LONDON", "NEW_YORK", "OTHER"]),
    "notes": nullable_string("Journal notes (max 2000 characters)."),
    "created_at": timestamp("When the record was created."),
    "updated_at": timestamp("When the record was last changed."),
    "result": {
        "anyOf": [{"$ref": "#/components/schemas/TradeResult"}, {"type": "null"}],
        "description": "Calculated by the analytics engine for CLOSED trades; null for OPEN trades.",
    },
}

TRADE_RESULT = obj(
    {
        "outcome": {
            "type": "string",
            "enum": ["WIN", "LOSS", "BREAKEVEN"],
            "description": "From net P&L: > 0 WIN, < 0 LOSS, exactly 0 BREAKEVEN.",
        },
        "gross_pnl": decimal("(exit - entry) x direction x size x contract_multiplier."),
        "fees": decimal("Fees."),
        "net_pnl": decimal("gross_pnl - fees."),
        "initial_risk": decimal(
            "Money lost if the stop is hit (fees excluded). null if the stop is invalid.", nullable=True
        ),
        "realised_r": decimal(
            "net_pnl / initial_risk. null when initial risk is not positive.", nullable=True
        ),
        "planned_rr": decimal(
            "Planned reward / planned risk from prices. null without a valid target.", nullable=True
        ),
        "r_unavailable_reason": nullable_string("Why realised_r is null, if it is."),
    }
)

TRADE_INPUT_PROPERTIES = {
    "instrument_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Must reference an existing instrument.",
    },
    "trade_date": TIMESTAMP_INPUT,
    "closed_at": {
        **TIMESTAMP_INPUT,
        "type": ["string", "null"],
        "description": "Exit time. Required when the trade is CLOSED; must be null/absent when OPEN; "
        "must not be before trade_date. Exception: a legacy CLOSED trade whose exit time "
        "was never recorded may keep null when replaced with PUT.",
    },
    "direction": {
        "type": "string",
        "enum": ["LONG", "SHORT", "long", "short"],
        "description": "Case-insensitive.",
    },
    "status": {
        "type": ["string", "null"],
        "description": "OPEN or CLOSED (case-insensitive). If omitted: CLOSED when exit_price is given, "
        "otherwise OPEN. CLOSED requires exit_price; OPEN must not have one.",
    },
    "entry_price": {**DECIMAL_INPUT, "description": "Greater than 0. " + DECIMAL_INPUT["description"]},
    "stop_loss": {
        **DECIMAL_INPUT,
        "description": "Greater than 0. LONG: below entry. SHORT: above entry. Never equal to entry.",
    },
    "take_profit": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Optional. LONG: above entry. SHORT: below entry.",
    },
    "exit_price": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Required for CLOSED, absent/null for OPEN. Greater than 0.",
    },
    "position_size": {**DECIMAL_INPUT, "description": "Greater than 0."},
    "fees": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "0 or more. Defaults to 0.",
    },
    "strategy": {"type": ["string", "null"], "maxLength": 50},
    "session": {
        "type": ["string", "null"],
        "description": "ASIA, LONDON, NEW_YORK or OTHER (case-insensitive).",
    },
    "notes": {"type": ["string", "null"], "maxLength": 2000},
}

TRADE_INPUT_REQUIRED = [
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "position_size",
]

INSTRUMENT_PROPERTIES = {
    "id": {"type": "integer"},
    "symbol": {"type": "string", "description": "Unique, upper case, 1-20 letters, digits or underscores."},
    "name": {"type": "string"},
    "asset_class": {"type": "string", "enum": ["FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER"]},
    "price_increment": decimal("Smallest price step quoted, e.g. 0.00001."),
    "contract_multiplier": decimal(
        "Money (in quote_currency) per 1.0 move in price per 1.0 unit of position_size. "
        "Depends on your broker's contract specification; sample instruments use illustrative values."
    ),
    "quote_currency": {
        "type": "string",
        "description": "3-letter currency code in which P&L is denominated.",
    },
    "created_at": timestamp("When the record was created."),
}

INSTRUMENT_INPUT = obj(
    {
        "symbol": {
            "type": "string",
            "description": "1-20 letters, digits or underscores; stored upper case.",
        },
        "name": {"type": "string", "maxLength": 100},
        "asset_class": {
            "type": "string",
            "description": "FOREX, COMMODITY, INDEX, STOCK or OTHER (case-insensitive).",
        },
        "price_increment": {**DECIMAL_INPUT, "description": "Greater than 0."},
        "contract_multiplier": {
            **DECIMAL_INPUT,
            "description": "Greater than 0. See Instrument.contract_multiplier.",
        },
        "quote_currency": {"type": "string", "description": "3 letters, e.g. USD."},
    }
)

TRADE_REF = obj(
    {
        "trade_id": {"type": "integer"},
        "symbol": {"type": "string"},
        "trade_date": timestamp("Entry time."),
        "value": decimal("The value that ranked this trade (R or net P&L)."),
    }
)

DRAWDOWN = obj(
    {
        "amount": decimal(
            "Largest fall from a running peak (>= 0), in R or in one currency. Never a percentage."
        ),
        "peak_trade_id": {"type": ["integer", "null"], "description": "null = the starting point (0)."},
        "trough_trade_id": {"type": ["integer", "null"], "description": "null when there was no drawdown."},
    }
)

MONEY_SUMMARY = obj(
    {
        "currency": {"type": "string"},
        "trades": {"type": "integer"},
        "gross_pnl": decimal("Sum of P&L before fees."),
        "fees": decimal("Sum of fees."),
        "net_pnl": decimal("Sum of P&L after fees."),
        "winning_pnl": decimal("Sum of net P&L of winning trades (>= 0)."),
        "losing_pnl": decimal("Sum of net P&L of losing trades (<= 0)."),
        "profit_factor": decimal("winning_pnl / |losing_pnl|. null with no losing trades.", nullable=True),
        "best_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "worst_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "max_drawdown": ref("Drawdown"),
    }
)

SUMMARY_PROPERTIES = {
    "trades": {"type": "integer", "description": "Closed trades evaluated."},
    "wins": {"type": "integer"},
    "losses": {"type": "integer"},
    "breakevens": {"type": "integer"},
    "win_rate": decimal("wins / trades, as a fraction 0-1. null with no trades.", nullable=True),
    "loss_rate": decimal("losses / trades, 0-1.", nullable=True),
    "breakeven_rate": decimal("breakevens / trades, 0-1.", nullable=True),
    "r_trades": {"type": "integer", "description": "Trades with a usable realised R."},
    "total_r": decimal("Sum of realised R."),
    "average_r": decimal("Mean realised R.", nullable=True),
    "average_win_r": decimal("Mean R of winning trades.", nullable=True),
    "average_loss_r": decimal("Mean R of losing trades (negative).", nullable=True),
    "expectancy_r": decimal(
        "P(win) x average_win_r + P(loss) x average_loss_r, in R per trade.", nullable=True
    ),
    "profit_factor_r": decimal(
        "Sum of positive R / |sum of negative R|. null with no losses.", nullable=True
    ),
    "planned_rr_trades": {"type": "integer"},
    "average_planned_rr": decimal("Mean planned R:R of trades with a valid target.", nullable=True),
    "longest_win_streak": {"type": "integer"},
    "longest_loss_streak": {"type": "integer"},
    "max_drawdown_r": ref("Drawdown"),
    "best_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "worst_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "money": {
        "type": "object",
        "additionalProperties": ref("MoneySummary"),
        "description": "One entry per quote currency. Currencies are never added together.",
    },
}

EXCLUDED_TRADE = obj({"trade_id": {"type": ["integer", "null"]}, "reason": {"type": "string"}})

CURVE_POINT = obj(
    {
        "trade_id": {"type": "integer"},
        "trade_date": timestamp("Entry time."),
        "realised_at": timestamp("Exit time, or entry time for legacy trades with no recorded exit."),
        "close_time_known": {"type": "boolean"},
        "symbol": {"type": "string"},
        "realised_r": decimal("This trade's realised R."),
        "cumulative_r": decimal("Running total of realised R. NOT account equity."),
    }
)

ANALYTICS_META = obj(
    {
        "filters": obj(
            {
                "instrument_id": {"type": ["integer", "null"]},
                "strategy": {"type": ["string", "null"]},
                "session": {
                    "type": ["string", "null"],
                    "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER", None],
                },
                "start": {"type": ["string", "null"], "format": "date"},
                "end": {"type": ["string", "null"], "format": "date"},
            }
        ),
        "notes": {"type": "array", "items": {"type": "string"}},
        "label": {"type": "string"},
    },
    required=["filters", "notes"],
)

ERROR = obj(
    {
        "error": obj(
            {
                "code": {"type": "string", "description": "Stable machine-readable code."},
                "message": {"type": "string", "description": "Human-readable explanation."},
                "details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Field name -> problem. Present only when useful.",
                },
            },
            required=["code", "message"],
        ),
    }
)

PAGINATION = obj(
    {
        "page": {"type": "integer", "minimum": 1},
        "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
        "total": {"type": "integer", "description": "Matching trades across all pages."},
        "total_pages": {"type": "integer", "description": "0 when there are no matches."},
    }
)

# ---------- Parameters ----------


def query_param(name, schema, description):
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": description}


PARAMS = {
    "instrument_id": query_param(
        "instrument_id", {"type": "integer", "minimum": 1}, "Only this instrument. 400 if it does not exist."
    ),
    "status": query_param(
        "status",
        {"type": "string", "enum": ["OPEN", "CLOSED"]},
        "Only trades with this status (case-insensitive).",
    ),
    "trade_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "trade_end": query_param(
        "end",
        {"type": "string", "format": "date"},
        "Last entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "realised_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First realised date (closed_at, or trade_date if unknown) to include.",
    ),
    "realised_end": query_param(
        "end", {"type": "string", "format": "date"}, "Last realised date to include (inclusive)."
    ),
    "strategy": query_param(
        "strategy",
        {"type": "string", "minLength": 1, "maxLength": 50},
        "Only trades with exactly this strategy label (case-sensitive). Phase 6.",
    ),
    "session": query_param(
        "session",
        {"type": "string", "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER"]},
        "Only trades recorded in this session (case-insensitive). Phase 6.",
    ),
    "page": query_param("page", {"type": "integer", "minimum": 1, "default": 1}, "Page number."),
    "per_page": query_param(
        "per_page", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "Trades per page."
    ),
}


def path_id(name, what):
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "integer", "minimum": 1},
        "description": f"{what} id.",
    }


# ---------- Responses ----------


def json_response(description, schema, example=None):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def error(description):
    return json_response(description, ref("Error"))


ERRORS = {
    "400": error(
        "Invalid request: malformed JSON, unknown fields or parameters, or failed validation "
        "(details lists each field)."
    ),
    "401": error(
        "Missing, malformed, unknown, expired or revoked API token, or a disabled account. "
        "The response carries `WWW-Authenticate: Bearer`."
    ),
    "403": error("The API is switched off in this environment (TRADELEDGER_API_ENABLED=0)."),
    "404": error("The resource does not exist, or belongs to another user (the two are indistinguishable)."),
    "405": error("Method not allowed for this URL."),
    "409": error("Conflict, e.g. an instrument symbol you have already recorded."),
    "413": error("Request body larger than 64 KB."),
    "415": error("Request body is not application/json."),
    "500": error("Unexpected error. No internal details are included."),
}


def errors(*codes):
    """Error responses for one operation. Every data endpoint can answer 401."""
    return {code: ERRORS[code] for code in sorted({"401", *codes})}


def envelope(data_schema, **extra):
    return obj({"data": data_schema, **extra})


EXAMPLE_TRADE_INPUT = {
    "instrument_id": 1,
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "entry_price": "1.08500",
    "stop_loss": "1.08300",
    "take_profit": "1.08900",
    "exit_price": "1.08900",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
}

EXAMPLE_TRADE = {
    "id": 17,
    "instrument_id": 1,
    "symbol": "EURUSD",
    "quote_currency": "USD",
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "status": "CLOSED",
    "entry_price": "1.085",
    "stop_loss": "1.083",
    "take_profit": "1.089",
    "exit_price": "1.089",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
    "created_at": "2026-11-15T09:00:00Z",
    "updated_at": "2026-11-15T09:00:00Z",
    "result": {
        "outcome": "WIN",
        "gross_pnl": "400",
        "fees": "7",
        "net_pnl": "393",
        "initial_risk": "200",
        "realised_r": "1.965",
        "planned_rr": "2",
        "r_unavailable_reason": None,
    },
}

EXAMPLE_VALIDATION_ERROR = {
    "error": {
        "code": "validation_error",
        "message": "The request is invalid.",
        "details": {
            "position_size": "must be greater than zero",
            "stop_loss": "must be below the entry price for a LONG trade",
        },
    }
}


# The analytics filters, in the same order as report_filters.FILTER_NAMES.
ANALYTICS_PARAMS = [
    PARAMS["instrument_id"],
    PARAMS["strategy"],
    PARAMS["session"],
    PARAMS["realised_start"],
    PARAMS["realised_end"],
]


def grouped_path(summary, description):
    return {
        "get": {
            "tags": ["Analytics"],
            "summary": summary,
            "description": description,
            "parameters": ANALYTICS_PARAMS,
            "responses": {
                "200": json_response("Groups in a stable order.", ref("GroupedAnalytics")),
                **errors("400", "403", "405"),
            },
        }
    }


def build_spec():
    group_summary = obj({"group": {"type": "string", "description": "Group label."}, **SUMMARY_PROPERTIES})
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TradeLedger API",
            "version": "1.0.0",
            "description": (
                "JSON API for the TradeLedger trading journal.\n\n"
                "**Authentication:** every endpoint except this document needs a personal API token, "
                "created on the Account page of the website: `Authorization: Bearer tl_...`. "
                "Tokens expire (90 days by default) and can be revoked; session cookies are ignored. "
                "Each token sees and changes only its owner's records; another user's record answers 404.\n\n"
                "**Conventions:** decimal values are exact strings; timestamps are UTC "
                "`YYYY-MM-DDTHH:MM:SSZ`; unavailable values are `null`; money is per quote currency; "
                "R values are currency-neutral; cumulative R is not account equity. "
                "Unknown body fields and query parameters are rejected with 400."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/trades": {
                "get": {
                    "tags": ["Trades"],
                    "summary": "List trades",
                    "description": "Newest trade_date first, then highest id first. Dates filter on entry time.",
                    "parameters": [
                        PARAMS[p]
                        for p in ("instrument_id", "status", "trade_start", "trade_end", "page", "per_page")
                    ],
                    "responses": {
                        "200": json_response("A page of trades.", ref("TradeList")),
                        **errors("400", "403", "405"),
                    },
                },
                "post": {
                    "tags": ["Trades"],
                    "summary": "Create a trade",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": ref("TradeInput"), "example": EXAMPLE_TRADE_INPUT}
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response(
                                "Created. Location header holds the new trade's URL.",
                                ref("TradeEnvelope"),
                                {"data": EXAMPLE_TRADE},
                            ),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        "400": json_response(
                            "Invalid trade. Nothing was written.", ref("Error"), EXAMPLE_VALIDATION_ERROR
                        ),
                        **errors("403", "413", "415"),
                    },
                },
            },
            "/trades/{trade_id}": {
                "parameters": [path_id("trade_id", "Trade")],
                "get": {
                    "tags": ["Trades"],
                    "summary": "Get a trade",
                    "responses": {
                        "200": json_response("The trade.", ref("TradeEnvelope")),
                        **errors("403", "404"),
                    },
                },
                "put": {
                    "tags": ["Trades"],
                    "summary": "Replace a trade",
                    "description": "Full replacement: every field in TradeReplace must be present "
                    "(null for empty optional fields). There are no partial updates.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("TradeReplace"),
                                "example": EXAMPLE_TRADE_INPUT,
                            }
                        },
                    },
                    "responses": {
                        "200": json_response("The updated trade.", ref("TradeEnvelope")),
                        **errors("400", "403", "404", "413", "415"),
                    },
                },
                "delete": {
                    "tags": ["Trades"],
                    "summary": "Delete a trade",
                    "responses": {"204": {"description": "Deleted. No body."}, **errors("403", "404")},
                },
            },
            "/instruments": {
                "get": {
                    "tags": ["Instruments"],
                    "summary": "List instruments (sorted by symbol)",
                    "responses": {
                        "200": json_response("All instruments.", ref("InstrumentList")),
                        **errors("400", "403"),
                    },
                },
                "post": {
                    "tags": ["Instruments"],
                    "summary": "Create an instrument",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("InstrumentInput"),
                                "example": {
                                    "symbol": "US30",
                                    "name": "Wall Street 30 CFD (example values)",
                                    "asset_class": "INDEX",
                                    "price_increment": "1",
                                    "contract_multiplier": "1",
                                    "quote_currency": "USD",
                                },
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response("Created.", ref("InstrumentEnvelope")),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        **errors("400", "403", "409", "413", "415"),
                    },
                },
            },
            "/instruments/{instrument_id}": {
                "parameters": [path_id("instrument_id", "Instrument")],
                "get": {
                    "tags": ["Instruments"],
                    "summary": "Get an instrument",
                    "responses": {
                        "200": json_response("The instrument.", ref("InstrumentEnvelope")),
                        **errors("403", "404"),
                    },
                },
            },
            "/analytics/summary": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Overall realised performance",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Overall statistics.", ref("SummaryAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/analytics/instruments": grouped_path("Performance by instrument", "Sorted by symbol."),
            "/analytics/strategies": grouped_path(
                "Performance by strategy", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/sessions": grouped_path(
                "Performance by session", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/weekday": grouped_path(
                "Performance by realised weekday (UTC)", "Monday to Sunday; days with no trades are omitted."
            ),
            "/analytics/monthly": grouped_path(
                "Performance by realised month (YYYY-MM, UTC)", "Oldest first."
            ),
            "/analytics/curve": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Cumulative realised R (chart data)",
                    "description": "In realised order. NOT an account equity curve.",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Curve points.", ref("CurveAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "tags": ["Documentation"],
                    "summary": "This specification",
                    "description": "Public: needs no token, and is available even when the API is switched off.",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Trade": obj(TRADE_PROPERTIES),
                "TradeResult": TRADE_RESULT,
                "TradeInput": obj(TRADE_INPUT_PROPERTIES, required=TRADE_INPUT_REQUIRED),
                "TradeReplace": obj(TRADE_INPUT_PROPERTIES),
                "Instrument": obj(INSTRUMENT_PROPERTIES),
                "InstrumentInput": INSTRUMENT_INPUT,
                "Pagination": PAGINATION,
                "TradeEnvelope": envelope(ref("Trade")),
                "TradeList": envelope({"type": "array", "items": ref("Trade")}, pagination=ref("Pagination")),
                "InstrumentEnvelope": envelope(ref("Instrument")),
                "InstrumentList": envelope({"type": "array", "items": ref("Instrument")}),
                "TradeRef": TRADE_REF,
                "Drawdown": DRAWDOWN,
                "MoneySummary": MONEY_SUMMARY,
                "Summary": obj(SUMMARY_PROPERTIES),
                "GroupSummary": group_summary,
                "ExcludedTrade": EXCLUDED_TRADE,
                "CurvePoint": CURVE_POINT,
                "AnalyticsMeta": ANALYTICS_META,
                "SummaryAnalytics": envelope(
                    obj(
                        {
                            "overall": ref("Summary"),
                            "open_trades": {
                                "type": "integer",
                                "description": "Counted, but excluded from realised metrics.",
                            },
                            "excluded": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades that could not be evaluated, with reasons.",
                            },
                            "r_unavailable": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades counted, but with no realised R.",
                            },
                            "unknown_close_time_trade_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Legacy trades ordered by entry time.",
                            },
                        }
                    ),
                    meta=ref("AnalyticsMeta"),
                ),
                "GroupedAnalytics": envelope(
                    {"type": "array", "items": ref("GroupSummary")}, meta=ref("AnalyticsMeta")
                ),
                "CurveAnalytics": envelope(
                    {"type": "array", "items": ref("CurvePoint")}, meta=ref("AnalyticsMeta")
                ),
                "Error": ERROR,
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Personal API token (starts with tl_). Create and revoke tokens on /account.",
                }
            },
        },
    }


@api_bp.get("/openapi.json")
def openapi_spec():
    spec = build_spec()
    spec["info"]["x-app-version"] = current_app.config["APP_VERSION"]
    return jsonify(spec)

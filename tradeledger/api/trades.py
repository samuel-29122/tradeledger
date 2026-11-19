"""Trade endpoints.

GET    /api/v1/trades         list, filter, paginate
POST   /api/v1/trades         create                  -> 201 + Location
GET    /api/v1/trades/<id>    retrieve                -> 404 if missing
PUT    /api/v1/trades/<id>    full replacement        -> 404 if missing
DELETE /api/v1/trades/<id>    delete                  -> 204, 404 if missing

Each handler: check the request shape (http.py), call the repository (which
validates and writes inside a transaction), serialise the result.
"""

import math

from flask import jsonify, url_for

from ..db import get_db
from ..repositories import instruments as instrument_repo
from ..repositories import trades as trade_repo
from ..validation import ValidationError
from . import api_bp, api_user_id
from .errors import ApiError
from .http import (
    TRADE_INPUT_FIELDS,
    check_fields,
    date_arg,
    json_body,
    pagination_args,
    positive_int,
    query_args,
    status_arg,
)
from .serialise import trade_json

LIST_PARAMETERS = ("instrument_id", "status", "start", "end", "page", "per_page")


def _not_found(trade_id):
    return ApiError(404, "not_found", f"Trade {trade_id} does not exist.")


def require_known_instrument(conn, instrument_id):
    """Another user's instrument id gets the same answer as a missing one."""
    if (
        instrument_id is not None
        and instrument_repo.get_instrument(conn, instrument_id, user_id=api_user_id()) is None
    ):
        raise ValidationError({"instrument_id": "does not match an existing instrument"})


@api_bp.get("/trades")
def list_trades():
    args = query_args(LIST_PARAMETERS)
    instrument_id = positive_int(args, "instrument_id")
    filters = {
        "instrument_id": instrument_id,
        "status": status_arg(args),
        "start": date_arg(args, "start"),
        "end": date_arg(args, "end"),
    }
    page, per_page = pagination_args(args)

    conn = get_db()
    require_known_instrument(conn, instrument_id)
    user_id = api_user_id()
    total = trade_repo.count_trades(conn, user_id=user_id, **filters)
    rows = trade_repo.list_trades(
        conn, user_id=user_id, **filters, limit=per_page, offset=(page - 1) * per_page
    )
    return jsonify(
        data=[trade_json(row) for row in rows],
        pagination={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": math.ceil(total / per_page),
        },
    )


@api_bp.post("/trades")
def create_trade():
    body = json_body()
    check_fields(body, TRADE_INPUT_FIELDS)
    conn = get_db()
    # Ownership comes from the token, never from the body: check_fields()
    # rejects unknown fields such as "user_id" (400), and the repository
    # always writes the authenticated user's id.
    trade_id = trade_repo.create_trade(conn, body, user_id=api_user_id())
    response = jsonify(data=trade_json(trade_repo.get_trade(conn, trade_id, user_id=api_user_id())))
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_trade", trade_id=trade_id)
    return response


@api_bp.get("/trades/<int:trade_id>")
def get_trade(trade_id):
    trade = trade_repo.get_trade(get_db(), trade_id, user_id=api_user_id())
    if trade is None:
        raise _not_found(trade_id)
    return jsonify(data=trade_json(trade))


@api_bp.put("/trades/<int:trade_id>")
def replace_trade(trade_id):
    body = json_body()
    check_fields(body, TRADE_INPUT_FIELDS, require_all=True)
    conn = get_db()
    if not trade_repo.update_trade(conn, trade_id, body, user_id=api_user_id()):
        raise _not_found(trade_id)
    return jsonify(data=trade_json(trade_repo.get_trade(conn, trade_id, user_id=api_user_id())))


@api_bp.delete("/trades/<int:trade_id>")
def delete_trade(trade_id):
    if not trade_repo.delete_trade(get_db(), trade_id, user_id=api_user_id()):
        raise _not_found(trade_id)
    return "", 204

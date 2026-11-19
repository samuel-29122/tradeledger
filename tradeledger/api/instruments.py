"""Instrument endpoints.

GET  /api/v1/instruments        list (sorted by symbol; no pagination,
                                a journal has tens of instruments, not thousands)
GET  /api/v1/instruments/<id>   retrieve -> 404 if missing
POST /api/v1/instruments        create   -> 201 + Location; 409 if YOU already have the symbol

There is deliberately no PUT or DELETE yet: changing an instrument's
contract_multiplier would silently change the P&L of every existing trade
on it, and deleting one with trades is refused by the database. Those need
their own design (versioned contract specifications) before they exist.
"""

from flask import jsonify, url_for

from ..db import get_db
from ..repositories import instruments as instrument_repo
from . import api_bp, api_user_id
from .errors import ApiError
from .http import INSTRUMENT_INPUT_FIELDS, check_fields, json_body, query_args
from .serialise import instrument_json


@api_bp.get("/instruments")
def list_instruments():
    query_args(())  # no parameters accepted
    return jsonify(
        data=[instrument_json(i) for i in instrument_repo.list_instruments(get_db(), user_id=api_user_id())]
    )


@api_bp.get("/instruments/<int:instrument_id>")
def get_instrument(instrument_id):
    instrument = instrument_repo.get_instrument(get_db(), instrument_id, user_id=api_user_id())
    if instrument is None:
        raise ApiError(404, "not_found", f"Instrument {instrument_id} does not exist.")
    return jsonify(data=instrument_json(instrument))


@api_bp.post("/instruments")
def create_instrument():
    body = json_body()
    check_fields(body, INSTRUMENT_INPUT_FIELDS)
    conn = get_db()
    # DuplicateError (repeated symbol) becomes 409 via the blueprint handler.
    instrument_id = instrument_repo.create_instrument(conn, body, user_id=api_user_id())
    response = jsonify(
        data=instrument_json(instrument_repo.get_instrument(conn, instrument_id, user_id=api_user_id()))
    )
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_instrument", instrument_id=instrument_id)
    return response

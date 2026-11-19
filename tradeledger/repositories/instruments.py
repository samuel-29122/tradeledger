"""Instrument repository.

Every function takes the OWNER's user id as a required keyword argument
(Phase 8). Each SQL statement includes "user_id = ?", so a user can only
ever create, see or look up their own instruments. Forgetting the argument
is a TypeError, never an accidental "all users" query.

Return values
  create_instrument          -> new id (int)
  get_instrument             -> dict, or None if not found (or not yours)
  get_instrument_by_symbol   -> dict, or None if not found (or not yours)
  list_instruments           -> list of dicts (empty list if none), by symbol

Errors
  ValidationError  invalid input (nothing is written)
  DuplicateError   you already have an instrument with this symbol
"""

from decimal import Decimal

from ..db import IntegrityError
from ..validation import validate_instrument
from . import DuplicateError, require_user_id

_COLUMNS = "id, symbol, name, asset_class, price_increment, contract_multiplier, quote_currency, created_at"


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    record["price_increment"] = Decimal(record["price_increment"])
    record["contract_multiplier"] = Decimal(record["contract_multiplier"])
    return record


def create_instrument(conn, data, *, user_id):
    require_user_id(user_id)
    clean = validate_instrument(data)
    try:
        with conn:  # commit on success, roll back on any exception
            row = conn.execute(
                """
                INSERT INTO instruments
                    (user_id, symbol, name, asset_class, price_increment,
                     contract_multiplier, quote_currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    user_id,
                    clean["symbol"],
                    clean["name"],
                    clean["asset_class"],
                    conn.numeric(clean["price_increment"]),
                    conn.numeric(clean["contract_multiplier"]),
                    clean["quote_currency"],
                ),
            ).fetchone()
    except IntegrityError as exc:
        if exc.kind == "unique":
            raise DuplicateError(f"Instrument '{clean['symbol']}' already exists") from exc
        raise
    return row["id"]


def get_instrument(conn, instrument_id, *, user_id):
    require_user_id(user_id)
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE id = ? AND user_id = ?",  # noqa: S608 - _COLUMNS is a module constant
        (instrument_id, user_id),
    ).fetchone()
    return _to_dict(row)


def get_instrument_by_symbol(conn, symbol, *, user_id):
    require_user_id(user_id)
    if not isinstance(symbol, str):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE symbol = ? AND user_id = ?",  # noqa: S608 - _COLUMNS is a module constant
        (symbol.strip().upper(), user_id),
    ).fetchone()
    return _to_dict(row)


def list_instruments(conn, *, user_id):
    require_user_id(user_id)
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE user_id = ? ORDER BY symbol",  # noqa: S608 - constant
        (user_id,),
    ).fetchall()
    return [_to_dict(row) for row in rows]

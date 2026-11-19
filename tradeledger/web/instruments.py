"""Instrument list and add form.

There is no edit or delete: changing an instrument's contract_multiplier
would silently change the P&L of every past trade on it. That needs
versioned contract specifications first (see the Phase 4 guide).
"""

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..repositories import DuplicateError
from ..repositories.instruments import create_instrument, list_instruments
from ..validation import ASSET_CLASSES, ValidationError
from . import main_bp
from .auth import current_user_id
from .forms import INSTRUMENT_FORM_FIELDS, read_form

BLANK_INSTRUMENT_FORM = dict.fromkeys(INSTRUMENT_FORM_FIELDS, "")

INSTRUMENT_LABELS = {
    "symbol": "Symbol",
    "name": "Name",
    "asset_class": "Asset class",
    "price_increment": "Price increment",
    "contract_multiplier": "Contract multiplier",
    "quote_currency": "Quote currency",
}


def page(values, errors, status=200):
    return render_template(
        "instruments/index.html",
        instruments=list_instruments(get_db(), user_id=current_user_id()),
        values=values,
        errors=errors,
        labels=INSTRUMENT_LABELS,
        asset_class_options=[(value, value.title()) for value in ASSET_CLASSES],
    ), status


@main_bp.route("/instruments", methods=["GET", "POST"])
def instruments():
    if request.method == "GET":
        return page(dict(BLANK_INSTRUMENT_FORM), {})

    values = read_form(request.form, INSTRUMENT_FORM_FIELDS)
    try:
        create_instrument(get_db(), values, user_id=current_user_id())
    except ValidationError as exc:
        return page(values, exc.errors, 400)
    except DuplicateError:
        return page(values, {"symbol": "is already recorded. Symbols must be unique."}, 400)

    flash(f"Instrument {values['symbol'].upper()} added.", "success")
    return redirect(url_for("main.instruments"))

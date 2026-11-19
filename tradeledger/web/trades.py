"""Trade pages: journal, add, details, edit, delete.

Pattern for every form (POST-Redirect-GET):
  GET   shows the form
  POST  validates. If invalid, re-renders the form with the user's values
        and field errors (status 400). If valid, writes inside a
        transaction, flashes a message and REDIRECTS, so refreshing the
        next page cannot submit the form twice.

All rules live in validation.py and the repositories (shared with the
API). All performance numbers come from performance.trade_math.
"""

from decimal import DecimalException

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from ..db import get_db
from ..performance.trade_math import IncompleteTradeError, evaluate_trade, initial_risk, planned_rr
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from ..validation import SESSIONS, ValidationError, date_range_bounds, parse_date, validate_trade
from . import main_bp
from .auth import current_user_id
from .forms import (
    BLANK_TRADE_FORM,
    TRADE_FORM_FIELDS,
    form_errors_for_status,
    read_form,
    safe_next,
    trade_input,
    trade_to_form,
)

FILTER_FIELDS = ("instrument_id", "status", "start", "end", "strategy", "q")

FIELD_LABELS = {
    "instrument_id": "Instrument",
    "direction": "Direction",
    "status": "Status",
    "trade_date": "Entry time",
    "entry_price": "Entry price",
    "stop_loss": "Stop-loss",
    "take_profit": "Take-profit",
    "position_size": "Position size",
    "fees": "Fees",
    "exit_price": "Exit price",
    "closed_at": "Exit time",
    "strategy": "Strategy",
    "session": "Session",
    "notes": "Notes",
}


# ---------- Helpers ----------


def result_for(trade):
    """The engine's TradeResult for a CLOSED trade; None for OPEN trades
    (they have no realised result) or data that cannot be evaluated."""
    if trade["status"] != "CLOSED":
        return None
    try:
        return evaluate_trade(trade)
    except IncompleteTradeError:
        return None


def with_results(trades):
    return [{"trade": t, "result": result_for(t)} for t in trades]


def plan_for(trade):
    """Planned risk and planned R:R from the engine's own functions. These
    describe the PLAN and are shown for open trades; they are not results."""
    args = (trade["direction"], trade["entry_price"], trade["stop_loss"])
    try:
        return {
            "initial_risk": initial_risk(*args, trade["position_size"], trade["contract_multiplier"]),
            "planned_rr": planned_rr(*args, trade["take_profit"]),
        }
    except DecimalException:
        # Out-of-range stored values (Phase 7): show "n/a" instead of a 500.
        return {"initial_risk": None, "planned_rr": None}


def get_trade_or_404(conn, trade_id):
    trade = trade_repo.get_trade(conn, trade_id, user_id=current_user_id())
    if trade is None:  # missing OR another user's: the same 404
        abort(404)
    return trade


def current_path():
    """This page's path and query, for 'return to where I was' links."""
    path = request.full_path
    return path[:-1] if path.endswith("?") else path


def form_context(conn, values, errors, **extra):
    instruments = list_instruments(conn, user_id=current_user_id())
    return {
        "values": values,
        "errors": errors,
        "labels": FIELD_LABELS,
        "instruments": instruments,
        "instrument_options": [(i["id"], f"{i['symbol']} · {i['name']}") for i in instruments],
        "strategies": trade_repo.list_strategies(conn, user_id=current_user_id()),
        "sessions": SESSIONS,
        **extra,
    }


def collect_errors(values, data, *, require_closed_at=True):
    """Every problem with a submitted form, without writing anything."""
    errors = {}
    try:
        validate_trade(data, require_closed_at=require_closed_at)
    except ValidationError as exc:
        errors.update(exc.errors)
    errors.update(form_errors_for_status(values))
    return errors


# ---------- Journal ----------


def read_filters(args):
    """Validate the journal's query string. Returns (filters, raw, errors).
    `raw` holds what the user typed, to refill the filter form."""
    raw = {name: args.get(name, "").strip() for name in FILTER_FIELDS}
    errors, filters = {}, {}

    if raw["instrument_id"]:
        if raw["instrument_id"].isdigit() and int(raw["instrument_id"]) > 0:
            filters["instrument_id"] = int(raw["instrument_id"])
        else:
            errors["instrument_id"] = "Choose an instrument from the list."
    if raw["status"]:
        if raw["status"].upper() in ("OPEN", "CLOSED"):
            filters["status"] = raw["status"].upper()
        else:
            errors["status"] = "Choose OPEN or CLOSED."
    for name in ("start", "end"):
        if raw[name]:
            try:
                filters[name] = parse_date(raw[name]).isoformat()
            except ValueError:
                errors[name] = "Use a date in YYYY-MM-DD form."
    if "start" in filters and "end" in filters:
        try:
            date_range_bounds(filters["start"], filters["end"])
        except ValidationError:
            errors["end"] = "The end date must be on or after the start date."
    if raw["strategy"]:
        filters["strategy"] = raw["strategy"]
    if raw["q"]:
        if len(raw["q"]) > trade_repo.MAX_SEARCH_LENGTH:
            errors["q"] = f"Search text must be {trade_repo.MAX_SEARCH_LENGTH} characters or fewer."
        else:
            filters["search"] = raw["q"]
    return filters, raw, errors


@main_bp.route("/trades")
def journal():
    conn = get_db()
    filters, raw, errors = read_filters(request.args)
    page_text = request.args.get("page", "1")
    page = int(page_text) if page_text.isdigit() and int(page_text) > 0 else 1
    per_page = current_app.config["JOURNAL_PER_PAGE"]

    rows, total = [], 0
    if not errors:
        total = trade_repo.count_trades(conn, user_id=current_user_id(), **filters)
        if total and page > (total + per_page - 1) // per_page:
            abort(404)  # e.g. an old bookmark to ?page=9 after deletions
        rows = trade_repo.list_trades(
            conn, user_id=current_user_id(), **filters, limit=per_page, offset=(page - 1) * per_page
        )

    active = {name: value for name, value in raw.items() if value}
    return render_template(
        "trades/journal.html",
        rows=with_results(rows),
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        raw=raw,
        active=active,
        errors=errors,
        instruments=list_instruments(conn, user_id=current_user_id()),
        strategies=trade_repo.list_strategies(conn, user_id=current_user_id()),
        here=current_path(),
    ), (400 if errors else 200)


# ---------- Add ----------


@main_bp.route("/trades/new", methods=["GET", "POST"])
def new_trade():
    conn = get_db()
    if request.method == "GET":
        values = dict(BLANK_TRADE_FORM)
        if request.args.get("instrument_id", "").isdigit():
            values["instrument_id"] = request.args["instrument_id"]
        return render_template("trades/form.html", **form_context(conn, values, {}, mode="new"))

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data)
    if not errors:
        try:
            trade_id = trade_repo.create_trade(conn, data, user_id=current_user_id())
        except ValidationError as exc:  # e.g. the instrument was deleted meanwhile
            errors = exc.errors
    if errors:
        return render_template("trades/form.html", **form_context(conn, values, errors, mode="new")), 400

    flash(f"Trade #{trade_id} saved.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Details ----------


@main_bp.route("/trades/<int:trade_id>")
def trade_detail(trade_id):
    trade = get_trade_or_404(get_db(), trade_id)
    return render_template(
        "trades/detail.html",
        trade=trade,
        result=result_for(trade),
        plan=plan_for(trade),
        here=current_path(),
    )


# ---------- Edit ----------


@main_bp.route("/trades/<int:trade_id>/edit", methods=["GET", "POST"])
def edit_trade(trade_id):
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    is_legacy = trade["status"] == "CLOSED" and trade["closed_at"] is None
    back = safe_next(request.values.get("next"), url_for("main.trade_detail", trade_id=trade_id))

    if request.method == "GET":
        return render_template(
            "trades/form.html",
            **form_context(
                conn, trade_to_form(trade), {}, mode="edit", trade=trade, is_legacy=is_legacy, back=back
            ),
        )

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data, require_closed_at=not is_legacy)
    if not errors:
        try:
            trade_repo.update_trade(conn, trade_id, data, user_id=current_user_id())
        except ValidationError as exc:
            errors = exc.errors
    if errors:
        return render_template(
            "trades/form.html",
            **form_context(conn, values, errors, mode="edit", trade=trade, is_legacy=is_legacy, back=back),
        ), 400

    flash(f"Trade #{trade_id} updated.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Delete ----------


@main_bp.route("/trades/<int:trade_id>/delete", methods=["GET", "POST"])
def delete_trade(trade_id):
    """GET shows a confirmation page and changes nothing. Only a POST with
    a valid CSRF token deletes."""
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    back = safe_next(request.values.get("next"), url_for("main.journal"))

    if request.method == "GET":
        return render_template("trades/confirm_delete.html", trade=trade, result=result_for(trade), back=back)

    trade_repo.delete_trade(conn, trade_id, user_id=current_user_id())
    flash(f"Trade #{trade_id} ({trade['symbol']} {trade['direction']}) was deleted.", "success")
    return redirect(back)

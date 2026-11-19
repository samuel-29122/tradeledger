"""Analytics endpoints: thin wrappers around the Phase 3 engine.

Every endpoint accepts the same optional filters, defined once in
report_filters.py and shared with the /analytics dashboard:
  instrument_id   only this instrument
  strategy        exact strategy label (Phase 6)
  session         ASIA, LONDON, NEW_YORK or OTHER (Phase 6)
  start, end      inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                  closed_at, or trade_date for legacy trades with no exit time

No formula lives here. Each handler calls build_report() and returns one
part of the PerformanceReport, so the API, the CLI report and the /analytics
dashboard always show the same numbers.
"""

from flask import jsonify

from ..db import get_db
from ..performance import build_report
from ..performance.formatting import to_jsonable
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..validation import ValidationError
from . import api_bp, api_user_id
from .http import query_args
from .serialise import groups_json, summary_json

FILTERS = FILTER_NAMES

NOTES = [
    "Decimal values are strings holding the exact value; null means not calculable.",
    "Rates (win_rate, loss_rate, breakeven_rate) are fractions from 0 to 1.",
    "R values are currency-neutral. Money is reported per quote currency and never summed across currencies.",
    "Open trades are excluded from realised metrics.",
    "Ordering and date filters use the realised time: closed_at, or trade_date for legacy trades "
    "with no recorded exit time (listed in unknown_close_time_trade_ids).",
]


def _report_and_meta():
    conn = get_db()
    user_id = api_user_id()
    filters, errors = parse_report_filters(
        query_args(FILTERS), conn, user_id=user_id, blank_means_unset=False
    )
    if errors:
        raise ValidationError(errors)
    report = build_report(conn, user_id=user_id, **filters.as_kwargs())
    return report, {"filters": filters.as_kwargs(), "notes": NOTES}


@api_bp.get("/analytics/summary")
def analytics_summary():
    report, meta = _report_and_meta()
    return jsonify(
        data={
            "overall": summary_json(report.overall),
            "open_trades": report.open_trades,
            "excluded": to_jsonable(report.excluded),
            "r_unavailable": to_jsonable(report.r_unavailable),
            "unknown_close_time_trade_ids": report.unknown_close_time_trade_ids,
        },
        meta=meta,
    )


def _grouped(attribute):
    report, meta = _report_and_meta()
    return jsonify(data=groups_json(getattr(report, attribute)), meta=meta)


@api_bp.get("/analytics/instruments")
def analytics_instruments():
    return _grouped("by_instrument")


@api_bp.get("/analytics/strategies")
def analytics_strategies():
    return _grouped("by_strategy")


@api_bp.get("/analytics/sessions")
def analytics_sessions():
    return _grouped("by_session")


@api_bp.get("/analytics/weekday")
def analytics_weekday():
    return _grouped("by_weekday")


@api_bp.get("/analytics/monthly")
def analytics_monthly():
    return _grouped("by_month")


@api_bp.get("/analytics/curve")
def analytics_curve():
    report, meta = _report_and_meta()
    meta = {**meta, "label": "Cumulative realised R. This is NOT an account equity curve."}
    return jsonify(data=to_jsonable(report.cumulative_r), meta=meta)

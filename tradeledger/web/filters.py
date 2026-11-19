"""Jinja filters: display formatting for exact values.

All rounding goes through performance/formatting.py (the Phase 3 rounding
policy), so a value looks the same in the CLI report, the website and any
future page. Templates never do arithmetic on money.

  {{ value | r }}                  +1.97R      (None -> n/a)
  {{ value | r_amount }}           1.05R       (a size, no sign)
  {{ value | money(currency) }}    393.00 USD
  {{ value | rate }}               50.0%
  {{ value | ratio }}              2.85
  {{ value | plain }}              1.085       (exact stored value)
  {{ ts | when }}                  02 Mar 2026, 08:15 UTC
  {{ ts | day }} / {{ ts | clock }} 02 Mar 2026 / 08:15 UTC (for compact tables)
  {{ ts | datetime_local }}        2026-03-02T08:15:00 (for <input type=datetime-local>)
"""

from datetime import UTC, datetime

from ..performance.formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from ..timestamps import parse_stored
from . import main_bp


def _parse(timestamp):
    try:
        return parse_stored(timestamp)
    except (TypeError, ValueError):
        return None


@main_bp.app_template_filter("r")
def r_filter(value):
    return format_r(value)


@main_bp.app_template_filter("r_amount")
def r_amount_filter(value):
    return format_r_amount(value)


@main_bp.app_template_filter("money")
def money_filter(value, currency):
    return format_money(value, currency)


@main_bp.app_template_filter("rate")
def rate_filter(value):
    return format_rate(value)


@main_bp.app_template_filter("ratio")
def ratio_filter(value):
    return format_ratio(value)


@main_bp.app_template_filter("plain")
def plain_filter(value):
    if value is None:
        return "—"
    return decimal_to_text(value)


@main_bp.app_template_filter("when")
def when_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return NOT_AVAILABLE if timestamp is None else timestamp
    text = moment.strftime("%d %b %Y, %H:%M")
    if moment.second:
        text += moment.strftime(":%S")
    return text + " UTC"


@main_bp.app_template_filter("when_epoch")
def when_epoch_filter(seconds):
    """Whole seconds since 1970 (UTC), as stored for sessions and reset
    links (Phase 8.5), shown like every other time: "16 Nov 2026, 09:30 UTC"."""
    if seconds is None:
        return NOT_AVAILABLE
    moment = datetime.fromtimestamp(int(seconds), UTC)
    return moment.strftime("%d %b %Y, %H:%M UTC")


@main_bp.app_template_filter("day")
def day_filter(timestamp):
    moment = _parse(timestamp)
    return moment.strftime("%d %b %Y") if moment else NOT_AVAILABLE


@main_bp.app_template_filter("clock")
def clock_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return ""
    return moment.strftime("%H:%M:%S UTC" if moment.second else "%H:%M UTC")


@main_bp.app_template_filter("datetime_local")
def datetime_local_filter(timestamp):
    """Stored '2026-03-02T08:15:00Z' -> '2026-03-02T08:15:00'. Seconds are
    kept so editing a trade never silently changes its time."""
    moment = _parse(timestamp)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if moment else (timestamp or "")

"""Filters for performance reports, shared by the dashboard and the API.

One definition of what each filter means and how it is validated, so the
website (/analytics) and the JSON API (/api/v1/analytics/...) can never
disagree about which trades a report covers.

  instrument_id  only this instrument (must exist: an unknown id would
                 otherwise produce an empty report that looks like "no
                 trades", which is misleading)
  strategy       exact strategy label, as stored on trades
  session        ASIA, LONDON, NEW_YORK or OTHER (case-insensitive input)
  start, end     inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                 closed_at, or trade_date for legacy trades with no
                 recorded exit time

parse_report_filters() never raises for bad input. It returns every
problem at once as {field: message}, so a form can show them all and the
API can turn them into one 400 response.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .repositories.instruments import get_instrument
from .validation import SESSIONS, parse_date

FILTER_NAMES = ("instrument_id", "strategy", "session", "start", "end")
STRATEGY_MAX_LENGTH = 50  # the same limit validation.py applies to trades


@dataclass(frozen=True)
class ReportFilters:
    instrument_id: int | None = None
    strategy: str | None = None
    session: str | None = None
    start: str | None = None  # 'YYYY-MM-DD'
    end: str | None = None  # 'YYYY-MM-DD'

    def as_kwargs(self) -> dict[str, object]:
        """Keyword arguments for build_report() and the repository."""
        return asdict(self)

    def active(self) -> dict[str, object]:
        """Only the filters that are set, e.g. for building URLs."""
        return {name: value for name, value in asdict(self).items() if value is not None}

    @property
    def is_empty(self) -> bool:
        return not self.active()


def parse_report_filters(
    args: Mapping[str, str | None], conn, *, user_id: int, blank_means_unset: bool
) -> tuple[ReportFilters, dict[str, str]]:
    """Validate raw query-string values.

    args               name -> raw text (missing names are simply unset)
    conn               database connection, to check the instrument exists
    user_id            the signed-in user: another user's instrument id is
                       reported exactly like an id that does not exist
    blank_means_unset  True for HTML forms, where "All" submits an empty
                       value. False for the API, where an empty value is
                       almost certainly a client bug and is rejected.

    Returns (filters, errors). When errors is not empty, filters holds only
    the values that were valid and must not be used to build a report.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    def raw(name):
        value = args.get(name)
        if value is None:
            return None
        if blank_means_unset and value.strip() == "":
            return None
        return value

    instrument_text = raw("instrument_id")
    if instrument_text is not None:
        text = instrument_text.strip()
        if not text.isdigit() or int(text) < 1:
            errors["instrument_id"] = "must be a whole number of 1 or more"
        elif get_instrument(conn, int(text), user_id=user_id) is None:
            errors["instrument_id"] = "does not match an existing instrument"
        else:
            values["instrument_id"] = int(text)

    strategy = raw("strategy")
    if strategy is not None:
        strategy = strategy.strip()
        if not strategy:
            errors["strategy"] = "must not be empty"
        elif len(strategy) > STRATEGY_MAX_LENGTH:
            errors["strategy"] = f"must be {STRATEGY_MAX_LENGTH} characters or fewer"
        else:
            values["strategy"] = strategy

    session = raw("session")
    if session is not None:
        session = session.strip().upper()
        if session not in SESSIONS:
            errors["session"] = f"must be one of: {', '.join(SESSIONS)}"
        else:
            values["session"] = session

    for name in ("start", "end"):
        text = raw(name)
        if text is None:
            continue
        try:
            values[name] = parse_date(text).isoformat()
        except ValueError as exc:
            errors[name] = str(exc)

    if "start" in values and "end" in values and values["start"] > values["end"]:
        errors["end"] = "must be on or after the start date"

    return ReportFilters(**values), errors

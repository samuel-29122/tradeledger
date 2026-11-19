"""Phase 5: the Add, Edit and Delete forms and the Instruments form.

Every test goes through the real HTTP layer (test client) with a valid
CSRF token, exactly like a browser, and then checks what was - or was not -
written to SQLite.
"""

import pytest

from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    csrf_token,
    fetch_trade,
    new_trade_id,
    open_trade_form,
    post_form,
)

# ---------- Add trade: success ----------


def test_create_closed_trade_redirects_to_details(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form())
    assert response.status_code == 302  # POST-Redirect-GET
    trade_id = new_trade_id(response)
    assert count_rows(app, "trades") == before + 1

    stored = fetch_trade(app, trade_id)
    assert stored["status"] == "CLOSED"
    assert stored["trade_date"] == "2026-04-01T08:15:00Z"  # normalised to UTC
    assert stored["closed_at"] == "2026-04-01T11:40:00Z"
    assert stored["entry_price"] == "1.085"  # canonical decimal text
    assert stored["strategy"] == "Breakout"
    assert stored["notes"] == "Website test trade (synthetic)"

    page = seeded_client.get(response.headers["Location"]).get_data(as_text=True)
    assert f"Trade #{trade_id} saved." in page  # flash message
    assert "393.00 USD" in page and "+1.97R" in page  # engine values


def test_create_open_trade_stores_no_exit(app, seeded_client):
    response = post_form(seeded_client, "/trades/new", open_trade_form())
    assert response.status_code == 302
    stored = fetch_trade(app, new_trade_id(response))
    assert stored["status"] == "OPEN"
    assert stored["exit_price"] is None
    assert stored["closed_at"] is None


def test_open_trade_ignores_stale_exit_values(app, seeded_client):
    """With JavaScript off, a user can type exit values and then choose
    OPEN. The server discards them instead of storing a half-closed trade."""
    form = closed_trade_form(status="OPEN")  # exit fields still filled in
    response = post_form(seeded_client, "/trades/new", form)
    assert response.status_code == 302
    stored = fetch_trade(app, new_trade_id(response))
    assert stored["exit_price"] is None and stored["closed_at"] is None


def test_seconds_in_timestamps_are_kept(app, seeded_client):
    form = closed_trade_form(trade_date="2026-04-01T08:15:30", closed_at="2026-04-01T11:40:05")
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["trade_date"] == "2026-04-01T08:15:30Z"
    assert stored["closed_at"] == "2026-04-01T11:40:05Z"


def test_blank_fees_mean_zero(app, seeded_client):
    stored = fetch_trade(
        app, new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(fees="")))
    )
    assert stored["fees"] == "0"


def test_optional_text_fields_may_be_blank(app, seeded_client):
    form = closed_trade_form(strategy="", notes="", session="", take_profit="")
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["strategy"] is None and stored["notes"] is None
    assert stored["session"] is None and stored["take_profit"] is None


def test_short_trade_with_stop_above_entry(app, seeded_client):
    form = closed_trade_form(
        direction="SHORT",
        entry_price="1.08500",
        stop_loss="1.08700",
        take_profit="1.08100",
        exit_price="1.08200",
    )
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["direction"] == "SHORT"


def test_instrument_can_be_preselected(seeded_client):
    html = seeded_client.get("/trades/new?instrument_id=3").get_data(as_text=True)
    assert '<option value="3" selected' in html


# ---------- Add trade: validation ----------


def _assert_rejected(app, response, before, *fields):
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'class="error-summary"' in html
    for field in fields:
        assert f'id="field-{field}"' in html
        assert f'href="#field-{field}"' in html  # summary links to the field
    assert count_rows(app, "trades") == before  # nothing written
    return html


@pytest.mark.parametrize(
    "field", ["instrument_id", "trade_date", "entry_price", "stop_loss", "position_size"]
)
def test_missing_required_field_is_rejected(app, seeded_client, field):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(**{field: ""}))
    _assert_rejected(app, response, before, field)


def test_closed_trade_needs_exit_price_and_exit_time(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(exit_price="", closed_at=""))
    _assert_rejected(app, response, before, "exit_price", "closed_at")


def test_stop_on_wrong_side_is_rejected(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(stop_loss="1.09000"))
    html = _assert_rejected(app, response, before, "stop_loss")
    assert 'aria-invalid="true"' in html


def test_exit_before_entry_is_rejected(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(closed_at="2026-04-01T08:00"))
    _assert_rejected(app, response, before, "closed_at")


@pytest.mark.parametrize(
    "field, value",
    [
        ("entry_price", "abc"),
        ("entry_price", "-1"),
        ("position_size", "0"),
        ("fees", "-5"),
        ("instrument_id", "999"),
        ("direction", "SIDEWAYS"),
        ("status", "MAYBE"),
        ("trade_date", "yesterday"),
        ("session", "MOON"),
    ],
)
def test_invalid_values_are_rejected(app, seeded_client, field, value):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(**{field: value}))
    assert response.status_code == 400
    assert count_rows(app, "trades") == before


def test_rejected_form_keeps_what_the_user_typed(seeded_client):
    form = closed_trade_form(stop_loss="1.09000", strategy="My <b>setup</b>", notes="Keep me")
    html = post_form(seeded_client, "/trades/new", form).get_data(as_text=True)
    assert 'value="1.09000"' in html
    assert 'value="1.08500"' in html
    assert "My &lt;b&gt;setup&lt;/b&gt;" in html  # kept, and escaped
    assert "Keep me</textarea>" in html


def test_rejected_form_keeps_the_chosen_radio_buttons(seeded_client):
    import re

    form = closed_trade_form(direction="SHORT", stop_loss="1.08000")  # wrong side for a short
    html = post_form(seeded_client, "/trades/new", form).get_data(as_text=True)
    short = re.search(r'<input[^>]*value="SHORT"[^>]*>', html).group(0)
    long_ = re.search(r'<input[^>]*value="LONG"[^>]*>', html).group(0)
    assert "checked" in short and "checked" not in long_


# ---------- Edit ----------


def test_edit_form_is_prefilled(seeded_client):
    html = seeded_client.get("/trades/1/edit").get_data(as_text=True)
    assert 'value="1.085"' in html
    assert 'value="2026-03-02T08:15:00"' in html  # datetime-local, seconds kept
    assert "Breakout" in html


def test_edit_persists_changes(app, seeded_client):
    form = closed_trade_form(exit_price="1.08700", notes="Edited")
    response = post_form(seeded_client, "/trades/1/edit", form)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/trades/1")
    stored = fetch_trade(app, 1)
    assert stored["exit_price"] == "1.087" and stored["notes"] == "Edited"
    page = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Trade #1 updated." in page


def test_edit_can_reopen_a_trade(app, seeded_client):
    post_form(seeded_client, "/trades/1/edit", open_trade_form())
    stored = fetch_trade(app, 1)
    assert stored["status"] == "OPEN" and stored["exit_price"] is None and stored["closed_at"] is None


def test_edit_can_close_an_open_trade(app, seeded_client):
    form = closed_trade_form(
        trade_date="2026-03-19T14:35",
        closed_at="2026-03-19T18:00",
        instrument_id="4",
        entry_price="18500",
        stop_loss="18440",
        take_profit="18620",
        exit_price="18600",
        fees="0.75",
    )
    response = post_form(seeded_client, "/trades/14/edit", form)
    assert response.status_code == 302
    assert fetch_trade(app, 14)["status"] == "CLOSED"


def test_invalid_edit_changes_nothing(app, seeded_client):
    before = fetch_trade(app, 1)
    response = post_form(seeded_client, "/trades/1/edit", closed_trade_form(stop_loss="1.09"))
    assert response.status_code == 400
    assert fetch_trade(app, 1) == before


def _make_legacy(app, trade_id=1):
    from tradeledger.db import get_db

    with app.app_context():
        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        db.commit()


def test_legacy_trade_can_be_edited_without_inventing_an_exit_time(app, seeded_client):
    _make_legacy(app)
    html = seeded_client.get("/trades/1/edit").get_data(as_text=True)
    assert "Exit time not recorded" in html
    assert "data-legacy-close" in html
    response = post_form(
        seeded_client, "/trades/1/edit", closed_trade_form(closed_at="", notes="Legacy edit")
    )
    assert response.status_code == 302
    stored = fetch_trade(app, 1)
    assert stored["closed_at"] is None  # still unknown, never invented
    assert stored["notes"] == "Legacy edit"


def test_legacy_trade_can_gain_a_real_exit_time(app, seeded_client):
    _make_legacy(app)
    post_form(seeded_client, "/trades/1/edit", closed_trade_form(closed_at="2026-04-01T12:00"))
    assert fetch_trade(app, 1)["closed_at"] == "2026-04-01T12:00:00Z"


def test_edit_of_missing_trade_is_404(seeded_client):
    response = post_form(seeded_client, "/trades/999/edit", closed_trade_form())
    assert response.status_code == 404


# ---------- Delete ----------


def test_get_delete_shows_confirmation_and_deletes_nothing(app, seeded_client):
    before = count_rows(app, "trades")
    response = seeded_client.get("/trades/1/delete")
    assert response.status_code == 200
    assert b"Delete trade #1?" in response.data
    assert b'method="post"' in response.data
    assert count_rows(app, "trades") == before


def test_post_delete_removes_trade_and_returns_to_journal(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/1/delete", {})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/trades")
    assert count_rows(app, "trades") == before - 1
    assert fetch_trade(app, 1) is None
    page = seeded_client.get("/trades").get_data(as_text=True)
    assert "Trade #1 (EURUSD LONG) was deleted." in page


def test_delete_returns_to_a_safe_next_page(seeded_client):
    response = post_form(seeded_client, "/trades/2/delete", {"next": "/trades?status=CLOSED"})
    assert response.headers["Location"].endswith("/trades?status=CLOSED")


def test_delete_missing_trade_is_404(seeded_client):
    assert post_form(seeded_client, "/trades/999/delete", {}).status_code == 404


def test_deleting_twice_is_404_not_an_error(seeded_client):
    post_form(seeded_client, "/trades/3/delete", {})
    assert post_form(seeded_client, "/trades/3/delete", {}).status_code == 404


# ---------- Instruments ----------

INSTRUMENT = {
    "symbol": "USDJPY",
    "name": "US Dollar / Yen (test)",
    "asset_class": "FOREX",
    "price_increment": "0.001",
    "contract_multiplier": "100000",
    "quote_currency": "JPY",
}


def test_add_instrument(app, seeded_client):
    before = count_rows(app, "instruments")
    response = post_form(seeded_client, "/instruments", INSTRUMENT, token_page="/instruments")
    assert response.status_code == 302
    assert count_rows(app, "instruments") == before + 1
    page = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Instrument USDJPY added." in page
    assert "US Dollar / Yen (test)" in page


def test_new_instrument_is_offered_on_the_trade_form(seeded_client):
    post_form(seeded_client, "/instruments", INSTRUMENT, token_page="/instruments")
    assert "USDJPY" in seeded_client.get("/trades/new").get_data(as_text=True)


def test_duplicate_symbol_is_rejected_with_explanation(app, seeded_client):
    before = count_rows(app, "instruments")
    response = post_form(
        seeded_client, "/instruments", {**INSTRUMENT, "symbol": "EURUSD"}, token_page="/instruments"
    )
    assert response.status_code == 400
    assert b"already recorded" in response.data
    assert count_rows(app, "instruments") == before


@pytest.mark.parametrize(
    "field, value",
    [
        ("symbol", ""),
        ("contract_multiplier", "0"),
        ("price_increment", "-1"),
        ("quote_currency", "dollars"),
        ("asset_class", "BEANS"),
    ],
)
def test_invalid_instrument_is_rejected_and_values_kept(app, seeded_client, field, value):
    before = count_rows(app, "instruments")
    response = post_form(
        seeded_client, "/instruments", {**INSTRUMENT, field: value}, token_page="/instruments"
    )
    assert response.status_code == 400
    assert count_rows(app, "instruments") == before
    assert b"US Dollar / Yen (test)" in response.data  # typed values kept


def test_token_is_the_same_across_pages_in_one_session(seeded_client):
    assert csrf_token(seeded_client, "/trades/new") == csrf_token(seeded_client, "/instruments")

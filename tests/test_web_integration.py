"""Phase 5 end to end (inside the test client): form -> SQLite -> journal ->
details -> analytics -> API, plus the repository search added for the
journal."""

import pytest

from tests.factories import USER_ID
from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    new_trade_id,
    open_trade_form,
    post_form,
)
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

# ---------- Full journeys ----------


def test_trade_lifecycle_open_close_edit_delete(app, client):
    """Start from an EMPTY database, as a new user would."""
    # 1. Add an instrument through the website.
    response = post_form(
        client,
        "/instruments",
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    assert response.status_code == 302

    # 2. Record an open trade.
    trade_id = new_trade_id(post_form(client, "/trades/new", open_trade_form()))
    journal = client.get("/trades").get_data(as_text=True)
    assert "Showing 1–1 of 1 trade." in journal
    assert ">Open</span>" in journal
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in overview  # open trades have no result

    # 3. Close it via the edit form.
    response = post_form(client, f"/trades/{trade_id}/edit", closed_trade_form())
    assert response.status_code == 302
    detail = client.get(f"/trades/{trade_id}").get_data(as_text=True)
    assert "393.00 USD" in detail and "+1.97R" in detail and ">Win</span>" in detail

    # 4. The overview, analytics preview and API all agree.
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" not in overview
    assert "393.00 USD" in overview
    analytics = client.get("/analytics").get_data(as_text=True)
    assert "EURUSD" in analytics and "100.0%" in analytics
    api = client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert api["trades"] == 1 and api["money"]["USD"]["net_pnl"] == "393"

    # 5. Delete it; everything returns to empty.
    post_form(client, f"/trades/{trade_id}/delete", {})
    assert count_rows(app, "trades") == 0
    assert "Your journal is empty" in client.get("/").get_data(as_text=True)
    assert client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]["trades"] == 0


def test_website_trade_matches_api_trade_exactly(seeded_client, trade_body):
    """The same trade entered through the form and through the API is stored
    identically: one validation layer, one storage format."""
    web_id = new_trade_id(
        post_form(
            seeded_client,
            "/trades/new",
            closed_trade_form(
                trade_date="2026-04-01T08:15", closed_at="2026-04-01T11:40", notes=trade_body["notes"]
            ),
        )
    )
    api_id = seeded_client.post("/api/v1/trades", json=trade_body).get_json()["data"]["id"]

    web = seeded_client.get(f"/api/v1/trades/{web_id}").get_json()["data"]
    api = seeded_client.get(f"/api/v1/trades/{api_id}").get_json()["data"]
    for volatile in ("id", "created_at", "updated_at"):
        web.pop(volatile, None)
        api.pop(volatile, None)
    assert web == api


def test_trade_added_by_api_appears_on_the_website(seeded_client, trade_body):
    trade_id = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "from-the-api"}).get_json()[
        "data"
    ]["id"]
    assert f'href="/trades/{trade_id}"' in seeded_client.get("/trades?q=from-the-api").get_data(as_text=True)


def test_website_edits_change_the_analytics(seeded_client):
    before = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    post_form(seeded_client, "/trades/1/delete", {})
    after = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert after["trades"] == before["trades"] - 1
    assert after["wins"] == before["wins"] - 1  # trade 1 was a win


def test_detail_values_match_the_api(seeded_client):
    api = seeded_client.get("/api/v1/trades/1").get_json()["data"]
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert api["result"]["net_pnl"] == "393"
    assert "393.00 USD" in html


def test_journal_and_api_list_the_same_trades(seeded_client):
    api_ids = [t["id"] for t in seeded_client.get("/api/v1/trades?per_page=100").get_json()["data"]]
    html = seeded_client.get("/trades").get_data(as_text=True)
    for trade_id in api_ids:
        assert f'href="/trades/{trade_id}"' in html


# ---------- Repository: search and strategy filters ----------


def test_search_matches_symbol_case_insensitively(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, search="xau", user_id=USER_ID)
    assert len(rows) == 4 and {r["symbol"] for r in rows} == {"XAUUSD"}
    assert trade_repo.count_trades(seeded_conn, search="xau", user_id=USER_ID) == 4


def test_search_matches_strategy_and_notes(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="pull", user_id=USER_ID) == 5
    assert trade_repo.count_trades(seeded_conn, search="fees", user_id=USER_ID) == 1


@pytest.mark.parametrize("wildcard", ["%", "_", "\\"])
def test_search_wildcards_are_literal(seeded_conn, wildcard):
    """% and _ are LIKE wildcards. They are escaped, so searching for
    them looks for the character itself, which no sample trade contains."""
    assert trade_repo.count_trades(seeded_conn, search=wildcard, user_id=USER_ID) == 0


def test_search_injection_attempt_is_plain_text(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="' OR 1=1 --", user_id=USER_ID) == 0
    assert trade_repo.count_trades(seeded_conn, user_id=USER_ID) == 16


def test_search_combines_with_other_filters_and_pagination(seeded_conn):
    closed = trade_repo.count_trades(seeded_conn, search="usd", status="CLOSED", user_id=USER_ID)
    page_1 = trade_repo.list_trades(
        seeded_conn, search="usd", status="CLOSED", limit=3, offset=0, user_id=USER_ID
    )
    page_2 = trade_repo.list_trades(
        seeded_conn, search="usd", status="CLOSED", limit=3, offset=3, user_id=USER_ID
    )
    assert len(page_1) == 3
    assert not {r["id"] for r in page_1} & {r["id"] for r in page_2}
    assert all(r["status"] == "CLOSED" for r in page_1 + page_2)
    assert closed >= 6


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101])
def test_search_rejects_empty_or_overlong_text(seeded_conn, bad):
    with pytest.raises(ValidationError) as excinfo:
        trade_repo.list_trades(seeded_conn, search=bad, user_id=USER_ID)
    assert "q" in excinfo.value.errors


def test_strategy_filter_is_exact(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, strategy="Breakout", user_id=USER_ID) == 6
    assert trade_repo.count_trades(seeded_conn, strategy="break", user_id=USER_ID) == 0


def test_list_strategies_is_sorted_and_distinct(seeded_conn):
    assert trade_repo.list_strategies(seeded_conn, user_id=USER_ID) == ["Breakout", "Pullback", "Reversal"]

"""Phase 8: adversarial two-user isolation tests.

Alice (user 1) and Bob (user 2) both hold the full sample data, so both
have instruments called EURUSD, XAUUSD, ... with DIFFERENT ids. Alice also
has one trade carrying a private note and a private strategy name.

Bob then tries everything a curious or malicious user could: guessing ids
in URLs, filters and API calls, paging, searching, pointing his trades at
Alice's instruments, and smuggling an owner field into a request. Every
attempt must fail, and must fail with the same answer a non-existent
record gets, so Bob cannot even learn which ids exist.
"""

import pytest

from tests.conftest import OTHER_EMAIL, ensure_user, issue_token, sign_in
from tests.web_helpers import closed_trade_form, count_rows, csrf_token, fetch_trade
from tradeledger import analytics
from tradeledger.db import get_db
from tradeledger.performance import build_report
from tradeledger.repositories import instruments as instrument_repo
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

ALICE, BOB = 1, 2
SECRET_NOTE = "ALICE-PRIVATE-NOTE-7731"
SECRET_STRATEGY = "AlicePrivateEdge"
MISSING = 999_999


@pytest.fixture
def world(app):
    """Both accounts seeded; returns ids of each user's records."""
    with app.app_context():
        conn = get_db()
        assert ensure_user(conn, OTHER_EMAIL) == BOB
        seed_sample_data(conn, user_id=ALICE)
        seed_sample_data(conn, user_id=BOB)
        alice_trade = trade_repo.list_trades(conn, user_id=ALICE, status="CLOSED")[0]
        data = {k: alice_trade[k] for k in trade_repo._WRITE_FIELDS}
        data.update(notes=SECRET_NOTE, strategy=SECRET_STRATEGY)
        for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
            data[key] = None if data[key] is None else str(data[key])
        trade_repo.update_trade(conn, alice_trade["id"], data, user_id=ALICE)
        ids = {
            "alice_trades": [t["id"] for t in trade_repo.list_trades(conn, user_id=ALICE)],
            "bob_trades": [t["id"] for t in trade_repo.list_trades(conn, user_id=BOB)],
            "alice_instruments": [i["id"] for i in instrument_repo.list_instruments(conn, user_id=ALICE)],
            "bob_instruments": [i["id"] for i in instrument_repo.list_instruments(conn, user_id=BOB)],
            "alice_secret_trade": alice_trade["id"],
        }
    assert set(ids["alice_trades"]).isdisjoint(ids["bob_trades"])
    assert len(ids["alice_trades"]) == len(ids["bob_trades"]) == 16
    return ids


@pytest.fixture
def bob(app):
    """Bob's browser, signed in, with his own API token."""
    client = app.test_client()
    sign_in(client, user_id=BOB)
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {issue_token(app, user_id=BOB, name='bob')}"
    return client


def strip_ids(text, *ids):
    for value in ids:
        text = text.replace(str(value), "<id>")
    return text


# ---------- Website: guessed ids ----------


@pytest.mark.parametrize("suffix", ["", "/edit", "/delete"])
def test_bob_cannot_open_alices_trade_pages(world, bob, suffix):
    for trade_id in world["alice_trades"]:
        assert bob.get(f"/trades/{trade_id}{suffix}").status_code == 404


def test_foreign_and_missing_trades_look_identical(world, bob):
    foreign = world["alice_trades"][0]
    page_foreign = bob.get(f"/trades/{foreign}").get_data(as_text=True)
    page_missing = bob.get(f"/trades/{MISSING}").get_data(as_text=True)
    assert strip_ids(page_foreign, foreign) == strip_ids(page_missing, MISSING)


def test_bob_cannot_edit_alices_trade(app, world, bob):
    target = world["alice_secret_trade"]
    before = fetch_trade(app, target)
    token = csrf_token(bob, "/trades/new")
    response = bob.post(
        f"/trades/{target}/edit", data={"csrf_token": token, **closed_trade_form(notes="bob was here")}
    )
    assert response.status_code == 404
    assert fetch_trade(app, target) == before


def test_bob_cannot_delete_alices_trade(app, world, bob):
    target = world["alice_trades"][3]
    response = bob.post(f"/trades/{target}/delete", data={"csrf_token": csrf_token(bob, "/trades/new")})
    assert response.status_code == 404
    assert fetch_trade(app, target) is not None


# ---------- Website: lists, search, filters, analytics ----------


def test_journal_lists_only_bobs_trades(world, bob):
    html = bob.get("/trades?per_page=100").get_data(as_text=True)
    assert "of 16 trades" in html
    for trade_id in world["alice_trades"]:
        assert f'href="/trades/{trade_id}"' not in html


def test_search_never_reaches_alices_notes_or_strategies(world, bob):
    for term in (SECRET_NOTE, SECRET_STRATEGY, "ALICE-PRIVATE"):
        html = bob.get(f"/trades?q={term}").get_data(as_text=True)
        assert "No trades match" in html  # (the search box itself echoes the term Bob typed)
        assert f'href="/trades/{world["alice_secret_trade"]}"' not in html
    # Nor do Alice's strategy names appear in Bob's filter choices.
    for url in ("/trades", "/trades/new", "/analytics"):
        assert SECRET_STRATEGY not in bob.get(url).get_data(as_text=True)


def test_bob_cannot_filter_by_alices_instrument(world, bob):
    alice_instrument = world["alice_instruments"][0]
    journal = bob.get(f"/trades?instrument_id={alice_instrument}")
    assert f'href="/trades/{world["alice_trades"][0]}"' not in journal.get_data(as_text=True)
    dashboard = bob.get(f"/analytics?instrument_id={alice_instrument}")
    missing = bob.get(f"/analytics?instrument_id={MISSING}")
    assert dashboard.status_code == missing.status_code == 400


def test_overview_and_dashboard_count_only_bobs_trades(app, world, bob):
    # Bob deletes 2 of his own trades; Alice's 16 must not appear in his totals.
    for trade_id in world["bob_trades"][:2]:
        bob.post(f"/trades/{trade_id}/delete", data={"csrf_token": csrf_token(bob, "/trades/new")})
    with app.app_context():
        expected = build_report(get_db(), user_id=BOB).overall.trades
        assert build_report(get_db(), user_id=ALICE).overall.trades == 14
    assert expected < 14
    html = bob.get("/analytics").get_data(as_text=True)
    assert 'data-kpi="trades"' in html and f">{expected}<" in html.split('data-kpi="trades"')[1][:300]


def test_bob_cannot_attach_a_trade_to_alices_instrument(app, world, bob):
    before = count_rows(app, "trades")
    form = closed_trade_form(instrument_id=str(world["alice_instruments"][0]))
    response = bob.post("/trades/new", data={"csrf_token": csrf_token(bob, "/trades/new"), **form})
    assert response.status_code == 400
    assert "does not match an existing instrument" in response.get_data(as_text=True)
    assert count_rows(app, "trades") == before


def test_bob_cannot_move_his_trade_onto_alices_instrument(app, world, bob):
    own = world["bob_trades"][0]
    before = fetch_trade(app, own)
    form = closed_trade_form(instrument_id=str(world["alice_instruments"][0]))
    response = bob.post(f"/trades/{own}/edit", data={"csrf_token": csrf_token(bob, "/trades/new"), **form})
    assert response.status_code == 400
    assert fetch_trade(app, own) == before


def test_trade_form_offers_only_bobs_instruments(world, bob):
    html = bob.get("/trades/new").get_data(as_text=True)
    for instrument_id in world["alice_instruments"]:
        assert f'<option value="{instrument_id}"' not in html
    for instrument_id in world["bob_instruments"]:
        assert f'<option value="{instrument_id}"' in html


def test_the_same_symbol_is_allowed_in_both_accounts_but_not_twice_in_one(app, world, bob):
    response = bob.post(
        "/instruments",
        data={
            "csrf_token": csrf_token(bob, "/instruments"),
            "symbol": "EURUSD",
            "name": "dup",
            "asset_class": "FOREX",
            "quote_currency": "USD",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
        },
    )
    assert response.status_code == 409 or "already recorded" in response.get_data(as_text=True)
    with app.app_context():
        rows = (
            get_db()
            .execute("SELECT user_id FROM instruments WHERE symbol = 'EURUSD' ORDER BY user_id")
            .fetchall()
        )
    assert [r["user_id"] for r in rows] == [ALICE, BOB]


# ---------- JSON API ----------


def test_api_guessed_trade_ids_get_404(world, bob):
    for trade_id in world["alice_trades"]:
        response = bob.get(f"/api/v1/trades/{trade_id}")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


def test_api_foreign_and_missing_answers_are_identical(world, bob):
    foreign = world["alice_trades"][0]
    a = bob.get(f"/api/v1/trades/{foreign}").get_json()
    b = bob.get(f"/api/v1/trades/{MISSING}").get_json()
    assert strip_ids(str(a), foreign) == strip_ids(str(b), MISSING)


def test_api_cannot_replace_or_delete_alices_trade(app, world, bob, trade_body):
    target = world["alice_secret_trade"]
    before = fetch_trade(app, target)
    body = {**trade_body, "instrument_id": world["bob_instruments"][0]}
    assert bob.put(f"/api/v1/trades/{target}", json=body).status_code == 404
    assert bob.delete(f"/api/v1/trades/{target}").status_code == 404
    assert fetch_trade(app, target) == before


def test_api_paging_through_everything_returns_only_bobs_trades(world, bob):
    seen, page = [], 1
    while True:
        payload = bob.get(f"/api/v1/trades?per_page=5&page={page}").get_json()
        seen += [t["id"] for t in payload["data"]]
        if page >= payload["pagination"]["total_pages"]:
            break
        page += 1
    assert payload["pagination"]["total"] == 16
    assert sorted(seen) == sorted(world["bob_trades"])


def test_api_search_and_filters_stay_inside_the_account(world, bob):
    # (The trade list has no text search; the website's search is tested above.)
    groups = bob.get("/api/v1/analytics/strategies").get_json()["data"]
    assert SECRET_STRATEGY not in {g["group"] for g in groups}
    filtered = bob.get(f"/api/v1/analytics/summary?strategy={SECRET_STRATEGY}").get_json()["data"]
    assert filtered["overall"]["trades"] == 0
    response = bob.get(f"/api/v1/trades?instrument_id={world['alice_instruments'][0]}")
    assert response.status_code == 400  # someone else's instrument: same as an unknown one


def test_api_instruments_are_private(world, bob):
    listed = [i["id"] for i in bob.get("/api/v1/instruments").get_json()["data"]]
    assert sorted(listed) == sorted(world["bob_instruments"])
    for instrument_id in world["alice_instruments"]:
        assert bob.get(f"/api/v1/instruments/{instrument_id}").status_code == 404


def test_api_create_on_alices_instrument_is_refused(app, world, bob, trade_body):
    before = count_rows(app, "trades")
    response = bob.post("/api/v1/trades", json={**trade_body, "instrument_id": world["alice_instruments"][0]})
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["instrument_id"] == "does not match an existing instrument"
    assert count_rows(app, "trades") == before


@pytest.mark.parametrize("field", ["user_id", "owner", "owner_id"])
def test_api_owner_cannot_be_set_through_the_body(app, world, bob, trade_body, field):
    body = {**trade_body, "instrument_id": world["bob_instruments"][0], field: ALICE}
    before = count_rows(app, "trades")
    response = bob.post("/api/v1/trades", json=body)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_fields"
    assert count_rows(app, "trades") == before
    replace = bob.put(f"/api/v1/trades/{world['bob_trades'][0]}", json=body)
    assert replace.status_code == 400


def test_api_owner_cannot_be_set_on_instruments(world, bob):
    body = {
        "symbol": "NEWONE",
        "name": "x",
        "asset_class": "OTHER",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
        "user_id": ALICE,
    }
    assert bob.post("/api/v1/instruments", json=body).status_code == 400


def test_api_analytics_use_only_bobs_trades(app, world, bob):
    # Make Bob's figures differ from Alice's, then compare with Bob-only reports.
    for trade_id in world["bob_trades"][:5]:
        bob.delete(f"/api/v1/trades/{trade_id}")
    summary = bob.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    with app.app_context():
        mine = build_report(get_db(), user_id=BOB).overall
        alices = build_report(get_db(), user_id=ALICE).overall
    assert summary["trades"] == mine.trades != alices.trades
    assert summary["total_r"] == str(mine.total_r)
    curve = bob.get("/api/v1/analytics/curve").get_json()["data"]
    assert len(curve) == mine.trades


def test_alice_still_sees_everything_of_hers(world, client):
    """The isolation is not achieved by hiding data from everyone."""
    assert client.get("/api/v1/trades?per_page=100").get_json()["pagination"]["total"] == 16
    secret = client.get(f"/api/v1/trades/{world['alice_secret_trade']}").get_json()["data"]
    assert secret["notes"] == SECRET_NOTE


# ---------- Repository layer ----------


@pytest.mark.parametrize("bad", [None, 0, -1, "1", True, 1.0])
def test_repositories_refuse_anything_but_a_real_user_id(seeded_conn, bad):
    with pytest.raises(TypeError, match="user_id must be a positive int"):
        trade_repo.list_trades(seeded_conn, user_id=bad)
    with pytest.raises(TypeError):
        instrument_repo.list_instruments(seeded_conn, user_id=bad)
    with pytest.raises(TypeError):
        build_report(seeded_conn, user_id=bad)


def test_forgetting_the_owner_is_an_error_not_a_query_of_everything(seeded_conn):
    with pytest.raises(TypeError):
        trade_repo.list_trades(seeded_conn)
    with pytest.raises(TypeError):
        trade_repo.get_trade(seeded_conn, 1)
    with pytest.raises(TypeError):
        trade_repo.count_trades(seeded_conn)


def test_repository_returns_nothing_for_another_owner(app, world):
    with app.app_context():
        conn = get_db()
        target = world["alice_trades"][0]
        assert trade_repo.get_trade(conn, target, user_id=BOB) is None
        assert trade_repo.delete_trade(conn, target, user_id=BOB) is False
        assert trade_repo.get_trade(conn, target, user_id=ALICE) is not None
        assert instrument_repo.get_instrument(conn, world["alice_instruments"][0], user_id=BOB) is None
        assert (
            instrument_repo.get_instrument_by_symbol(conn, "EURUSD", user_id=BOB)["id"]
            in world["bob_instruments"]
        )
        assert SECRET_STRATEGY not in trade_repo.list_strategies(conn, user_id=BOB)
        assert SECRET_STRATEGY in trade_repo.list_strategies(conn, user_id=ALICE)


def test_sql_analytics_are_per_user(app, world):
    with app.app_context():
        conn = get_db()
        trade_repo.delete_trade(conn, world["bob_trades"][0], user_id=BOB)
        assert analytics.closed_trade_count(conn, user_id=ALICE) == 14
        assert analytics.closed_trade_count(conn, user_id=BOB) in (13, 14)
        strategies = {row["strategy"] for row in analytics.trades_by_strategy(conn, user_id=BOB)}
        assert SECRET_STRATEGY not in strategies

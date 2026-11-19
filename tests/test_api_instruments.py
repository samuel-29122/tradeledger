"""Integration tests for /api/v1/instruments."""

import pytest

from tests.factories import table_snapshot

URL = "/api/v1/instruments"

US30 = {
    "symbol": "US30",
    "name": "Wall Street 30 CFD (example values)",
    "asset_class": "INDEX",
    "price_increment": "1",
    "contract_multiplier": "1",
    "quote_currency": "USD",
}


def test_list_instruments_sorted_by_symbol(seeded_client):
    data = seeded_client.get(URL).get_json()["data"]
    assert [i["symbol"] for i in data] == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    eurusd = data[0]
    assert set(eurusd) == {
        "id",
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
        "created_at",
    }
    assert (eurusd["price_increment"], eurusd["contract_multiplier"]) == ("0.00001", "100000")


def test_list_on_empty_database(client):
    assert client.get(URL).get_json() == {"data": []}


def test_list_rejects_query_parameters(seeded_client):
    assert seeded_client.get(f"{URL}?page=2").status_code == 400


def test_get_instrument(seeded_client):
    data = seeded_client.get(f"{URL}/3").get_json()["data"]
    assert (data["symbol"], data["asset_class"], data["quote_currency"]) == ("XAUUSD", "COMMODITY", "USD")


@pytest.mark.parametrize("path", ["999", "0", "abc"])
def test_missing_instrument_is_404(seeded_client, path):
    response = seeded_client.get(f"{URL}/{path}")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_create_instrument(seeded_client):
    response = seeded_client.post(URL, json={**US30, "symbol": "us30", "asset_class": "index"})
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert response.headers["Location"] == f"{URL}/{data['id']}"
    assert (data["symbol"], data["asset_class"]) == ("US30", "INDEX")
    assert seeded_client.get(f"{URL}/{data['id']}").get_json()["data"] == data


@pytest.mark.parametrize("symbol", ["XAUUSD", "xauusd", " XAUUSD "])
def test_duplicate_symbol_is_409_and_writes_nothing(app, seeded_client, symbol):
    before = table_snapshot(app, "instruments")
    response = seeded_client.post(URL, json={**US30, "symbol": symbol})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "conflict"
    assert table_snapshot(app, "instruments") == before


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"symbol": ""}, "symbol"),
        ({"symbol": "US 30"}, "symbol"),
        ({"asset_class": "CRYPTO"}, "asset_class"),
        ({"contract_multiplier": "0"}, "contract_multiplier"),
        ({"price_increment": "-0.1"}, "price_increment"),
        ({"quote_currency": "DOLLARS"}, "quote_currency"),
        ({"name": "x" * 101}, "name"),
    ],
)
def test_invalid_instrument_is_400(app, seeded_client, changes, field):
    before = table_snapshot(app, "instruments")
    response = seeded_client.post(URL, json={**US30, **changes})
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]
    assert table_snapshot(app, "instruments") == before


def test_missing_instrument_fields_are_all_reported(seeded_client):
    details = seeded_client.post(URL, json={}).get_json()["error"]["details"]
    assert set(details) == {
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
    }


def test_unknown_instrument_field_is_rejected(seeded_client):
    response = seeded_client.post(URL, json={**US30, "id": 99})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_fields"


def test_create_instrument_requires_json(seeded_client):
    assert seeded_client.post(URL, data="symbol=US30").status_code == 415


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
def test_instruments_cannot_be_changed_or_deleted_yet(seeded_client, method):
    assert getattr(seeded_client, method)(f"{URL}/1", json=US30).status_code == 405

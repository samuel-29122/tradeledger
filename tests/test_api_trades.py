"""Integration tests for /api/v1/trades through Flask's test client.

Each test gets its own temporary database (see conftest.py). seeded_client
holds the 16 synthetic sample trades: 14 CLOSED, 2 OPEN, on 5 instruments
(ids 1-5: EURUSD, GBPUSD, XAUUSD, NAS100, UK100).
"""

import json

import pytest

from tests.factories import table_snapshot

URL = "/api/v1/trades"


def post(client, body, url=URL):
    return client.post(url, json=body)


def put(client, trade_id, body):
    return client.put(f"{URL}/{trade_id}", json=body)


def full(body):
    """A complete PUT body: every accepted field present."""
    return {
        "take_profit": None,
        "fees": None,
        "strategy": None,
        "session": None,
        "notes": None,
        "closed_at": None,
        "status": None,
        **body,
    }


# ---------- Create ----------


def test_create_trade_returns_201_location_and_exact_values(seeded_client, trade_body):
    response = post(seeded_client, trade_body)
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert response.headers["Location"] == f"{URL}/{data['id']}"
    assert data["symbol"] == "EURUSD"
    assert data["entry_price"] == "1.085"  # canonical exact string
    assert data["closed_at"] == "2026-04-01T11:40:00Z"
    assert data["result"] == {
        "outcome": "WIN",
        "gross_pnl": "400",
        "fees": "7",
        "net_pnl": "393",
        "initial_risk": "200",
        "realised_r": "1.965",
        "planned_rr": "2",
        "r_unavailable_reason": None,
    }
    assert seeded_client.get(f"{URL}/{data['id']}").status_code == 200


def test_json_numbers_are_read_exactly_not_as_floats(seeded_client, trade_body):
    body = dict(trade_body, entry_price=1.08500, stop_loss=1.083, exit_price=1.089, fees=7)
    # Send the raw text so the test controls exactly what the JSON says.
    text = json.dumps(body).replace("1.085", "1.08500")
    response = seeded_client.post(URL, data=text, content_type="application/json")
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert (data["entry_price"], data["result"]["realised_r"]) == ("1.085", "1.965")


def test_create_open_trade_has_null_result(seeded_client, trade_body):
    body = {k: v for k, v in trade_body.items() if k not in ("exit_price", "closed_at", "status")}
    data = post(seeded_client, body).get_json()["data"]
    assert data["status"] == "OPEN"
    assert data["exit_price"] is None and data["closed_at"] is None
    assert data["result"] is None


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"position_size": "0"}, "position_size"),
        ({"position_size": "-1"}, "position_size"),
        ({"entry_price": "abc"}, "entry_price"),
        ({"direction": "SIDEWAYS"}, "direction"),
        ({"status": "MAYBE"}, "status"),
        ({"stop_loss": "1.09000"}, "stop_loss"),  # long stop above entry
        ({"exit_price": None}, "exit_price"),  # CLOSED without exit
        ({"closed_at": None}, "closed_at"),  # CLOSED without exit time
        ({"closed_at": "2026-04-01T08:00:00Z"}, "closed_at"),  # exit before entry
        ({"fees": "-1"}, "fees"),
        ({"trade_date": "01/04/2026"}, "trade_date"),
        ({"instrument_id": 999}, "instrument_id"),  # does not exist
        ({"instrument_id": "one"}, "instrument_id"),
        ({"session": "SYDNEY"}, "session"),
    ],
)
def test_invalid_trade_is_rejected_and_nothing_is_written(app, seeded_client, trade_body, changes, field):
    before = table_snapshot(app, "trades")
    response = post(seeded_client, {**trade_body, **changes})
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "validation_error"
    assert field in error["details"]
    assert table_snapshot(app, "trades") == before


def test_all_problems_are_reported_together(seeded_client, trade_body):
    response = post(seeded_client, {**trade_body, "position_size": "0", "fees": "-1"})
    assert set(response.get_json()["error"]["details"]) == {"position_size", "fees"}


@pytest.mark.parametrize("field", ["id", "created_at", "updated_at", "symbol", "quote_currency"])
def test_mass_assignment_of_protected_fields_is_rejected(app, seeded_client, trade_body, field):
    before = table_snapshot(app, "trades")
    response = post(seeded_client, {**trade_body, field: "1"})
    assert response.status_code == 400
    assert response.get_json()["error"] == {
        "code": "unknown_fields",
        "message": "The request contains fields that cannot be set.",
        "details": {field: "is not an accepted field"},
    }
    assert table_snapshot(app, "trades") == before


@pytest.mark.parametrize(
    "text, code",
    [
        ("{not json", "malformed_json"),
        ("[1, 2, 3]", "malformed_json"),
        ('"just a string"', "malformed_json"),
        ('{"entry_price": NaN}', "malformed_json"),
        ('{"entry_price": Infinity}', "malformed_json"),
        ("", "malformed_json"),
    ],
)
def test_malformed_json_is_rejected(seeded_client, text, code):
    response = seeded_client.post(URL, data=text, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == code


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", None])
def test_non_json_content_type_is_415(seeded_client, trade_body, content_type):
    kwargs = {"data": json.dumps(trade_body)}
    if content_type:
        kwargs["content_type"] = content_type
    response = seeded_client.post(URL, **kwargs)
    assert response.status_code == 415
    assert response.get_json()["error"]["code"] == "unsupported_media_type"


def test_json_content_type_with_charset_is_accepted(seeded_client, trade_body):
    response = seeded_client.post(
        URL, data=json.dumps(trade_body), content_type="application/json; charset=utf-8"
    )
    assert response.status_code == 201


# ---------- List, filter, paginate ----------


def test_list_default_page(seeded_client):
    body = seeded_client.get(URL).get_json()
    assert body["pagination"] == {"page": 1, "per_page": 20, "total": 16, "total_pages": 1}
    dates = [t["trade_date"] for t in body["data"]]
    assert len(dates) == 16 and dates == sorted(dates, reverse=True)


def test_pagination_pages_do_not_overlap(seeded_client):
    seen = []
    for page in (1, 2, 3, 4):
        body = seeded_client.get(f"{URL}?per_page=5&page={page}").get_json()
        assert body["pagination"] == {"page": page, "per_page": 5, "total": 16, "total_pages": 4}
        seen += [t["id"] for t in body["data"]]
    assert len(seen) == 16 and len(set(seen)) == 16
    assert seen == [t["id"] for t in seeded_client.get(URL).get_json()["data"]]


def test_page_past_the_end_is_empty_not_an_error(seeded_client):
    body = seeded_client.get(f"{URL}?page=99").get_json()
    assert body["data"] == [] and body["pagination"]["total"] == 16


@pytest.mark.parametrize(
    "query, field",
    [
        ("page=0", "page"),
        ("page=abc", "page"),
        ("page=-1", "page"),
        ("page=1.5", "page"),
        ("per_page=0", "per_page"),
        ("per_page=101", "per_page"),
        ("status=PENDING", "status"),
        ("start=2026-13-01", "start"),
        ("end=yesterday", "end"),
        ("start=2026-03-10&end=2026-03-01", "end"),
        ("instrument_id=999", "instrument_id"),
        ("instrument_id=abc", "instrument_id"),
    ],
)
def test_invalid_query_parameters_are_400(seeded_client, query, field):
    response = seeded_client.get(f"{URL}?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]


def test_unknown_query_parameter_is_400(seeded_client):
    response = seeded_client.get(f"{URL}?instrument=XAUUSD")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_parameters"


def test_filter_by_instrument(seeded_client):
    body = seeded_client.get(f"{URL}?instrument_id=3").get_json()
    assert body["pagination"]["total"] == 4
    assert {t["symbol"] for t in body["data"]} == {"XAUUSD"}


def test_filter_by_status_is_case_insensitive(seeded_client):
    assert seeded_client.get(f"{URL}?status=open").get_json()["pagination"]["total"] == 2
    assert seeded_client.get(f"{URL}?status=CLOSED").get_json()["pagination"]["total"] == 14


def test_filter_by_entry_date_range(seeded_client):
    body = seeded_client.get(f"{URL}?start=2026-03-02&end=2026-03-04").get_json()
    assert sorted(t["trade_date"] for t in body["data"]) == [
        "2026-03-02T08:15:00Z",
        "2026-03-03T13:40:00Z",
        "2026-03-04T08:30:00Z",
    ]


def test_filters_combine_with_pagination(seeded_client):
    body = seeded_client.get(f"{URL}?instrument_id=3&status=CLOSED&per_page=2&page=2").get_json()
    assert body["pagination"] == {"page": 2, "per_page": 2, "total": 4, "total_pages": 2}
    assert len(body["data"]) == 2


@pytest.mark.parametrize(
    "query", ["status=CLOSED' OR '1'='1", "start=2026-01-01' --", "instrument_id=1 OR 1=1"]
)
def test_sql_injection_in_query_string_is_rejected_as_invalid(app, seeded_client, query):
    before = table_snapshot(app, "trades")
    assert seeded_client.get(f"{URL}?{query}").status_code == 400
    assert table_snapshot(app, "trades") == before


def test_empty_database_list(client):
    body = client.get(URL).get_json()
    assert body == {"data": [], "pagination": {"page": 1, "per_page": 20, "total": 0, "total_pages": 0}}


# ---------- Retrieve ----------


def test_get_trade(seeded_client):
    data = seeded_client.get(f"{URL}/9").get_json()["data"]
    assert data["id"] == 9 and data["symbol"] == "EURUSD"
    assert data["result"]["outcome"] == "LOSS"  # fees made a price gain a loss
    assert data["result"]["net_pnl"] == "-5"


@pytest.mark.parametrize("path", ["999", "0", "-1", "abc", "1.5"])
def test_missing_or_invalid_ids_are_404_json(seeded_client, path):
    response = seeded_client.get(f"{URL}/{path}")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


# ---------- Replace (PUT) ----------


def test_put_replaces_the_trade(seeded_client, trade_body):
    trade_id = post(seeded_client, trade_body).get_json()["data"]["id"]
    replacement = full({**trade_body, "exit_price": "1.08700", "notes": "Closed early"})
    response = put(seeded_client, trade_id, replacement)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["exit_price"] == "1.087" and data["notes"] == "Closed early"
    assert data["result"]["net_pnl"] == "193"
    assert seeded_client.get(f"{URL}/{trade_id}").get_json()["data"] == data


def test_put_with_missing_fields_is_400_and_changes_nothing(app, seeded_client, trade_body):
    before = table_snapshot(app, "trades")
    partial = {"notes": "only this"}
    response = put(seeded_client, 1, partial)
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "missing_fields"
    assert "instrument_id" in error["details"] and "notes" not in error["details"]
    assert table_snapshot(app, "trades") == before


def test_put_invalid_values_is_400_and_changes_nothing(app, seeded_client, trade_body):
    before = table_snapshot(app, "trades")
    response = put(seeded_client, 1, full({**trade_body, "position_size": "-5"}))
    assert response.status_code == 400
    assert table_snapshot(app, "trades") == before


def test_put_missing_trade_is_404(seeded_client, trade_body):
    assert put(seeded_client, 999, full(trade_body)).status_code == 404


def test_put_can_reopen_a_trade(seeded_client, trade_body):
    trade_id = post(seeded_client, trade_body).get_json()["data"]["id"]
    reopened = full({**trade_body, "status": "OPEN", "exit_price": None, "closed_at": None})
    data = put(seeded_client, trade_id, reopened).get_json()["data"]
    assert data["status"] == "OPEN" and data["result"] is None


def test_put_requires_json(seeded_client):
    response = seeded_client.put(f"{URL}/1", data="notes=x", content_type="application/x-www-form-urlencoded")
    assert response.status_code == 415


# ---------- Delete ----------


def test_delete_returns_204_with_no_body(seeded_client):
    response = seeded_client.delete(f"{URL}/16")
    assert response.status_code == 204
    assert response.data == b""
    assert seeded_client.get(f"{URL}/16").status_code == 404
    assert seeded_client.delete(f"{URL}/16").status_code == 404
    assert seeded_client.get(URL).get_json()["pagination"]["total"] == 15


def test_delete_missing_trade_is_404(seeded_client):
    response = seeded_client.delete(f"{URL}/999")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


# ---------- Methods ----------


@pytest.mark.parametrize("method, url", [("patch", f"{URL}/1"), ("delete", URL), ("put", URL)])
def test_unsupported_methods_are_405_json(seeded_client, method, url):
    response = getattr(seeded_client, method)(url, json={})
    assert response.status_code == 405
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["Allow"]

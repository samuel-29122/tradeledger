"""The OpenAPI specification must match the running application.

1. Every /api/v1 route and method in Flask is documented, and nothing
   documented is missing from Flask.
2. Real responses validate against the documented schemas. Schemas use
   additionalProperties: false, so an undocumented field fails.
3. The examples in the specification are real: the example POST body
   creates a trade.
4. docs/openapi.json is not stale.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tradeledger.api.http import INSTRUMENT_INPUT_FIELDS, TRADE_INPUT_FIELDS
from tradeledger.api.openapi import EXAMPLE_TRADE_INPUT, build_spec
from tradeledger.api.serialise import INSTRUMENT_FIELDS, RESULT_FIELDS, TRADE_FIELDS

SPEC = build_spec()
SCHEMAS = SPEC["components"]["schemas"]


def validate(instance, schema_name):
    """Validate against a named component, resolving $refs inside the spec."""
    root = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    errors = sorted(Draft202012Validator(root).iter_errors(instance), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:5])


def flask_api_routes(app):
    routes = set()
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("api."):
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)[len("/api/v1") :]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            routes.add((path, method.lower()))
    return routes


def spec_routes():
    return {
        (path, method)
        for path, item in SPEC["paths"].items()
        for method in item
        if method in ("get", "post", "put", "delete", "patch")
    }


# ---------- Structure ----------


def test_spec_is_openapi_3_1_and_served(client, app):
    assert SPEC["openapi"] == "3.1.0"
    served = client.get("/api/v1/openapi.json").get_json()
    assert served["openapi"] == "3.1.0"
    # Phase 5: compare with the configured version, not a hard-coded "0.4.0".
    assert served["info"]["x-app-version"] == app.config["APP_VERSION"]
    assert served["paths"] == SPEC["paths"]


def test_every_route_is_documented_and_every_documented_route_exists(app):
    assert flask_api_routes(app) == spec_routes()


def test_component_schemas_are_valid_json_schema():
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema({**schema, "components": SPEC["components"]})


def test_field_lists_match_the_code():
    assert list(SCHEMAS["Trade"]["properties"]) == [*TRADE_FIELDS, "result"]
    assert list(SCHEMAS["TradeResult"]["properties"]) == list(RESULT_FIELDS)
    assert list(SCHEMAS["TradeInput"]["properties"]) == list(TRADE_INPUT_FIELDS)
    assert SCHEMAS["TradeReplace"]["required"] == list(TRADE_INPUT_FIELDS)
    assert list(SCHEMAS["Instrument"]["properties"]) == list(INSTRUMENT_FIELDS)
    assert list(SCHEMAS["InstrumentInput"]["properties"]) == list(INSTRUMENT_INPUT_FIELDS)


def test_every_path_parameter_is_declared():
    for path, item in SPEC["paths"].items():
        names = set(re.findall(r"{(\w+)}", path))
        declared = {p["name"] for p in item.get("parameters", []) if p["in"] == "path"}
        assert names == declared, path


def test_documented_query_parameters_match_what_the_code_accepts():
    from tradeledger.api.analytics import FILTERS
    from tradeledger.api.trades import LIST_PARAMETERS

    def query_names(path):
        return {p["name"] for p in SPEC["paths"][path]["get"].get("parameters", []) if p["in"] == "query"}

    assert query_names("/trades") == set(LIST_PARAMETERS)
    for path in SPEC["paths"]:
        if path.startswith("/analytics/"):
            assert query_names(path) == set(FILTERS), path


# ---------- Real responses validate ----------


def test_trade_responses_match_schemas(seeded_client, trade_body):
    validate(trade_body, "TradeInput")
    created = seeded_client.post("/api/v1/trades", json=trade_body)
    validate(created.get_json(), "TradeEnvelope")
    trade_id = created.get_json()["data"]["id"]
    validate(seeded_client.get(f"/api/v1/trades/{trade_id}").get_json(), "TradeEnvelope")
    validate(seeded_client.get("/api/v1/trades").get_json(), "TradeList")
    validate(seeded_client.get("/api/v1/trades?status=OPEN").get_json(), "TradeList")
    replacement = {name: trade_body.get(name) for name in TRADE_INPUT_FIELDS}
    validate(replacement, "TradeReplace")
    validate(seeded_client.put(f"/api/v1/trades/{trade_id}", json=replacement).get_json(), "TradeEnvelope")


def test_instrument_responses_match_schemas(seeded_client):
    body = {
        "symbol": "US30",
        "name": "Example",
        "asset_class": "INDEX",
        "price_increment": "1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
    }
    validate(body, "InstrumentInput")
    validate(seeded_client.post("/api/v1/instruments", json=body).get_json(), "InstrumentEnvelope")
    validate(seeded_client.get("/api/v1/instruments").get_json(), "InstrumentList")
    validate(seeded_client.get("/api/v1/instruments/1").get_json(), "InstrumentEnvelope")


@pytest.mark.parametrize(
    "endpoint, schema",
    [
        ("summary", "SummaryAnalytics"),
        ("instruments", "GroupedAnalytics"),
        ("strategies", "GroupedAnalytics"),
        ("sessions", "GroupedAnalytics"),
        ("weekday", "GroupedAnalytics"),
        ("monthly", "GroupedAnalytics"),
        ("curve", "CurveAnalytics"),
    ],
)
@pytest.mark.parametrize("query", ["", "?instrument_id=5", "?start=2026-03-10&end=2026-03-12"])
def test_analytics_responses_match_schemas(seeded_client, endpoint, schema, query):
    validate(seeded_client.get(f"/api/v1/analytics/{endpoint}{query}").get_json(), schema)


def test_empty_analytics_match_schemas(client):
    validate(client.get("/api/v1/analytics/summary").get_json(), "SummaryAnalytics")
    validate(client.get("/api/v1/analytics/curve").get_json(), "CurveAnalytics")


def test_legacy_trade_matches_schemas(app, seeded_client):
    """A legacy trade (no exit time), simulated by clearing closed_at in SQL."""
    from tradeledger.db import get_db

    with app.app_context():
        conn = get_db()
        with conn:
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
    body = seeded_client.get("/api/v1/analytics/summary").get_json()
    assert body["data"]["unknown_close_time_trade_ids"] == [1]
    validate(body, "SummaryAnalytics")
    validate(seeded_client.get("/api/v1/trades/1").get_json(), "TradeEnvelope")


def test_unavailable_r_matches_schema():
    """realised_r = null cannot come from the database (its CHECK constraint
    forbids a stop on the wrong side), but the engine supports it for other
    data sources, so the documented null shape is checked directly."""
    from decimal import Decimal

    from tests.factories import make_trade
    from tradeledger.api.serialise import trade_json

    trade = make_trade(
        stop_loss=Decimal("105"),
        instrument_id=1,
        closed_at="2026-01-05T11:00:00Z",
        take_profit=None,
        notes=None,
        created_at="2026-01-05T11:00:00Z",
        updated_at="2026-01-05T11:00:00Z",
    )
    body = trade_json(trade)
    assert body["result"]["realised_r"] is None and body["result"]["initial_risk"] is None
    assert body["result"]["r_unavailable_reason"]
    validate({"data": body}, "TradeEnvelope")


@pytest.mark.parametrize(
    "method, url, kwargs, status",
    [
        ("post", "/api/v1/trades", {"json": {"position_size": "0"}}, 400),
        ("post", "/api/v1/trades", {"data": "x", "content_type": "text/plain"}, 415),
        ("get", "/api/v1/trades/999", {}, 404),
        ("delete", "/api/v1/instruments", {}, 405),
        (
            "post",
            "/api/v1/instruments",
            {
                "json": {
                    "symbol": "EURUSD",
                    "name": "x",
                    "asset_class": "FOREX",
                    "price_increment": "1",
                    "contract_multiplier": "1",
                    "quote_currency": "USD",
                }
            },
            409,
        ),
    ],
)
def test_error_responses_match_schema_and_are_documented(seeded_client, method, url, kwargs, status):
    response = getattr(seeded_client, method)(url, **kwargs)
    assert response.status_code == status
    validate(response.get_json(), "Error")


# ---------- Examples are real ----------


def test_example_post_body_creates_a_trade(seeded_client):
    response = seeded_client.post("/api/v1/trades", json=EXAMPLE_TRADE_INPUT)
    assert response.status_code == 201
    example = SPEC["paths"]["/trades"]["post"]["responses"]["201"]["content"]["application/json"]["example"]
    actual = response.get_json()["data"]
    for field in ("symbol", "entry_price", "exit_price", "status", "closed_at"):
        assert actual[field] == example["data"][field]
    assert actual["result"] == example["data"]["result"]
    validate(example, "TradeEnvelope")


def test_validation_error_example_is_what_the_api_returns(seeded_client):
    example = SPEC["paths"]["/trades"]["post"]["responses"]["400"]["content"]["application/json"]["example"]
    body = {**EXAMPLE_TRADE_INPUT, "position_size": "0", "stop_loss": "1.09"}
    assert seeded_client.post("/api/v1/trades", json=body).get_json() == example


# ---------- Exported file ----------


def test_docs_openapi_json_is_up_to_date():
    path = Path(__file__).parent.parent / "docs" / "openapi.json"
    assert json.loads(path.read_text(encoding="utf-8")) == SPEC, (
        "docs/openapi.json is stale: run  flask --app tradeledger openapi"
    )


def test_openapi_cli_writes_the_spec(runner, tmp_path):
    out = tmp_path / "spec.json"
    result = runner.invoke(args=["openapi", "--output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == SPEC

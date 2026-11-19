"""Phase 8.5: least privilege in PostgreSQL (the grant-app-role command).

Plain English: the web app should not be able to delete or reshape the
database's tables, only read and write rows. These tests create a real
PostgreSQL role, give it exactly what grant-app-role grants, run the
application AS that role (sign in, add an instrument and a trade, use the
dashboard, the API and the account page) and then check that every kind
of structural change is refused.
"""

import uuid
from urllib.parse import quote

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.conftest import PG_URL, ensure_user, issue_token, sign_in
from tests.web_helpers import closed_trade_form, post_form
from tradeledger import create_app
from tradeledger.cli import grant_app_role_command
from tradeledger.db import connect, get_db, init_schema

pytestmark = pytest.mark.postgres_only


@pytest.fixture
def app_role():
    """A fresh role with no rights at all; dropped afterwards."""
    name = "tlapp_" + uuid.uuid4().hex[:10]
    admin = connect(PG_URL)
    admin.execute(f"CREATE ROLE {name} NOLOGIN")
    yield name
    admin.execute(f"DROP OWNED BY {name}")
    admin.execute(f"DROP ROLE {name}")
    admin.close()


def as_role(url, role):
    """The same database and schema, but every statement runs as `role`."""
    base, _, options = url.partition("options=")
    extra = quote(" -crole=" + role)
    return f"{base}options={options}{extra}"


def owner_app(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


def grant(app, role):
    return CliRunner().invoke(
        grant_app_role_command, ["--role", role], obj=ScriptInfo(create_app=lambda: app)
    )


def test_the_app_works_with_only_the_granted_rights(db_path, app_role):
    owner = owner_app(db_path)
    result = grant(owner, app_role)
    assert result.exit_code == 0, result.output
    owner.extensions["tradeledger_db"].close()

    app = create_app("testing", overrides={"DATABASE": as_role(db_path, app_role)})
    with app.app_context():
        assert get_db().execute("SELECT current_user").fetchone()[0] == app_role
    client = app.test_client()
    sign_in(client)
    response = post_form(
        client,
        "/instruments",
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar",
            "asset_class": "FOREX",
            "price_increment": "0.0001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    assert response.status_code in (302, 303), response.get_data(as_text=True)[:500]
    response = post_form(client, "/trades/new", closed_trade_form())
    assert response.status_code in (302, 303), response.get_data(as_text=True)[:500]
    for path in ("/", "/trades", "/trades/1", "/analytics", "/account", "/instruments"):
        assert client.get(path).status_code == 200, path
    token = issue_token(app)
    api = client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"})
    assert api.status_code == 200 and api.get_json()["pagination"]["total"] == 1
    assert client.get("/health/ready").status_code == 200
    app.extensions["tradeledger_db"].close()


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE trades",
        "TRUNCATE trades",
        "ALTER TABLE trades ADD COLUMN extra TEXT",
        "CREATE TABLE stolen (x INTEGER)",
        "DELETE FROM schema_migrations",
        "INSERT INTO schema_migrations (version, name) VALUES (99, 'fake')",
        "CREATE INDEX idx_extra ON trades (notes)",
        "ALTER TABLE users DROP CONSTRAINT users_email_unique",
    ],
)
def test_structural_changes_are_refused(db_path, app_role, statement):
    owner = owner_app(db_path)
    assert grant(owner, app_role).exit_code == 0
    owner.extensions["tradeledger_db"].close()
    conn = connect(as_role(db_path, app_role))
    try:
        with pytest.raises(Exception) as caught:
            conn.execute(statement)
        assert "permission denied" in str(caught.value) or "must be owner" in str(caught.value)
    finally:
        conn.close()


def test_a_superuser_or_the_owner_is_refused_as_the_app_role(db_path):
    owner = owner_app(db_path)
    with owner.app_context():
        me = get_db().execute("SELECT current_user").fetchone()[0]
    result = grant(owner, me)
    assert result.exit_code != 0 and "separate role" in result.output
    owner.extensions["tradeledger_db"].close()


def test_an_administrative_role_is_refused(db_path):
    name = "tladmin_" + uuid.uuid4().hex[:10]
    admin = connect(PG_URL)
    admin.execute(f"CREATE ROLE {name} NOLOGIN CREATEROLE")
    try:
        owner = owner_app(db_path)
        result = grant(owner, name)
        assert result.exit_code != 0 and "administrative rights" in result.output
        owner.extensions["tradeledger_db"].close()
    finally:
        admin.execute(f"DROP ROLE {name}")
        admin.close()


@pytest.mark.parametrize("role", ["Robert'); DROP TABLE trades;--", "has space", "UPPER", "a" * 70, ""])
def test_unsafe_role_names_are_refused_before_any_sql(db_path, role):
    owner = owner_app(db_path)
    result = grant(owner, role)
    assert result.exit_code != 0
    with owner.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0  # table still there
    owner.extensions["tradeledger_db"].close()


def test_an_unknown_role_is_refused(db_path):
    owner = owner_app(db_path)
    result = grant(owner, "tlnosuchrole_" + uuid.uuid4().hex[:6])
    assert result.exit_code != 0 and "does not exist" in result.output
    owner.extensions["tradeledger_db"].close()

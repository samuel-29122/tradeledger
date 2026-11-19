"""TradeLedger JSON API, version 1, mounted at /api/v1.

Modules
  errors.py       ApiError and the single JSON error format
  http.py         reading JSON bodies and query strings safely
  serialise.py    rows and analytics results -> JSON-safe dicts
  trades.py       /trades endpoints
  instruments.py  /instruments endpoints
  analytics.py    /analytics endpoints (thin wrappers around build_report)
  openapi.py      the OpenAPI 3.1 specification, served at /openapi.json

Request flow
  HTTP request -> authenticate (Bearer token) -> route (thin) -> http.py
  checks shape -> repository (validation.py rules + parameterised SQL,
  always scoped to the token's owner) -> SQLite or PostgreSQL
  -> performance engine (analytics only) -> serialise.py -> JSON response

Authentication (Phase 8)
------------------------
Every endpoint except the OpenAPI document needs
    Authorization: Bearer tl_...
a personal API token created on the Account page (repositories/api_tokens.py).

The API deliberately IGNORES session cookies. Why it matters: a browser
attaches cookies to requests that other websites trigger, which is what
makes CSRF possible. It never attaches an Authorization header on its own,
and a cross-site script cannot add one without a CORS preflight, which
this API never approves (it sends no Access-Control-Allow-* headers). So a
request forged by another website arrives with no credentials and gets 401.

Why tokens and not JWTs: a JWT is checked without the database, so it
stays valid until it expires even after "revoke"; making revocation work
needs a server-side list, which removes the main benefit. TradeLedger
already reads the database on every request, so an opaque random token
looked up by its hash is simpler and revocable immediately.

Responses
  401 unauthorized   no token, a malformed, unknown, expired or revoked
                     token, or a disabled account (one message for all)
  404 not_found      the record does not exist OR belongs to another user
"""

import logging

from flask import Blueprint, current_app, g, request

from ..db import get_db
from ..repositories import DuplicateError, api_tokens
from ..validation import ValidationError
from .errors import ApiError, error_response

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
log = logging.getLogger("tradeledger.auth")

# Endpoints that need no token: documentation only, never trading data.
PUBLIC_API_ENDPOINTS = frozenset({"api.openapi_spec"})


def api_user_id():
    """The authenticated API user's id; raises if there is none (fails closed)."""
    user = g.get("api_user")
    if user is None:
        raise RuntimeError("api_user_id() called without an authenticated API user")
    return user["id"]


@api_bp.before_request
def authenticate():
    if request.endpoint in PUBLIC_API_ENDPOINTS:
        return
    if not current_app.config["API_ENABLED"]:
        raise ApiError(403, "api_disabled", "The API is switched off in this environment.")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    user = None
    if scheme.lower() == "bearer" and token.strip():
        user = api_tokens.authenticate(get_db(), token.strip())
    if user is None:
        log.info("API request without a valid token", extra={"event": "auth.api.rejected"})
        raise ApiError(401, "unauthorized", "A valid API token is required (Authorization: Bearer <token>).")
    g.api_user = user


@api_bp.after_request
def no_caching(response):
    # Trading data should not be stored by browsers or proxies.
    response.headers["Cache-Control"] = "no-store"
    return response


@api_bp.errorhandler(ApiError)
def handle_api_error(exc):
    response, status = error_response(exc.status, exc.code, exc.message, exc.details)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="TradeLedger API"'
    return response, status


@api_bp.errorhandler(ValidationError)
def handle_validation_error(exc):
    return error_response(400, "validation_error", "The request is invalid.", exc.errors)


@api_bp.errorhandler(DuplicateError)
def handle_duplicate(exc):
    return error_response(409, "conflict", str(exc))


# Importing the modules registers their routes on api_bp.
from . import analytics, instruments, openapi, trades  # noqa: E402,F401

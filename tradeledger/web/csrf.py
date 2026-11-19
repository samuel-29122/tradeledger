"""Cross-Site Request Forgery (CSRF) protection for HTML forms.

The attack: while you are signed in to TradeLedger, a malicious page you
visit contains a hidden form that posts to https://your-journal/trades/3/delete.
Your browser would happily send it. The defence: every form we render
includes a secret token, and every POST must send it back. Another site
cannot read our pages, so it cannot know the token.

How it works here (the "synchroniser token" pattern):
  1. get_token() creates one random token per browser session and stores
     it in Flask's signed session cookie.
  2. Templates put it in a hidden field: {{ csrf_field() }}.
  3. Before any POST/PUT/PATCH/DELETE on this blueprint, the submitted
     token is compared with the session's using hmac.compare_digest
     (constant time, so timing cannot leak how much of it matched).
  4. Missing or wrong token -> 400 with a "form expired" page. Nothing
     is written.

Why not Flask-WTF? It is the usual choice and does the same thing, but it
is not installed in this project and 40 lines of standard library code
are easy to read, test and explain. The JSON API (/api/v1) does not use
these tokens: since Phase 8 it ignores cookies completely and accepts only
an Authorization: Bearer token, which a browser never attaches on its own,
so a forged cross-site request has no credentials to ride on.

The token lives in the session, so signing in (which clears the session)
gives the browser a new token: a token learned before sign-in is useless.
"""

import hmac
import secrets

from flask import render_template, request, session
from markupsafe import Markup, escape

from . import main_bp

SESSION_KEY = "_csrf_token"
FIELD_NAME = "csrf_token"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def get_token():
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def token_is_valid(submitted):
    expected = session.get(SESSION_KEY)
    if not expected or not isinstance(submitted, str) or not submitted:
        return False
    return hmac.compare_digest(expected.encode(), submitted.encode())


def check_csrf_token():
    """before_request hook, registered after the login check in web/__init__.py."""
    if request.method not in STATE_CHANGING:
        return None
    if token_is_valid(request.form.get(FIELD_NAME)):
        return None
    return render_template("errors/400.html", reason="csrf"), 400


@main_bp.app_context_processor
def inject_csrf_helpers():
    def csrf_field():
        return Markup('<input type="hidden" name="%s" value="%s">') % (FIELD_NAME, escape(get_token()))

    return {"csrf_token": get_token, "csrf_field": csrf_field}

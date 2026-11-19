"""Choosing a new password from a one-time reset link (Phase 8.5).

Flow
----
1. The operator runs:  flask --app tradeledger create-reset-link --email you@example.com
   and hands you the printed link (repositories/password_resets.py).
2. GET /reset-password?token=tlr_...  checks the link WITHOUT using it, keeps
   the token in this browser's signed session cookie and immediately
   redirects to /reset-password, so the token leaves the address bar, the
   browser history and any Referer header.
3. GET /reset-password  shows the form (or "invalid or expired").
4. POST /reset-password  (CSRF-protected): new password twice. On success
   the link is used up, every session ends, every API token is revoked
   and you are sent to the sign-in page. You are NOT signed in
   automatically, so two-step verification (if switched on) still applies.

Every failure (unknown, used, expired, malformed link) shows the same
message. Failed attempts are counted per network address; past the limit
the page answers 429, although a 256-bit token cannot be guessed anyway.
"""

import logging

from flask import current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, parameters_from_config, password_problem
from ..repositories import api_tokens, password_resets, throttle
from ..repositories.users import get_user, set_password_hash
from . import main_bp
from .auth import client_ip, digest, now_seconds

log = logging.getLogger("tradeledger.auth")

INVALID = (
    "This reset link is invalid, has already been used or has expired. Ask your administrator for a new one."
)
LABELS = {"new_password": "New password", "confirm_password": "Confirm new password"}
SESSION_KEY = "reset_token"


def _blocked(conn):
    config = current_app.config
    return throttle.is_blocked(
        conn,
        "reset-ip",
        digest("reset-ip", client_ip()),
        limit=config["LOGIN_MAX_FAILURES_PER_IP"],
        window_seconds=config["LOGIN_WINDOW_SECONDS"],
    )


def _record_failure(conn):
    throttle.record_failure(
        conn,
        "reset-ip",
        digest("reset-ip", client_ip()),
        window_seconds=current_app.config["LOGIN_WINDOW_SECONDS"],
    )


def _page(status=200, **context):
    g.referrer_policy = "no-referrer"
    defaults = {"valid": False, "errors": {}, "labels": LABELS, "message": None}
    return render_template("auth/reset_password.html", **{**defaults, **context}), status


@main_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    conn = get_db()
    if _blocked(conn):
        log.warning("password reset refused: too many failures", extra={"event": "auth.reset.throttled"})
        return _page(429, message="Too many attempts. Wait a few minutes, then try again.")

    if request.method == "GET" and "token" in request.args:
        token = request.args.get("token", "")
        if password_resets.find_valid(conn, token, now=now_seconds()) is None:
            _record_failure(conn)
            session.pop(SESSION_KEY, None)
            log.info("password reset link refused", extra={"event": "auth.reset.invalid"})
            return _page(400, message=INVALID)
        session[SESSION_KEY] = token
        return redirect(url_for("main.reset_password"), code=303)

    token = session.get(SESSION_KEY)
    link = password_resets.find_valid(conn, token, now=now_seconds()) if token else None
    if link is None:
        session.pop(SESSION_KEY, None)
        return _page(400 if request.method == "POST" else 200, message=INVALID)
    user = get_user(conn, link["user_id"])
    if user is None or not user["is_active"]:
        session.pop(SESSION_KEY, None)
        return _page(400, message=INVALID)

    if request.method == "GET":
        return _page(valid=True, email=user["email"])

    new = request.form.get("new_password", "")
    errors = {}
    problem = password_problem(new, email=user["email"])
    if problem:
        errors["new_password"] = problem
    elif request.form.get("confirm_password", "") != new:
        errors["confirm_password"] = "does not match the new password."
    if errors:
        return _page(400, valid=True, email=user["email"], errors=errors)

    now = now_seconds()
    with conn:
        if not password_resets.consume(conn, link["id"], user_id=user["id"], now=now):
            session.pop(SESSION_KEY, None)
            return _page(400, message=INVALID)
        set_password_hash(
            conn,
            user["id"],
            hash_password(new, parameters_from_config(current_app.config)),
            end_other_sessions=True,
        )
        revoked = api_tokens.revoke_all(conn, user_id=user["id"])
        throttle.clear(conn, "login-email", digest("login-email", user["email"]))
    session.clear()
    log.info(
        "password reset with a link",
        extra={"event": "auth.reset.completed", "user_id": user["id"], "tokens_revoked": revoked},
    )
    flash(
        "Your password has been changed and every session has been signed out. Sign in with your new password.",
        "success",
    )
    return redirect(url_for("main.login"), code=303)

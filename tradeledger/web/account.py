"""Account settings: password change, signed-in browsers, API tokens (Phase 8, 8.5).

Every route here needs a signed-in user (it is not in PUBLIC_ENDPOINTS) and
every POST carries a CSRF token (web/csrf.py).
"""

import logging
import secrets

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, parameters_from_config, password_problem, verify_password
from ..repositories import api_tokens, sessions, throttle
from ..repositories.users import bump_session_version, get_user, public_user, set_password_hash
from ..validation import ValidationError
from . import main_bp
from .auth import THROTTLED, current_session_id, current_user_id, digest, now_seconds, start_session

log = logging.getLogger("tradeledger.auth")

PASSWORD_LABELS = {
    "current_password": "Current password",
    "new_password": "New password",
    "confirm_password": "Confirm new password",
}
TOKEN_LABELS = {"name": "Token name", "lifetime_days": "Expires after"}
LIFETIME_OPTIONS = [(30, "30 days"), (90, "90 days"), (365, "365 days")]


def new_token_form_nonce():
    """A one-time value for the "Create token" form.

    The new token is shown in the POST response itself (it must never be
    stored anywhere, including the session cookie). Without a nonce, a
    browser refresh would re-send that POST and silently create a second
    token. The nonce makes each rendered form usable once."""
    session["token_form_nonce"] = secrets.token_urlsafe(16)
    return session["token_form_nonce"]


def render_account(status=200, **extra):
    config = current_app.config
    context = {
        "account": public_user(g.user),
        "sessions": sessions.list_active(
            get_db(),
            user_id=current_user_id(),
            now=now_seconds(),
            idle_timeout_seconds=config["SESSION_IDLE_TIMEOUT_SECONDS"],
        ),
        "current_session_id": current_session_id(),
        "tokens": api_tokens.list_tokens(get_db(), user_id=current_user_id()),
        "password_errors": {},
        "token_errors": {},
        "token_values": {"lifetime_days": str(current_app.config["API_TOKEN_DEFAULT_LIFETIME_DAYS"])},
        "new_token": None,
        "password_labels": PASSWORD_LABELS,
        "token_labels": TOKEN_LABELS,
        "lifetime_options": LIFETIME_OPTIONS,
        "api_enabled": current_app.config["API_ENABLED"],
        "token_form_nonce": new_token_form_nonce(),
    }
    context.update(extra)
    return render_template("account/settings.html", **context), status


@main_bp.route("/account")
def account():
    return render_account()


@main_bp.route("/account/password", methods=["POST"])
def change_password():
    config = current_app.config
    conn = get_db()
    user = g.user
    key = digest("login-email", user["email"])
    window = config["LOGIN_WINDOW_SECONDS"]
    if throttle.is_blocked(
        conn, "login-email", key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ):
        return render_account(429, password_errors={"current_password": THROTTLED})

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    errors = {}
    if not verify_password(current, user["password_hash"]):
        throttle.record_failure(conn, "login-email", key, window_seconds=window)
        errors["current_password"] = "is incorrect."
    problem = password_problem(new, email=user["email"])
    if problem:
        errors["new_password"] = problem
    elif request.form.get("confirm_password", "") != new:
        errors["confirm_password"] = "does not match the new password."
    elif new == current and not errors:
        errors["new_password"] = "must be different from your current password."
    if errors:
        return render_account(400, password_errors=errors)

    set_password_hash(
        conn, user["id"], hash_password(new, parameters_from_config(config)), end_other_sessions=True
    )
    revoked = api_tokens.revoke_all(conn, user_id=user["id"])
    # Every session (including this one) was revoked; this browser gets a
    # brand-new session, as after signing in with the new password.
    start_session(get_user(conn, user["id"]))
    log.info(
        "password changed",
        extra={"event": "auth.password.changed", "user_id": user["id"], "tokens_revoked": revoked},
    )
    flash(
        "Password changed. Every other browser has been signed out"
        + (f" and {revoked} API token(s) revoked." if revoked else "."),
        "success",
    )
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/sessions/end-others", methods=["POST"])
def end_other_sessions():
    user_id = current_user_id()
    conn = get_db()
    bump_session_version(conn, user_id)  # revokes every row, this one included
    start_session(get_user(conn, user_id))  # ... so this browser starts afresh
    log.info("other sessions ended", extra={"event": "auth.sessions.ended_elsewhere", "user_id": user_id})
    flash("Every other browser signed in to your account has been signed out.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/sessions/<int:session_id>/revoke", methods=["POST"])
def revoke_session(session_id):
    """Sign out ONE browser (Phase 8.5). Another user's session id, an
    unknown id and an already-ended session all get the same 404."""
    user_id = current_user_id()
    this_browser = session_id == current_session_id()
    if not sessions.revoke(get_db(), session_id, user_id=user_id, now=now_seconds()):
        abort(404)
    log.info(
        "session revoked",
        extra={"event": "auth.session.revoked", "user_id": user_id, "session_ref": session_id},
    )
    if this_browser:
        session.clear()
        flash("You have signed out.", "success")
        return redirect(url_for("main.login"), code=303)
    flash("That browser has been signed out.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/tokens", methods=["POST"])
def create_api_token():
    config = current_app.config
    if not config["API_ENABLED"]:
        abort(404)
    expected = session.pop("token_form_nonce", None)
    if not expected or request.form.get("form_nonce") != expected:
        flash("That form had already been submitted, so no new token was created.", "info")
        return redirect(url_for("main.account"), code=303)
    name = request.form.get("name", "")
    raw_days = request.form.get("lifetime_days", "")
    values = {"name": name, "lifetime_days": raw_days}
    lifetime = int(raw_days) if raw_days.isdigit() else None
    try:
        token, record = api_tokens.create_token(
            get_db(),
            user_id=current_user_id(),
            name=name,
            lifetime_days=lifetime,
            max_active=config["API_TOKEN_MAX_ACTIVE"],
        )
    except ValidationError as exc:
        return render_account(400, token_errors=exc.errors, token_values=values)
    log.info(
        "API token created",
        extra={"event": "auth.token.created", "user_id": current_user_id(), "token_id": record["id"]},
    )
    # The token is shown in THIS response only. HTML responses are already
    # Cache-Control: no-store, so the browser does not keep a copy.
    return render_account(201, new_token={"value": token, "record": record})


@main_bp.route("/account/tokens/<int:token_id>/revoke", methods=["POST"])
def revoke_api_token(token_id):
    if not api_tokens.revoke_token(get_db(), token_id, user_id=current_user_id()):
        abort(404)  # not yours, already revoked, or does not exist: same answer
    log.info(
        "API token revoked",
        extra={"event": "auth.token.revoked", "user_id": current_user_id(), "token_id": token_id},
    )
    flash("API token revoked. Programs using it will be refused from now on.", "success")
    return redirect(url_for("main.account"), code=303)

"""Signing in, signing out and registering (Phase 8).

Plain English
-------------
* Sign in: you type your email and password. If they match, TradeLedger
  puts your user number in a cookie that it signs with a secret key, so
  the browser cannot change it without the signature breaking.
* Every page then checks that cookie, loads your account and refuses
  anything private if you are not signed in.
* Sign out: a button (a POST form with a CSRF token), never a plain link,
  so another website cannot sign you out by embedding an image.

Technical summary
-----------------
Session    Flask's signed-cookie session (itsdangerous HMAC with SECRET_KEY).
           It holds: sid (a random session key, Phase 8.5), uid (user id),
           sv (the user's session_version), auth_at (sign-in time), seen_at
           (last activity), the CSRF token. No password, hash or personal
           data is ever put in it. The database keeps one user_sessions row
           per signed-in browser, holding only SHA-256(sid)
           (repositories/sessions.py).
Checks     On every website request (load_logged_in_user):
             * the account still exists and is active;
             * sv equals users.session_version; raising that number
               ("sign out everywhere", password change, account disabled)
               ends every existing session at once;
             * the user_sessions row for sid exists, belongs to this user,
               is not revoked, not expired and not idle (Phase 8.5);
             * auth_at is younger than SESSION_MAX_AGE_SECONDS (absolute
               limit) and seen_at younger than SESSION_IDLE_TIMEOUT_SECONDS.
           These are checked on the SERVER: the cookie's own expiry date is
           only a hint to the browser, and a copied cookie would ignore it.
Fixation   start_session() clears the session before writing the new user,
           so any session state planted before sign-in (including the CSRF
           token) is discarded. Signed cookies have no server-side session id
           to fixate, but clearing them is still required.
Default    require_login() runs before EVERY route in the website blueprint
deny       and only lets the endpoints in PUBLIC_ENDPOINTS through.
           A new route is private unless someone deliberately adds it to
           that set (tests/test_auth.py checks every registered route).
Brute      Failed sign-ins are counted per email and per IP address
force      (repositories/throttle.py); past the limits the form answers 429.
           Unknown emails still run a full Argon2id verification against a
           dummy hash, so response time does not reveal which emails exist.
Messages   One message for every failure: "Email or password is incorrect."
           A disabled account gets the same message.

Signed cookie AND a server-side record (Phase 8.5): Phase 8 used only the
signed cookie plus session_version, so it could end ALL of a user's
sessions but not one device's, and a cookie copied before sign-out stayed
valid until it expired. The user_sessions row closes both gaps; the signed
cookie still carries the CSRF token and the other values, and the row
lives in the shared database, so it works across containers.
"""

import logging
import time

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, needs_rehash, parameters_from_config, password_problem, verify_password
from ..repositories import DuplicateError, sessions, throttle
from ..repositories.users import (
    create_user,
    email_problem,
    get_user,
    get_user_by_email,
    normalise_email,
    record_login,
    set_password_hash,
)
from . import forms, main_bp

log = logging.getLogger("tradeledger.auth")

PUBLIC_ENDPOINTS = frozenset(
    {
        "main.login",
        "main.login_two_step",  # Phase 8.5: second step, needs a pending sign-in
        "main.register",
        "main.reset_password",  # Phase 8.5: needs a one-time link
        "main.health",
        "main.health_live",
        "main.health_ready",
        "main.about",
    }
)

LOGIN_FAILED = "Email or password is incorrect."
THROTTLED = "Too many sign-in attempts. Wait a few minutes, then try again."

LOGIN_LABELS = {"email": "Email address", "password": "Password"}
REGISTER_LABELS = {"email": "Email address", "password": "Password", "confirm_password": "Confirm password"}


def now_seconds():
    """Current time in whole seconds. One function so tests can move the clock."""
    return int(time.time())


def client_ip():
    """The client's IP address. Behind a load balancer this is only right
    when PROXY_HOPS matches the number of proxies (see config.py)."""
    return request.remote_addr or "unknown"


def digest(scope, value):
    return throttle.key_digest(current_app.config["SECRET_KEY"], scope, value)


def safe_next(target):
    """Accept only a path on THIS site for the post-login redirect (or None).

    Uses the same rules as every other "next" link (forms.safe_next):
    absolute URLs (https://evil.example), scheme-relative URLs
    (//evil.example), backslash tricks and control characters are refused.
    Otherwise an attacker could send a link to
    /login?next=https://evil.example and the real sign-in page would
    forward the victim to a fake one.
    """
    return forms.safe_next(target, None)


def dummy_hash():
    """A real Argon2id hash of a random value, computed once per process
    with the current settings: verifying against it costs the same as a
    real verification."""
    cache = current_app.extensions.setdefault("tradeledger_auth", {})
    params = parameters_from_config(current_app.config)
    if cache.get("dummy_params") != params:
        cache["dummy_hash"] = hash_password("dummy password that matches no account", params)
        cache["dummy_params"] = params
    return cache["dummy_hash"]


def start_session(user):
    """Sign this browser in as `user`: a fresh cookie plus a new
    user_sessions row (Phase 8.5) that can be revoked on the server."""
    session.clear()
    session.permanent = True
    now = now_seconds()
    key, session_id = sessions.create_session(
        get_db(),
        user_id=user["id"],
        now=now,
        max_age_seconds=current_app.config["SESSION_MAX_AGE_SECONDS"],
        user_agent=request.headers.get("User-Agent", ""),
    )
    session["sid"] = key
    session["uid"] = user["id"]
    session["sv"] = user["session_version"]
    session["auth_at"] = now
    session["seen_at"] = now
    g.session_id = session_id
    return session_id


def current_session_id():
    """The user_sessions id of THIS browser (None if not signed in)."""
    return g.get("session_id")


def current_user_id():
    """The signed-in user's id. Raises if called where nobody is signed in,
    so a coding mistake fails closed instead of reading someone's data."""
    user = g.get("user")
    if user is None:
        raise RuntimeError("current_user_id() called without a signed-in user")
    return user["id"]


# ---------- Request hooks (registered in web/__init__.py) ----------


def load_logged_in_user():
    g.user = None
    user_id = session.get("uid")
    if user_id is None:
        return None
    config = current_app.config
    user = get_user(get_db(), user_id)
    now = now_seconds()
    reason = None
    if user is None or not user["is_active"]:
        reason = "account_unavailable"
    elif session.get("sv") != user["session_version"]:
        reason = "signed_out_elsewhere"
    elif now - int(session.get("auth_at", 0)) > config["SESSION_MAX_AGE_SECONDS"]:
        reason = "expired"
    elif now - int(session.get("seen_at", 0)) > config["SESSION_IDLE_TIMEOUT_SECONDS"]:
        reason = "idle"
    record = None
    if reason is None:
        # Phase 8.5: the server-side row decides as well. A cookie whose row
        # was revoked (signed out, signed out from another device, password
        # changed) or never existed is refused even if its signature is valid.
        record = sessions.find_session(get_db(), session.get("sid"))
        reason = sessions.problem_with(
            record, user_id=user_id, now=now, idle_timeout_seconds=config["SESSION_IDLE_TIMEOUT_SECONDS"]
        )
        if reason == "unknown":
            reason = "no_server_session"
    if reason:
        session.clear()
        g.session_ended = reason
        log.info("session ended", extra={"event": "auth.session.ended", "reason": reason, "user_id": user_id})
        return None
    g.user = user
    g.session_id = record["id"]
    sessions.touch(get_db(), record, now)
    if now - int(session.get("seen_at", 0)) >= 60:  # refresh at most once a minute
        session["seen_at"] = now
    return None


def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or g.get("user") is not None:
        return None
    if g.get("session_ended"):
        flash("Your session has ended. Please sign in again.", "info")
    next_path = None
    if request.method == "GET":
        next_path = request.full_path.rstrip("?")
    return redirect(url_for("main.login", next=next_path), code=303 if request.method != "GET" else 302)


# ---------- Routes ----------


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    next_path = safe_next(request.values.get("next"))
    if request.method == "GET":
        return render_template("auth/login.html", values={}, error=None, next_path=next_path)

    config = current_app.config
    conn = get_db()
    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    email_key = digest("login-email", email)
    ip_key = digest("login-ip", client_ip())
    window = config["LOGIN_WINDOW_SECONDS"]

    if throttle.is_blocked(
        conn, "login-email", email_key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ) or throttle.is_blocked(
        conn, "login-ip", ip_key, limit=config["LOGIN_MAX_FAILURES_PER_IP"], window_seconds=window
    ):
        log.warning("sign-in refused: too many failures", extra={"event": "auth.login.throttled"})
        response = render_template(
            "auth/login.html", values={"email": email}, error=THROTTLED, next_path=next_path
        )
        return response, 429, {"Retry-After": str(window)}

    user = get_user_by_email(conn, email) if email else None
    password_ok = verify_password(password, user["password_hash"] if user else dummy_hash())
    if user is None or not password_ok or not user["is_active"]:
        reason = "unknown_email" if user is None else ("bad_password" if not password_ok else "inactive")
        throttle.record_failure(conn, "login-email", email_key, window_seconds=window)
        throttle.record_failure(conn, "login-ip", ip_key, window_seconds=window)
        log.warning(
            "sign-in failed",
            extra={"event": "auth.login.failed", "reason": reason, "email_ref": email_key[:12]},
        )
        return render_template(
            "auth/login.html", values={"email": email}, error=LOGIN_FAILED, next_path=next_path
        )

    throttle.clear(conn, "login-email", email_key)
    params = parameters_from_config(config)
    if needs_rehash(user["password_hash"], params):
        set_password_hash(conn, user["id"], hash_password(password, params), end_other_sessions=False)
        log.info("password hash upgraded", extra={"event": "auth.password.rehashed", "user_id": user["id"]})
    if user["totp_enabled_at"]:
        # Phase 8.5: the password was right, but the session only starts
        # after the second step (web/two_step.py).
        from .two_step import begin_pending

        return begin_pending(user, next_path)
    record_login(conn, user["id"])
    start_session(user)
    log.info("sign-in succeeded", extra={"event": "auth.login.succeeded", "user_id": user["id"]})
    return redirect(next_path or url_for("main.index"), code=303)


@main_bp.route("/logout", methods=["POST"])
def logout():
    user_id = current_user_id()
    # Cross the session out on the server too, so a copy of this cookie
    # (for example from a shared computer) cannot be used afterwards.
    sessions.revoke(get_db(), current_session_id(), user_id=user_id, now=now_seconds())
    session.clear()
    log.info("signed out", extra={"event": "auth.logout", "user_id": user_id})
    flash("You have signed out.", "success")
    return redirect(url_for("main.login"), code=303)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    config = current_app.config
    if not config["REGISTRATION_ENABLED"]:
        abort(404)
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    if request.method == "GET":
        return render_template("auth/register.html", values={}, errors={}, labels=REGISTER_LABELS)

    conn = get_db()
    ip_key = digest("register-ip", client_ip())
    if throttle.is_blocked(
        conn, "register-ip", ip_key, limit=config["REGISTRATIONS_PER_IP_PER_HOUR"], window_seconds=3600
    ):
        log.warning("registration refused: too many attempts", extra={"event": "auth.register.throttled"})
        errors = {"email": "could not be registered: too many attempts from your network. Try again later."}
        response = render_template("auth/register.html", values={}, errors=errors, labels=REGISTER_LABELS)
        return response, 429, {"Retry-After": "3600"}
    throttle.record_failure(conn, "register-ip", ip_key, window_seconds=3600)  # every attempt counts

    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    errors = {}
    problem = email_problem(email)
    if problem:
        errors["email"] = problem
    problem = password_problem(password, email=email)
    if problem:
        errors["password"] = problem
    elif request.form.get("confirm_password", "") != password:
        errors["confirm_password"] = "does not match the password."
    if not errors:
        try:
            user_id = create_user(conn, email, hash_password(password, parameters_from_config(config)))
        except DuplicateError:
            # Registration cannot avoid revealing that an address is taken
            # without an email-verification step (docs/PHASE_8_SECURITY_REVIEW.md).
            errors["email"] = "is already registered. Sign in instead."
    if errors:
        return (
            render_template(
                "auth/register.html", values={"email": email}, errors=errors, labels=REGISTER_LABELS
            ),
            400,
        )
    user = get_user(conn, user_id)
    record_login(conn, user_id)
    start_session(user)
    log.info("account created", extra={"event": "auth.register.succeeded", "user_id": user_id})
    flash("Your account is ready. Start by adding the instruments you trade.", "success")
    return redirect(url_for("main.instruments"), code=303)

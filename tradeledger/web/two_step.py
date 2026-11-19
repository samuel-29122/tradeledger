"""Two-step verification: setting it up, and the second sign-in step (Phase 8.5).

Sign-in with two-step switched on
---------------------------------
1. /login checks the email and password as before. If they are right and
   the account uses two-step, NO session is started: the cookie only holds
   a "pending" note (user id, session version, time, where to go next) and
   the browser is sent to /login/two-step.
2. /login/two-step asks for the 6-digit code (or a recovery code). The
   pending note is valid for 5 minutes. Wrong codes are counted per account
   (5 per 15 minutes) and per network address, like passwords.
3. A right code starts the real session (web/auth.start_session).

Setting it up (Account -> Two-step verification)
------------------------------------------------
GET shows a new secret (kept only in this browser's signed session until
confirmed) as text and as an otpauth:// link. POST /enable needs the current
password AND a code from the app, which proves the app was set up correctly
before the account depends on it. Ten recovery codes are then shown ONCE.
Turning it off needs the password and a current code or a recovery code.
"""

import logging

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import totp
from ..db import get_db
from ..passwords import verify_password
from ..repositories import throttle, two_step
from ..repositories.users import get_user, record_login
from . import main_bp
from .auth import THROTTLED, client_ip, current_user_id, digest, now_seconds, safe_next, start_session

log = logging.getLogger("tradeledger.auth")

PENDING_KEY = "pending_two_step"
SETUP_KEY = "two_step_setup"
PENDING_SECONDS = 300
WRONG_CODE = "That code is not right. Check the time on your phone, then try the newest code."
LABELS = {"code": "Code", "current_password": "Current password"}


# ---------- Shared checks ----------


def _secret_of(user):
    return totp.decrypt_secret(
        user["totp_secret"], secret_key=current_app.config["SECRET_KEY"], user_id=user["id"]
    )


def check_second_factor(conn, user, code, *, allow_recovery=True):
    """True if `code` is a valid, unused TOTP code or recovery code for
    `user`. A successful TOTP code is recorded so it cannot be replayed."""
    now = now_seconds()
    secret = _secret_of(user)
    if secret is not None:
        step = totp.verify_code(secret, code, now=now, last_step=user["totp_last_step"])
        if step is not None and two_step.accept_step(conn, user_id=user["id"], step=step):
            return "code"
    if (
        allow_recovery
        and totp.looks_like_recovery_code(code)
        and two_step.use_recovery_code(conn, user_id=user["id"], code=code, now=now)
    ):
        return "recovery"
    return None


def _throttle_keys(user_id):
    return digest("two-step", str(user_id)), digest("login-ip", client_ip())


def _blocked(conn, user_id):
    config = current_app.config
    user_key, ip_key = _throttle_keys(user_id)
    window = config["LOGIN_WINDOW_SECONDS"]
    return throttle.is_blocked(
        conn, "two-step", user_key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ) or throttle.is_blocked(
        conn, "login-ip", ip_key, limit=config["LOGIN_MAX_FAILURES_PER_IP"], window_seconds=window
    )


def _record_failure(conn, user_id):
    window = current_app.config["LOGIN_WINDOW_SECONDS"]
    user_key, ip_key = _throttle_keys(user_id)
    throttle.record_failure(conn, "two-step", user_key, window_seconds=window)
    throttle.record_failure(conn, "login-ip", ip_key, window_seconds=window)


# ---------- Second sign-in step ----------


def begin_pending(user, next_path):
    """Called by /login after a correct password for a two-step account."""
    session.clear()
    session[PENDING_KEY] = {
        "uid": user["id"],
        "sv": user["session_version"],
        "at": now_seconds(),
        "next": next_path,
    }
    log.info(
        "password accepted, code required",
        extra={"event": "auth.login.two_step_required", "user_id": user["id"]},
    )
    return redirect(url_for("main.login_two_step"), code=303)


def _pending_user(conn):
    pending = session.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return None, None
    if now_seconds() - int(pending.get("at", 0)) > PENDING_SECONDS:
        session.pop(PENDING_KEY, None)
        return None, None
    user = get_user(conn, pending.get("uid"))
    if (
        user is None
        or not user["is_active"]
        or not user["totp_enabled_at"]
        or user["session_version"] != pending.get("sv")
    ):
        session.pop(PENDING_KEY, None)
        return None, None
    return user, pending


@main_bp.route("/login/two-step", methods=["GET", "POST"])
def login_two_step():
    conn = get_db()
    user, pending = _pending_user(conn)
    if user is None:
        flash("Please sign in again.", "info")
        return redirect(url_for("main.login"), code=303)
    if request.method == "GET":
        return render_template("auth/two_step.html", error=None)
    if _blocked(conn, user["id"]):
        log.warning("two-step refused: too many failures", extra={"event": "auth.two_step.throttled"})
        return render_template("auth/two_step.html", error=THROTTLED), 429
    method = check_second_factor(conn, user, request.form.get("code", ""))
    if method is None:
        _record_failure(conn, user["id"])
        log.warning("two-step code wrong", extra={"event": "auth.two_step.failed", "user_id": user["id"]})
        return render_template("auth/two_step.html", error=WRONG_CODE), 400
    record_login(conn, user["id"])
    start_session(get_user(conn, user["id"]))
    log.info(
        "sign-in succeeded",
        extra={"event": "auth.login.succeeded", "user_id": user["id"], "second_factor": method},
    )
    if method == "recovery":
        left = two_step.unused_recovery_codes(conn, user_id=user["id"])
        flash(
            f"You used a recovery code. {left} unused code(s) left: create new ones on the Account page.",
            "info",
        )
    return redirect(safe_next(pending.get("next")) or url_for("main.index"), code=303)


# ---------- Account page ----------


def _render_setup(status=200, **extra):
    user = g.user
    context = {"enabled": bool(user["totp_enabled_at"]), "errors": {}, "labels": LABELS, "new_codes": None}
    if user["totp_enabled_at"]:
        context["unused_codes"] = two_step.unused_recovery_codes(get_db(), user_id=user["id"])
        context["enabled_at"] = user["totp_enabled_at"]
    else:
        secret = session.get(SETUP_KEY)
        if not secret:
            secret = session[SETUP_KEY] = totp.new_secret()
        context["secret_grouped"] = totp.grouped(secret)
        context["uri"] = totp.provisioning_uri(
            secret, account=user["email"], issuer=current_app.config["TOTP_ISSUER"]
        )
    context.update(extra)
    return render_template("account/two_step.html", **context), status


@main_bp.route("/account/two-step")
def two_step_settings():
    return _render_setup()


def _password_ok(conn, user, errors):
    """Check the current password, counting failures like sign-in does."""
    config = current_app.config
    key = digest("login-email", user["email"])
    window = config["LOGIN_WINDOW_SECONDS"]
    if throttle.is_blocked(
        conn, "login-email", key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ):
        errors["current_password"] = THROTTLED
        return False
    if not verify_password(request.form.get("current_password", ""), user["password_hash"]):
        throttle.record_failure(conn, "login-email", key, window_seconds=window)
        errors["current_password"] = "is incorrect."
        return False
    return True


@main_bp.route("/account/two-step/enable", methods=["POST"])
def two_step_enable():
    conn = get_db()
    user = g.user
    if user["totp_enabled_at"]:
        return redirect(url_for("main.two_step_settings"), code=303)
    secret = session.get(SETUP_KEY)
    if not secret:
        return redirect(url_for("main.two_step_settings"), code=303)
    errors = {}
    password_ok = _password_ok(conn, user, errors)
    step = totp.verify_code(secret, request.form.get("code", ""), now=now_seconds())
    if step is None:
        errors["code"] = "is not right. Enter the 6-digit code your app shows now."
    if errors or not password_ok:
        return _render_setup(400, errors=errors)
    codes = totp.new_recovery_codes()
    two_step.enable(
        conn,
        user_id=user["id"],
        encrypted_secret=totp.encrypt_secret(
            secret, secret_key=current_app.config["SECRET_KEY"], user_id=user["id"]
        ),
        accepted_step=step,
        recovery_codes=codes,
    )
    session.pop(SETUP_KEY, None)
    g.user = get_user(conn, user["id"])
    log.info("two-step enabled", extra={"event": "auth.two_step.enabled", "user_id": user["id"]})
    return _render_setup(201, new_codes=codes)


@main_bp.route("/account/two-step/disable", methods=["POST"])
def two_step_disable():
    conn = get_db()
    user = g.user
    if not user["totp_enabled_at"]:
        abort(404)
    errors = {}
    password_ok = _password_ok(conn, user, errors)
    if password_ok and check_second_factor(conn, user, request.form.get("code", "")) is None:
        errors["code"] = "is not right. Enter a current code from your app, or an unused recovery code."
    if errors:
        return _render_setup(400, errors=errors)
    two_step.disable(conn, user_id=user["id"])
    log.info("two-step disabled", extra={"event": "auth.two_step.disabled", "user_id": user["id"]})
    flash("Two-step verification is off. Signing in now needs only your password.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/two-step/recovery-codes", methods=["POST"])
def two_step_new_codes():
    conn = get_db()
    user = g.user
    if not user["totp_enabled_at"]:
        abort(404)
    errors = {}
    if not _password_ok(conn, user, errors):
        return _render_setup(400, errors=errors)
    codes = totp.new_recovery_codes()
    two_step.replace_recovery_codes(conn, user_id=current_user_id(), codes=codes)
    log.info(
        "recovery codes replaced", extra={"event": "auth.two_step.codes_replaced", "user_id": user["id"]}
    )
    return _render_setup(201, new_codes=codes)

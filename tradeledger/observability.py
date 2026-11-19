"""Logging, request ids and health checks (Phase 8).

Plain English
-------------
When something goes wrong on a server, logs are how you find out what
happened. Good logs say what happened, to which request, and when, without
ever containing passwords, session cookies, API tokens or your private
trading notes.

Technical summary
-----------------
* configure_logging(app): one handler on the "tradeledger" logger family
  and Flask's app.logger. LOG_FORMAT=json writes one JSON object per line,
  which CloudWatch Logs Insights can query by field; "text" is for people.
* RedactingFilter runs on EVERY record and masks, wherever they appear:
  API tokens (tl_...), "Bearer ..." values, passwords in connection URLs
  (postgresql://user:***@host) and password=... / secret=... pairs. It is
  a safety net; the code is also written never to log such values.
* Request ids: each request gets an id (a client may send a safe-looking
  X-Request-ID, e.g. from the load balancer; otherwise a random one). It is
  returned in the X-Request-ID response header and added to every log line
  written during the request, so one failing request can be traced.
* Access log: method, path WITHOUT the query string (search text and
  filters can contain note contents), status, duration, user id.
"""

import json
import logging
import re
import time
import uuid

from flask import g, has_request_context, request

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

_REDACTIONS = (
    (re.compile(r"tl_[A-Za-z0-9_-]{8,}"), "tl_***"),
    (re.compile(r"tlr_[A-Za-z0-9_-]{8,}"), "tlr_***"),  # password-reset links (Phase 8.5)
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|secret_key)(\s*[=:]\s*)('[^']*'|\"[^\"]*\"|\S+)"),
        r"\1\2***",
    ),
)

STANDARD_ATTRIBUTES = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def redact(text):
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Masks secrets in the message, its arguments and any extra fields."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        for key, value in list(vars(record).items()):
            if key not in STANDARD_ATTRIBUTES and isinstance(value, str):
                setattr(record, key, redact(value))
        if has_request_context():
            record.request_id = g.get("request_id")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in STANDARD_ATTRIBUTES and not key.startswith("_") and value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record):
        text = super().format(record)
        extras = {
            k: v
            for k, v in vars(record).items()
            if k not in STANDARD_ATTRIBUTES and not k.startswith("_") and v
        }
        if extras:
            text += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return redact(text)


def configure_logging(app):
    """Attach one redacting handler to the "tradeledger" logger.

    Flask names app.logger after the package, so app.logger IS the
    "tradeledger" logger, and module loggers such as "tradeledger.auth"
    reach the same handler. Flask's own default handler is removed so
    lines are not printed twice. Records still propagate, so pytest's
    caplog sees them (already redacted by the logger-level filter).
    """
    from flask.logging import default_handler

    logger = logging.getLogger("tradeledger")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if app.config["LOG_FORMAT"] == "json" else TextFormatter())
    handler.set_name("tradeledger")
    handler.addFilter(RedactingFilter())  # covers records from child loggers too
    for existing in list(logger.handlers):
        if existing is default_handler or existing.get_name() == "tradeledger":
            logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    # Phase 8.5: the development server ("flask run") writes its own access
    # lines through the "werkzeug" logger, with the full URL, so a one-time
    # reset link (?token=tlr_...) would appear in the terminal. Production
    # runs Gunicorn with its access log switched off, but development and
    # tests get the same protection.
    werkzeug_logger = logging.getLogger("werkzeug")
    if not any(isinstance(f, RedactingFilter) for f in werkzeug_logger.filters):
        werkzeug_logger.addFilter(RedactingFilter())


def register_request_hooks(app):
    access = logging.getLogger("tradeledger.access")

    @app.before_request
    def assign_request_id():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()

    @app.after_request
    def add_request_id(response):
        if "request_id" in g:
            response.headers["X-Request-ID"] = g.request_id
        if app.config["ACCESS_LOG"] and request.endpoint != "static":
            user = g.get("user") or g.get("api_user")
            access.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(
                        (time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000, 1
                    ),
                    "user_id": user["id"] if user else None,
                },
            )
        return response
